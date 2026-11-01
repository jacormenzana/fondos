# -*- coding: utf-8 -*-
"""
shared/backlog_client.py — Fase 3 of the Postgres Action-Points Backlog plan (2026-09-21
session): automatic incident capture into the `gestion` database's `backlog.BACKLOG` /
`backlog.BACKLOG_LOGS` tables (schema deployed by that same session's Fase 1 DDL).

This module must never be the reason a pipeline run fails harder than it already did — the
backlog it writes to is operational tooling, not part of any pipeline's correctness contract.
`report_incident()` and `capture_exceptions()` catch every exception of their own (connection
refused, missing DSN, psycopg3 not installed, CHECK-constraint violation, ...) and degrade to a
logged warning instead of propagating.

Connection: `FONDOS_BACKLOG_PG_DSN` — deliberately a *different* env var from `FONDOS_PG_DSN`
(shared/db.py), which points at the separate P1/P2/P3 operational-migration target. This module
always writes to the `gestion` database's `backlog` schema regardless of which SQLite/Postgres
backend the calling pipeline itself is using. Password via PGPASSWORD env var or a .pgpass file,
same convention as shared/db.py — never on the command line or hardcoded. If the env var is
unset, every function here is a silent no-op: existing pipeline runs are unaffected until an
operator opts in by setting it.

Dedup rule (spec Fase 3 §"Motor de Registro Automático de Eventos y Deduplicación"): before
opening a new ticket, look for an OPEN/TODO/IN_PROGRESS/BLOCKED ticket already tracking the same
(project_code, object_name) opened within `dedup_window`. If found, append a reincidence note to
its BACKLOG_LOGS instead of opening a duplicate; otherwise open a new ticket with
nature='BUG', priority='HIGH', reporter='SYSTEM_AUTODETECT', status='OPEN' — exactly the Fase 3
spec defaults.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN"
_HUMAN_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_HUMAN"          # the owner-only role (FND-0337); deliberately NOT in .env
_LOGGER_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_LOGGER"        # role backlog_logger (FND-0354): appends notes to EXISTING tickets, nothing else; lives in the git-ignored .env
_INCIDENT_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_INCIDENT"    # role backlog_incident (FND-0354): the automatic incident capture, insert-only; lives in the git-ignored .env
_GATE_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_GATE"            # role backlog_gate (FND-0334 C2): the commit-msg hook's login, three routines and nothing else; lives in the git-ignored .env; NO fallback to another login
_ADJUDICATOR_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_ADJUDICATOR"   # role backlog_adjudicator (FND-0334 C3): decides collision pairs; created by the owner, kept OUTSIDE .env (typed in his own terminal)
_AGENT_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_AGENT"          # role backlog_agent (Backlog v3 W2, FND-0357): an agent opens and moves tickets through a short list of routines; lives in the git-ignored .env; NO fallback to another login
_AUTOPILOT_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_AUTOPILOT"  # role backlog_autopilot (Backlog v3 W3, FND-0358): records facts, appends notes and makes the two evidence-backed moves; lives in the git-ignored .env; NO fallback to another login
_FACTS_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_FACTS"           # role backlog_facts (Backlog v3.1, FND-0355): records the DEPLOY fact and nothing else; kept in the OWNER VAULT (the deploy watcher reads it), never in .env; NO fallback to another login
AUTOPILOT_ACTOR = "system:autopilot"                         # the one identity of the autopilot process (scripts/audit/backlog_autopilot.py sets FONDOS_ACTOR to it); sp_auto_transition fixes it in the database too
NON_INTERACTIVE_OPERATIONS = ("agent_log", "incident", "gate", "agent", "autopilot", "facts")   # the ONLY writes a non-interactive process may make (owner decisions 2026-10-29: agent_log, incident; 2026-10-31: gate, agent, autopilot; 2026-11-01, plan v3.1: facts); anything else is refused by confirm_live_write
_SCOPED_DSN_ENV = {"gate": _GATE_DSN_ENV_VAR, "agent": _AGENT_DSN_ENV_VAR, "autopilot": _AUTOPILOT_DSN_ENV_VAR, "facts": _FACTS_DSN_ENV_VAR}   # operations that use their own login EXCLUSIVELY: without its DSN there is no live write
_FALLBACK_LOG_ENV_VAR = "FONDOS_BACKLOG_FALLBACK_LOG"
_DEFAULT_FALLBACK_LOG = Path(__file__).resolve().parent / "log" / "backlog_autolog_fallback.jsonl"   # == LOG_DIRS["shared"]

try:
    import psycopg
except ImportError:  # pragma: no cover — psycopg3 not installed; every call below no-ops
    psycopg = None  # type: ignore[assignment]


def _dsn() -> Optional[str]:
    return os.environ.get(_DSN_ENV_VAR)


def _fallback_log_path() -> Path:
    override = os.environ.get(_FALLBACK_LOG_ENV_VAR)
    return Path(override) if override else _DEFAULT_FALLBACK_LOG


def _write_fallback(**fields) -> None:
    """
    Last-resort local record when Postgres itself is unreachable/erroring — so a real incident
    is never silently lost just because the backlog store is the thing that's down. Best-effort:
    if even this fails (read-only filesystem, disk full), it logs and gives up — it must not
    raise back into report_incident()'s already-broad except clause.
    """
    try:
        path = _fallback_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"logged_at": datetime.now(timezone.utc).isoformat(), **fields}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.warning("[BACKLOG-AUTOLOG] Postgres write failed — incident recorded to fallback log %s", path)
    except Exception:
        logger.warning("[BACKLOG-AUTOLOG] fallback log write also failed — incident not persisted anywhere", exc_info=True)


def _overlap_settings() -> Optional[tuple]:
    """(high_cosine, medium_cosine) when the two-tier overlap policy is switched on, else None. Kept apart so tests can set it
    and so a missing / broken config can never get in the way of recording an incident."""
    try:
        from shared import config
        if not getattr(config, "BACKLOG_INCIDENT_AUTO_APPEND_ENABLED", False):
            return None
        return (float(config.BACKLOG_INCIDENT_HIGH_COSINE), float(config.BACKLOG_INCIDENT_MEDIUM_COSINE))
    except Exception:
        return None


def _overlap_policy(cur, object_name: str, scenario_description: str) -> Optional[dict]:
    """Owner safeguard S-2: compare an incident with the OPEN tickets (shared/backlog_overlap.py). None when the policy is off or
    cannot be evaluated -- an overlap check must never stop an incident from being recorded."""
    settings = _overlap_settings()
    if settings is None:
        return None
    try:
        from shared.backlog_overlap import classify_incident
        with cur.connection.transaction():      # a savepoint: a failing check must not abort the incident's own inserts
            cur.execute(
                "SELECT action_point_id, title, object_name, scenario_description FROM backlog.BACKLOG "
                "WHERE status IN ('OPEN','TODO','IN_PROGRESS','BLOCKED')"
            )
            cols = [c.name for c in cur.description]
            candidates = [dict(zip(cols, r)) for r in cur.fetchall()]
        return classify_incident(scenario=scenario_description, object_name=object_name, candidates=candidates,
                                 high_cosine=settings[0], medium_cosine=settings[1])
    except Exception:
        logger.warning("[BACKLOG-AUTOLOG] overlap check failed -- recording the incident without it", exc_info=True)
        return None


def report_incident(
    *,
    object_name: str,
    scenario_description: str,
    project_code: str = "FND",
    schema_name: Optional[str] = None,
    object_type: str = "JOB",
    title: Optional[str] = None,
    dedup_window: str = "24 hours",
) -> Optional[str]:
    """
    Record an operational exception/anomaly as a BACKLOG ticket (or a reincidence log entry on
    an existing one). Returns the action_point_id touched, or None if the write could not be
    made for any reason — callers should not, and do not need to, branch on the return value.
    """
    dsn = dsn_for_operation("incident")
    if not dsn:
        logger.info(
            "[BACKLOG-AUTOLOG] disabled — %s not set — skipping incident capture for %s",
            _INCIDENT_DSN_ENV_VAR, object_name,
        )
        return None
    if psycopg is None:
        logger.info(
            "[BACKLOG-AUTOLOG] disabled — psycopg3 not installed — skipping incident capture for %s",
            object_name,
        )
        return None

    try:
        # connect_timeout only bounds the TCP/auth handshake, not query execution — this code
        # runs inside the exception handler of an already-crashing pipeline, so a hung/locked
        # server must not add its own indefinite delay on top. statement_timeout covers that.
        with _connect_allowlisted(
            "incident", dsn, autocommit=False, connect_timeout=5,
            options="-c statement_timeout=5000",
        ) as conn:
            with conn.cursor() as cur:
                if _routine_present(cur, "sp_report_incident"):
                    # FND-0354: the insert-only routine does the dedup, the reincidence note and the ticket; the client only reads (overlap) and passes the result.
                    cur.execute(
                        "SELECT 1 FROM backlog.BACKLOG WHERE project_code = %s AND object_name = %s AND status IN ('OPEN','TODO','IN_PROGRESS','BLOCKED') "
                        "AND creation_ts >= now() - %s::interval LIMIT 1", (project_code, object_name, dedup_window))
                    policy = None if cur.fetchone() else _overlap_policy(cur, object_name, scenario_description)
                    high = policy if policy and policy["tier"] == "HIGH" else None
                    medium = policy if policy and policy["tier"] == "MEDIUM" else None
                    cur.execute(
                        "SELECT backlog.sp_report_incident(p_object_name => %s, p_description => %s, p_project_code => %s, p_schema_name => %s, p_object_type => %s, p_title => %s, "
                        "p_dedup => %s::interval, p_overlap_ticket => %s, p_overlap_reason => %s, p_suggest_ticket => %s, p_suggest_reason => %s)",
                        (object_name, scenario_description, project_code, schema_name, object_type, title, dedup_window,
                         high["ticket"] if high else None, high["reason"] if high else None, medium["ticket"] if medium else None, medium["reason"] if medium else None))
                    apid = cur.fetchone()[0]
                    conn.commit()
                    logger.warning("[BACKLOG-AUTOLOG] Incident recorded on %s (%s)", apid, object_name)
                    return apid
                cur.execute(
                    """
                    SELECT action_point_id FROM backlog.BACKLOG
                    WHERE project_code = %s AND object_name = %s
                      AND status IN ('OPEN','TODO','IN_PROGRESS','BLOCKED')
                      AND creation_ts >= now() - %s::interval
                    ORDER BY creation_ts DESC LIMIT 1
                    """,
                    (project_code, object_name, dedup_window),
                )
                row = cur.fetchone()
                if row:
                    apid = row[0]
                    cur.execute(
                        """
                        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
                        VALUES (%s, 'SYSTEM_AUTODETECT', %s)
                        """,
                        (apid, f"Reincidence detected: {scenario_description}"),
                    )
                    conn.commit()
                    logger.warning(
                        "[BACKLOG-AUTOLOG] Reincidence logged on existing %s (%s)", apid, object_name,
                    )
                    return apid

                policy = _overlap_policy(cur, object_name, scenario_description)
                if policy and policy["tier"] == "HIGH":
                    apid = policy["ticket"]
                    cur.execute(
                        """
                        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
                        VALUES (%s, 'SYSTEM_AUTODETECT', %s)
                        """,
                        (apid, f"Reincidence detected (overlap: {policy['reason']}) on {object_name}: {scenario_description}"),
                    )
                    conn.commit()
                    logger.warning("[BACKLOG-AUTOLOG] Incident appended to overlapping %s (%s)", apid, policy["reason"])
                    return apid
                suggestion = policy if policy and policy["tier"] == "MEDIUM" else None

                first_log = (f"Auto-opened: {scenario_description}"
                             + (f"\n\nPossibly related (NOT merged): {suggestion['ticket']} -- {suggestion['reason']}"
                                if suggestion else ""))
                new_title = title or f"[auto] {object_name}: {scenario_description[:100]}"
                if _transitions_present(cur):
                    # Backlog v2 Phase A (FND-0331): ONE routine opens the ticket, its first log and its first status event, with a canonical reporter.
                    cur.execute(
                        "SELECT backlog.sp_open_ticket(p_title => %s, p_nature => 'BUG', p_object_type => %s, p_object_name => %s, p_priority => 'HIGH', "
                        "p_actor => %s, p_session_id => %s, p_description => %s, p_first_log => %s, p_schema_name => %s, p_project_code => %s)",
                        (new_title, object_type, object_name, _AUTODETECT_ACTOR, make_session_id("AUTODETECT", "REVB"),
                         scenario_description, first_log, schema_name, project_code),
                    )
                    apid = cur.fetchone()[0]
                else:                                   # step A-1 is applied in production; without it NOTHING writes a ticket: the legacy raw INSERTs were removed (owner order 2026-10-28)
                    raise BacklogUnavailable("Backlog v2 step A-1 (backlog.sp_open_ticket) is not applied: the incident goes to the local fallback log, not to the backlog")
                conn.commit()
                logger.warning("[BACKLOG-AUTOLOG] New incident opened: %s (%s)", apid, object_name)
                return apid
    except Exception:
        logger.warning(
            "[BACKLOG-AUTOLOG] Failed to record incident for %s — pipeline continues",
            object_name, exc_info=True,
        )
        _write_fallback(
            project_code=project_code, object_name=object_name, schema_name=schema_name,
            object_type=object_type, title=title, scenario_description=scenario_description,
        )
        return None


@contextmanager
def capture_exceptions(
    *,
    object_name: str,
    project_code: str = "FND",
    schema_name: Optional[str] = None,
    object_type: str = "JOB",
) -> Iterator[None]:
    """
    Wrap a pipeline entry point: on any Exception, best-effort log it to the backlog (via
    report_incident) and then re-raise unchanged — this never swallows, delays, or changes the
    original exception, exit code, or traceback. Intended for `if __name__ == "__main__":`
    blocks of the canonical launchers; not for use inside per-fund/per-ISIN loops, where a
    single fund's failure is expected and already handled by the pipeline's own error counters.
    """
    try:
        yield
    except Exception as exc:
        tb = traceback.format_exc()
        try:
            report_incident(
                object_name=object_name,
                project_code=project_code,
                schema_name=schema_name,
                object_type=object_type,
                scenario_description=f"{type(exc).__name__}: {exc}\n\n{tb[-4000:]}",
            )
        except Exception:
            # report_incident() already guards itself end-to-end; this is defense in depth so
            # that even a caller-supplied monkeypatch/override can never mask the ORIGINAL
            # exception below.
            logger.warning("[BACKLOG-AUTOLOG] report_incident raised unexpectedly", exc_info=True)
        raise


def install_excepthook(
    *,
    object_name: str,
    project_code: str = "FND",
    schema_name: Optional[str] = None,
    object_type: str = "JOB",
) -> None:
    """
    One-line alternative to capture_exceptions() for an entry point whose `__main__` block is long
    (re-indenting it into a `with` block just to add a wrapper is churn and risk):

        if __name__ == "__main__":
            from shared.backlog_client import install_excepthook
            install_excepthook(object_name="export_metrics.py")
            ...existing code, unchanged...

    Installs a `sys.excepthook` that best-effort logs an UNHANDLED Exception to the backlog (via
    report_incident) and then hands over to the previous hook, so the traceback, the exit code and
    everything else behave exactly as before. SystemExit and KeyboardInterrupt never reach an
    excepthook as Exceptions and are not reported. Like the rest of this module it is a silent
    no-op while FONDOS_BACKLOG_PG_DSN is unset.
    """
    previous = sys.excepthook

    def _hook(exc_type, exc, tb):
        if isinstance(exc, Exception):
            try:
                text = "".join(traceback.format_exception(exc_type, exc, tb))
                report_incident(
                    object_name=object_name,
                    project_code=project_code,
                    schema_name=schema_name,
                    object_type=object_type,
                    scenario_description=f"{exc_type.__name__}: {exc}\n\n{text[-4000:]}",
                )
            except Exception:
                logger.warning("[BACKLOG-AUTOLOG] report_incident raised unexpectedly", exc_info=True)
        previous(exc_type, exc, tb)

    sys.excepthook = _hook


def get_action_point_status(ap_id: str) -> str:
    """Read-only status of one backlog ticket for the cycle telemetry (FND-0239): its real status, 'NOT_FOUND' (the backlog answered and has
    no such AP) or 'LOOKUP_FAILED' (no DSN, outage, timeout -- never to be read as "no ticket"). Never raises; same 5 s limits as the
    rest of this module's short-lived connections."""
    try:
        dsn = _dsn()
        if not dsn or psycopg is None:
            return "LOOKUP_FAILED"
        with psycopg.connect(dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            row = conn.execute("SELECT status FROM backlog.backlog WHERE action_point_id = %s", (ap_id,)).fetchone()
        return "NOT_FOUND" if row is None else str(row[0])
    except Exception:
        return "LOOKUP_FAILED"


# --- Relationships between action points (db/pg_backlog/20_links.sql; procedure in NORMAS_BACKLOG.md) ------------------
# Unlike report_incident(), this API is FAIL-LOUD: its callers are operator tools (scripts/audit/backlog_links.py) whose whole
# result IS the data, so an unreachable backlog must stop them -- it must never read as "no tickets" or "no links". Every function
# takes an optional `conn` (a caller-owned psycopg connection, never committed here) so the hermetic tests and a multi-step
# caller share one transaction; without it each call opens its own short-lived connection under FONDOS_BACKLOG_PG_DSN.

LINK_TYPES = ("DEPENDS_ON", "RELEASE_WITH", "OVERLAPS", "DUPLICATES", "SPLIT_FROM", "RELATED", "IMPACTS", "SUPERSEDES")
LINK_STATUSES = ("PROPOSED", "CONFIRMED", "REJECTED")
PHASE_B_ROUTINES = ("trg_link_cycle", "fn_gate_mode", "fn_gate_record", "fn_gate_blockers", "fn_raise_review_flags", "sp_ack_review", "sp_raise_hold", "sp_release_hold", "sp_set_gate", "sp_annotate_wait")
GATE_MODES = ("OFF", "AUDIT", "ENFORCE")                         # the delivery gate: Phase B deploys in AUDIT (evaluate and log, never refuse); ENFORCE needs a clear 48-hour audit on 5+ tickets
LINK_ORIGINS = ("REVIEW", "GATE", "STEP0", "SOURCE")                 # the column origin of backlog_link_event (Phase B); REVIEW is the default
_ANALYSIS_OPTIONS = "-c statement_timeout=30000"

_TICKET_COLS = (
    "action_point_id, title, project_code, nature, schema_name, object_type, object_name, status, priority, reporter, "
    "assignee, creation_ts, update_ts, closure_ts, solution_type, release_version, scenario_description"
)
_LINK_COLS = "event_id, from_id, to_id, link_type, status, evidence, absorbs, username, event_ts"


class BacklogUnavailable(RuntimeError):
    """The backlog cannot be reached (no DSN, no driver). Raised instead of returning an empty answer."""


class HumanDsnMissing(BacklogUnavailable):
    """A human actor writes only through the owner-only role (FND-0337) and its connection string is not available (FONDOS_BACKLOG_PG_DSN_HUMAN). Raised instead of
    falling back to the shared writer connection, which the database would refuse anyway once the role exists."""


def dsn_for_actor(actor: Optional[str]) -> Optional[str]:
    """The connection string an actor must write with: the owner-only one (FONDOS_BACKLOG_PG_DSN_HUMAN, kept outside .env and outside the environment agents inherit) for
    human:*, the shared writer one for everyone else. Raises HumanDsnMissing for a human actor when it is not set; never falls back."""
    if str(actor or "").startswith("human:"):
        dsn = os.environ.get(_HUMAN_DSN_ENV_VAR)
        if not dsn:
            raise HumanDsnMissing(f"{_HUMAN_DSN_ENV_VAR} is not set: the human actor {actor} writes only through the owner-only role (type it in your own terminal; never put it in .env)")
        return dsn
    return _dsn()


def dsn_for_operation(operation: str) -> Optional[str]:
    """The connection string of an allow-listed non-interactive operation: its own login (FONDOS_BACKLOG_PG_DSN_LOGGER / _INCIDENT), else the general one (transition period, until the
    shared writer retires: with a reader login in FONDOS_BACKLOG_PG_DSN the database refuses the write, which is the point). libpq parameters in the string (sslmode, sslcert, sslkey,
    sslrootcert) are passed to the driver untouched."""
    if operation not in NON_INTERACTIVE_OPERATIONS:
        raise ValueError(f"operation {operation!r} is not allow-listed for non-interactive writes: {NON_INTERACTIVE_OPERATIONS}")
    scoped = _SCOPED_DSN_ENV.get(operation)
    if scoped:                                                      # the hook (gate) and the agent use their scoped login EXCLUSIVELY: without it there is no live write, never the general writer
        return os.environ.get(scoped) or None
    return os.environ.get(_LOGGER_DSN_ENV_VAR if operation == "agent_log" else _INCIDENT_DSN_ENV_VAR) or _dsn()


def agent_operation(who: str) -> Optional[str]:
    """'agent' when `who` is an agent:<model> identity, the scoped agent login is configured (FONDOS_BACKLOG_PG_DSN_AGENT) and the switch BACKLOG_AGENT_WRITES_ENABLED is on: its writes go through
    backlog_agent (owner decision 2026-10-31, Backlog v3 W2) and need no terminal. Otherwise None, and the caller takes the console path exactly as before. A system:* or human:* actor never routes here."""
    if not str(who).startswith("agent:") or not os.environ.get(_AGENT_DSN_ENV_VAR):
        return None
    try:
        from shared import config
        if not getattr(config, "BACKLOG_AGENT_WRITES_ENABLED", True):
            return None
    except Exception:  # noqa: BLE001 -- a broken config must not turn the route on or off by accident; the DSN decides
        pass
    return "agent"


def autopilot_operation(who: str) -> Optional[str]:
    """'autopilot' when `who` is system:autopilot and its scoped login is configured (FONDOS_BACKLOG_PG_DSN_AUTOPILOT); otherwise None (the console path, exactly as before). Only the
    helpers the autopilot may use call it: record_ops_event, append_log and auto_transition."""
    return "autopilot" if str(who) == AUTOPILOT_ACTOR and os.environ.get(_AUTOPILOT_DSN_ENV_VAR) else None


def _connect_allowlisted(operation: str, dsn: str, **kw):
    """THE connection factory of the non-interactive writes: it checks the allow-list, then opens the connection. Raises ValueError for an operation that is not allow-listed."""
    confirm_live_write(_target_of(dsn), operation=operation)
    if psycopg is None:
        raise BacklogUnavailable("psycopg3 is not installed (pip install 'psycopg[binary]')")
    return psycopg.connect(dsn, **kw)


def _routine_present(cur, name: str) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace = 'backlog'::regnamespace AND proname = %s)", (name,))
    return bool(cur.fetchone()[0])


