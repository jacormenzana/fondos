#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_audit_finding_p3_gate.py -- let control.audit_finding record P3 `--allow-stale` overrides and create the reading view (FND-0244 follow-up).

    python scripts/ops/migrate_audit_finding_p3_gate.py            # DRY RUN: read-only, reports what exists, no DDL
    python scripts/ops/migrate_audit_finding_p3_gate.py --apply    # runs the block in ONE transaction (all or nothing)

The DDL is the block between `-- BEGIN p3_gate_bypass` and `-- END p3_gate_bypass` in db/pg/35_control.sql (single source of truth, P#11):
(a) extends the `audit_finding.domain` CHECK with 'p3_gate' (severity stays ALARM; the HIGH flag lives in control.cycle_flag), and
(b) creates control.v_p3_gate_bypass, a UNION of the `P3_STALE_BYPASS` rows of control.ingestion_log and the audit_finding rows of domain
'p3_gate'. The view needs no CHECK change, so it works before (a) is applied too. Idempotent.

Nothing in the pipeline depends on this migration: shared/gate_bypass.py always writes control.ingestion_log and feature-detects the CHECK,
so the audit_finding rows start to appear by themselves once (a) is applied. ROLLBACK: DROP VIEW control.v_p3_gate_bypass; then restore the
two-value CHECK (ALTER TABLE control.audit_finding DROP CONSTRAINT audit_finding_domain_check; ADD CONSTRAINT audit_finding_domain_check
CHECK (domain IN ('p2_metrics','cost_attributes'))) after deleting any domain='p3_gate' rows.

Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role). Never prints the DSN.
Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK = "p3_gate_bypass"
VIEW = "v_p3_gate_bypass"
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def existing(conn) -> dict:
    """{'check': bool (the domain CHECK admits 'p3_gate'), 'view': bool}"""
    chk = conn.execute(
        "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('control.audit_finding') AND contype = 'c' "
        "AND pg_get_constraintdef(oid) ILIKE '%p3_gate%' LIMIT 1").fetchone()
    view = conn.execute("SELECT 1 FROM information_schema.views WHERE table_schema = 'control' AND table_name = %s", (VIEW,)).fetchone()
    return {"check": chk is not None, "view": view is not None}


def migrate(conn, lock_timeout: str = LOCK_TIMEOUT) -> None:
    """The whole block in ONE transaction. Raises on any failure (the caller rolls back)."""
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
        print(f"audit_finding.domain admits 'p3_gate': {have['check']}; view control.{VIEW}: {'present' if have['view'] else 'absent'}")
        if not args.apply:
            print("\nDRY RUN: nothing changed. DDL that --apply would run is the `p3_gate_bypass` block in db/pg/35_control.sql.")
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
        print(f"\nAPPLIED: CHECK admits 'p3_gate': {after['check']}; view {VIEW}: {'present' if after['view'] else 'ABSENT'}. "
              "shared/gate_bypass.py starts writing audit_finding rows by itself.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
