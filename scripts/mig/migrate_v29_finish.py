# scripts/mig/migrate_v29_finish.py
# -*- coding: utf-8 -*-
"""
Migración v29 — Completion step only.

fund_metric_timeseries_new already exists with 16.6M renamed rows (the INSERT
committed before the previous process was killed mid-checkpoint).  This script:
  1. Verifies row counts + spot-checks metric names in the new table
  2. DROP TABLE fund_metric_timeseries (old roll_* rows)
  3. ALTER TABLE fund_metric_timeseries_new RENAME TO fund_metric_timeseries
  4. CREATE INDEX x2 (the slow part — 30–60 min each on HDD)
  5. Migrate fund_metrics rolling rows → fund_metric_timeseries
  6. ANALYZE

Usage:
  python scripts/mig/migrate_v29_finish.py [--db path] [--dry-run]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "db" / "fondos.sqlite"

_INDEXES = [
    ("idx_fmts_isin_metric_window_real_date",
     "CREATE INDEX idx_fmts_isin_metric_window_real_date "
     "ON fund_metric_timeseries (isin, metric, window, real_flag, date)"),
    ("idx_fmts_mwr_isin_date",
     "CREATE INDEX idx_fmts_mwr_isin_date "
     "ON fund_metric_timeseries (metric, window, real_flag, isin, date)"),
]

_FM_RENAMES = [
    ("roll_vol_ann",    "vol_ann"),
    ("roll_max_dd",     "max_dd"),
    ("roll_return_ann", "return_ann"),
]


def run(db_path: Path, dry_run: bool) -> None:
    print(f"DB  : {db_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")   # 64 MB — conservative for 8 GB RAM
    conn.execute("PRAGMA temp_store=FILE")

    # ── Preflight: verify new table exists and counts match ────────────────
    print("=== Preflight ===")
    new_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='fund_metric_timeseries_new'"
    ).fetchone()[0]
    if not new_exists:
        print("ERROR: fund_metric_timeseries_new does not exist.", file=sys.stderr)
        print("  Run migrate_v29_chunked.py instead (full INSERT+DELETE path).", file=sys.stderr)
        conn.close()
        sys.exit(1)

    print("  fund_metric_timeseries_new: EXISTS")

    # Count rows in both tables (uses index scans — fast)
    print("  Counting rows in both tables (may take a moment)...")
    t0 = time.time()
    n_old = conn.execute("SELECT COUNT(*) FROM fund_metric_timeseries").fetchone()[0]
    n_new = conn.execute("SELECT COUNT(*) FROM fund_metric_timeseries_new").fetchone()[0]
    print(f"  fund_metric_timeseries     (old, roll_* names): {n_old:>12,}  [{time.time()-t0:.1f}s]")
    print(f"  fund_metric_timeseries_new (new, v29 names)   : {n_new:>12,}")

    if n_new != n_old:
        print(f"  WARNING: row count mismatch ({n_new} vs {n_old}). Continuing with caution.")

    # Spot-check: new table must NOT have roll_* names
    bad = conn.execute(
        "SELECT COUNT(*) FROM fund_metric_timeseries_new "
        "WHERE metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')"
    ).fetchone()[0]
    if bad:
        print(f"  ERROR: {bad:,} rows in _new table still have roll_* names!", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # Spot-check: new table has expected metric names
    sample = conn.execute(
        "SELECT DISTINCT metric FROM fund_metric_timeseries_new "
        "WHERE metric IN ('vol_ann','max_dd','return_ann') LIMIT 3"
    ).fetchall()
    print(f"  Spot-check metric names in _new: {[r[0] for r in sample]}")

    fm_rolling = conn.execute(
        "SELECT COUNT(*) FROM fund_metrics WHERE horizon LIKE 'rolling_%'"
    ).fetchone()[0]
    print(f"  fund_metrics rolling rows to migrate: {fm_rolling:,}")

    if dry_run:
        print("\n[DRY RUN] Preflight passed. Re-run without --dry-run to finish migration.")
        conn.close()
        return

    # ── Step 1: Swap tables ────────────────────────────────────────────────
    print("\n=== Step 1/4: Swap tables ===")
    t0 = time.time()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DROP TABLE fund_metric_timeseries")
    conn.execute(
        "ALTER TABLE fund_metric_timeseries_new RENAME TO fund_metric_timeseries"
    )
    conn.execute("COMMIT")
    print(f"  DROP + RENAME done in {time.time()-t0:.1f}s")

    # ── Step 2: Create indexes ─────────────────────────────────────────────
    print("\n=== Step 2/4: Create indexes ===")
    for idx_name, idx_ddl in _INDEXES:
        print(f"  Building {idx_name} ...")
        print("  (This will take 30–60 min on HDD — HD will be at 100%)")
        t0 = time.time()
        conn.execute(idx_ddl)
        print(f"  Done in {time.time()-t0:.1f}s")

    # ── Step 3: Migrate fund_metrics rolling rows ─────────────────────────
    print(f"\n=== Step 3/4: Migrate {fm_rolling:,} fund_metrics rolling rows ===")
    if fm_rolling > 0:
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")
        for old, new in _FM_RENAMES:
            conn.execute("""
                INSERT OR IGNORE INTO fund_metric_timeseries
                    (isin, metric, window, date, value, real_flag,
                     ref_type, ref_value, source_rows)
                SELECT isin, ?, horizon, calculation_date,
                       value, real_flag, NULL, NULL, source_rows
                FROM fund_metrics
                WHERE metric = ? AND horizon LIKE 'rolling_%'
            """, (new, old))
        # Scope the DELETE to only the 3 renamed metrics — NOT the short-horizon
        # metrics (short_vol_ann, short_max_drawdown, short_vol_adj,
        # short_return_cum_real) which live in fund_metrics at rolling_1m/3m/6m
        # and are single-date snapshots, not time-series (Option A, v30 design).
        conn.execute("""
            DELETE FROM fund_metrics
            WHERE metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')
              AND horizon LIKE 'rolling_%'
        """)
        conn.execute("COMMIT")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM fund_metrics WHERE horizon LIKE 'rolling_%'"
        ).fetchone()[0]
        print(f"  Done in {time.time()-t0:.1f}s  |  remaining rolling rows: {remaining}")
    else:
        print("  Nothing to migrate.")

    # ── Step 4: ANALYZE ────────────────────────────────────────────────────
    print("\n=== Step 4/4: ANALYZE ===")
    t0 = time.time()
    conn.execute("ANALYZE fund_metric_timeseries")
    conn.execute("ANALYZE fund_metrics")
    print(f"  Done in {time.time()-t0:.1f}s")

    # ── Final counts ───────────────────────────────────────────────────────
    print("\n=== Post-migration counts ===")
    for _, new in _FM_RENAMES:
        n = conn.execute(
            "SELECT COUNT(*) FROM fund_metric_timeseries WHERE metric=?", (new,)
        ).fetchone()[0]
        print(f"  '{new}': {n:,}")
    for old, _ in _FM_RENAMES:
        n = conn.execute(
            "SELECT COUNT(*) FROM fund_metric_timeseries WHERE metric=?", (old,)
        ).fetchone()[0]
        if n:
            print(f"  WARNING: old name '{old}' still has {n:,} rows!")

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v29 migration completion (swap + index)")
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(Path(args.db), args.dry_run)
