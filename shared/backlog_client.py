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
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN"
_HUMAN_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_HUMAN"          # the owner-only role (FND-0337); deliberately NOT in .env
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
    dsn = _dsn()
    if not dsn:
        logger.info(
            "[BACKLOG-AUTOLOG] disabled — %s not set — skipping incident capture for %s",
            _DSN_ENV_VAR, object_name,
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
        with psycopg.connect(
            dsn, autocommit=False, connect_timeout=5,
            options="-c statement_timeout=5000",
        ) as conn:
            with conn.cursor() as cur:
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


@contextmanager
def _session(conn=None, *, human: bool = False):
    if conn is not None:
        yield conn
        return
    dsn = dsn_for_actor("human:x") if human else _dsn()
    if not dsn:
        raise BacklogUnavailable(f"{_DSN_ENV_VAR} is not set")
    if psycopg is None:
        raise BacklogUnavailable("psycopg3 is not installed (pip install 'psycopg[binary]')")
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


def append_log(action_point_id: str, username: str, comment: str, *, conn=None) -> int:
    """Append one progress note to `backlog.BACKLOG_LOGS` (insert-only, like every progress record: AGENTS.md "never overwrite
    prior progress"). Does NOT touch the ticket's status. Fail-loud; returns the new log_id."""
    if not str(action_point_id or "").strip() or not str(username or "").strip() or not str(comment or "").strip():
        raise ValueError("action_point_id, username and comment are all required")
    with _session(conn, human=str(username).startswith("human:")) as c:
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
    with _session(conn, human=any(str(ev["username"]).startswith("human:") for ev in events)) as c:
        for row in checked:
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


class ActorUnknown(ValueError):
    """No canonical actor could be established. Raised instead of guessing: the OS login is the owner's for every session that runs
    under their account, so it cannot tell the owner from an agent."""


def current_actor(environ: Optional[dict] = None) -> str:
    """The canonical identity of whoever is writing, from FONDOS_ACTOR (e.g. `agent:claude-opus-5-5`, `human:jacor`). Raises ActorUnknown
    when it is unset or not in canonical form; it never falls back to the OS user."""
    env = os.environ if environ is None else environ
    value = (env.get(_ACTOR_ENV_VAR) or "").strip()
    if not value:
        raise ActorUnknown(f"{_ACTOR_ENV_VAR} is not set (expected human:<login> | agent:<model> | system:<name>)")
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
    """The canonical identity to write with: the explicit `actor` when given (validated), else FONDOS_ACTOR (validated). Raises ActorUnknown."""
    if actor is not None:
        value = str(actor).strip()
        if not ACTOR_PATTERN.match(value):
            raise ActorUnknown(f"actor {value!r} is not canonical (expected human:<login> | agent:<model> | system:<name>)")
        return value
    return current_actor(environ)


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
    with _session(conn, human=who.startswith("human:")) as c:
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
    `comment` becomes the progress log verbatim. Returns the status_event id. Raises on any refusal; never swallows."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:")) as c:
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
    with _session(conn, human=who.startswith("human:")) as c:
        return int(c.execute("SELECT backlog.sp_ack_review(%s, %s, %s)", (flag_id, who, comment)).fetchone()[0])


