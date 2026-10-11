#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_portfolio_scenario_status.py -- install the DRAFT / PRODUCTION / ARCHIVED lifecycle of portfolio scenarios
(proyecto3/src/scenario_lifecycle.py; owner decision 2026-10-11).

    python scripts/ops/migrate_portfolio_scenario_status.py            # DRY RUN: read-only, reports what exists, no DDL
    python scripts/ops/migrate_portfolio_scenario_status.py --apply    # runs the block in ONE transaction (all or nothing)

The DDL is NOT duplicated here: it is the block between `-- BEGIN scenario_lifecycle` and `-- END scenario_lifecycle` in db/pg/30_gold.sql
(single source of truth, P#11), idempotent. Effect on the live table: adds status / promoted_at / promoted_by / supersedes, a CHECK and a partial
unique index (one PRODUCTION row). Existing scenarios become ARCHIVED except the newest, which stays DRAFT. No scenario is promoted.

Nothing in the pipeline breaks before or after: the P3 code feature-detects the column (`scenario_lifecycle.has_status_column`). Rollback:
DROP INDEX gold.ux_portfolio_scenarios_production; ALTER TABLE gold.portfolio_scenarios DROP CONSTRAINT portfolio_scenarios_status_chk,
DROP COLUMN status, DROP COLUMN promoted_at, DROP COLUMN promoted_by, DROP COLUMN supersedes.

Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role; fondos_app has DML only by design, FND-0072). Never prints the DSN.
Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL_FILE = ROOT / "db" / "pg" / "30_gold.sql"
BLOCK = "scenario_lifecycle"
COLUMNS = ("status", "promoted_at", "promoted_by", "supersedes")
INDEX = "ux_portfolio_scenarios_production"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def ddl_block(path: Path = DDL_FILE, name: str = BLOCK) -> str:
    """The block exactly as written in db/pg/30_gold.sql, markers included (the extraction of migrate_cycle_telemetry, reused: P#11)."""
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    from migrate_cycle_telemetry import ddl_block as _extract
    return _extract(path, name)


def existing(conn) -> dict:
    """{'columns': [present lifecycle columns], 'index': bool, 'statuses': {status: n}}"""
    cols = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'gold' AND table_name = 'portfolio_scenarios' "
        "AND column_name = ANY(%s)", (list(COLUMNS),)).fetchall()]
    idx = conn.execute("SELECT 1 FROM pg_indexes WHERE schemaname = 'gold' AND indexname = %s", (INDEX,)).fetchone() is not None
    statuses = {}
    if "status" in cols:
        statuses = {r[0]: int(r[1]) for r in conn.execute(
            "SELECT status, COUNT(*) FROM gold.portfolio_scenarios GROUP BY status ORDER BY status").fetchall()}
    return {"columns": sorted(cols), "index": idx, "statuses": statuses}


def migrate(conn, lock_timeout: str = LOCK_TIMEOUT) -> None:
    """The whole block in ONE transaction. Raises on any failure (the caller rolls back)."""
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(ddl_block())


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
    from migrate_fund_metric_state_ols_calc_version import LOCK_FILE, lock_file_held

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
    try:
        have = existing(conn)
        conn.rollback()
        print(f"lifecycle columns present: {have['columns']} of {list(COLUMNS)}; production index: "
              f"{'present' if have['index'] else 'absent'}; scenarios by status: {have['statuses'] or 'n/a (no status column yet)'}")
        if not args.apply:
            print("\nDRY RUN: nothing changed. DDL that --apply would run is the block in db/pg/30_gold.sql "
                  f"({len(ddl_block().splitlines())} lines).")
            return RC_OK
        if lock_file_held():
            print(f"REFUSED: the launcher cycle lock is held ({LOCK_FILE}); a cycle is running.", file=sys.stderr)
            return RC_REFUSED
        try:
            migrate(conn)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        after = existing(conn)
        conn.rollback()
        print(f"\nAPPLIED: columns {after['columns']}; production index: {'present' if after['index'] else 'ABSENT'}; "
              f"scenarios by status: {after['statuses']}. No scenario is PRODUCTION until the owner promotes one "
              "(scripts\\launch\\p3_promote_scenario.py).")
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
