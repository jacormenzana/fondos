#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_backlog_links.py -- create the action-point relationship store in the `gestion` database (doc/reglas/NORMAS_BACKLOG.md).

    python scripts/ops/migrate_backlog_links.py            # DRY RUN: read-only, reports what exists, no DDL
    python scripts/ops/migrate_backlog_links.py --apply    # runs the block in ONE transaction (all or nothing)

The DDL is NOT duplicated here: it is the block between `-- BEGIN backlog_links` and `-- END backlog_links` in
db/pg_backlog/20_links.sql (single source of truth, P#11). It is idempotent (CREATE ... IF NOT EXISTS, CREATE OR REPLACE VIEW,
guarded grants), so re-running only completes what is missing.

Target: the owner role on the `gestion` database = FONDOS_PG_DSN_OWNER with its dbname replaced by `gestion` (AGENTS.md: "swap
dbname to gestion for the backlog"); the pipeline never reads these objects. Never prints the DSN (only host/port/dbname/user).
ROLLBACK: DROP VIEW backlog.v_open_link_graph, backlog.v_backlog_link; DROP TABLE backlog.backlog_link_event; nothing else
references them.

Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL_FILE = ROOT / "db" / "pg_backlog" / "20_links.sql"
TABLE = "backlog_link_event"
VIEWS = ("v_backlog_link", "v_open_link_graph")
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2


def ddl_block(path: Path = DDL_FILE, name: str = "backlog_links") -> str:
    """The DDL block exactly as written in db/pg_backlog/20_links.sql, markers included."""
    m = re.search(rf"-- BEGIN {re.escape(name)}.*?-- END {re.escape(name)}[^\n]*", path.read_text(encoding="utf-8"), re.S)
    if not m:
        raise RuntimeError(f"`-- BEGIN {name}` ... `-- END {name}` block not found in {path}")
    return m.group(0)


def gestion_dsn(owner_dsn: str) -> str:
    """The owner DSN pointed at the `gestion` database (same server, same role)."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    d = conninfo_to_dict(owner_dsn)
    d["dbname"] = "gestion"
    return make_conninfo(**d)


def existing(conn) -> dict:
    """{'table': bool, 'views': [present view names]}"""
    rows = conn.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'backlog' "
                        "AND (table_name = %s OR table_name = ANY(%s))", (TABLE, list(VIEWS))).fetchall()
    return {"table": any(r[0] == TABLE and r[1] == "BASE TABLE" for r in rows),
            "views": sorted(r[0] for r in rows if r[1] == "VIEW")}


def migrate(conn, lock_timeout: str = LOCK_TIMEOUT) -> None:
    """The whole block in ONE transaction. Raises on any failure (the caller rolls back)."""
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(ddl_block())


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute the migration (default: dry run)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from shared.env_guard import require_db_driver     # RC 106 up front, not a traceback mid-run
    require_db_driver()
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    owner = os.environ.get("FONDOS_PG_DSN_OWNER")
    if not owner:
        print("FONDOS_PG_DSN_OWNER is not set: DDL needs the owner role. Owner strings are deliberately NOT kept in .env. Store it ONCE in the owner vault: `python scripts/ops/owner_vault.py set FONDOS_PG_DSN_OWNER` (hidden input); after that this tool finds it by itself whenever you run it at a real console (or, for one session, `set \"FONDOS_PG_DSN_OWNER=...\"` in cmd).", file=sys.stderr)
        return RC_USAGE
    dsn = gestion_dsn(owner)
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
        print(f"table {TABLE}: {'present' if have['table'] else 'absent'}; views present: {have['views']} of {list(VIEWS)}")
        if not args.apply:
            print("\nDRY RUN: nothing changed. DDL that --apply would run is the block in db/pg_backlog/20_links.sql "
                  f"({len(ddl_block().splitlines())} lines).")
            return RC_OK
        try:
            migrate(conn)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        after = existing(conn)
        conn.rollback()
        print(f"\nAPPLIED: table {TABLE}: {'present' if after['table'] else 'ABSENT'}; views: {after['views']}.")
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