LIVE_WRITE_PHRASE = "I CONFIRM THIS IS NOT A SYNTHETIC TEST"
_live_write_confirmed = False


class LiveWriteNotConfirmed(BacklogUnavailable):
    """A person at a terminal did not type the confirmation phrase before a write to the LIVE backlog."""


class LiveWriteForbidden(BacklogUnavailable):
    """A NON-interactive process (an agent's shell tool, a script, a pipeline, a hook) tried to write to the LIVE backlog. Hard fail, no exception (owner order 2026-10-29)."""


LIVE_WRITE_EXIT_CODE = 1


def _is_console(stream) -> bool:
    """True only for a REAL terminal. `isatty()` alone is wrong on Windows: it answers True for the NUL device (what a tool shell gets as stdin) and a pipe answers False, so on Windows the handle
    must also be a console input handle (GetConsoleMode succeeds only for a console). A missing stream (pythonw) is not a console."""
    try:
        if stream is None or not stream.isatty():
            return False
        if os.name == "nt":
            import ctypes
            import msvcrt
            mode = ctypes.c_uint32()
            return bool(ctypes.windll.kernel32.GetConsoleMode(msvcrt.get_osfhandle(stream.fileno()), ctypes.byref(mode)))
        return True
    except Exception:  # noqa: BLE001
        return False


def confirm_live_write(target: str, *, interactive: Optional[bool] = None, ask=input, environ: Optional[dict] = None, operation: Optional[str] = None) -> bool:
    """The gate on every write that opens ITS OWN connection to the live backlog (owner orders 2026-10-28 and 2026-10-29):
    * a NON-interactive process (no terminal: an agent's shell tool, a script, a pipeline, a hook) is REFUSED with LiveWriteForbidden: autonomous or scripted writes to the live production
      backlog are banned outside authorized, signed CI/CD deployments, and there is no exception in this code (an authorization mechanism, if the owner wants one, is a decision of his: see FND-0351);
    * a person at a terminal must type exactly LIVE_WRITE_PHRASE, once per process, else LiveWriteNotConfirmed and nothing is written.
    Allow-list (owner decision 2026-10-29): the two operations in NON_INTERACTIVE_OPERATIONS pass without a terminal because the DATABASE scopes them (backlog_logger can only append a note to an
    existing ticket, backlog_incident can only run the insert-only incident routine); any other `operation` is a programming error. Never asked and never refused: pytest (tests cannot see the
    live DSN: shared/testing/sandbox.py) and callers that own their connection (tests, staging)."""
    global _live_write_confirmed
    env = os.environ if environ is None else environ
    if operation is not None:
        if operation not in NON_INTERACTIVE_OPERATIONS:
            raise ValueError(f"operation {operation!r} is not allow-listed for non-interactive writes: {NON_INTERACTIVE_OPERATIONS}")
        return True
    if _live_write_confirmed or env.get("PYTEST_CURRENT_TEST"):
        return True
    if interactive is None:
        interactive = _is_console(sys.stdin)
    try:
        who = current_actor(env)
    except ActorUnknown:
        who = "(FONDOS_ACTOR is not set)"
    if not interactive:
        raise LiveWriteForbidden(f"live write REFUSED (exit code {LIVE_WRITE_EXIT_CODE}): a non-interactive process may not write to the LIVE backlog ({target}) as {who}; only a person at a terminal can, after confirming. "
                                 "Nothing was written. (To test, use the staging container: FND-0351.)")
    answer = ask(f"\nYou are about to WRITE to the LIVE backlog ({target}) as {who}.\nType exactly: {LIVE_WRITE_PHRASE}\n> ")
    if str(answer).strip() != LIVE_WRITE_PHRASE:
        raise LiveWriteNotConfirmed("live write not confirmed: nothing was written (to test, use the staging container: FND-0351)")
    _live_write_confirmed = True
    return True


def require_live_write_cli(dsn: Optional[str] = None, *, environ: Optional[dict] = None, interactive: Optional[bool] = None, ask=input) -> Optional[int]:
    """For the command-line tools that write: None when the write may go on; otherwise the message is printed on stderr and the process exit code to return (LIVE_WRITE_EXIT_CODE = 1)."""
    try:
        confirm_live_write(_target_of(dsn or _dsn() or ""), environ=environ, interactive=interactive, ask=ask)
        return None
    except (LiveWriteForbidden, LiveWriteNotConfirmed) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return LIVE_WRITE_EXIT_CODE


def _target_of(dsn: str) -> str:
    """host:port/dbname of a DSN, never its credentials."""
    try:
        from psycopg.conninfo import conninfo_to_dict
        d = conninfo_to_dict(dsn)
        return f"{d.get('host') or 'local'}:{d.get('port') or '5432'}/{d.get('dbname') or '?'}"
    except Exception:  # noqa: BLE001
        return "the configured backlog"


@contextmanager
def _session(conn=None, *, human: bool = False, write: bool = False, operation: Optional[str] = None, adjudicator: bool = False):
    if conn is not None:
        yield conn
        return
    if adjudicator:                                        # a person at a terminal, with the login only the owner holds; never an allow-listed (non-interactive) operation
        dsn = os.environ.get(_ADJUDICATOR_DSN_ENV_VAR)
        if not dsn:
            raise BacklogUnavailable(f"{_ADJUDICATOR_DSN_ENV_VAR} is not set: the adjudicator login is the owner's, typed in his own terminal and never kept in .env")
    else:
        dsn = dsn_for_actor("human:x") if human else (dsn_for_operation(operation) if operation else _dsn())
    if not dsn:
        raise BacklogUnavailable(f"{_SCOPED_DSN_ENV.get(operation, _DSN_ENV_VAR)} is not set")
    if psycopg is None:
        raise BacklogUnavailable("psycopg3 is not installed (pip install 'psycopg[binary]')")
    if write:
        confirm_live_write(_target_of(dsn), operation=operation)
    # `with` commits on a normal exit and rolls back on an exception, then closes.
    with psycopg.connect(dsn, connect_timeout=5, options=_ANALYSIS_OPTIONS) as own:
        yield own


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def list_tickets(*, open_only: bool = False, closed_since: Optional[datetime] = None,
                 ids: Optional[list[str]] = None, conn=None) -> list[dict]:
    """Tickets of `backlog.BACKLOG`, ordered by id. `open_only` = status <> CLOSED; adding `closed_since` also returns the
    tickets closed at or after it (the sweep set: open + recently closed). `ids` restricts the answer (a requested id that is
    missing from it no longer exists -- the staleness pre-flight of `--apply-review` relies on that)."""
    if closed_since is not None and not open_only:
        raise ValueError("closed_since only applies together with open_only=True")
    where: list[str] = []
    params: list = []
    if ids is not None:
        where.append("action_point_id = ANY(%s)")
        params.append(list(ids))
    if open_only and closed_since is None:
        where.append("status <> 'CLOSED'")
    elif open_only:
        where.append("(status <> 'CLOSED' OR closure_ts >= %s)")
        params.append(closed_since)
    sql = f"SELECT {_TICKET_COLS} FROM backlog.BACKLOG"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _session(conn) as c:
        return _rows(c, sql + " ORDER BY action_point_id", tuple(params))


def list_logs(*, ids: Optional[list[str]] = None, conn=None) -> list[dict]:
    """Progress log rows of `backlog.BACKLOG_LOGS` (oldest first within a ticket), optionally for the given ticket ids."""
    sql = "SELECT log_id, action_point_id, log_ts, username, comment FROM backlog.BACKLOG_LOGS"
    params: tuple = ()
    if ids is not None:
        sql += " WHERE action_point_id = ANY(%s)"
        params = (list(ids),)
    with _session(conn) as c:
        return _rows(c, sql + " ORDER BY action_point_id, log_ts, log_id", params)


