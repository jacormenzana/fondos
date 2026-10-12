#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_audit_governance.py -- governed exceptions + run manifest (FND-0263, GOV-04).

    python scripts/ops/migrate_audit_governance.py            # DRY RUN: read-only, reports what exists and what the backfill would write
    python scripts/ops/migrate_audit_governance.py --apply    # ONE transaction: DDL block + backfill + consistency check (all or nothing)

1. Runs the block between `-- BEGIN audit_governance` and `-- END audit_governance` in db/pg/35_control.sql (single source of truth,
   P#11): control.audit_exception_event (append-only, trigger-enforced) and control.audit_run.manifest. Idempotent.
2. Backfills the CURRENT control.audit_accepted_finding rows as history: one GRANT + one APPROVE per decision (same domain, rule, AP,
   reason, accepted_by, accepted_at, expiry), dated accepted_at, actor accepted_by, reason prefixed "[backfill FND-0263]". Only for
   (domain, rule) pairs that have no event yet, so a re-run writes nothing.
3. Checks that the table equals the folded history for every domain (accepted_residuals.verify_governed); any drift rolls back.

Nothing changes behaviour by itself: accept_audit_residual.py starts writing events once the table exists, and the promotion gate only
fails closed on the history with shared.config.AUDIT_EXCEPTION_EVENTS_ENABLED. ROLLBACK (before the switch): DROP TABLE
control.audit_exception_event; DROP FUNCTION control.audit_exception_event_append_only(); the manifest column may stay.

Runs with FONDOS_PG_DSN_OWNER (DDL needs the owner role). Never prints the DSN.
Exit codes: 0 ok (or dry run), 1 apply failed, 2 usage or connection error, 3 refused (a cycle appears to be running).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCK = "audit_governance"
BACKFILL_TAG = "[backfill FND-0263] "
LOCK_TIMEOUT = "5s"
RC_OK, RC_FAILED, RC_USAGE, RC_REFUSED = 0, 1, 2, 3


def existing(conn) -> dict:
    events = conn.execute("SELECT to_regclass('control.audit_exception_event')").fetchone()[0] is not None
    manifest = conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass('control.audit_run') AND attname = 'manifest' "
                            "AND NOT attisdropped").fetchone() is not None
    return {"events_table": events, "manifest_column": manifest}


def backfill_plan(conn, events_table: bool) -> list:
    """[(domain, rule_id, ap_id, reason, accepted_by, accepted_at, expires_at, [isins])]: the decisions to backfill (pairs with no event)."""
    rows = conn.execute(
        "SELECT domain, rule_id, ap_id, reason, accepted_by, accepted_at, expires_at, array_agg(isin ORDER BY isin) "
        "FROM control.audit_accepted_finding GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 1, 2, 6").fetchall()
    if not events_table:
        return [tuple(r) for r in rows]
    done = {tuple(r) for r in conn.execute("SELECT DISTINCT domain, rule_id FROM control.audit_exception_event").fetchall()}
    return [tuple(r) for r in rows if (r[0], r[1]) not in done]


def apply_backfill(conn, plan: list) -> int:
    """GRANT + APPROVE per decision. No commit. Returns the number of events written."""
    n = 0
    for domain, rule_id, ap_id, reason, accepted_by, accepted_at, expires_at, isins in plan:
        actor = accepted_by or "unknown"
        (grant_id,) = conn.execute(
            "INSERT INTO control.audit_exception_event (event_ts, event_type, domain, rule_id, isins, ap_id, reason, expires_at, actor) "
            "VALUES (%s::date::timestamptz, 'GRANT', %s, %s, %s, %s, %s, %s, %s) RETURNING event_id",
            (accepted_at, domain, rule_id, list(isins), ap_id, BACKFILL_TAG + (reason or ""), expires_at, actor)).fetchone()
        conn.execute(
            "INSERT INTO control.audit_exception_event (event_ts, event_type, domain, rule_id, ref_event_id, actor) "
            "VALUES (%s::date::timestamptz, 'APPROVE', %s, %s, %s, %s)", (accepted_at, domain, rule_id, grant_id, actor))
        n += 2
    return n


def verify(conn) -> dict:
    """{domain: drift list} for every domain with residuals or events (empty lists = consistent)."""
    sys.path.insert(0, str(ROOT))
    from shared.statistical_audit.accepted_residuals import verify_governed
    domains = [r[0] for r in conn.execute("SELECT domain FROM control.audit_accepted_finding UNION "
                                          "SELECT domain FROM control.audit_exception_event ORDER BY 1").fetchall()]
    return {d: verify_governed(conn, d)["drift"] for d in domains}


def migrate(conn, lock_timeout: str = LOCK_TIMEOUT) -> tuple:
    """Block + backfill + check in the caller's transaction. Returns (events written, {domain: drift}); raises on drift."""
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    from migrate_cycle_telemetry import ddl_block
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute("SET LOCAL search_path = control, public")      # verify_governed uses unqualified names
    conn.execute(ddl_block(name=BLOCK))
    n = apply_backfill(conn, backfill_plan(conn, events_table=True))
    drift = verify(conn)
    bad = {d: v for d, v in drift.items() if v}
    if bad:
        raise RuntimeError(f"projection != history after backfill: {bad}")
    return n, drift


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
        plan = backfill_plan(conn, have["events_table"])
        conn.rollback()
        print(f"control.audit_exception_event: {'present' if have['events_table'] else 'absent'}; "
              f"control.audit_run.manifest: {'present' if have['manifest_column'] else 'absent'}")
        print(f"backfill: {len(plan)} decision(s), {sum(len(p[7]) for p in plan)} ISIN(s), {2 * len(plan)} event(s)")
        for domain, rule_id, ap_id, _reason, by, at, exp, isins in plan:
            print(f"  {domain} {rule_id} {ap_id}: {len(isins)} ISINs, accepted {at} by {by}, until {exp}")
        if not args.apply:
            print(f"\nDRY RUN: nothing changed. --apply runs the `{BLOCK}` block of db/pg/35_control.sql, the backfill and the check.")
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
            n, drift = migrate(conn)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        print(f"\nAPPLIED: {n} backfill event(s); projection == history for {', '.join(drift) or 'no domain'}. "
              "accept_audit_residual.py now records GRANT / APPROVE / REVOKE events; flip AUDIT_EXCEPTION_EVENTS_ENABLED when ready.")
        return RC_OK


if __name__ == "__main__":
    sys.exit(main())
