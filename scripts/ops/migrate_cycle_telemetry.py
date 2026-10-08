#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_cycle_telemetry.py -- create the cycle-telemetry tables, seeds and the executive view control.v_cycle_exec (FND-0239).

    python scripts/ops/migrate_cycle_telemetry.py            # DRY RUN: read-only, reports what exists, no DDL
    python scripts/ops/migrate_cycle_telemetry.py --apply    # runs the block in ONE transaction (all or nothing)

The DDL is NOT duplicated here: it is the block between `-- BEGIN cycle_telemetry` and `-- END cycle_telemetry` in db/pg/35_control.sql
(single source of truth, P#11). It is idempotent (CREATE ... IF NOT EXISTS, CREATE OR REPLACE VIEW, seeds ON CONFLICT DO NOTHING), so re-running
only completes what is missing and never touches rows already tuned with UPDATEs.

Nothing in the pipeline depends on these tables: shared/cycle_telemetry.py is opt-in (FONDOS_CYCLE_ID) and failure-isolated, so applying this
migration changes no behaviour by itself. ROLLBACK: DROP VIEW control.v_cycle_exec; DROP TABLE control.cycle_flag_ack, cycle_flag,
cycle_audit_run, cycle_metric, cycle_metric_def, cycle_step, cycle_step_def, cycle_run (in that order); nothing else references them.

Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role; fondos_app has DML only by design, FND-0072); privileges for fondos_app / fondos_ro /
superset_ro come from the schema's default privileges. Never prints the DSN.

Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DDL_FILE = ROOT / "db" / "pg" / "35_control.sql"
TABLES = ("cycle_run", "cycle_step_def", "cycle_step", "cycle_metric_def", "cycle_metric", "cycle_audit_run", "cycle_flag", "cycle_flag_ack")
VIEW = "v_cycle_exec"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def ddl_block(path: Path = DDL_FILE, name: str = "cycle_telemetry") -> str:
    """A DDL block exactly as written in db/pg/35_control.sql, markers included (default: the telemetry block;
    migrate_audit_finding_p3_gate.py reuses this with name='p3_gate_bypass', P#11)."""
    m = re.search(rf"-- BEGIN {re.escape(name)}.*?-- END {re.escape(name)}[^\n]*", path.read_text(encoding="utf-8"), re.S)
    if not m:
        raise RuntimeError(f"`-- BEGIN {name}` ... `-- END {name}` block not found in {path}")
    return m.group(0)


def existing(conn) -> dict:
    """{'tables': [present table names], 'view': bool}"""
    rows = conn.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'control' "
                        "AND (table_name = ANY(%s) OR table_name = %s)", (list(TABLES), VIEW)).fetchall()
    return {"tables": sorted(r[0] for r in rows if r[1] == "BASE TABLE"), "view": any(r[0] == VIEW for r in rows)}


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
        print(f"tables present: {len(have['tables'])}/{len(TABLES)} {have['tables']}; view {VIEW}: {'present' if have['view'] else 'absent'}")
        if not args.apply:
            print("\nDRY RUN: nothing changed. DDL that --apply would run is the block in db/pg/35_control.sql "
                  f"({len(ddl_block().splitlines())} lines).")
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
        print(f"\nAPPLIED: tables {len(after['tables'])}/{len(TABLES)}, view {VIEW}: {'present' if after['view'] else 'ABSENT'}. "
              "Nothing in the pipeline reads them until a launcher exports FONDOS_CYCLE_ID.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