def links_store_present(*, conn=None) -> bool:
    """True when the relationship DDL (db/pg_backlog/20_links.sql) has been applied. Lets a read-only tool say "links not
    deployed yet" instead of failing -- or, worse, reporting an empty link set as if it were a reviewed one."""
    with _session(conn) as c:
        (reg,) = c.execute("SELECT to_regclass('backlog.v_backlog_link') IS NOT NULL").fetchone()
    return bool(reg)


def list_links(*, history: bool = False, status: Optional[str] = None, ids: Optional[list[str]] = None,
               conn=None) -> list[dict]:
    """Links between tickets. Default: the CURRENT state of every (from, to, type) pair (`v_backlog_link`, latest event wins).
    `history=True` returns every event instead, oldest first. `status` keeps one state; `ids` keeps the links that touch any of
    those tickets (either end)."""
    if status is not None and status not in LINK_STATUSES:
        raise ValueError(f"status must be one of {LINK_STATUSES}, got {status!r}")
    where: list[str] = []
    params: list = []
    if status is not None:
        where.append("status = %s")
        params.append(status)
    if ids is not None:
        where.append("(from_id = ANY(%s) OR to_id = ANY(%s))")
        params += [list(ids), list(ids)]
    src = "backlog.backlog_link_event" if history else "backlog.v_backlog_link"
    sql = f"SELECT {_LINK_COLS} FROM {src}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with _session(conn) as c:
        return _rows(c, sql + " ORDER BY from_id, to_id, link_type, event_ts, event_id", tuple(params))


_AGENT_LOG_ACTOR = re.compile(r"^(agent|system):[A-Za-z0-9._-]+$")


def append_log(action_point_id: str, username: str, comment: str, *, conn=None) -> int:
    """Append one progress note to `backlog.BACKLOG_LOGS` (insert-only, like every progress record: AGENTS.md "never overwrite
    prior progress"). Does NOT touch the ticket's status. Fail-loud; returns the new log_id."""
    if not str(action_point_id or "").strip() or not str(username or "").strip() or not str(comment or "").strip():
        raise ValueError("action_point_id, username and comment are all required")
    if conn is None and _AGENT_LOG_ACTOR.match(str(username)):
        # FND-0354 (owner decision 2026-10-29): an agent or system identity appends through the scoped routine with its own login; the allow-list lets it run without a terminal.
        with _session(None, write=True, operation=autopilot_operation(username) or "agent_log") as c:
            with c.cursor() as cur:
                if not _routine_present(cur, "sp_append_log"):
                    raise BacklogUnavailable("backlog.sp_append_log is missing: the owner applies migrate_backlog_v2.py --phase iam first (until then an agent's notes wait)")
                cur.execute("SELECT backlog.sp_append_log(%s, %s, %s)", (action_point_id, username, comment))
                return cur.fetchone()[0]
    with _session(conn, human=str(username).startswith("human:"), write=True) as c:
        (log_id,) = c.execute(
            "INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, %s) RETURNING log_id",
            (action_point_id, username, comment),
        ).fetchone()
    return log_id


def record_link_events(events: list[dict], *, conn=None) -> list[int]:
    """Append link events (insert-only; a retype, confirmation or rejection is a NEW event, never an update) in ONE transaction:
    every event is validated before the first insert, so a batch is applied whole or not at all. Each event is a dict with
    from_id, to_id, link_type, status, evidence, username and optional absorbs. Returns the new event ids in order. Raises
    ValueError on a bad event and the driver's error (e.g. a foreign-key violation on an unknown ticket); never swallows."""
    checked = []
    for ev in events:
        missing = [k for k in ("from_id", "to_id", "link_type", "status", "evidence", "username") if not str(ev.get(k) or "").strip()]
        if missing:
            raise ValueError(f"link event {ev!r} lacks {missing}")
        if ev["link_type"] not in LINK_TYPES:
            raise ValueError(f"link_type must be one of {LINK_TYPES}, got {ev['link_type']!r}")
        if ev["status"] not in LINK_STATUSES:
            raise ValueError(f"status must be one of {LINK_STATUSES}, got {ev['status']!r}")
        if ev["from_id"] == ev["to_id"]:
            raise ValueError(f"a ticket cannot be linked to itself ({ev['from_id']})")
        if ev.get("origin") not in (None, *LINK_ORIGINS):
            raise ValueError(f"origin must be one of {LINK_ORIGINS}, got {ev['origin']!r}")
        checked.append((ev["from_id"], ev["to_id"], ev["link_type"], ev["status"], ev["evidence"], ev.get("absorbs"),
                        ev["username"], ev.get("origin")))
    ids: list[int] = []
    op = agent_operation(checked[0][6]) if checked and all(str(row[6]).startswith("agent:") for row in checked) else None      # an agent writes through the routine of its scoped login
    with _session(conn, human=any(str(ev["username"]).startswith("human:") for ev in events), write=True, operation=op) as c:
        for row in checked:
            if op:
                (event_id,) = c.execute("SELECT backlog.sp_record_link(%s, %s, %s, %s, %s, %s, %s, %s)", row).fetchone()
                ids.append(event_id)
                continue
            if row[7] is None:                                   # the column origin exists only after Phase B (40_relations_v2.sql): name it only when asked
                sql = ("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, absorbs, username) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING event_id")
                row = row[:7]
            else:
                sql = ("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, absorbs, username, origin) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING event_id")
            (event_id,) = c.execute(sql, row).fetchone()
            ids.append(event_id)
    return ids


def record_link_event(*, from_id: str, to_id: str, link_type: str, status: str, evidence: str, username: str,
                      absorbs: Optional[str] = None, origin: Optional[str] = None, conn=None) -> int:
    """One-event form of record_link_events()."""
    return record_link_events([{
        "from_id": from_id, "to_id": to_id, "link_type": link_type, "status": status,
        "evidence": evidence, "absorbs": absorbs, "username": username, "origin": origin,
    }], conn=conn)[0]


# --- Actor identity (db/pg_backlog/25_actor_alias.sql; FND-0330) ----------------------------------------------------------
# Logs and link events are append-only, so their raw `username` is never rewritten: `backlog.actor_alias` maps each raw spelling to
# one canonical identity and the views read through it. New writes carry the canonical form directly, taken from FONDOS_ACTOR.

ACTOR_PATTERN = re.compile(r"^(human|agent|system):[A-Za-z0-9._-]+$")     # == the CHECK of backlog.actor_alias (tests keep them equal)
_ACTOR_ENV_VAR = "FONDOS_ACTOR"
_AGENT_ACTOR_ENV_VAR = "FONDOS_AGENT_ACTOR"          # agent sessions only (see current_actor); lives in the git-ignored .env, never in a tracked file


class ActorUnknown(ValueError):
    """No canonical actor could be established. Raised instead of guessing: the OS login is the owner's for every session that runs
    under their account, so it cannot tell the owner from an agent."""


def current_actor(environ: Optional[dict] = None) -> str:
    """The canonical identity of whoever is writing, from FONDOS_ACTOR (e.g. `agent:claude-opus-5-5`, `human:jacor`). Raises ActorUnknown
    when it is unset or not in canonical form; it never falls back to the OS user."""
    env = os.environ if environ is None else environ
    value = (env.get(_ACTOR_ENV_VAR) or "").strip()
    if not value and str(env.get("CLAUDECODE") or "").strip() == "1":
        # Inside a Claude Code session ONLY (the harness sets CLAUDECODE=1; a person's terminal does not), the agent identity may come from FONDOS_AGENT_ACTOR, a key of the git-ignored .env that
        # shared.config loads. A person's shell never inherits it, so his commands cannot be labelled as the agent's; FONDOS_ACTOR, when set, always wins.
        value = (env.get(_AGENT_ACTOR_ENV_VAR) or "").strip()
    if not value:
        raise ActorUnknown(f"{_ACTOR_ENV_VAR} is not set (expected human:<login> | agent:<model> | system:<name>; an agent session may declare {_AGENT_ACTOR_ENV_VAR} in the git-ignored .env)")
    if not ACTOR_PATTERN.match(value):
        raise ActorUnknown(f"{_ACTOR_ENV_VAR}={value!r} is not canonical (expected human:<login> | agent:<model> | system:<name>)")
    return value


def canonical_actor(raw: str, aliases: dict) -> str:
    """Canonical identity of a stored author string: its alias, itself when already canonical, else 'unmapped:<raw>'. Pure; `aliases`
    is the {raw_name: canonical} dict of list_actor_aliases()."""
    if raw in aliases:
        return aliases[raw]
    return raw if ACTOR_PATTERN.match(raw or "") else f"unmapped:{raw}"


def actor_store_present(*, conn=None) -> bool:
    """True when db/pg_backlog/25_actor_alias.sql has been applied (the coverage view exists)."""
    with _session(conn) as c:
        (reg,) = c.execute("SELECT to_regclass('backlog.v_actor_unmapped') IS NOT NULL").fetchone()
    return bool(reg)


def list_actor_aliases(*, conn=None) -> dict:
    """{raw_name: canonical} of backlog.actor_alias."""
    with _session(conn) as c:
        return {r["raw_name"]: r["canonical"] for r in _rows(c, "SELECT raw_name, canonical FROM backlog.actor_alias")}


def unmapped_actors(*, conn=None) -> list[dict]:
    """Author strings of logs and link events that have no alias and are not canonical (empty = covered). Fail-loud like the rest of
    the read API: an unreachable backlog or a store not applied must never read as "covered"."""
    with _session(conn) as c:
        return _rows(c, "SELECT raw_actor, n_rows, first_ts, last_ts FROM backlog.v_actor_unmapped ORDER BY raw_actor")


def history_fingerprint(*, max_log_id: Optional[int] = None, max_event_id: Optional[int] = None, conn=None) -> dict:
    """Count and md5 of the append-only history (progress logs and link events) up to the given ids (default: the current maxima).
    History is never rewritten, so the same bounds always give the same answer; rows appended later have higher ids and do not move
    it. Used to prove that a DDL phase left the history untouched (FND-0330 acceptance). Timestamps are rendered in UTC so the value
    does not depend on the session time zone."""
    with _session(conn) as c:
        if max_log_id is None:
            (max_log_id,) = c.execute("SELECT coalesce(max(log_id), 0) FROM backlog.BACKLOG_LOGS").fetchone()
        if max_event_id is None:
            (max_event_id,) = c.execute("SELECT coalesce(max(event_id), 0) FROM backlog.backlog_link_event").fetchone()
        n_logs, logs_md5 = c.execute(
            "SELECT count(*), md5(coalesce(string_agg(concat_ws('|', log_id, action_point_id, log_ts AT TIME ZONE 'UTC', username, comment), "
            "E'\\n' ORDER BY log_id), '')) FROM backlog.BACKLOG_LOGS WHERE log_id <= %s", (max_log_id,)).fetchone()
        n_events, events_md5 = c.execute(
            "SELECT count(*), md5(coalesce(string_agg(concat_ws('|', event_id, from_id, to_id, link_type, status, evidence, absorbs, username, "
            "event_ts AT TIME ZONE 'UTC'), E'\\n' ORDER BY event_id), '')) FROM backlog.backlog_link_event WHERE event_id <= %s",
            (max_event_id,)).fetchone()
    return {"max_log_id": int(max_log_id), "logs": int(n_logs), "logs_md5": logs_md5,
            "max_event_id": int(max_event_id), "events": int(n_events), "events_md5": events_md5}


# --- Transition kernel (db/pg_backlog/30_transitions.sql; FND-0331, Backlog v2 Phase A) -------------------------------------------------------
# Tickets are opened and moved through ONE door: sp_open_ticket and sp_transition. The SQL of those calls lives here and only here
# (tests/test_backlog_single_door.py enforces it). Fail-loud like the rest of the read/write API; only report_incident() degrades quietly.
# The identity of the writer is explicit (D5): pass `actor=` (human:<login> | agent:<model> | system:<name>) or set FONDOS_ACTOR; nothing is guessed.

_AUTODETECT_ACTOR = "system:autodetect"
_SIG_OPEN = "backlog.sp_open_ticket(text,text,text,text,text,text,text,text,text,text,text)"
_SIG_TRANSITION = "backlog.sp_transition(text,text,text,text,text,text,text,text,text,date,text,text,text,boolean)"
SESSION_TYPES = ("WKSH", "DEV", "REVB", "SYNC", "ANL")


def make_session_id(topic: str, kind: str = "DEV", *, project: str = "FND", today: Optional[datetime] = None) -> str:
    """`FND_20261026_DEV_TOPIC`: the session id of the norm (NORMAS_BACKLOG section 2). The topic is upper-cased and reduced to [A-Z0-9_-]."""
    if kind not in SESSION_TYPES:
        raise ValueError(f"session type must be one of {SESSION_TYPES}, got {kind!r}")
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(topic or "").strip()).strip("_").upper()
    if not clean:
        raise ValueError("a session topic is required")
    return f"{project}_{(today or datetime.now(timezone.utc)):%Y%m%d}_{kind}_{clean}"


def resolve_actor(actor: Optional[str] = None, environ: Optional[dict] = None) -> str:
    """The canonical identity to write with, ALWAYS taken from FONDOS_ACTOR (owner order 2026-10-28: one process, one identity, set by the environment, never typed per call).
    Raises ActorUnknown (a hard fail: nothing is written) when FONDOS_ACTOR is unset or not canonical, and when an explicit `actor` is given that is malformed or DIFFERS
    from it. Passing the explicit `actor` that equals the environment stays allowed (scripts print it); a different one is refused, so a caller cannot type another identity.
    What this guarantees is presence, format and consistency; it cannot prove WHICH model is running (the harness exposes none to child processes): that is set by whoever configures
    the session (.claude/settings.local.json env for an agent session, the shell for a human one) and stays declarative among agents (limitation L3)."""
    env_value = current_actor(environ)                     # raises when unset or malformed
    if actor is not None:
        value = str(actor).strip()
        if not ACTOR_PATTERN.match(value):
            raise ActorUnknown(f"actor {value!r} is not canonical (expected human:<login> | agent:<model> | system:<name>)")
        if value != env_value:
            raise ActorUnknown(f"explicit actor {value!r} differs from {_ACTOR_ENV_VAR}={env_value!r}: one process writes as one identity, the one the environment declares")
    return env_value


def _transitions_present(cur) -> bool:
    """True when the A-1 routines exist. Takes a cursor (report_incident runs on one) so it adds no connection."""
    cur.execute("SELECT to_regprocedure(%s) IS NOT NULL AND to_regprocedure(%s) IS NOT NULL", (_SIG_OPEN, _SIG_TRANSITION))
    return bool(cur.fetchone()[0])


def transitions_store_present(*, conn=None) -> bool:
    """True when db/pg_backlog/30_transitions.sql (step A-1) has been applied. Lets callers keep the legacy path until the owner applies it."""
    with _session(conn) as c:
        with c.cursor() as cur:
            return _transitions_present(cur)


_MENTION = re.compile(r"\bFND-\d{4}\b")


