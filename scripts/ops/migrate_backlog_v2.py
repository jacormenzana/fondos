#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_backlog_v2.py -- apply (or roll back) ONE phase of the Backlog v2 store in the `gestion` database
(doc/especificacion/PROPUESTA_BACKLOG_V2.md; owner action O-2).

    python scripts/ops/migrate_backlog_v2.py --phase alias                    # DRY RUN: read-only, reports state, no DDL
    python scripts/ops/migrate_backlog_v2.py --phase alias --apply            # the phase's block in ONE transaction
    python scripts/ops/migrate_backlog_v2.py --phase alias --down              # DRY RUN of the staged rollback
    python scripts/ops/migrate_backlog_v2.py --phase alias --down --apply      # rollback in ONE transaction

One phase per transaction (Postgres DDL is transactional: a failure rolls back whole). The DDL is NOT duplicated here: it is the block
between `-- BEGIN <name>` and `-- END <name>` in the phase's file under db/pg_backlog/ (single source of truth, P#11); the rollback is
the block of its file under db/pg_backlog/down/. Every phase is idempotent. Target, DSN handling and exit codes are those of
migrate_backlog_links.py (owner role of FONDOS_PG_DSN_OWNER pointed at `gestion`; the DSN is never printed).
After an apply or a rollback, record it in the phase's backlog ticket (who applied, when, the printed state): the script persists
nothing itself, the ticket log is the evidence.

Exit codes: 0 ok (or dry run), 1 apply failed (rolled back), 2 usage or connection error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate_backlog_links as base  # noqa: E402  (ddl_block, gestion_dsn, LOCK_TIMEOUT, RC_*)

DDL_DIR = ROOT / "db" / "pg_backlog"
RC_OK, RC_FAILED, RC_USAGE = base.RC_OK, base.RC_FAILED, base.RC_USAGE

# phase -> files and block names, the objects it creates, and the tables it needs first
PHASES: dict = {
    "alias": {
        "up": ("25_actor_alias.sql", "backlog_actor_alias"),
        "down": ("down/25_actor_alias_down.sql", "backlog_actor_alias_down"),
        "tables": ("actor_alias",),
        "views": ("v_backlog_log_actor", "v_actor_unmapped"),
        "requires": ("backlog", "backlog_logs", "backlog_link_event"),
    },
}


def ddl_block(phase: str, down: bool = False) -> str:
    """The DDL (or rollback) block of `phase` exactly as written in its file, markers included."""
    fname, name = PHASES[phase]["down" if down else "up"]
    return base.ddl_block(DDL_DIR / fname, name)


_SEED_ROW = re.compile(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(?:NULL|'[^']*')\s*\)")


def seed_aliases(phase: str = "alias") -> dict:
    """{raw_name: canonical} rows the phase's DDL block seeds (the acceptance check compares the database with exactly this)."""
    return {m.group(1): m.group(2) for m in _SEED_ROW.finditer(ddl_block(phase))}


def existing(conn, phase: str) -> dict:
    """{'tables': [present], 'views': [present], 'missing_requires': [...]} for `phase` (read-only)."""
    spec = PHASES[phase]
    names = list(spec["tables"]) + list(spec["views"]) + list(spec["requires"])
    rows = conn.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'backlog' "
                        "AND table_name = ANY(%s)", ([n.lower() for n in names],)).fetchall()
    have = {r[0].lower() for r in rows}
    return {
        "tables": sorted(t for t in spec["tables"] if t in have),
        "views": sorted(v for v in spec["views"] if v in have),
        "missing_requires": [r for r in spec["requires"] if r not in have],
    }


def migrate(conn, phase: str, down: bool = False, lock_timeout: str = base.LOCK_TIMEOUT) -> None:
    """The phase's block (or its rollback) in ONE transaction. Raises on any failure (the caller rolls back)."""
    if not down:
        missing = existing(conn, phase)["missing_requires"]
        if missing:
            raise RuntimeError(f"phase {phase!r} needs backlog.{', backlog.'.join(missing)} first (apply 00_schema.sql / 20_links.sql)")
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(ddl_block(phase, down))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", required=True, choices=sorted(PHASES))
    ap.add_argument("--down", action="store_true", help="the staged rollback of the phase instead of the phase")
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
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
        print("FONDOS_PG_DSN_OWNER is not set (it is in .env); DDL needs the owner role.", file=sys.stderr)
        return RC_USAGE
    dsn = base.gestion_dsn(owner)
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")
    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:            # never echo the DSN
        print(f"connection failed: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    what = f"phase {args.phase} {'ROLLBACK' if args.down else 'DDL'}"
    try:
        have = existing(conn, args.phase)
        conn.rollback()
        print(f"{what}: tables present {have['tables']} of {list(PHASES[args.phase]['tables'])}; "
              f"views present {have['views']} of {list(PHASES[args.phase]['views'])}; "
              f"missing prerequisites {have['missing_requires']}")
        if not args.apply:
            print(f"\nDRY RUN: nothing changed. --apply would run the {len(ddl_block(args.phase, args.down).splitlines())}-line block "
                  f"of {PHASES[args.phase]['down' if args.down else 'up'][0]} in one transaction.")
            return RC_OK
        try:
            migrate(conn, args.phase, args.down)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        after = existing(conn, args.phase)
        conn.rollback()
        print(f"\nAPPLIED ({what}): tables {after['tables']}; views {after['views']}. Record it in the phase ticket log.")
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
