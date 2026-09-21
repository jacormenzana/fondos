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
                    (apid, f"Auto-opened: {scenario_description}"),
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
