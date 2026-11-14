#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_fund_master_derived_fee_flags.py -- Package H (FND-0408, plan R6.3 / R8): fee_known_flag and the new exit_fee_known_flag become GENERATED columns.

    python scripts/mig/fondos/migrate_fund_master_derived_fee_flags.py            # DRY RUN: read-only, reports the state of both columns
    python scripts/mig/fondos/migrate_fund_master_derived_fee_flags.py --apply    # runs the DDL in ONE transaction, then verifies

The DDL is db/migrations/20261114_fund_master_derived_fee_flags.sql (generated from shared.fee_state.fee_state_sql, the expression a Postgres test proves equal to
shared.fee_state.derive_fee_state): the old stored fee_known_flag is dropped and both flags are added as GENERATED ALWAYS ... STORED. Apply it AFTER the whole-universe pass
ran with ATTRIBUTE_GROUP_WRITE_ENABLED ON (the writer then no longer writes the flag; a generated column refuses an explicit value). Idempotent: when the columns are already
generated it reports so and does nothing. The pre-change values are in out/pkg_h/backup_fund_master_fee.csv.
After --apply the verification must show 0 NULL flags; then the post-migration commit follows (db/pg/20_silver.sql, shared/schema_checks.py V24, bi_compat.sql, rename_map.yaml).
Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role). Never prints the DSN.
Exit codes: 0 ok (or dry run / already applied), 1 apply failed (rolled back), 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "db" / "migrations" / "20261114_fund_master_derived_fee_flags.sql"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def ddl_statement(text: str) -> str:
    """The ALTER TABLE statement of the migration file (without its BEGIN / COMMIT: the caller owns the transaction). Pure."""
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
    body = re.sub(r"^\s*(BEGIN|COMMIT)\s*;\s*$", "", body, flags=re.MULTILINE | re.IGNORECASE)
    statement = body.strip()
    if not statement.upper().startswith("ALTER TABLE SILVER.FUND_MASTER") or statement.count(";") != 1:
        raise ValueError("the migration file no longer holds exactly one ALTER TABLE silver.fund_master statement")
    return statement


def state(conn) -> dict:
    """{column: 'generated' | 'plain' | 'absent'} for the two flags."""
    out = {}
    for col in ("fee_known_flag", "exit_fee_known_flag"):
        row = conn.execute("SELECT attgenerated FROM pg_attribute WHERE attrelid = 'silver.fund_master'::regclass AND attname = %s AND NOT attisdropped", (col,)).fetchone()
        out[col] = "absent" if row is None else ("generated" if row[0] == "s" else "plain")
    return out


def verify(conn) -> dict:
    nulls = conn.execute("SELECT count(*) FILTER (WHERE fee_known_flag IS NULL), count(*) FILTER (WHERE exit_fee_known_flag IS NULL), count(*) FROM silver.fund_master").fetchone()
    entry = conn.execute("SELECT fee_known_flag, count(*) FROM silver.fund_master GROUP BY 1 ORDER BY 2 DESC").fetchall()
    exit_ = conn.execute("SELECT exit_fee_known_flag, count(*) FROM silver.fund_master GROUP BY 1 ORDER BY 2 DESC").fetchall()
    return {"null_entry": nulls[0], "null_exit": nulls[1], "funds": nulls[2], "entry": [tuple(r) for r in entry], "exit": [tuple(r) for r in exit_]}


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
        have = state(conn)
        conn.rollback()
        print(f"silver.fund_master: {have}")
        if have["fee_known_flag"] == "generated" and have["exit_fee_known_flag"] == "generated":
            print("\nALREADY APPLIED: both flags are generated columns. Nothing to do.")
            return RC_OK
        if not args.apply:
            print("\nDRY RUN: nothing changed. --apply drops the stored fee_known_flag and adds both flags as generated columns, in ONE transaction.")
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
            conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            conn.execute(statement)
            result = verify(conn)
            if result["null_entry"] or result["null_exit"]:
                raise RuntimeError(f"verification failed inside the transaction: NULL flags {result['null_entry']} / {result['null_exit']}")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        print(f"\nAPPLIED: {state(conn)}; funds {result['funds']}, NULL flags 0 / 0.")
        print(f"  entry: {result['entry']}")
        print(f"  exit : {result['exit']}")
        print("Next: the post-migration commit (db/pg/20_silver.sql, shared/schema_checks.py V24, bi_compat.sql, rename_map.yaml), then the one receipt.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
