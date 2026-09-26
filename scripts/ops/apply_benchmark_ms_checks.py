#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
apply_benchmark_ms_checks.py — create control.benchmark_ms_checks on a Postgres database (the negative
cache of proyecto1/src/loaders/benchmark_loader.py) and verify that the pipeline role can use it.

    python scripts/ops/apply_benchmark_ms_checks.py            # DRY RUN: shows the target and the SQL, changes nothing
    python scripts/ops/apply_benchmark_ms_checks.py --apply    # creates the table (idempotent: IF NOT EXISTS)

The DDL is read from db/pg/35_control.sql (the single source of truth), never duplicated here. It runs
with FONDOS_PG_DSN_OWNER (DDL needs table ownership; the pipeline role fondos_app has none by design,
FND-0072). The pipeline role's DML rights come from the ALTER DEFAULT PRIVILEGES in
db/pg/00_roles_schemas.sql; this script does not grant anything, it VERIFIES them and fails loudly if
they are missing, so a missing grant is found now and not in the middle of PASO 1.

Safe to re-run: `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. Without the table the loader
still works, just without the cache (it re-queries the never-mapped ISINs every run).

Exit codes: 0 ok (or dry run), 1 verification failed, 2 usage/connection error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL_FILE = ROOT / "db" / "pg" / "35_control.sql"
TABLE = "control.benchmark_ms_checks"
APP_ROLE = "fondos_app"
_BLOCK = re.compile(
    r"(CREATE TABLE IF NOT EXISTS control\.benchmark_ms_checks\b.*?;\s*"
    r"CREATE INDEX IF NOT EXISTS idx_bms_checks_next\b[^;]*;)", re.S)


def extract_ddl(sql_text: str) -> str:
    """The CREATE TABLE + CREATE INDEX statements of benchmark_ms_checks from 35_control.sql."""
    m = _BLOCK.search(sql_text)
    if not m:
        raise ValueError("benchmark_ms_checks DDL block not found in 35_control.sql")
    return m.group(1)


def check_privileges(conn, role: str = APP_ROLE) -> dict:
    """{privilege: bool} for `role` on the table, as Postgres itself reports it."""
    return {p: bool(conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, TABLE, p)).fetchone()[0])
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")}


def apply(conn, ddl: str) -> None:
    conn.execute(ddl)
    conn.commit()


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually create the table (default: dry run)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ.get("FONDOS_PG_DSN_OWNER")
    if not dsn:
        print("FONDOS_PG_DSN_OWNER is not set (it is in .env); DDL needs the owner role.", file=sys.stderr)
        return 2
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")
    ddl = extract_ddl(DDL_FILE.read_text(encoding="utf-8"))
    print("\n" + ddl + "\n")
    if not args.apply:
        print("DRY RUN: nothing changed. Re-run with --apply to create the table.")
        return 0
    try:
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            existed = conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] is not None
            apply(conn, ddl)
            privs = check_privileges(conn)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"{'already existed (no change)' if existed else 'created'}: {TABLE}")
    print(f"{APP_ROLE} privileges: {privs}")
    if not all(privs.values()):
        missing = [p for p, ok in privs.items() if not ok]
        print(f"VERIFICATION FAILED: {APP_ROLE} lacks {missing} on {TABLE}. Check ALTER DEFAULT PRIVILEGES in "
              "db/pg/00_roles_schemas.sql (or GRANT SELECT, INSERT, UPDATE, DELETE explicitly as the owner).",
              file=sys.stderr)
        return 1
    print("OK: the pipeline role can read and write the negative cache.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