class TicketOpened(str):
    """The id returned by open_ticket(): a plain str, plus `.mentions`, the FND-nnnn ids named in the text, and `.linked`, those recorded as PROPOSED RELATED links."""
    mentions: tuple = ()
    linked: tuple = ()


def mentioned_ids(*texts: Optional[str]) -> list:
    """The distinct FND-nnnn ids named in the texts, in order of appearance. Pure."""
    seen: list = []
    for text in texts:
        for m in _MENTION.findall(text or ""):
            if m not in seen:
                seen.append(m)
    return seen


def relations_store_present(*, conn=None) -> bool:
    """True when db/pg_backlog/40_relations_v2.sql (Phase B) has been applied (the column origin of backlog_link_event exists)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'backlog_link_event' "
                              "AND column_name = 'origin')").fetchone()[0])


def open_ticket(*, title: str, nature: str, object_type: str, priority: str, session_id: str, description: str, first_log: str,
                actor: Optional[str] = None, object_name: Optional[str] = None, schema_name: Optional[str] = None,
                project_code: str = "FND", link_mentions: bool = True, conn=None) -> str:
    """Open a ticket through backlog.sp_open_ticket: id from the sequence, status OPEN, reporter = the canonical actor, the first log and the first
    status event, in one call. Returns the id (a TicketOpened: a str that also carries `.mentions`). Raises on any refusal (bad actor, bad session id, a
    domain CHECK) and when A-1 is not applied. Phase B (FND-0333): once applied, every other existing ticket named in the title, description or first log
    is recorded as a PROPOSED RELATED link (origin SOURCE) unless the pair already has any link event, so linking no longer waits for the next review."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=agent_operation(who)) as c:
        (apid,) = c.execute(
            "SELECT backlog.sp_open_ticket(p_title => %s, p_nature => %s, p_object_type => %s, p_object_name => %s, p_priority => %s, "
            "p_actor => %s, p_session_id => %s, p_description => %s, p_first_log => %s, p_schema_name => %s, p_project_code => %s)",
            (title, nature, object_type, object_name, priority, who, session_id, description, first_log, schema_name, project_code)).fetchone()
        out = TicketOpened(apid)
        found = [i for i in mentioned_ids(title, description, first_log) if i != apid]
        out.mentions = tuple(found)
        if link_mentions and found and relations_store_present(conn=c):
            known = {r[0] for r in c.execute("SELECT action_point_id FROM backlog.BACKLOG WHERE action_point_id = ANY(%s)", (found,)).fetchall()}
            paired = {r[0] for r in c.execute("SELECT DISTINCT CASE WHEN from_id = %s THEN to_id ELSE from_id END FROM backlog.backlog_link_event "
                                              "WHERE from_id = %s OR to_id = %s", (apid, apid, apid)).fetchall()}
            todo = [i for i in found if i in known and i not in paired]
            if todo:
                record_link_events([dict(from_id=apid, to_id=i, link_type="RELATED", status="PROPOSED", origin="SOURCE", username=who,
                                         evidence=f"named in the text of {apid} when it was opened (S1 mention)") for i in todo], conn=c)
            out.linked = tuple(todo)
    return out


def transition(ap_id: str, to_status: str, *, session_id: str, comment: str, actor: Optional[str] = None,
               solution_type: Optional[str] = None, release_version: Optional[str] = None, blocker: Optional[str] = None,
               condition: Optional[str] = None, revisit_by=None, revisit_on_event: Optional[str] = None,
               followup: Optional[str] = None, assignee: Optional[str] = None, force: bool = False, conn=None) -> int:
    """Move a ticket through backlog.sp_transition (legality, closure rules, waiting states, log and status event in one transaction).
    `comment` becomes the progress log verbatim. Returns the status_event id. Raises on any refusal; never swallows.
    An agent (agent:<model>, with its scoped login configured: agent_operation) moves tickets through backlog.sp_agent_transition, which has no `force`, never reopens a CLOSED
    ticket and makes no delivery closure (Backlog v3 W2): `force=True` from an agent is refused here, before any connection."""
    who = resolve_actor(actor)
    if who == AUTOPILOT_ACTOR:
        raise ValueError("the autopilot moves tickets only through auto_transition(): two evidence-backed edges, never forced")
    op = agent_operation(who)
    if op and force:
        raise ValueError("an agent cannot force a transition (p_force is a person's override): ask the owner, or record the request as a note")
    with _session(conn, human=who.startswith("human:"), write=True, operation=op) as c:
        if op:
            (event_id,) = c.execute(
                "SELECT backlog.sp_agent_transition(p_ap_id => %s, p_to => %s, p_actor => %s, p_session_id => %s, p_comment => %s, p_solution_type => %s, "
                "p_release_version => %s, p_blocker => %s, p_condition => %s, p_revisit_by => %s, p_revisit_on_event => %s, p_followup => %s, p_assignee => %s)",
                (ap_id, to_status, who, session_id, comment, solution_type, release_version, blocker, condition, revisit_by, revisit_on_event,
                 followup, assignee)).fetchone()
            return int(event_id)
        (event_id,) = c.execute(
            "SELECT backlog.sp_transition(p_ap_id => %s, p_to => %s, p_actor => %s, p_session_id => %s, p_comment => %s, p_solution_type => %s, "
            "p_release_version => %s, p_blocker => %s, p_condition => %s, p_revisit_by => %s, p_revisit_on_event => %s, p_followup => %s, "
            "p_assignee => %s, p_force => %s)",
            (ap_id, to_status, who, session_id, comment, solution_type, release_version, blocker, condition, revisit_by, revisit_on_event,
             followup, assignee, bool(force))).fetchone()
    return int(event_id)


# --- Phase B (FND-0333): the delivery gate, review flags, holds, the gate switch, structured waits ------------------------------------------

def gate_blockers(ap_id: str, to_status: str = "READY_FOR_DEPLOY", delivery: bool = False, *, conn=None) -> list:
    """What would block `ap_id` from READY_FOR_DEPLOY (or, with delivery=True, from a CODE_FIX / DATA_FIX / WORKAROUND closure): a list of dicts, empty = nothing.
    Read-only; the same function sp_transition calls. Needs Phase B applied (before it, A-1 has a stub that returns [])."""
    with _session(conn) as c:
        return list(c.execute("SELECT backlog.fn_gate_blockers(%s, %s, %s)", (ap_id, to_status, bool(delivery))).fetchone()[0])


def open_review_flags(ap_id: Optional[str] = None, *, conn=None) -> list:
    """Unacknowledged review flags (all, or those on `ap_id`): flag_id, action_point_id (who must review), source_id, link_type, event_ts."""
    with _session(conn) as c:
        return _rows(c, "SELECT flag_id, action_point_id, source_id, link_type, event_ts FROM backlog.v_open_review_flag "
                        "WHERE %s::text IS NULL OR action_point_id = %s ORDER BY flag_id", (ap_id, ap_id))


def ack_review(flag_id: int, comment: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Acknowledge a review flag through backlog.sp_ack_review (what was reviewed, 10+ characters). Returns the ACK id."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True) as c:
        return int(c.execute("SELECT backlog.sp_ack_review(%s, %s, %s)", (flag_id, who, comment)).fetchone()[0])


def raise_hold(ap_id: str, reason: str, *, code: Optional[str] = None, actor: Optional[str] = None, conn=None) -> int:
    """Raise a hold on a ticket (it cannot reach READY_FOR_DEPLOY or a delivery closure until a human releases it). Returns the hold id."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True) as c:
        return int(c.execute("SELECT backlog.sp_raise_hold(%s, %s, %s, %s)", (ap_id, reason, code, who)).fetchone()[0])


def release_hold(hold_id: int, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Release a hold. Only a human actor can (through FONDOS_BACKLOG_PG_DSN_HUMAN, the owner-only role); the identity is FONDOS_ACTOR (see resolve_actor)."""
    who = resolve_actor(actor)
    if not who.startswith("human:"):
        raise ActorUnknown(f"only a human actor releases a hold (FONDOS_ACTOR={who!r})")
    with _session(conn, human=True, write=True) as c:
        return int(c.execute("SELECT backlog.sp_release_hold(%s, %s, %s)", (hold_id, who, reason)).fetchone()[0])


def set_gate(mode: str, reason: str, *, executive_override: bool = False, actor: Optional[str] = None, gate: str = "DELIVERY", conn=None) -> int:
    """Set the delivery gate mode: OFF, AUDIT (evaluate and log, never refuse) or ENFORCE (refuse; only after a clear 48-hour audit window). Only a human actor can (the
    owner-only role); the switch is append-only and the deployment seeds AUDIT. `executive_override=True` (FND-0350) skips only the audit-window condition of ENFORCE, needs a reason of 80+
    characters citing the written directive, and is recorded as such (v_gate_override_event); an agent can never pass it: the owner runs it in his own session."""
    if mode not in GATE_MODES:
        raise ValueError(f"mode must be one of {GATE_MODES}, got {mode!r}")
    who = resolve_actor(actor)
    if not who.startswith("human:"):
        raise ActorUnknown(f"only a human actor changes the gate (FONDOS_ACTOR={who!r})")
    with _session(conn, human=True, write=True) as c:
        return int(c.execute("SELECT backlog.sp_set_gate(%s, %s, %s, %s, %s)", (gate, mode, who, reason, bool(executive_override))).fetchone()[0])


def gate_mode(gate: str = "DELIVERY", *, conn=None) -> str:
    """The current mode of the gate: OFF, AUDIT or ENFORCE (before Phase B the A-1 stub answers OFF)."""
    with _session(conn) as c:
        return str(c.execute("SELECT backlog.fn_gate_mode(%s)", (gate,)).fetchone()[0])


def _override_col(c) -> str:
    """`last_override_ts` when the window view has it (the current release) else a NULL of the same name (the previous release, after the Phase B rollback): readers never break on a rollback."""
    has = c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'v_gate_audit_window' AND column_name = 'last_override_ts')").fetchone()[0]
    return "last_override_ts" if has else "NULL::timestamptz AS last_override_ts"


def gate_audit_window(*, conn=None) -> dict:
    """The audit window the owner reads before authorizing enforcement: mode, since_ts, hours_in_audit, evaluated, tickets_48h (different tickets with a gated move in the last
    48 hours), required_tickets (5), violations_since, violations_48h and `clear` (48+ hours of audit, gated moves on 5+ different tickets in the last 48 hours, no violation in
    them: the DB-side condition of ENFORCE). Needs Phase B."""
    with _session(conn) as c:
        return _rows(c, f"SELECT mode, since_ts, hours_in_audit, evaluated, tickets_48h, required_tickets, violations_since, violations_48h, clear, {_override_col(c)} FROM backlog.v_gate_audit_window")[0]


def gate_audit_violations(*, conn=None) -> list:
    """What the gate would have refused (AUDIT) or did refuse and was forced (ENFORCE): event_id, action_point_id, to_status, delivery, blockers, mode, actor, forced, event_ts."""
    with _session(conn) as c:
        return _rows(c, "SELECT event_id, action_point_id, to_status, delivery, blockers, mode, actor, forced, event_ts FROM backlog.v_gate_audit_violation ORDER BY event_id")


def list_wait_events(ids: Optional[list] = None, *, conn=None) -> list:
    """The structured waits (BLOCKED / DEFERRED) of the given tickets (all when `ids` is None), oldest first: wait_id, action_point_id, kind, blocker_ref,
    condition_text, revisit_by, revisit_on_event, revisit_basis, actor, event_ts. Needs step A-1."""
    with _session(conn) as c:
        return _rows(c, "SELECT wait_id, action_point_id, kind, blocker_ref, condition_text, revisit_by, revisit_on_event, revisit_basis, actor, event_ts "
                        "FROM backlog.wait_event WHERE %s::text[] IS NULL OR action_point_id = ANY(%s) ORDER BY wait_id", (ids, ids))


def annotate_wait(ap_id: str, comment: str, *, blocker: Optional[str] = None, condition: Optional[str] = None, revisit_by=None,
                  revisit_on_event: Optional[str] = None, basis: str = "DECLARED", actor: Optional[str] = None, conn=None) -> int:
    """Record the structured blocker or revisit date of a ticket that is ALREADY BLOCKED or DEFERRED (no status change). Returns the wait_event id."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=agent_operation(who)) as c:
        return int(c.execute("SELECT backlog.sp_annotate_wait(%s, %s, %s, %s, %s, %s, %s, %s)",
                             (ap_id, who, comment, blocker, condition, revisit_by, revisit_on_event, basis)).fetchone()[0])


def phase_b_catalog(*, conn=None) -> dict:
    """Read-only facts the Phase B acceptance check needs: the objects of 40_relations_v2.sql, their owner and hardening, the link vocabulary the table accepts,
    whether the cycle guard is installed, whether sp_transition calls the gate, the state of the switch, and what the writer may do. Absent things are just absent."""
    names = PHASE_B_ROUTINES
    with _session(conn) as c:
        routines = _rows(c, "SELECT p.proname AS name, p.prosecdef AS secdef, pg_get_userbyid(p.proowner) AS owner, p.proconfig AS config, "
                            "pg_get_functiondef(p.oid) AS body FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = ANY(%s) ORDER BY 1", (list(names),))
        tables = {r[0]: r[1] for r in c.execute("SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace "
                                                "AND c.relname IN ('review_flag', 'hold_event', 'gate_switch', 'gate_audit_event') ORDER BY 1").fetchall()}
        append_only = sorted(r[0] for r in c.execute("SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE t.tgname = 'append_only' AND NOT t.tgisinternal "
                                                     "AND c.relnamespace = 'backlog'::regnamespace AND c.relname IN ('review_flag', 'hold_event', 'gate_switch', 'gate_audit_event')").fetchall())
        cycle_guard = bool(c.execute("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'link_cycle' AND NOT tgisinternal "
                                     "AND tgrelid = 'backlog.backlog_link_event'::regclass)").fetchone()[0])
        check = c.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'backlog.backlog_link_event'::regclass AND conname = 'backlog_link_event_link_type_check'").fetchone()
        origin = bool(c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'backlog_link_event' AND column_name = 'origin')").fetchone()[0])
        gate_hook = c.execute("SELECT pg_get_functiondef(p.oid) LIKE '%fn_gate_blockers%' FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = 'sp_transition'").fetchone()
        mode = str(c.execute("SELECT backlog.fn_gate_mode('DELIVERY')").fetchone()[0]) if any(r["name"] == "fn_gate_mode" for r in routines) else None
        window = None
        if c.execute("SELECT to_regclass('backlog.v_gate_audit_window') IS NOT NULL").fetchone()[0]:
            window = _rows(c, f"SELECT mode, since_ts, hours_in_audit, evaluated, tickets_48h, required_tickets, violations_since, violations_48h, clear, {_override_col(c)} FROM backlog.v_gate_audit_window")[0]
        override_col = bool(c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'gate_switch' AND column_name = 'override')").fetchone()[0])
        writer = None
        if c.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer')").fetchone()[0]:
            writer = {"can_write_new_tables": bool(c.execute("SELECT COALESCE(bool_or(CASE WHEN to_regclass('backlog.' || t) IS NULL THEN false ELSE has_table_privilege('backlog_writer', 'backlog.' || t, 'INSERT') END), false) "
                                                             "FROM unnest(ARRAY['review_flag', 'hold_event', 'gate_switch', 'gate_audit_event']) AS t").fetchone()[0]),
                      "can_execute_gate": bool(c.execute("SELECT COALESCE(has_function_privilege('backlog_writer', to_regprocedure('backlog.fn_gate_blockers(text,text,boolean)')::oid, 'EXECUTE'), false)").fetchone()[0])}
        cycles = c.execute("WITH RECURSIVE e AS (SELECT from_id a, to_id b FROM backlog.v_backlog_link WHERE link_type = 'DEPENDS_ON' AND status IN ('PROPOSED', 'CONFIRMED')), "
                           "walk(root, id, depth) AS (SELECT a, b, 1 FROM e UNION SELECT w.root, e.b, w.depth + 1 FROM walk w JOIN e ON e.a = w.id WHERE w.depth < 50) "
                           "SELECT count(DISTINCT root) FROM walk WHERE id = root").fetchone()[0]
    return {"routines": routines, "table_owners": tables, "append_only": append_only, "cycle_guard": cycle_guard,
            "link_type_check": check[0] if check else None, "origin_column": origin, "sp_transition_calls_gate": bool(gate_hook and gate_hook[0]),
            "gate_mode": mode, "audit_window": window, "override_column": override_col, "writer": writer, "existing_depends_on_cycles": int(cycles)}


DESCRIPTIVE_FIELDS = ("title", "nature", "schema_name", "object_type", "object_name", "priority", "session_id", "scenario_description")   # exception E3


def update_attributes(ap_id: str, *, actor: Optional[str] = None, conn=None, **fields) -> int:
    """Change descriptive attributes of a ticket (exception E3: title, nature, schema_name, object_type, object_name, priority, session_id, scenario_description) and nothing
    else, declaring WHO does it for the transaction so the append-only history (backlog.field_change_event, FND-0338) records the actor next to the old and new value.
    Works before the history step is applied (the change is made, nothing is recorded). Returns the number of tickets changed (0 or 1). Raises ValueError for any other field,
    ActorUnknown without an actor, and the driver's error for a value the CHECKs refuse."""
    bad = sorted(set(fields) - set(DESCRIPTIVE_FIELDS))
    if bad or not fields:
        raise ValueError(f"update_attributes changes only {DESCRIPTIVE_FIELDS}; got {sorted(fields)}" if bad else "no field to change")
    who = resolve_actor(actor)
    sets = ", ".join(f"{name} = %s" for name in fields)
    with _session(conn, human=who.startswith("human:"), write=True) as c:
        c.execute("SELECT set_config('backlog.actor', %s, true)", (who,))
        n = c.execute(f"UPDATE backlog.backlog SET {sets} WHERE action_point_id = %s", (*fields.values(), ap_id)).rowcount
        c.execute("SELECT set_config('backlog.actor', '', true)")          # the declaration covers this change only, not later statements of a borrowed transaction
    return int(n)