def raise_hold(ap_id: str, reason: str, *, code: Optional[str] = None, actor: Optional[str] = None, conn=None) -> int:
    """Raise a hold on a ticket (it cannot reach READY_FOR_DEPLOY or a delivery closure until a human releases it). Returns the hold id."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:")) as c:
        return int(c.execute("SELECT backlog.sp_raise_hold(%s, %s, %s, %s)", (ap_id, reason, code, who)).fetchone()[0])


def release_hold(hold_id: int, reason: str, *, actor: str, conn=None) -> int:
    """Release a hold. Only a human actor can (through FONDOS_BACKLOG_PG_DSN_HUMAN, the owner-only role)."""
    with _session(conn, human=True) as c:
        return int(c.execute("SELECT backlog.sp_release_hold(%s, %s, %s)", (hold_id, actor, reason)).fetchone()[0])


def set_gate(mode: str, reason: str, *, actor: str, gate: str = "DELIVERY", conn=None) -> int:
    """Set the delivery gate mode: OFF, AUDIT (evaluate and log, never refuse) or ENFORCE (refuse; only after a clear 48-hour audit window). Only a human actor can (the
    owner-only role); the switch is append-only and the deployment seeds AUDIT."""
    if mode not in GATE_MODES:
        raise ValueError(f"mode must be one of {GATE_MODES}, got {mode!r}")
    with _session(conn, human=True) as c:
        return int(c.execute("SELECT backlog.sp_set_gate(%s, %s, %s, %s)", (gate, mode, actor, reason)).fetchone()[0])


def gate_mode(gate: str = "DELIVERY", *, conn=None) -> str:
    """The current mode of the gate: OFF, AUDIT or ENFORCE (before Phase B the A-1 stub answers OFF)."""
    with _session(conn) as c:
        return str(c.execute("SELECT backlog.fn_gate_mode(%s)", (gate,)).fetchone()[0])


def gate_audit_window(*, conn=None) -> dict:
    """The audit window the owner reads before authorizing enforcement: mode, since_ts, hours_in_audit, evaluated, tickets_48h (different tickets with a gated move in the last
    48 hours), required_tickets (5), violations_since, violations_48h and `clear` (48+ hours of audit, gated moves on 5+ different tickets in the last 48 hours, no violation in
    them: the DB-side condition of ENFORCE). Needs Phase B."""
    with _session(conn) as c:
        return _rows(c, "SELECT mode, since_ts, hours_in_audit, evaluated, tickets_48h, required_tickets, violations_since, violations_48h, clear FROM backlog.v_gate_audit_window")[0]


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
    with _session(conn, human=who.startswith("human:")) as c:
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
            window = _rows(c, "SELECT mode, since_ts, hours_in_audit, evaluated, tickets_48h, required_tickets, violations_since, violations_48h, clear FROM backlog.v_gate_audit_window")[0]
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
            "gate_mode": mode, "audit_window": window, "writer": writer, "existing_depends_on_cycles": int(cycles)}


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
    with _session(conn, human=who.startswith("human:")) as c:
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
    with _session(conn, human=who.startswith("human:")) as c:
        return c.execute("SELECT backlog.sp_create_group(%s, %s, %s, %s)", (name, kind, description, who)).fetchone()[0]


def group_member(group: str, ap_id: str, op: str, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """ADD a ticket to a group or REMOVE it (backlog.sp_group_member; append-only events). Returns the event id; raises when the move changes nothing."""
    if op not in ("ADD", "REMOVE"):
        raise ValueError(f"op must be ADD or REMOVE, got {op!r}")
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:")) as c:
        return c.execute("SELECT backlog.sp_group_member(%s, %s, %s, %s, %s)", (group, ap_id, op, reason, who)).fetchone()[0]


def violation_summary(*, top: int = 3, conn=None) -> dict:
    """{'applied', 'total', 'by_code': [{'code', 'n'}] (largest first), 'top': the first `top` of them}: the count the cycle report prints
    (Phase D, backlog.v_backlog_violations). 'applied' is False, with no counts, while the owner has not applied the phase."""
    with _session(conn) as c:
        if not c.execute("SELECT to_regclass('backlog.v_backlog_violations') IS NOT NULL").fetchone()[0]:
            return {"applied": False, "total": None, "by_code": [], "top": []}
        by_code = _rows(c, "SELECT code, count(*) AS n FROM backlog.v_backlog_violations GROUP BY code ORDER BY count(*) DESC, code")
    return {"applied": True, "total": sum(r["n"] for r in by_code), "by_code": by_code, "top": by_code[:top]}


OPS_KINDS = ("COMMIT", "RELEASE", "PROMOTION", "CYCLE_FLAG", "AUDIT_INCIDENT")     # Phase C (FND-0334): db/pg_backlog/70_sync.sql
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
    with _session(conn, human=who.startswith("human:")) as c:
        return bool(c.execute("SELECT backlog.sp_record_ops_event(%s, %s, %s, %s, %s::jsonb, %s, %s)",
                              (kind, ref, ap_id, source, json.dumps(payload or {}), fact_ts, who)).fetchone()[0])


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


def add_close_condition(ap_id: str, probe_kind: str, args: list, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Add one executable closing condition to a ticket (append-only; refused when the same condition is already active)."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:")) as c:
        return c.execute("SELECT backlog.sp_add_close_condition(%s, %s, %s::jsonb, %s, %s)",
                         (ap_id, probe_kind, json.dumps(list(args)), reason, who)).fetchone()[0]


def retire_close_condition(condition_id: int, reason: str, *, actor: Optional[str] = None, conn=None) -> int:
    """Retire an active closing condition (append-only RETIRE row)."""
    who = resolve_actor(actor)
    with _session(conn, human=who.startswith("human:")) as c:
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
