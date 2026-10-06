#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_fund_metric_family_state.py — create control.fund_metric_family_state (FND-0236), the per-metric-family
calculation state read by P2 while shared.config.FAMILY_VERSIONING_ENABLED is on.

    python scripts/ops/migrate_fund_metric_family_state.py            # DRY RUN: read-only, no DDL
    python scripts/ops/migrate_fund_metric_family_state.py --apply    # CREATE TABLE IF NOT EXISTS, one transaction

No backfill: the first P2 run with the switch on adopts every fund from its legacy fund_metric_state.input_hash
(utils.family_versions.decide_fund, "seeded from legacy hash"), recomputing nothing.

The DDL is NOT duplicated here: the statement is read from db/pg/35_control.sql (single source of truth, P#11).

Run ORDER (strictly sequential): make sure no cycle is running -> this script with --apply -> verify -> set
FAMILY_VERSIONING_ENABLED = True in shared/config.py -> the next P2 run seeds the family state.
ROLLBACK: set the switch back to False (the next plain run recomputes every fund once); the empty table is harmless
and may stay. Never DROP it while code with the switch on is deployed (P2 asserts it at startup).

Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role; fondos_app has DML only by design, FND-0072); table
privileges for fondos_app come from the schema's default privileges. Never prints the DSN. Safe to re-run.

Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLE = "control.fund_metric_family_state"
DDL_FILE = ROOT / "db" / "pg" / "35_control.sql"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3

def ddl_sql(path: Path = DDL_FILE, table: str = TABLE) -> str:
    """The CREATE TABLE statement for `table`, as written in db/pg/35_control.sql (without the trailing ';').
    The statement ends at the first closing parenthesis in column 0 (plus any table options up to the ';').
    `table` is a parameter so the other control-table migrations reuse this (scripts/ops/migrate_audit_accepted_finding.py)."""
    pattern = re.compile(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \(.*?\n\)[^;]*;", re.S)
    m = pattern.search(path.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"CREATE TABLE {table} not found in {path}")
    return m.group(0).rstrip(";")


def table_exists(conn, table: str = TABLE) -> bool:
    schema, _, name = table.rpartition(".")
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s AND table_schema = %s", (name, schema)
    ).fetchone() is not None


def migrate(conn, lock_timeout: str = LOCK_TIMEOUT, table: str = TABLE) -> None:
    """CREATE TABLE IF NOT EXISTS in ONE transaction. Raises on any failure (caller rolls back)."""
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(ddl_sql(table=table))


def main(argv: list | None = None, table: str = TABLE) -> int:
    """Shared CLI of the control-table migrations; migrate_audit_accepted_finding.py calls it with its own table."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute the migration (default: dry run)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from migrate_fund_metric_state_ols_calc_version import (   # same guards as the FND-0229 migration
        ACTIVITY_WINDOW_MIN, LOCK_FILE, lock_file_held, recent_activity,
    )

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
        existed = table_exists(conn, table)
        print(f"{table}: {'present' if existed else 'absent'}")
        conn.rollback()
        if not args.apply:
            print("\nDRY RUN: nothing changed. DDL that --apply would run:\n" + ddl_sql(table=table))
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
        try:
            migrate(conn, table=table)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        print(f"\nAPPLIED: {table} is {'already there (no change)' if existed else 'created'}. "
              + ("Verify, then set FAMILY_VERSIONING_ENABLED = True." if table == TABLE else "Verify."))
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