SHADOW_REQUIRED = 50          # the A-2 gate (owner decision D2, 2026-10-26): non-system transitions since the last direct write; scripts/ops/migrate_backlog_v2.py keeps a pinned twin


def shadow_actors(*, conn=None) -> list:
    """[(actor, transitions)] of the transitions the A-2 counter has counted (LIVE, non-system, a real change of status, since the last V-00), most active first: who has exercised the
    routines. The sum equals live_non_system_since_last_v00 (a test pins it). Needs step A-1."""
    with _session(conn) as c:
        return [(r[0], int(r[1])) for r in c.execute(
            "SELECT e.actor, count(*) FROM backlog.status_event e WHERE e.origin = 'LIVE' AND e.from_status IS NOT NULL AND e.from_status <> e.to_status "
            "AND e.actor NOT LIKE 'system:%' AND e.event_ts > COALESCE((SELECT max(r.event_ts) FROM backlog.raw_update_event r), '-infinity'::timestamptz) "
            "GROUP BY e.actor ORDER BY 2 DESC, 1").fetchall()]


def shadow_progress(*, conn=None) -> dict:
    """{'last_v00_ts', 'v00_total', 'live_non_system_since_last_v00'}: how far the shadow stage is from the A-2 gate (50 non-system transitions
    since the last direct write). Raises when A-1 is not applied."""
    with _session(conn) as c:
        return _rows(c, "SELECT last_v00_ts, v00_total, live_non_system_since_last_v00 FROM backlog.v_shadow_stage")[0]


def latest_status_events(ids: list[str], *, conn=None) -> dict:
    """{ap_id: {'status', 'event_ts', 'origin'}}: each ticket's current status and the time of its latest status event (step A-1), for the
    decision-time read rule (FND-0332): a stored status snapshot read before `event_ts` is stale (V-STALE-READ). Ids with no ticket are absent.
    Fail-loud (callers wrap it). Read-only."""
    if not ids:
        return {}
    with _session(conn) as c:
        rows = _rows(c, "SELECT b.action_point_id, b.status, e.event_ts, e.origin FROM backlog.BACKLOG b "
                        "LEFT JOIN LATERAL (SELECT event_ts, origin FROM backlog.status_event s WHERE s.action_point_id = b.action_point_id "
                        "ORDER BY s.event_id DESC LIMIT 1) e ON true WHERE b.action_point_id = ANY(%s)", (list(ids),))
    return {r["action_point_id"]: {"status": r["status"], "event_ts": r["event_ts"], "origin": r["origin"]} for r in rows}


GROUP_KINDS = ("TRAIN", "THEME", "DECISION")      # Phase D (FND-0335): db/pg_backlog/60_groups_violations.sql, CHECK of backlog.group_tag.kind


def create_group(name: str, kind: str, description: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Create a group of tickets through backlog.sp_create_group (Phase D): a TRAIN released together, a THEME, or a DECISION that is pending (it
    replaces the free-text "DECISION PENDIENTE" label). The name is upper-cased by the routine. Returns the group id; raises on any refusal."""
    if kind not in GROUP_KINDS:
        raise ValueError(f"group kind must be one of {GROUP_KINDS}, got {kind!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=agent_operation(who)) as c:      # write=True: a group is a write (it skipped the live-write gate before Backlog v3 W2)
        return c.execute("SELECT backlog.sp_create_group(%s, %s, %s, %s)", (name, kind, description, who)).fetchone()[0]


def group_member(group: str, ap_id: str, op: str, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """ADD a ticket to a group or REMOVE it (backlog.sp_group_member; append-only events). Returns the event id; raises when the move changes nothing."""
    if op not in ("ADD", "REMOVE"):
        raise ValueError(f"op must be ADD or REMOVE, got {op!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=agent_operation(who)) as c:
        return c.execute("SELECT backlog.sp_group_member(%s, %s, %s, %s, %s)", (group, ap_id, op, reason, who)).fetchone()[0]


def violation_summary(*, top: int = 3, conn=None) -> dict:
    """{'applied', 'total', 'by_code': [{'code', 'n'}] (largest first), 'top': the first `top` of them}: the count the cycle report prints
    (Phase D, backlog.v_backlog_violations). 'applied' is False, with no counts, while the owner has not applied the phase."""
    with _session(conn) as c:
        if not c.execute("SELECT to_regclass('backlog.v_backlog_violations') IS NOT NULL").fetchone()[0]:
            return {"applied": False, "total": None, "by_code": [], "top": []}
        by_code = _rows(c, "SELECT code, count(*) AS n FROM backlog.v_backlog_violations GROUP BY code ORDER BY count(*) DESC, code")
    return {"applied": True, "total": sum(r["n"] for r in by_code), "by_code": by_code, "top": by_code[:top]}


OPS_KINDS = ("COMMIT", "RELEASE", "PROMOTION", "CYCLE_FLAG", "AUDIT_INCIDENT", "MERGE", "DEPLOY", "CYCLE")     # Phase C (FND-0334): db/pg_backlog/70_sync.sql; MERGE, DEPLOY, CYCLE: 97_autonomy.sql (W3)
OPS_SOURCES = ("REFS", "SUBJECT", "OTHER")


def sync_store_present(*, conn=None) -> bool:
    """True when the owner has applied Phase C slice 1 (backlog.ops_event and the routines)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT to_regclass('backlog.ops_event') IS NOT NULL "
                              "AND to_regprocedure('backlog.sp_record_ops_event(text,text,text,text,jsonb,timestamptz,text)') IS NOT NULL").fetchone()[0])


