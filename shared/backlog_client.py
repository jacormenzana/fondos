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
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN"
_FALLBACK_LOG_ENV_VAR = "FONDOS_BACKLOG_FALLBACK_LOG"
_DEFAULT_FALLBACK_LOG = Path(__file__).resolve().parents[1] / "log" / "backlog_autolog_fallback.jsonl"

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

                cur.execute(
                    "SELECT 'FND-' || LPAD(nextval('backlog.seq_backlog_fnd')::text, 4, '0')"
                )
                apid = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO backlog.BACKLOG
                        (action_point_id, title, project_code, nature, schema_name, object_type,
                         object_name, status, priority, reporter, scenario_description)
                    VALUES (%s, %s, %s, 'BUG', %s, %s, %s, 'OPEN', 'HIGH', 'SYSTEM_AUTODETECT', %s)
                    """,
                    (
                        apid,
                        title or f"[auto] {object_name}: {scenario_description[:100]}",
                        project_code, schema_name, object_type, object_name, scenario_description,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
                    VALUES (%s, 'SYSTEM_AUTODETECT', %s)
                    """,
                    (apid, f"Auto-opened: {scenario_description}"
                           + (f"\n\nPossibly related (NOT merged): {suggestion['ticket']} -- {suggestion['reason']}"
                              if suggestion else "")),
                )
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

LINK_TYPES = ("DEPENDS_ON", "RELEASE_WITH", "OVERLAPS", "DUPLICATES", "SPLIT_FROM", "RELATED")
LINK_STATUSES = ("PROPOSED", "CONFIRMED", "REJECTED")
_ANALYSIS_OPTIONS = "-c statement_timeout=30000"

_TICKET_COLS = (
    "action_point_id, title, project_code, nature, schema_name, object_type, object_name, status, priority, reporter, "
    "assignee, creation_ts, update_ts, closure_ts, solution_type, release_version, scenario_description"
)
_LINK_COLS = "event_id, from_id, to_id, link_type, status, evidence, absorbs, username, event_ts"


class BacklogUnavailable(RuntimeError):
    """The backlog cannot be reached (no DSN, no driver). Raised instead of returning an empty answer."""


@contextmanager
def _session(conn=None):
    if conn is not None:
        yield conn
        return
    dsn = _dsn()
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
        checked.append((ev["from_id"], ev["to_id"], ev["link_type"], ev["status"], ev["evidence"], ev.get("absorbs"),
                        ev["username"]))
    ids: list[int] = []
    with _session(conn) as c:
        for row in checked:
            (event_id,) = c.execute(
                "INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, absorbs, username) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING event_id", row,
            ).fetchone()
            ids.append(event_id)
    return ids


def record_link_event(*, from_id: str, to_id: str, link_type: str, status: str, evidence: str, username: str,
                      absorbs: Optional[str] = None, conn=None) -> int:
    """One-event form of record_link_events()."""
    return record_link_events([{
        "from_id": from_id, "to_id": to_id, "link_type": link_type, "status": status,
        "evidence": evidence, "absorbs": absorbs, "username": username,
    }], conn=conn)[0]


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

    _parser = argparse.ArgumentParser(description=__doc__)
    _parser.add_argument(
        "--check-dsn", action="store_true",
        help=f"Read-only connectivity/permissions check against {_DSN_ENV_VAR} — exits "
             "non-zero on any failure. Does not create or modify any row.",
    )
    _args = _parser.parse_args()
    if _args.check_dsn:
        _sys.exit(0 if check_dsn(verbose=True) else 1)
    else:
        _parser.print_help()
