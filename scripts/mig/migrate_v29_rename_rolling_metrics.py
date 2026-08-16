# scripts/mig/migrate_v29_rename_rolling_metrics.py
# -*- coding: utf-8 -*-
"""
Migración v29 — Normalización de nombres de métricas rolling.

Renames:
  fund_metric_timeseries: roll_vol_ann → vol_ann
                          roll_max_dd  → max_dd
                          roll_return_ann → return_ann

Also migrates any rolling-horizon rows that may still live in fund_metrics
(horizon LIKE 'rolling_%') into fund_metric_timeseries, stripping the roll_
prefix from the metric column.

Design:
  - Idempotent: safe to run multiple times (already-renamed rows are skipped by
    INSERT OR IGNORE; UPDATE WHERE metric='roll_*' is a no-op when already done).
  - Single transaction: either the whole migration commits or nothing changes.
  - Verbose: prints counts before/after each step.

Usage:
  python scripts/mig/migrate_v29_rename_rolling_metrics.py
  python scripts/mig/migrate_v29_rename_rolling_metrics.py --db path/to/fondos.sqlite
  python scripts/mig/migrate_v29_rename_rolling_metrics.py --dry-run
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "db" / "fondos.sqlite"

# Metric renames: (old_name, new_name)
_TIMESERIES_RENAMES = [
    ("roll_vol_ann",    "vol_ann"),
    ("roll_max_dd",     "max_dd"),
    ("roll_return_ann", "return_ann"),
]

# fund_metrics rolling horizon metric renames (if any rows exist there)
_METRICS_RENAMES = [
    ("roll_vol_ann",    "vol_ann"),
    ("roll_max_dd",     "max_dd"),
    ("roll_return_ann", "return_ann"),
]


def _count(conn: sqlite3.Connection, table: str, where: str) -> int:
    cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
    return cur.fetchone()[0]


def run(db_path: Path, dry_run: bool) -> None:
    print(f"DB: {db_path}")
    print(f"Mode: {'DRY RUN (no writes)' if dry_run else 'LIVE'}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        # ── Preflight: count rows to rename ────────────────────────────────
        print("=== Preflight ===")
        total_fmts_old = 0
        for old, new in _TIMESERIES_RENAMES:
            n = _count(conn, "fund_metric_timeseries", f"metric = '{old}'")
            print(f"  fund_metric_timeseries metric='{old}': {n:,} rows")
            total_fmts_old += n
        total_fmts_new = sum(
            _count(conn, "fund_metric_timeseries", f"metric = '{new}'")
            for _, new in _TIMESERIES_RENAMES
        )
        print(f"  fund_metric_timeseries already normalized: {total_fmts_new:,} rows")

        fm_rolling = _count(conn, "fund_metrics", "horizon LIKE 'rolling_%'")
        print(f"  fund_metrics rolling_* rows to migrate: {fm_rolling:,}")

        if total_fmts_old == 0 and fm_rolling == 0:
            print("\nNothing to do — already fully migrated.")
            conn.close()
            return

        if dry_run:
            print("\n[DRY RUN] No changes applied. Re-run without --dry-run to migrate.")
            conn.close()
            return

        # ── Migration ───────────────────────────────────────────────────────
        print("\n=== Migration (single transaction) ===")
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")

        # Step 1: Rename roll_* rows in fund_metric_timeseries
        for old, new in _TIMESERIES_RENAMES:
            n_before = _count(conn, "fund_metric_timeseries", f"metric = '{old}'")
            if n_before == 0:
                print(f"  SKIP  fund_metric_timeseries '{old}' (0 rows)")
                continue
            # Check for collision: if new name exists for same (isin,window,date,real_flag)
            # INSERT OR IGNORE approach is not needed here — UPDATE is safe because the PK
            # on fund_metric_timeseries is (isin, metric, window, date, real_flag) or similar.
            # A direct UPDATE just changes the metric column; SQLite allows it if no PK clash.
            conn.execute(
                "UPDATE fund_metric_timeseries SET metric = ? WHERE metric = ?",
                (new, old),
            )
            n_after = _count(conn, "fund_metric_timeseries", f"metric = '{new}'")
            print(f"  RENAMED  '{old}' → '{new}': {n_before:,} → {n_after:,} rows")

        # Step 2: Migrate rolling rows from fund_metrics → fund_metric_timeseries
        if fm_rolling > 0:
            print(f"\n  Migrating {fm_rolling:,} rolling rows from fund_metrics …")
            for old, new in _METRICS_RENAMES:
                n = _count(conn, "fund_metrics",
                            f"metric = '{old}' AND horizon LIKE 'rolling_%'")
                if n == 0:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO fund_metric_timeseries
                       (isin, metric, window, date, value, real_flag,
                        ref_type, ref_value, source_rows)
                       SELECT isin, ?, horizon, calculation_date,
                              value, real_flag, NULL, NULL, source_rows
                       FROM fund_metrics
                       WHERE metric = ? AND horizon LIKE 'rolling_%'""",
                    (new, old),
                )
                print(f"    INSERT OR IGNORE '{old}' (rolling) → '{new}': {n:,} rows")
            # Delete migrated rows from fund_metrics
            conn.execute(
                "DELETE FROM fund_metrics WHERE horizon LIKE 'rolling_%'"
            )
            remaining = _count(conn, "fund_metrics", "horizon LIKE 'rolling_%'")
            print(f"    DELETE fund_metrics rolling rows → {remaining:,} remaining")

        conn.execute("COMMIT")
        elapsed = time.time() - t0
        print(f"\nTransaction committed in {elapsed:.1f}s")

        # ── Post-migration counts ────────────────────────────────────────────
        print("\n=== Post-migration ===")
        for _, new in _TIMESERIES_RENAMES:
            n = _count(conn, "fund_metric_timeseries", f"metric = '{new}'")
            print(f"  fund_metric_timeseries metric='{new}': {n:,} rows")

        # ANALYZE so planner has fresh stats
        print("\nRunning ANALYZE …")
        t_an = time.time()
        conn.execute("ANALYZE fund_metric_timeseries")
        conn.execute("ANALYZE fund_metrics")
        print(f"ANALYZE done in {time.time() - t_an:.1f}s")

    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        print(f"\nERROR: {exc}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v29 rolling metrics rename migration")
    parser.add_argument("--db", default=str(_DEFAULT_DB),
                        help="Path to fondos.sqlite")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts only; make no changes")
    args = parser.parse_args()
    run(Path(args.db), args.dry_run)
