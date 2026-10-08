#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_fund_metric_state_ols_calc_version.py — add control.fund_metric_state.last_ols_calc_version and
backfill it, so the CALC_VERSION-aware OLS gate (run_pipeline._ols_is_fresh) does not recompute the macro
OLS of every fund on its first run.

    python scripts/ops/migrate_fund_metric_state_ols_calc_version.py            # DRY RUN: read-only, no DDL, no lock
    python scripts/ops/migrate_fund_metric_state_ols_calc_version.py --apply    # ALTER + backfill in ONE transaction

Why (2026-10-05 RCA): the 2026-10-04 CALC_VERSION bump bypassed the input-hash cache but not the quarterly
OLS gate, so every fund skipped OLS (ols_funds=0) and the macro rows stayed on the old version.

Backfill rule: a fund is marked with the live CALC_VERSION only when it has an OLS quarter AND its macro_r2
row is on that version AND no beta_*/macro_* row of the fund is on any other version. The beta set varies
per fund (VIF filter, coverage, FND-0208 guard), so no fixed row count is used. A partial set cannot exist:
run_pipeline._replace_beta_set deletes and re-inserts inside the fund's single transaction. Funds left NULL
are recomputed once by the next run — correct and small.

Run ORDER (strictly sequential): disable the scheduled task that launches P1_P2_P3.bat / P1_P2_Complete.bat
-> ANALYZE gold.fund_metrics (after the remediation P2 run) -> this script with --apply -> verify -> ONLY THEN
deploy the code (P2 asserts the column at startup and aborts if it is missing) -> re-enable the scheduler.
ROLLBACK: never DROP the column while the new code is deployed (P2 would abort at its startup check);
`git revert` the code first, the nullable column is harmless to old code.

Runs with FONDOS_PG_DSN_OWNER (DDL needs table ownership; fondos_app has none by design, FND-0072). Never
prints the DSN. Safe to re-run (IF NOT EXISTS; the UPDATE re-sets the same value).

Exit codes: 0 ok (or dry run), 1 apply failed / lock not obtained, 2 usage or connection error,
3 pre-check refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_TABLE = "control.fund_metric_state"
METRICS_TABLE = "gold.fund_metrics"
LOG_TABLE = "control.p2_pipeline_log"
LOCK_FILE = ROOT / "proyecto1" / "log" / "fondos_cycle.lock"
ACTIVITY_WINDOW_MIN = 15
LOCK_TIMEOUT = "5s"
RETRY_BACKOFF_S = (2, 5, 10)            # 1 attempt + 3 retries
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def _where(metrics_table: str) -> str:
    """Backfill predicate (alias `s` = state row). `%s` x3 = calc_version; `%%` escapes LIKE for psycopg."""
    return (
        "s.last_ols_quarter IS NOT NULL "
        f"AND EXISTS (SELECT 1 FROM {metrics_table} m WHERE m.isin = s.isin AND m.horizon = 'since_inception' "
        "AND m.metric = 'macro_r2' AND m.algorithm_version = %s) "
        f"AND NOT EXISTS (SELECT 1 FROM {metrics_table} m WHERE m.isin = s.isin AND m.horizon = 'since_inception' "
        "AND (m.metric LIKE 'beta\\_%%' OR m.metric LIKE 'macro\\_%%') "
        "AND m.algorithm_version IS DISTINCT FROM %s)"
    )


def update_sql(state_table: str = STATE_TABLE, metrics_table: str = METRICS_TABLE) -> str:
    return f"UPDATE {state_table} s SET last_ols_calc_version = %s WHERE {_where(metrics_table)}"


def count_sql(state_table: str = STATE_TABLE, metrics_table: str = METRICS_TABLE) -> str:
    return f"SELECT count(*) FROM {state_table} s WHERE {_where(metrics_table)}"


def lock_file_held(path: Path = LOCK_FILE) -> bool:
    """True when another process holds the launcher's cycle lock. The launcher keeps the file open through
    a cmd redirection (`9>file`), which denies write sharing, so opening it for append fails while held."""
    if not path.exists():
        return False
    try:
        with open(path, "a"):
            return False
    except PermissionError:
        return True
    except OSError:
        return True                     # cannot tell -> refuse, the safe side


def recent_activity(conn, log_table: str = LOG_TABLE, minutes: int = ACTIVITY_WINDOW_MIN) -> int:
    """p2_pipeline_log rows written in the last `minutes` (a P2 run in progress logs continuously)."""
    return conn.execute(
        f"SELECT count(*) FROM {log_table} WHERE created_at > now() - make_interval(mins => %s)", (minutes,)
    ).fetchone()[0]


def column_exists(conn, state_table: str = STATE_TABLE) -> bool:
    schema, _, table = state_table.rpartition(".")
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = 'last_ols_calc_version' "
        "AND (%s = '' OR table_schema = %s)", (table, schema, schema)).fetchone()
    return row is not None