def record_ops_event(kind: str, ref: str, ap_id: str, source: str, payload: dict, fact_ts: datetime, *, actor: Optional[str] = None,
                     conn=None) -> bool:
    """Record one operational fact that cites a ticket (backlog.sp_record_ops_event). True when it was new, False when already recorded."""
    if kind not in OPS_KINDS or source not in OPS_SOURCES:
        raise ValueError(f"kind must be one of {OPS_KINDS} and source one of {OPS_SOURCES}, got {kind!r} / {source!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=autopilot_operation(who)) as c:       # write=True: a fact is a write (the autopilot has its own login)
        return bool(c.execute("SELECT backlog.sp_record_ops_event(%s, %s, %s, %s, %s::jsonb, %s, %s)",
                              (kind, ref, ap_id, source, json.dumps(payload or {}), fact_ts, who)).fetchone()[0])


def autonomy_store_present(*, conn=None) -> bool:
    """True when the owner has applied the W3 block of db/pg_backlog/97_autonomy.sql (sp_auto_transition exists and ops_event accepts the MERGE kind)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT to_regprocedure('backlog.sp_auto_transition(text,text,text,text,text,text,text,text)') IS NOT NULL AND EXISTS ("
                              "SELECT 1 FROM pg_constraint WHERE conrelid = 'backlog.ops_event'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%MERGE%')").fetchone()[0])


AUTO_EVIDENCE_KINDS = ("MERGE", "BLOCKER", "REVISIT")      # the evidence sp_auto_transition accepts: a merge (delivery), a CLOSED blocker (unblock), a revisit date reached (undefer)


def auto_transition(ap_id: str, to_status: str, *, evidence_ref: str, session_id: str, comment: str, solution_type: Optional[str] = None,
                    release_version: Optional[str] = None, evidence_kind: str = "MERGE", conn=None) -> int:
    """The autopilot's evidence-backed move (backlog.sp_auto_transition, Backlog v3 W3 and v3.1): IN_PROGRESS -> READY_FOR_DEPLOY, or READY_FOR_DEPLOY -> CLOSED as CODE_FIX / DATA_FIX with a
    release label, on the MERGE event `evidence_ref` recorded for that ticket; BLOCKED -> IN_PROGRESS on evidence_kind 'BLOCKER' (the closed blocker's id); DEFERRED -> TODO on 'REVISIT' (the
    revisit date). Only the process whose identity is system:autopilot may call it; the database fixes the actor, verifies the evidence from its own records and never forces. Returns the
    status_event id; raises on any refusal (the gates of Phase B included), never swallows."""
    if evidence_kind not in AUTO_EVIDENCE_KINDS:
        raise ValueError(f"evidence_kind must be one of {AUTO_EVIDENCE_KINDS}, got {evidence_kind!r}")
    who = resolve_actor(None)
    if who != AUTOPILOT_ACTOR:
        raise ActorUnknown(f"auto_transition is the autopilot's: FONDOS_ACTOR must be {AUTOPILOT_ACTOR}, got {who!r}")
    with _session(conn, write=True, operation=autopilot_operation(who)) as c:
        (event_id,) = c.execute(
            "SELECT backlog.sp_auto_transition(p_ap_id => %s, p_to => %s, p_evidence_kind => %s, p_evidence_ref => %s, p_session_id => %s, p_comment => %s, "
            "p_solution_type => %s, p_release_version => %s)",
            (ap_id, to_status, evidence_kind, evidence_ref, session_id, comment, solution_type, release_version)).fetchone()
    return int(event_id)


# --- Backlog v3.1 (FND-0355): db/pg_backlog/98_events.sql -- reopening as an event, self-releasing holds, agent adjudication, the autopilot's mode ------------------------------
REOPEN_CAUSES = ("REGRESSION", "INCOMPLETE_FIX", "COVERAGE_GAP", "WRONG_ROOT_CAUSE", "DOWNSTREAM_EFFECT", "ENVIRONMENT", "SCOPE_CHANGE", "OTHER")     # == the CHECK of backlog.reopen_event.cause
AUTOPILOT_MODES = ("OFF", "SHADOW", "ON")


def events_store_present(*, conn=None) -> bool:
    """True when the owner's deploy has applied db/pg_backlog/98_events.sql (sp_reopen exists and the reopen_event table is there)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT to_regclass('backlog.reopen_event') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_proc WHERE pronamespace = 'backlog'::regnamespace AND proname = 'sp_reopen')").fetchone()[0])


def _scoped_operation(who: str) -> Optional[str]:
    """The allow-listed operation an agent or the autopilot writes with (its scoped login), else None (the console path)."""
    return autopilot_operation(who) or agent_operation(who)


def reopen(ap_id: str, cause: str, reason: str, *, session_id: str, actor: Optional[str] = None, to_status: str = "IN_PROGRESS", conn=None) -> int:
    """Reopen a CLOSED ticket (backlog.sp_reopen): a normal event any actor may record, never forced and never counted as a violation; the cause is kept. The reopening of a delivered
    ticket IS the rejection of its delivery. Returns the status_event id."""
    if cause not in REOPEN_CAUSES:
        raise ValueError(f"cause must be one of {REOPEN_CAUSES}, got {cause!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=_scoped_operation(who)) as c:
        return int(c.execute("SELECT backlog.sp_reopen(%s, %s, %s, %s, %s, %s)", (ap_id, cause, reason, who, session_id, to_status)).fetchone()[0])


def rescind_reopen(ap_id: str, *, session_id: str, actor: Optional[str] = None, conn=None) -> int:
    """Undo a reopening by the same actor within one hour of it (backlog.sp_rescind_reopen): the closure fields come back. Returns the status_event id."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=_scoped_operation(who)) as c:
        return int(c.execute("SELECT backlog.sp_rescind_reopen(%s, %s, %s)", (ap_id, who, session_id)).fetchone()[0])


def auto_release_holds(*, actor: Optional[str] = None, conn=None) -> int:
    """Release the holds of system codes (V-COLLISION, V-HOOK-BYPASS) whose cause has cleared (backlog.fn_auto_release_holds); a hold of another code, or a person's, stays. For an agent or
    system:* actor only (the database refuses a human). Returns how many were released."""
    who = resolve_actor(actor)
    with _session(conn, write=True, operation=_scoped_operation(who)) as c:
        return int(c.execute("SELECT backlog.fn_auto_release_holds(%s)", (who,)).fetchone()[0])


def agent_adjudicate_overlap(a: str, b: str, decision: str, reason: str, *, absorbs: Optional[str] = None, actor: Optional[str] = None, conn=None) -> int:
    """An agent decides one undecided collision pair with stated evidence (backlog.sp_agent_adjudicate_overlap): never REVERT, at least 40 characters of reason, and never a pair that is
    already decided, so a person's earlier decision is never overwritten. Returns the adjudication id."""
    if decision not in COLLISION_DECISIONS or decision == "REVERT":
        raise ValueError(f"decision must be one of {tuple(d for d in COLLISION_DECISIONS if d != 'REVERT')}, got {decision!r}")
    who = resolve_actor(actor)
    if not who.startswith("agent:"):
        raise ValueError(f"agent_adjudicate_overlap is for an agent:<model> identity, got {who!r} (a person uses adjudicate_overlap)")
    with _session(conn, write=True, operation=agent_operation(who)) as c:
        return int(c.execute("SELECT backlog.sp_agent_adjudicate_overlap(%s, %s, %s, %s, %s, %s)", (a, b, decision, absorbs, reason, who)).fetchone()[0])


def autopilot_mode(*, conn=None) -> str:
    """The autopilot's current mode, the latest autopilot_mode_event (backlog.fn_autopilot_mode)."""
    with _session(conn) as c:
        return str(c.execute("SELECT backlog.fn_autopilot_mode()").fetchone()[0])


_SHADOW_NOTE = r"^\[autopilot SHADOW\] (MOVE|CLOSE|REOPEN|UNBLOCK|UNDEFER) FND-"        # a SHADOW note that says "would act" (a WAIT note is not a decision)
_LIVE_REOPEN = ("kind = 'REOPEN' AND NOT EXISTS (SELECT 1 FROM backlog.reopen_event x WHERE x.action_point_id = r.action_point_id AND x.kind = 'RESCIND' AND x.reopen_id > r.reopen_id)")


def autopilot_track_record(window_days: int = 30, *, conn=None) -> dict:
    """What shared.backlog_autopilot.mode_change decides from: {'mode', 'decisions', 'contradicted', 'reopened_closures'}. `decisions` = the distinct SHADOW decisions (to act, not to wait) noted by
    system:autopilot since the latest mode event; `contradicted` = how many of them were followed by a (not rescinded) reopening of their ticket; `reopened_closures` = the (not rescinded) reopenings
    of the last `window_days` days of tickets whose latest closure before the reopening was made by system:autopilot. Read-only; needs the `events` DDL."""
    with _session(conn) as c:
        mode = c.execute("SELECT backlog.fn_autopilot_mode()").fetchone()[0]
        decisions, contradicted = c.execute(
            "WITH since AS (SELECT COALESCE(max(event_ts), '-infinity'::timestamptz) AS ts FROM backlog.autopilot_mode_event), "
            "notes AS (SELECT DISTINCT ON (l.action_point_id, substring(l.comment from '\\(key [^)]*\\)')) l.action_point_id, l.log_ts "
            "          FROM backlog.BACKLOG_LOGS l, since s WHERE l.username = %s AND l.comment ~ %s AND l.log_ts > s.ts "
            "          ORDER BY l.action_point_id, substring(l.comment from '\\(key [^)]*\\)'), l.log_id) "
            "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM backlog.reopen_event r WHERE r.action_point_id = n.action_point_id AND r.event_ts > n.log_ts AND " + _LIVE_REOPEN + ")) FROM notes n",
            (AUTOPILOT_ACTOR, _SHADOW_NOTE)).fetchone()
        (reopened,) = c.execute(
            "SELECT count(*) FROM backlog.reopen_event r WHERE " + _LIVE_REOPEN + " AND r.event_ts >= now() - make_interval(days => %s) "
            "AND (SELECT s.actor FROM backlog.status_event s WHERE s.action_point_id = r.action_point_id AND s.to_status = 'CLOSED' AND s.event_ts < r.event_ts ORDER BY s.event_id DESC LIMIT 1) = %s",
            (int(window_days), AUTOPILOT_ACTOR)).fetchone()
    return {"mode": str(mode), "decisions": int(decisions), "contradicted": int(contradicted), "reopened_closures": int(reopened)}


def raw_update_privilege_live(role: str = "backlog_writer", *, conn=None) -> bool:
    """True while `role` still holds the direct UPDATE privilege on backlog.backlog, i.e. while step A-2 (`revoke`) has NOT been applied. Read-only; raises when the table does not exist."""
    with _session(conn) as c:
        return bool(c.execute("SELECT has_table_privilege(%s, 'backlog.backlog', 'UPDATE')", (role,)).fetchone()[0])


def digest_facts(days: int = 7, *, conn=None) -> dict:
    """The numbers of the weekly digest (scripts/audit/backlog_digest.py), read-only. Each block is read on its own and a missing object leaves that block None, so the digest still works before every
    phase is applied. {'by_status', 'moves_by_actor_kind' (live status events of the last `days` days: {kind: {'n', 'forced'}}), 'reopens_by_cause', 'open_holds', 'idle_in_progress' (14 days or more),
    'deploy_facts', 'undecided_pairs'}."""
    def safe(c, sql, params=()):
        try:
            with c.transaction():
                return _rows(c, sql, params)
        except Exception:  # noqa: BLE001 -- an object of a phase that is not applied: that block is simply absent
            return None

    with _session(conn) as c:
        by_status = safe(c, "SELECT status, count(*) AS n FROM backlog.BACKLOG GROUP BY status ORDER BY status")
        moves = safe(c, "SELECT split_part(actor, ':', 1) AS kind, count(*) AS n, count(*) FILTER (WHERE forced) AS forced FROM backlog.status_event "
                        "WHERE origin = 'LIVE' AND event_ts >= now() - make_interval(days => %s) GROUP BY 1", (int(days),))
        reopens = safe(c, "SELECT cause, count(*) AS n FROM backlog.reopen_event WHERE kind = 'REOPEN' AND event_ts >= now() - make_interval(days => %s) GROUP BY cause ORDER BY cause", (int(days),))
        holds = safe(c, "SELECT count(*) AS n FROM backlog.v_open_hold")
        idle = safe(c, "SELECT count(*) AS n FROM backlog.BACKLOG WHERE status = 'IN_PROGRESS' AND update_ts < now() - interval '14 days'")
        deploys = safe(c, "SELECT count(*) AS n FROM backlog.ops_event WHERE kind = 'DEPLOY' AND fact_ts >= now() - make_interval(days => %s)", (int(days),))
        pairs = safe(c, "SELECT count(*) AS n FROM backlog.v_gate_queue")
    return {"by_status": {r["status"]: r["n"] for r in by_status} if by_status is not None else None,
            "moves_by_actor_kind": {r["kind"]: {"n": r["n"], "forced": r["forced"]} for r in moves} if moves is not None else None,
            "reopens_by_cause": {r["cause"]: r["n"] for r in reopens} if reopens is not None else None,
            "open_holds": holds[0]["n"] if holds else None, "idle_in_progress": idle[0]["n"] if idle else None,
            "deploy_facts": deploys[0]["n"] if deploys else None, "undecided_pairs": pairs[0]["n"] if pairs else None}


def record_deploy_fact(ref: str, ap_id: str, payload: dict, fact_ts: datetime, *, conn=None) -> bool:
    """Record that the DDL a ticket's merge brought is LIVE (backlog.sp_record_fact, kind DEPLOY, actor system:deploy fixed by the database). Only the scoped login backlog_facts can make it
    (FONDOS_BACKLOG_PG_DSN_FACTS, from the owner vault, set by the deploy watcher for this one call): a trigger refuses a DEPLOY row from any other login, so no agent can claim that DDL is live.
    Idempotent per (ref, ticket): returns True when the fact is new."""
    with _session(conn, write=True, operation="facts") as c:
        return bool(c.execute("SELECT backlog.sp_record_fact('DEPLOY', %s, %s, %s::jsonb, %s)", (ref, ap_id, json.dumps(payload), fact_ts)).fetchone()[0])


def set_autopilot_mode(mode: str, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Append an autopilot_mode_event (backlog.sp_set_autopilot_mode). The autopilot itself may only move SHADOW <-> ON; a person sets any mode. Returns the event id."""
    if mode not in AUTOPILOT_MODES:
        raise ValueError(f"mode must be one of {AUTOPILOT_MODES}, got {mode!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=autopilot_operation(who)) as c:
        return int(c.execute("SELECT backlog.sp_set_autopilot_mode(%s, %s, %s)", (mode, reason, who)).fetchone()[0])


def ops_event_keys(kind: str = "COMMIT", *, conn=None) -> set:
    """{(ref, ap_id)} already recorded for `kind`: what backlog_sync.py does not record (or forward) again."""
    with _session(conn) as c:
        return {(r[0], r[1]) for r in c.execute("SELECT ref, action_point_id FROM backlog.ops_event WHERE kind = %s", (kind,)).fetchall()}


def close_conditions(*, conn=None) -> Optional[dict]:
    """{ap_id: [(probe_kind, *args), ...]} of the active closing conditions (backlog.v_close_condition), in the shape of rfd_matrix_probe.SPEC;
    None while the owner has not applied Phase C slice 1."""
    with _session(conn) as c:
        if c.execute("SELECT to_regclass('backlog.v_close_condition') IS NULL").fetchone()[0]:
            return None
        out: dict = {}
        for ap, kind, args in c.execute("SELECT action_point_id, probe_kind, args FROM backlog.v_close_condition "
                                        "ORDER BY action_point_id, condition_id").fetchall():
            out.setdefault(ap, []).append((kind, *args))
        return out


HOOK_REASONS = ("TRAILER_MISSING", "UNKNOWN_NONCE", "TREE_MISMATCH")     # Phase C slice 2 (FND-0334): db/pg_backlog/80_hook.sql


def hook_store_present(*, conn=None) -> bool:
    """True when the owner has applied Phase C slice 2 (backlog.gate_check, backlog.hook_bypass and the routines)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT to_regclass('backlog.gate_check') IS NOT NULL AND to_regclass('backlog.hook_bypass') IS NOT NULL "
                              "AND to_regprocedure('backlog.sp_gate_check(text,text[],text)') IS NOT NULL "
                              "AND to_regprocedure('backlog.sp_record_hook_bypass(text,text,text,timestamptz,text)') IS NOT NULL").fetchone()[0])


def gate_check(tree_hash: str, ap_ids: list, *, actor: Optional[str] = None, conn=None) -> str:
    """One run of the commit-msg hook that reached the backlog (backlog.sp_gate_check): returns the nonce bound to `tree_hash`. Without `conn` it connects with the
    scoped login of the hook (FONDOS_BACKLOG_PG_DSN_GATE, allow-listed operation `gate`, no terminal needed, no fallback)."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=None if conn is not None else "gate") as c:
        return str(c.execute("SELECT backlog.sp_gate_check(%s, %s::text[], %s)", (tree_hash, list(ap_ids), who)).fetchone()[0])


def gate_statuses(ap_ids: list, *, conn=None) -> dict:
    """{ticket: status} of the cited tickets that exist (backlog.fn_gate_statuses), for the commit-msg hook; without `conn` through its scoped login."""
    with _session(conn, operation=None if conn is not None else "gate") as c:
        return {r[0]: r[1] for r in c.execute("SELECT action_point_id, status FROM backlog.fn_gate_statuses(%s::text[])", (list(ap_ids),)).fetchall()}


def gate_forward(ap_id: str, session_id: str, comment: str, *, actor: Optional[str] = None, conn=None) -> Optional[int]:
    """The hook's one write on a ticket (backlog.sp_gate_forward): OPEN/TODO -> IN_PROGRESS as system:gate. The status event id, or None when the ticket was in any other
    status (left alone). Without `conn` through the scoped login of the hook."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=None if conn is not None else "gate") as c:
        row = c.execute("SELECT backlog.sp_gate_forward(%s, %s, %s, %s)", (ap_id, session_id, comment, who)).fetchone()[0]
    return None if row is None else int(row)


def gate_checks(nonces: list, *, conn=None) -> dict:
    """{nonce: tree_hash} of the given nonces that exist in backlog.gate_check (what backlog_sync.py compares a commit's trailer with)."""
    if not nonces:
        return {}
    with _session(conn) as c:
        return {r[0]: r[1] for r in c.execute("SELECT nonce, tree_hash FROM backlog.gate_check WHERE nonce = ANY(%s)", (list(nonces),)).fetchall()}


def record_hook_bypass(sha: str, ap_id: str, reason: str, fact_ts: datetime, *, actor: Optional[str] = None, conn=None) -> bool:
    """Record a commit that cites `ap_id` without a valid gate trailer (backlog.sp_record_hook_bypass); a new one raises a hold on the ticket unless it is CLOSED.
    True when it was new, False when already recorded."""
    if reason not in HOOK_REASONS:
        raise ValueError(f"reason must be one of {HOOK_REASONS}, got {reason!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True) as c:
        return bool(c.execute("SELECT backlog.sp_record_hook_bypass(%s, %s, %s, %s, %s)", (sha, ap_id, reason, fact_ts, who)).fetchone()[0])


COLLISION_MODES = ("OFF", "AUDIT", "BLOCK")             # Phase C slice 3 (FND-0334): db/pg_backlog/90_collision.sql
COLLISION_DECISIONS = ("OVERLAPS", "RELATED", "DEPENDS_ON", "NONE", "REVERT")


def collision_store_present(*, conn=None) -> bool:
    """True when the owner has applied Phase C slice 3 (the collision rule, the modes and the adjudication)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT to_regclass('backlog.gate_state') IS NOT NULL AND to_regprocedure('backlog.sp_gate_collision(text[],text[],text,text,text)') IS NOT NULL").fetchone()[0])


def gate_collision(files: list, ap_ids: list, ceiling: str, nonce: str, *, actor: Optional[str] = None, conn=None) -> dict:
    """The commit-msg hook's evaluation of the collision rule (backlog.sp_gate_collision): {mode, tier, new: [{a, b, shared}], undecided: [{a, b}], held: [ticket]}. Without `conn` through the
    scoped login of the hook."""
    if ceiling not in COLLISION_MODES:
        raise ValueError(f"ceiling must be one of {COLLISION_MODES}, got {ceiling!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=None if conn is not None else "gate") as c:
        return c.execute("SELECT backlog.sp_gate_collision(%s::text[], %s::text[], %s, %s, %s)", (list(files), list(ap_ids), ceiling, nonce, who)).fetchone()[0]


def collision_state(ceiling: str, *, conn=None, scoped: bool = False) -> dict:
    """{'mode', 'tier', 'state_event'}: the effective mode (the lower of `ceiling` and the latest gate_state event) and the rule tier. `scoped` = through the hook's scoped login."""
    if ceiling not in COLLISION_MODES:
        raise ValueError(f"ceiling must be one of {COLLISION_MODES}, got {ceiling!r}")
    with _session(conn, operation="gate" if scoped and conn is None else None) as c:
        row = c.execute("SELECT mode, tier, state_event FROM backlog.fn_collision_state(%s)", (ceiling,)).fetchone()
    return {"mode": row[0], "tier": row[1], "state_event": row[2]}


def gate_undecided(ap_ids: list, *, conn=None, scoped: bool = False) -> list:
    """[{a, b, age_hours}]: the undecided collision pairs that touch any of the tickets (what the pre-push hook reads; `scoped` = through the hook's login)."""
    with _session(conn, operation="gate" if scoped and conn is None else None) as c:
        return _rows(c, "SELECT a, b, age_hours FROM backlog.fn_gate_undecided(%s::text[])", (list(ap_ids),))


def attest_store_present(*, conn=None) -> bool:
    """True when the owner has applied Phase C slice 4 (the branch-protection facts and the V-GATE-* violations)."""
    with _session(conn) as c:
        return bool(c.execute("SELECT to_regclass('backlog.gate_protection_event') IS NOT NULL AND to_regprocedure('backlog.sp_record_gate_protection(text,text,text)') IS NOT NULL").fetchone()[0])


def record_gate_protection(kind: str, reason: str, *, actor: Optional[str] = None, conn=None) -> bool:
    """Record one branch-protection fact (backlog.sp_record_gate_protection): an API reading (PROTECTED / UNPROTECTED / UNVERIFIED, by a system actor: recorded only when it changes or the latest is
    older than 24 h) or an owner event (VERIFIED, which clears V-GATE-UNVERIFIED for 30 days, or EMERGENCY: by a person, through the owner-only login). True when it was recorded."""
    if kind not in ("PROTECTED", "UNPROTECTED", "UNVERIFIED", "VERIFIED", "EMERGENCY"):
        raise ValueError(f"kind must be PROTECTED, UNPROTECTED, UNVERIFIED, VERIFIED or EMERGENCY, got {kind!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True) as c:
        return bool(c.execute("SELECT backlog.sp_record_gate_protection(%s, %s, %s)", (kind, reason, who)).fetchone()[0])


def gate_protection_state(*, conn=None) -> dict:
    """{'api': (kind, detail, ts) of the latest API reading or None, 'verified': ts of the latest owner VERIFIED event of the last 30 days or None, 'emergencies': n}."""
    with _session(conn) as c:
        api = c.execute("SELECT kind, reason, event_ts FROM backlog.gate_protection_event WHERE source = 'API' ORDER BY event_id DESC LIMIT 1").fetchone()
        ver = c.execute("SELECT max(event_ts) FROM backlog.gate_protection_event WHERE kind = 'VERIFIED' AND event_ts > clock_timestamp() - interval '30 days'").fetchone()[0]
        emg = c.execute("SELECT count(*) FROM backlog.gate_protection_event WHERE kind = 'EMERGENCY'").fetchone()[0]
    return {"api": tuple(api) if api else None, "verified": ver, "emergencies": int(emg)}


def phase_c_catalog(tables: list, views: list, routines: list, roles: list, *, conn=None) -> dict:
    """Read-only facts for the acceptance of Phase C (scripts/audit/backlog_phase_c_acceptance.py): what exists, who owns it and who can do what. Absent things are just absent.
    {'tables': {name: {owner, append_only (the trigger exists AND is enabled), select/insert/update/delete: {role: bool}}}, 'views': {name: bool},
     'scoped': {role: {'read': [relations of the whole backlog schema it can SELECT], 'write': [relations it can INSERT / UPDATE / DELETE]}} for backlog_gate and backlog_adjudicator,
     'routines': {name: {owner, secdef, search_path_pinned, public, executes: [roles]}},
     'roles': {role: {login, super, createrole, createdb, connlimit, config, member_of: [roles]}}, 'violations_view': text of backlog.v_backlog_violations or None}.
    `routines` are names; every overload of a name is read (the answer is the strictest: all overloads must hold)."""
    with _session(conn) as c:
        have_roles = {r[0]: r[1:] for r in c.execute("SELECT rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolconnlimit, rolconfig FROM pg_roles WHERE rolname = ANY(%s)", (list(roles),)).fetchall()}
        out_roles = {}
        for role, (login, sup, crole, cdb, lim, cfg) in have_roles.items():
            member = [r for r in roles if r != role and r in have_roles and c.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (role, r)).fetchone()[0]]
            out_roles[role] = {"login": login, "super": sup, "createrole": crole, "createdb": cdb, "connlimit": lim, "config": list(cfg or []), "member_of": member}
        out_tables = {}
        for t in tables:
            if not c.execute("SELECT to_regclass(%s) IS NOT NULL", (f"backlog.{t}",)).fetchone()[0]:
                continue
            owner = c.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = %s::regclass", (f"backlog.{t}",)).fetchone()[0]
            trig = c.execute("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = 'append_only' AND NOT tgisinternal AND tgenabled <> 'D')", (f"backlog.{t}",)).fetchone()[0]
            privs = {priv.lower(): {r: bool(c.execute("SELECT has_table_privilege(%s, %s, %s)", (r, f"backlog.{t}", priv)).fetchone()[0]) for r in have_roles} for priv in ("SELECT", "INSERT", "UPDATE", "DELETE")}
            out_tables[t] = {"owner": owner, "append_only": bool(trig), **privs}
        out_views = {v: bool(c.execute("SELECT to_regclass(%s) IS NOT NULL", (f"backlog.{v}",)).fetchone()[0]) for v in views}
        scoped = {}
        for role in ("backlog_gate", "backlog_adjudicator"):
            if role not in have_roles:
                continue
            rows = c.execute("SELECT c.relname, has_table_privilege(%s, c.oid, 'SELECT'), has_table_privilege(%s, c.oid, 'INSERT') OR has_table_privilege(%s, c.oid, 'UPDATE') "
                             "OR has_table_privilege(%s, c.oid, 'DELETE') FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace AND c.relkind IN ('r', 'v', 'm', 'p') ORDER BY 1",
                             (role, role, role, role)).fetchall()
            scoped[role] = {"read": [r[0] for r in rows if r[1]], "write": [r[0] for r in rows if r[2]]}
        out_routines = {}
        for name in routines:
            rows = c.execute("SELECT p.oid::regprocedure::text, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = %s",
                             (name,)).fetchall()
            if not rows:
                continue
            execs = [r for r in have_roles if all(c.execute("SELECT has_function_privilege(%s, %s::regprocedure, 'EXECUTE')", (r, sig)).fetchone()[0] for sig, *_ in rows)]
            out_routines[name] = {"owner": sorted({r[1] for r in rows})[0] if len({r[1] for r in rows}) == 1 else "MIXED", "secdef": all(r[2] for r in rows),
                                  "search_path_pinned": all(any(str(x).replace(" ", "").startswith("search_path=pg_catalog,backlog") for x in (r[3] or [])) for r in rows),
                                  "public": any(c.execute("SELECT has_function_privilege('public', %s::regprocedure, 'EXECUTE')", (sig,)).fetchone()[0] for sig, *_ in rows),
                                  "executes": sorted(execs)}
        view = c.execute("SELECT pg_get_viewdef('backlog.v_backlog_violations'::regclass)").fetchone()[0] if c.execute("SELECT to_regclass('backlog.v_backlog_violations') IS NOT NULL").fetchone()[0] else None
    return {"tables": out_tables, "views": out_views, "scoped": scoped, "routines": out_routines, "roles": out_roles, "violations_view": view}


def gate_reverts(*, conn=None) -> list:
    """[{a, b, reason, event_ts}]: the collision pairs adjudicated REVERT, oldest first (the reason carries the 40-character sha of the revert commit). Read-only; [] before slice 3."""
    with _session(conn) as c:
        if c.execute("SELECT to_regclass('backlog.gate_adjudication') IS NULL").fetchone()[0]:
            return []
        return _rows(c, "SELECT a, b, reason, event_ts FROM backlog.gate_adjudication WHERE decision = 'REVERT' ORDER BY adjudication_id")


def gate_pairs(ap_ids: list, *, conn=None) -> list:
    """[{a, b, decided}]: the collision pairs that touch any of the tickets, undecided (in the queue) and decided (adjudicated). Ticket ids only: it is what the signed attestation carries."""
    with _session(conn) as c:
        return _rows(c, "SELECT a, b, false AS decided FROM backlog.v_gate_queue WHERE a = ANY(%s::text[]) OR b = ANY(%s::text[]) "
                        "UNION ALL SELECT a, b, true FROM backlog.gate_adjudication WHERE a = ANY(%s::text[]) OR b = ANY(%s::text[]) ORDER BY 1, 2",
                     (list(ap_ids),) * 4)


def gate_queue(*, conn=None) -> list:
    """[{a, b, evidence, proposed_ts, age_hours, level}]: every undecided collision pair, oldest first (level OK / ATENCION at 48 h / ALERTA at 96 h)."""
    with _session(conn) as c:
        if c.execute("SELECT to_regclass('backlog.v_gate_queue') IS NULL").fetchone()[0]:
            return []
        return _rows(c, "SELECT a, b, evidence, proposed_ts, age_hours, level FROM backlog.v_gate_queue ORDER BY proposed_ts")


def gate_measurements(ceiling: str, *, conn=None) -> dict:
    """Everything the calibrator and the mode tool read, in one place (read-only): {'now', 'state': {'mode', 'tier'}, 'audit_start', 'anchor', 'share_3d', 'window': {...}}.
    `anchor` is the time of the latest gate_state event (None before any). The window counts run from the anchor, or the audit start, to now and never reach back more than 42 days:
    `adjudicated` / `positive` (decided OVERLAPS, DEPENDS_ON or REVERT) pairs of gate_adjudication; `confirmed`: distinct pairs whose OVERLAPS a person confirmed by another route (an
    origin other than GATE: the gate's own adjudications would make the recall circular); `flagged`: of those, the pairs the gate had already proposed."""
    if ceiling not in COLLISION_MODES:
        raise ValueError(f"ceiling must be one of {COLLISION_MODES}, got {ceiling!r}")
    with _session(conn) as c:
        now = c.execute("SELECT clock_timestamp()").fetchone()[0]
        mode, tier, _ = c.execute("SELECT mode, tier, state_event FROM backlog.fn_collision_state(%s)", (ceiling,)).fetchone()
        audit_start = c.execute("SELECT backlog.fn_audit_start()").fetchone()[0]
        anchor = c.execute("SELECT max(event_ts) FROM backlog.gate_state").fetchone()[0]
        share = c.execute("SELECT backlog.fn_gate_share(3)").fetchone()[0]
        since = max([t for t in (anchor or audit_start, now - timedelta(days=42)) if t is not None])
        adjudicated, positive = c.execute("SELECT count(*), count(*) FILTER (WHERE decision IN ('OVERLAPS', 'DEPENDS_ON', 'REVERT')) FROM backlog.gate_adjudication WHERE event_ts >= %s",
                                          (since,)).fetchone()
        confirmed, flagged = c.execute(
            "WITH conf AS (SELECT DISTINCT least(from_id, to_id) AS a, greatest(from_id, to_id) AS b FROM backlog.backlog_link_event "
            "              WHERE link_type = 'OVERLAPS' AND status = 'CONFIRMED' AND origin <> 'GATE' AND event_ts >= %s) "
            "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM backlog.backlog_link_event g WHERE g.link_type = 'OVERLAPS' AND g.status = 'PROPOSED' AND g.origin = 'GATE' "
            "                                                  AND least(g.from_id, g.to_id) = conf.a AND greatest(g.from_id, g.to_id) = conf.b)) FROM conf", (since,)).fetchone()
    return {"now": now, "state": {"mode": mode, "tier": tier}, "audit_start": audit_start, "anchor": anchor, "share_3d": None if share is None else float(share),
            "window": {"adjudicated": int(adjudicated), "positive": int(positive), "confirmed": int(confirmed), "flagged": int(flagged)}}


def adjudicate_overlap(a: str, b: str, decision: str, reason: str, *, absorbs: Optional[str] = None, actor: Optional[str] = None, conn=None) -> int:
    """Decide one undecided collision pair (backlog.sp_adjudicate_overlap). Only through the owner's adjudicator login (FONDOS_BACKLOG_PG_DSN_ADJUDICATOR), by a person at a terminal, with a
    human identity in FONDOS_ACTOR. Returns the adjudication id."""
    if decision not in COLLISION_DECISIONS:
        raise ValueError(f"decision must be one of {COLLISION_DECISIONS}, got {decision!r}")
    who = resolve_actor(actor)
    with _session(conn, write=True, adjudicator=conn is None) as c:
        return int(c.execute("SELECT backlog.sp_adjudicate_overlap(%s, %s, %s, %s, %s, %s)", (a, b, decision, absorbs, reason, who)).fetchone()[0])


def set_collision_mode(mode: str, tier: str, reason: str, ceiling: str, *, evidence: Optional[dict] = None, actor: Optional[str] = None, conn=None) -> int:
    """Append a gate_state event (backlog.sp_set_collision_mode). A person (human actor, the owner-only login) sets any mode, BLOCK only after 14 days of AUDIT and a share of 10 % or less;
    system:calibrator may only retire the gate (OFF) or tighten T0 to T1. Returns the event id."""
    if mode not in COLLISION_MODES or tier not in ("T0", "T1"):
        raise ValueError(f"mode must be one of {COLLISION_MODES} and tier T0 or T1, got {mode!r} / {tier!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True) as c:
        return int(c.execute("SELECT backlog.sp_set_collision_mode(%s, %s, %s, %s, %s, %s::jsonb)", (mode, tier, who, reason, ceiling, json.dumps(evidence or {}))).fetchone()[0])


def add_close_condition(ap_id: str, probe_kind: str, args: list, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Add one executable closing condition to a ticket (append-only; refused when the same condition is already active)."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=agent_operation(who)) as c:
        return c.execute("SELECT backlog.sp_add_close_condition(%s, %s, %s::jsonb, %s, %s)",
                         (ap_id, probe_kind, json.dumps(list(args)), reason, who)).fetchone()[0]


def retire_close_condition(condition_id: int, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Retire an active closing condition (append-only RETIRE row)."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:"), write=True, operation=agent_operation(who)) as c:
        return c.execute("SELECT backlog.sp_retire_close_condition(%s, %s, %s)", (condition_id, reason, who)).fetchone()[0]


def phase_a_catalog(*, conn=None) -> dict:
    """Read-only facts the Phase A acceptance check needs (FND-0331): the owner role, the routines and their hardening, who owns the audit tables, what
    backlog_writer may do, the guard triggers, and the ticket / backfill counts. Never raises on a store that is not applied: absent things are just absent."""
    with _session(conn) as c:
        role = c.execute("SELECT rolsuper, rolcanlogin FROM pg_roles WHERE rolname = 'backlog_owner'").fetchone()
        routines = _rows(c, "SELECT p.proname AS name, p.prosecdef AS secdef, pg_get_userbyid(p.proowner) AS owner, p.proconfig AS config FROM pg_proc p "
                            "WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_open_ticket', 'sp_transition', 'sp_close_release', "
                            "'trg_governed_writes', 'trg_actor_known', 'fn_actor_authorized') ORDER BY 1")
        tables = {r[0]: r[1] for r in c.execute("SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace "
                                                "AND c.relname IN ('status_event', 'wait_event', 'raw_update_event') ORDER BY 1").fetchall()}
        has_writer = c.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer')").fetchone()[0]
        writer = None
        if has_writer:
            def priv(sql):
                return bool(c.execute(sql).fetchone()[0])
            writer = {
                "can_update_status": priv("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'status', 'UPDATE')"),
                "can_update_release_version": priv("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'release_version', 'UPDATE')"),
                "can_update_assignee": priv("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'assignee', 'UPDATE')"),
                "can_update_title": priv("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'title', 'UPDATE')"),
                "can_insert_ticket": priv("SELECT has_table_privilege('backlog_writer', 'backlog.backlog', 'INSERT')"),
                "can_insert_log": priv("SELECT has_table_privilege('backlog_writer', 'backlog.backlog_logs', 'INSERT')"),
                "can_insert_link_event": priv("SELECT has_table_privilege('backlog_writer', 'backlog.backlog_link_event', 'INSERT')"),
                "can_write_audit_tables": priv("SELECT COALESCE(bool_or(CASE WHEN to_regclass('backlog.' || t) IS NULL THEN false "
                                               "ELSE has_table_privilege('backlog_writer', 'backlog.' || t, 'INSERT') END), false) "
                                               "FROM unnest(ARRAY['status_event', 'wait_event', 'raw_update_event']) AS t"),
                "can_execute_transition": priv("SELECT has_function_privilege('backlog_writer', to_regprocedure(%s)::oid, 'EXECUTE')" % ("'" + _SIG_TRANSITION + "'")),
            }
        triggers = sorted(r[0] for r in c.execute("SELECT t.tgname FROM pg_trigger t WHERE NOT t.tgisinternal AND t.tgrelid IN "
                                                  "('backlog.backlog'::regclass, 'backlog.backlog_logs'::regclass, 'backlog.backlog_link_event'::regclass)").fetchall())
        tickets, max_id = c.execute("SELECT count(*), max(action_point_id) FROM backlog.BACKLOG").fetchone()
        has_events = c.execute("SELECT to_regclass('backlog.status_event') IS NOT NULL").fetchone()[0]
        backfill = c.execute("SELECT count(*) FROM backlog.status_event WHERE origin = 'BACKFILL'").fetchone()[0] if has_events else None
        without_event = c.execute("SELECT count(*) FROM backlog.BACKLOG b WHERE NOT EXISTS (SELECT 1 FROM backlog.status_event e WHERE e.action_point_id = b.action_point_id)"
                                  ).fetchone()[0] if has_events else None
    return {"owner_role": None if role is None else {"superuser": role[0], "can_login": role[1]}, "routines": routines, "audit_table_owners": tables,
            "writer": writer, "triggers": triggers, "tickets": int(tickets), "max_ticket_id": max_id, "backfill_events": backfill,
            "tickets_without_status_event": None if without_event is None else int(without_event)}


def identity_history_catalog(*, conn=None) -> dict:
    """Read-only facts for the acceptance of the identity constraints (FND-0337) and the descriptive history (FND-0338): the owner-only role, whether the routines and the author
    trigger call the authorization rule, and the history table, its trigger (with the columns it watches) and what the writer may do with it. Absent things are just absent."""
    with _session(conn) as c:
        role = c.execute("SELECT r.rolsuper, r.rolcanlogin, CASE WHEN EXISTS (SELECT 1 FROM pg_roles w WHERE w.rolname = 'backlog_writer') "
                         "THEN pg_has_role('backlog_human', 'backlog_writer', 'MEMBER') END FROM pg_roles r WHERE r.rolname = 'backlog_human'").fetchone()
        fn = bool(c.execute("SELECT to_regprocedure('backlog.fn_actor_authorized(text)') IS NOT NULL").fetchone()[0])
        uses = {r[0]: bool(r[1]) for r in c.execute("SELECT p.proname, pg_get_functiondef(p.oid) LIKE '%fn_actor_authorized%' FROM pg_proc p "
                                                     "WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_open_ticket', 'sp_transition', 'trg_actor_known')").fetchall()}
        hist = None
        if c.execute("SELECT to_regclass('backlog.field_change_event') IS NOT NULL").fetchone()[0]:
            writer = c.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer')").fetchone()[0]
            trig = c.execute("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = 'field_changes' AND tgrelid = 'backlog.backlog'::regclass AND NOT tgisinternal").fetchone()
            hist = {
                "owner": c.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'backlog.field_change_event'::regclass").fetchone()[0],
                "trigger_def": trig[0] if trig else None,
                "append_only": bool(c.execute("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'append_only' AND NOT tgisinternal "
                                              "AND tgrelid = 'backlog.field_change_event'::regclass)").fetchone()[0]),
                "writer_can_insert": bool(writer and c.execute("SELECT has_table_privilege('backlog_writer', 'backlog.field_change_event', 'INSERT')").fetchone()[0]),
                "view": bool(c.execute("SELECT to_regclass('backlog.v_field_change_attribution') IS NOT NULL").fetchone()[0]),
            }
    return {"human_role": None if role is None else {"superuser": role[0], "can_login": role[1], "member_of_writer": role[2]},
            "authorization_function": fn, "calls_authorization": uses, "history": hist}


_IAM_ROLES = ("backlog_reader", "backlog_logger", "backlog_incident")
_IAM_TABLES = ("backlog", "backlog_logs", "backlog_link_event", "status_event", "actor_login_event")
_READ_HELPERS = ("fn_off_hours",)         # pure read functions the reader may execute (the binding audit view calls them): not a privilege to write or to act
_IAM_SIGS = {"sp_append_log": "backlog.sp_append_log(text,text,text)", "sp_bind_login": "backlog.sp_bind_login(text,text,text,text,boolean,text,text)", "sp_set_binding_window": "backlog.sp_set_binding_window(text,smallint[],time,time,text,text)", "fn_off_hours": "backlog.fn_off_hours(timestamptz)",
             "sp_report_incident": "backlog.sp_report_incident(text,text,text,text,text,text,interval,text,text,text,text)", "fn_actor_bound": "backlog.fn_actor_bound(text)",
             "sp_open_ticket": _SIG_OPEN, "sp_transition": _SIG_TRANSITION}


def iam_catalog(*, conn=None) -> dict:
    """Read-only facts for the acceptance of the IAM step (FND-0354): the three roles, what each can do (table privileges, routine privileges), the bindings (latest event per login,
    and the distinct PERSONS behind the active ones), and every login that can still write (EXECUTE on sp_open_ticket) with whether it is bound. Absent things are just absent."""
    with _session(conn) as c:
        have = {r[0]: r[1:] for r in c.execute("SELECT rolname, rolcanlogin, rolsuper FROM pg_roles WHERE rolname = ANY(%s)", (list(_IAM_ROLES),)).fetchall()}
        routines = {n: bool(c.execute("SELECT to_regprocedure(%s) IS NOT NULL", (sig,)).fetchone()[0]) for n, sig in _IAM_SIGS.items()}
        roles = {}
        for role in _IAM_ROLES:
            if role not in have:
                continue
            tables = [t for t in _IAM_TABLES if c.execute("SELECT to_regclass(%s) IS NOT NULL", (f"backlog.{t}",)).fetchone()[0]]
            roles[role] = {
                "can_login": have[role][0], "superuser": have[role][1],
                "select_all": all(c.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"backlog.{t}")).fetchone()[0] for t in tables),
                "write_tables": [t for t in tables for priv in ("INSERT", "UPDATE", "DELETE")
                                 if c.execute("SELECT has_table_privilege(%s, %s, %s)", (role, f"backlog.{t}", priv)).fetchone()[0]],
                "executes": sorted(n for n, present in routines.items() if present and n not in _READ_HELPERS
                                   and c.execute("SELECT has_function_privilege(%s, %s::regprocedure, 'EXECUTE')", (role, _IAM_SIGS[n])).fetchone()[0]),
            }
        bindings, writers, by_person, audit = [], [], {}, []
        store = {"declared_by_column": bool(c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'actor_login_event' AND column_name = 'declared_by')").fetchone()[0]),
                 "declared_by_constraint": bool(c.execute("SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'actor_login_declared_by')").fetchone()[0]),
                 "audit_view": bool(c.execute("SELECT to_regclass('backlog.v_binding_audit') IS NOT NULL").fetchone()[0]),
                 "window_view": bool(c.execute("SELECT to_regclass('backlog.v_binding_window') IS NOT NULL").fetchone()[0])}
        window = _rows(c, "SELECT tz, days, start_time, end_time, is_default FROM backlog.v_binding_window")[0] if store["window_view"] else None
        if c.execute("SELECT to_regclass('backlog.v_actor_login') IS NOT NULL").fetchone()[0]:
            has_declarer = bool(c.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'v_actor_login' AND column_name = 'declared_by')").fetchone()[0])
            bindings = _rows(c, "SELECT login_name, actor, person, kind, active, set_by, event_ts, " + ("declared_by" if has_declarer else "NULL::text AS declared_by") + " FROM backlog.v_actor_login ORDER BY login_name")
            if c.execute("SELECT to_regclass('backlog.v_binding_audit') IS NOT NULL").fetchone()[0]:
                audit = _rows(c, "SELECT event_id, login_name, bound_actor, person, active, declared_by, set_by, event_ts, flags FROM backlog.v_binding_audit ORDER BY event_id")
            if routines["sp_transition"]:
                # The transitions the A-2 counter counts (LIVE, a real change of status, since the last V-00) made by a HUMAN identity that is bound NOW, and made after that login was first bound
                # to it: per distinct PERSON (two logins of one person count once). status_event does not record the login; the binding makes it unforgeable from the moment it exists.
                by_person = {r[0]: int(r[1]) for r in c.execute(
                    "SELECT l.person, count(*) FROM backlog.status_event e JOIN backlog.v_actor_login l ON l.kind = 'HUMAN' AND l.active AND e.actor = l.actor "
                    "WHERE e.origin = 'LIVE' AND e.from_status IS NOT NULL AND e.from_status <> e.to_status "
                    "AND e.event_ts > COALESCE((SELECT max(r.event_ts) FROM backlog.raw_update_event r), '-infinity'::timestamptz) "
                    "AND e.event_ts >= (SELECT min(b.event_ts) FROM backlog.actor_login_event b WHERE b.login_name = l.login_name AND b.actor = l.actor AND b.active) "
                    "GROUP BY l.person ORDER BY 2 DESC, 1").fetchall()}
        if routines["sp_open_ticket"]:
            bound = {b["login_name"] for b in bindings if b["active"]}
            for (name,) in c.execute("SELECT r.rolname FROM pg_roles r WHERE r.rolcanlogin AND NOT r.rolsuper AND r.rolname !~ '^pg_' "
                                     "AND has_function_privilege(r.oid, %s::regprocedure, 'EXECUTE') ORDER BY 1", (_SIG_OPEN,)).fetchall():
                writers.append({"login": name, "bound": name in bound})
    active = [b for b in bindings if b["active"]]
    return {"roles": roles, "routines": routines, "bindings": bindings, "binding_audit": audit,
            "active_logins": len(active), "active_persons": sorted({b["person"] for b in active if b["kind"] == "HUMAN"}),
            "writers": writers, "unbound_writers": [w["login"] for w in writers if not w["bound"]],
            "transitions_by_person": by_person, "transition_persons": sorted(by_person), "binding_store": store, "binding_window": window}


def binding_audit(*, conn=None) -> list:
    """The binding audit (FND-0354, owner order 2026-10-29): one row per login binding with who declared it and the flags that need attention (LEGACY_NO_DECLARER, SELF_BINDING,
    DECLARER_NOT_BOUND_TO_SET_BY, RECENT). [] while step iam is not applied."""
    with _session(conn) as c:
        if not c.execute("SELECT to_regclass('backlog.v_binding_audit') IS NOT NULL").fetchone()[0]:
            return []
        return _rows(c, "SELECT event_id, login_name, bound_actor, person, active, declared_by, set_by, event_ts, flags FROM backlog.v_binding_audit ORDER BY event_id")


def field_change_attribution(*, conn=None) -> list:
    """Effectiveness of the descriptive history (FND-0338): per field and for ALL, how many recorded changes carry an actor. Raises when the history step is not applied."""
    with _session(conn) as c:
        return _rows(c, "SELECT field, n_changes, n_with_actor, pct_with_actor FROM backlog.v_field_change_attribution ORDER BY (field = 'ALL'), field")


def check_actors(verbose: bool = True) -> bool:
    """Read-only coverage check for the acceptance of FND-0330: True iff the alias store is applied and every author string is mapped."""
    def _line(ok: bool, msg: str) -> None:
        if verbose:
            print(f"{'OK' if ok else 'FAIL'} - {msg}")

    try:
        if not actor_store_present():
            _line(False, "backlog.actor_alias is not applied (db/pg_backlog/25_actor_alias.sql; owner runs scripts/ops/migrate_backlog_v2.py --phase alias)")
            return False
        missing = unmapped_actors()
        if missing:
            for m in missing:
                _line(False, f"author string {m['raw_actor']!r} has no alias ({m['n_rows']} rows, {m['first_ts']:%Y-%m-%d}..{m['last_ts']:%Y-%m-%d})")
            return False
        _line(True, f"every author string of logs and link events is mapped ({len(list_actor_aliases())} aliases)")
        return True
    except Exception as exc:
        _line(False, f"{type(exc).__name__}: {exc}")
        return False


def check_dsn(verbose: bool = True) -> bool:
    """
    Read-only diagnostic: verifies FONDOS_BACKLOG_PG_DSN is set, psycopg3 is installed, the
    server is reachable, and backlog.BACKLOG / backlog.BACKLOG_LOGS are queryable by the
    configured role. Never creates dummy incidents or mutates anything. Returns True iff every
    check passes.
    """
    def _line(ok: bool, msg: str) -> None:
        if verbose:
            print(f"{'OK' if ok else 'FAIL'} - {msg}")

    dsn = _dsn()
    if not dsn:
        _line(False, f"{_DSN_ENV_VAR} is not set")
        return False
    if psycopg is None:
        _line(False, "psycopg3 not installed (pip install 'psycopg[binary]')")
        return False

    try:
        with psycopg.connect(dsn, connect_timeout=5, options="-c statement_timeout=5000") as conn:
            _line(True, "connected to the configured server")
            n_backlog = conn.execute("SELECT count(*) FROM backlog.BACKLOG").fetchone()[0]
            _line(True, f"backlog.BACKLOG readable ({n_backlog} rows)")
            n_logs = conn.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS").fetchone()[0]
            _line(True, f"backlog.BACKLOG_LOGS readable ({n_logs} rows)")
            (has_seq_usage,) = conn.execute(
                "SELECT has_sequence_privilege(current_user, 'backlog.seq_backlog_fnd', 'USAGE')"
            ).fetchone()
            _line(has_seq_usage, "USAGE privilege on backlog.seq_backlog_fnd")
            return bool(has_seq_usage)
    except Exception as exc:
        _line(False, f"{type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    import argparse
    import sys as _sys

    try:
        import shared.config  # noqa: F401  (loads the repo .env so FONDOS_BACKLOG_PG_DSN is visible; run as `python -m shared.backlog_client`)
    except Exception:         # run as a bare script, or the config cannot load: the checks below then report the DSN as unset
        pass
    _parser = argparse.ArgumentParser(description=__doc__)
    _parser.add_argument(
        "--check-dsn", action="store_true",
        help=f"Read-only connectivity/permissions check against {_DSN_ENV_VAR} — exits "
             "non-zero on any failure. Does not create or modify any row.",
    )
    _parser.add_argument(
        "--check-actors", action="store_true",
        help="Read-only coverage check of the actor alias store (FND-0330) — exits non-zero when it is not applied or an author "
             "string has no alias.",
    )
    _args = _parser.parse_args()
    if _args.check_dsn:
        _sys.exit(0 if check_dsn(verbose=True) else 1)
    elif _args.check_actors:
        _sys.exit(0 if check_actors(verbose=True) else 1)
    else:
        _parser.print_help()
