#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_cycle_flag_read_ts.py -- add control.cycle_flag.ap_status_read_ts (FND-0332, decision-time read rule).

    python scripts/ops/migrate_cycle_flag_read_ts.py            # DRY RUN: read-only, reports what exists
    python scripts/ops/migrate_cycle_flag_read_ts.py --apply    # runs the block in ONE transaction

The DDL is the block between `-- BEGIN cycle_flag_read_ts` and `-- END cycle_flag_read_ts` in db/pg/35_control.sql (single source of truth,
P#11): one nullable column (ADD COLUMN IF NOT EXISTS, no rewrite, no default). Idempotent.

Nothing needs a switch: shared.cycle_telemetry feature-detects the column and, once it exists, stores the time each flag's ap_status was read;
the cycle report then lists V-STALE-READ (a snapshot read before the ticket's latest status event). ROLLBACK:
ALTER TABLE control.cycle_flag DROP COLUMN ap_status_read_ts. Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role). Never prints the DSN.
Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK = "cycle_flag_read_ts"
TABLE = "control.cycle_flag"
COLUMN = "ap_status_read_ts"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def existing(conn) -> dict:
    table = conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] is not None
    col = conn.execute("SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s AND NOT attisdropped",
                       (TABLE, COLUMN)).fetchone()[0]
    return {"table": table, "read_ts_column": col == 1}


def migrate(conn, lock_timeout: str = LOCK_TIMEOUT) -> None:
    """The whole block in the caller's transaction. Raises on any failure (the caller rolls back)."""
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    from migrate_cycle_telemetry import ddl_block
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(ddl_block(name=BLOCK))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute the migration (default: dry run)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    from shared.env_guard import require_db_driver     # RC 106 up front, not a traceback mid-run
    require_db_driver()
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from migrate_fund_metric_state_ols_calc_version import ACTIVITY_WINDOW_MIN, LOCK_FILE, lock_file_held, recent_activity

    dsn = os.environ.get("FONDOS_PG_DSN_OWNER")
    if not dsn:
        print("FONDOS_PG_DSN_OWNER is not set (it is in .env); DDL needs the owner role.", file=sys.stderr)
        return RC_USAGE
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")
    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:            # never echo the DSN
        print(f"connection failed: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    with conn:
        have = existing(conn)
        conn.rollback()
        if not have["table"]:
            print(f"{TABLE} is absent: the cycle telemetry migration (FND-0239) must be applied first.", file=sys.stderr)
            return RC_USAGE
        print(f"{TABLE}.{COLUMN}: {'present' if have['read_ts_column'] else 'absent'}")
        if not args.apply:
            print(f"\nDRY RUN: nothing changed. --apply runs the `{BLOCK}` block of db/pg/35_control.sql.")
            return RC_OK
        if lock_file_held():
            print(f"REFUSED: the launcher cycle lock is held ({LOCK_FILE}); a cycle is running.", file=sys.stderr)
            return RC_REFUSED
        n_recent = recent_activity(conn)
        conn.rollback()
        if n_recent:
            print(f"REFUSED: {n_recent} p2_pipeline_log rows in the last {ACTIVITY_WINDOW_MIN} min; P2 appears to be running.", file=sys.stderr)
            return RC_REFUSED
        try:
            migrate(conn)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        after = existing(conn)
        print(f"\nAPPLIED: {COLUMN} {'present' if after['read_ts_column'] else 'ABSENT'}. The next flag written with a ticket stores its read time.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