def report(conn, calc_version: str, state_table: str = STATE_TABLE, metrics_table: str = METRICS_TABLE) -> dict:
    """Counts only (read-only): rows in the state table, rows the backfill would mark, rows left NULL."""
    total = conn.execute(f"SELECT count(*) FROM {state_table}").fetchone()[0]
    with_ols = conn.execute(f"SELECT count(*) FROM {state_table} WHERE last_ols_quarter IS NOT NULL").fetchone()[0]
    markable = conn.execute(count_sql(state_table, metrics_table), (calc_version, calc_version)).fetchone()[0]
    return {"state_rows": total, "with_ols_quarter": with_ols, "markable": markable,
            "left_null": with_ols - markable}


def migrate(conn, calc_version: str, state_table: str = STATE_TABLE, metrics_table: str = METRICS_TABLE,
            lock_timeout: str = LOCK_TIMEOUT) -> int:
    """ALTER + backfill in ONE transaction; returns rows marked. Raises on any failure (caller rolls back)."""
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS last_ols_calc_version text")
    cur = conn.execute(update_sql(state_table, metrics_table), (calc_version, calc_version, calc_version))
    return cur.rowcount


def blockers(conn, state_table: str = STATE_TABLE) -> list:
    """Sessions currently holding a lock on the state table (named in the message when the lock times out)."""
    return conn.execute(
        "SELECT a.pid, a.usename, a.state, left(a.query, 100) FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
        "WHERE l.relation = %s::regclass AND l.pid <> pg_backend_pid()", (state_table,)).fetchall()


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute the migration (default: dry run)")
    ap.add_argument("--calc-version", help="CALC_VERSION to stamp (default: the one in run_pipeline.py)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "launch"))
    from shared.env_guard import require_db_driver     # RC 106 up front, not a traceback mid-run
    require_db_driver()
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from p1p2_state import calc_version as _live_calc_version

    calc_version = args.calc_version or _live_calc_version()
    if not calc_version:
        print("cannot read CALC_VERSION from run_pipeline.py; pass --calc-version", file=sys.stderr)
        return RC_USAGE
    dsn = os.environ.get("FONDOS_PG_DSN_OWNER")
    if not dsn:
        print("FONDOS_PG_DSN_OWNER is not set (it is in .env); DDL needs the owner role.", file=sys.stderr)
        return RC_USAGE
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")
    print(f"CALC_VERSION to stamp: {calc_version}")

    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:            # never echo the DSN
        print(f"connection failed: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    with conn:
        existed = column_exists(conn)
        print(f"column last_ols_calc_version: {'present' if existed else 'absent'}")
        rep = report(conn, calc_version)
        print("backfill preview: " + ", ".join(f"{k}={v}" for k, v in rep.items()))
        plan = conn.execute("EXPLAIN (ANALYZE, COSTS OFF) " + count_sql(), (calc_version, calc_version)).fetchall()
        print("EXPLAIN ANALYZE of the backfill predicate:\n  " + "\n  ".join(r[0] for r in plan))
        conn.rollback()

        if not args.apply:
            print("\nDRY RUN: nothing changed (no DDL, no lock). Compare `markable` with ols_funds of the last "
                  "remediation run, then re-run with --apply.")
            return RC_OK

        if lock_file_held():
            print(f"REFUSED: the launcher cycle lock is held ({LOCK_FILE}); a cycle is running.", file=sys.stderr)
            return RC_REFUSED
        n_recent = recent_activity(conn)
        conn.rollback()
        if n_recent:
            print(f"REFUSED: {n_recent} p2_pipeline_log rows in the last {ACTIVITY_WINDOW_MIN} min; "
                  "P2 appears to be running.", file=sys.stderr)
            return RC_REFUSED

        for attempt, wait in enumerate((0, *RETRY_BACKOFF_S), start=1):
            if wait:
                print(f"lock not obtained; retrying in {wait}s (attempt {attempt}/{len(RETRY_BACKOFF_S) + 1})")
                time.sleep(wait)
            try:
                marked = migrate(conn, calc_version)
                conn.commit()
                break
            except psycopg.errors.LockNotAvailable:
                conn.rollback()         # nothing committed on a failed attempt
            except Exception as exc:
                conn.rollback()
                print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
                return RC_FAILED
        else:
            print("FAILED: could not lock control.fund_metric_state; nothing committed. Blocking sessions:",
                  file=sys.stderr)
            for pid, user, state, query in blockers(conn):
                print(f"  pid={pid} user={user} state={state} query={query!r}", file=sys.stderr)
            conn.rollback()
            return RC_FAILED

        after = report(conn, calc_version)
        conn.rollback()
        print(f"\nAPPLIED: {marked} rows marked with {calc_version}; "
              + ", ".join(f"{k}={v}" for k, v in after.items()))
        print("Verify, THEN deploy the code, then re-enable the scheduler.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
