#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_fund_master_geography_source.py -- FND-0441 Step 3: add fund_master.Geography_Source (additive, nullable, closed vocabulary).

    python scripts/mig/fondos/migrate_fund_master_geography_source.py            # DRY RUN: read-only, reports whether the column exists
    python scripts/mig/fondos/migrate_fund_master_geography_source.py --apply    # runs db/migrations/20261126_fund_master_geography_source.sql in one transaction, then verifies

Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role) on the database named by FONDOS_PG_DSN. Never prints a DSN. Idempotent.
Exit codes: 0 ok (or dry run / already applied), 1 apply failed (rolled back), 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "db" / "migrations" / "20261126_fund_master_geography_source.sql"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def ddl_statement(text: str) -> str:
    """The one ALTER TABLE statement of the migration file, without comments. Pure."""
    statement = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--")).strip()
    if not re.match(r"ALTER TABLE silver\.fund_master ADD COLUMN IF NOT EXISTS geography_source ", statement) or statement.count(";") != 1:
        raise ValueError("the migration file no longer holds exactly the one ALTER TABLE that adds geography_source")
    return statement


def has_column(conn) -> bool:
    return conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = 'silver.fund_master'::regclass AND attname = 'geography_source' AND NOT attisdropped").fetchone() is not None


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute the migration (default: dry run)")
    args = ap.parse_args(argv)
    statement = ddl_statement(MIGRATION.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "mig" / "fondos"))
    from shared.env_guard import require_db_driver     # RC 106 up front, not a traceback mid-run
    require_db_driver()
    import os
    import shared.config  # noqa: F401  (autoloads .env)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from migrate_fund_metric_state_ols_calc_version import LOCK_FILE, lock_file_held

    dsn, app_dsn = os.environ.get("FONDOS_PG_DSN_OWNER"), os.environ.get("FONDOS_PG_DSN")
    target_db = conninfo_to_dict(app_dsn).get("dbname") if app_dsn else None
    if not dsn or not target_db:
        print("FONDOS_PG_DSN_OWNER and FONDOS_PG_DSN must both be set (they are in .env).", file=sys.stderr)
        return RC_USAGE
    dsn = make_conninfo(dsn, dbname=target_db)
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")
    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:            # never echo the DSN
        print(f"connection failed: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    with conn:
        present = has_column(conn)
        conn.rollback()
        if present:
            print("ALREADY APPLIED: silver.fund_master.geography_source exists. Nothing to do.")
            return RC_OK
        if not args.apply:
            print("DRY RUN: the column does not exist; --apply adds it (nullable, closed vocabulary). Nothing changed.")
            return RC_OK
        if lock_file_held():
            print(f"REFUSED: the launcher cycle lock is held ({LOCK_FILE}); a cycle is running.", file=sys.stderr)
            return RC_REFUSED
        try:
            conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            conn.execute(statement)
            if not has_column(conn):
                raise RuntimeError("the column is absent after the ALTER TABLE")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        print("APPLIED: silver.fund_master.geography_source added. Next: the P1 pass, then backfill_geography_source.py.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
