#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_audit_incident.py -- create control.audit_incident (FND-0264), the closed-loop remediation record of CRITICAL audit findings.

    python scripts/ops/migrate_audit_incident.py            # DRY RUN: read-only, reports what exists
    python scripts/ops/migrate_audit_incident.py --apply    # runs the block in ONE transaction

The DDL is the block between `-- BEGIN audit_incident` and `-- END audit_incident` in db/pg/35_control.sql (single source of truth, P#11):
the table, its state / certification CHECKs and the partial unique index "one live incident per finding key". Idempotent.

Nothing changes behaviour by itself: scripts/audit/audit_incident.py --plan and --close-check only read the table, and incidents are
written only with shared.config.AUDIT_INCIDENT_LOOP_ENABLED on. ROLLBACK (before the switch): DROP TABLE control.audit_incident.
Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role). Never prints the DSN.
Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK = "audit_incident"
TABLE = "control.audit_incident"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def existing(conn) -> dict:
    table = conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] is not None
    index = conn.execute("SELECT to_regclass('control.uq_audit_incident_live')").fetchone()[0] is not None
    return {"table": table, "live_index": index}


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
        print(f"{TABLE}: {'present' if have['table'] else 'absent'}; unique live-incident index: {'present' if have['live_index'] else 'absent'}")
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
        print(f"\nAPPLIED: table {'present' if after['table'] else 'ABSENT'}, live index {'present' if after['live_index'] else 'ABSENT'}. "
              "Incidents are written only with AUDIT_INCIDENT_LOOP_ENABLED on.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
