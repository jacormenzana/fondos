# scripts/mig/migrate_v29_rebuild_rename.py
# -*- coding: utf-8 -*-
"""
Migración v29 — Table-rebuild approach (faster than UPDATE on PK column).

Renames roll_vol_ann → vol_ann, roll_max_dd → max_dd,
         roll_return_ann → return_ann in fund_metric_timeseries.
Also migrates 866K rolling rows from fund_metrics → fund_metric_timeseries.

Why faster than UPDATE:
  Updating a PK column forces SQLite to DELETE + re-INSERT every row in the
  B-tree at a new position (random I/O, O(n log n)).  This approach:
    1. Creates a new table.
    2. Inserts all rows in PK order (ORDER BY) → sequential B-tree build.
    3. Drops the old table and renames the new one.
    4. Recreates the two secondary indexes.
  Net result: one sequential read + one sequential write vs. 18.7M random
  B-tree page rewrites.  ~3–5× faster on HDD.

Usage:
  python scripts/mig/migrate_v29_rebuild_rename.py [--db path] [--dry-run]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "db" / "fondos.sqlite"

_RENAMES = {
    "roll_vol_ann":    "vol_ann",
    "roll_max_dd":     "max_dd",
    "roll_return_ann": "return_ann",
}

# Secondary indexes to recreate on the rebuilt table (PK is auto from DDL)
_INDEXES = [
    ("idx_fmts_isin_metric_window_real_date",
     "CREATE INDEX idx_fmts_isin_metric_window_real_date "
     "ON fund_metric_timeseries (isin, metric, window, real_flag, date)"),
    ("idx_fmts_mwr_isin_date",
     "CREATE INDEX idx_fmts_mwr_isin_date "
     "ON fund_metric_timeseries (metric, window, real_flag, isin, date)"),
]

_NEW_TABLE_DDL = """
CREATE TABLE fund_metric_timeseries_new (
    isin        TEXT    NOT NULL,
    metric      TEXT    NOT NULL,
    window      TEXT    NOT NULL,
    date        TEXT    NOT NULL,
    value       REAL,
    real_flag   INTEGER NOT NULL DEFAULT 0,
    ref_type    TEXT,
    ref_value   REAL,
    source_rows INTEGER,
    PRIMARY KEY (isin, metric, window, date, real_flag)
)
"""

_INSERT_SQL = """
INSERT INTO fund_metric_timeseries_new
    (isin, metric, window, date, value, real_flag, ref_type, ref_value, source_rows)
SELECT
    isin,
    CASE metric
        WHEN 'roll_vol_ann'    THEN 'vol_ann'
        WHEN 'roll_max_dd'     THEN 'max_dd'
        WHEN 'roll_return_ann' THEN 'return_ann'
        ELSE metric
    END AS metric,
    window, date, value, real_flag, ref_type, ref_value, source_rows
FROM fund_metric_timeseries
ORDER BY
    isin,
    CASE metric
        WHEN 'roll_vol_ann'    THEN 'vol_ann'
        WHEN 'roll_max_dd'     THEN 'max_dd'
        WHEN 'roll_return_ann' THEN 'return_ann'
        ELSE metric
    END,
    window, date, real_flag
"""

_FM_RENAMES = {
    "roll_vol_ann":    "vol_ann",
    "roll_max_dd":     "max_dd",
    "roll_return_ann": "return_ann",
}


def _count(conn, table, where):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def run(db_path: Path, dry_run: bool) -> None:
    print(f"DB : {db_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-131072")   # 128 MB page cache
    conn.execute("PRAGMA temp_store=MEMORY")

    # ── Preflight ──────────────────────────────────────────────────────────
    print("=== Preflight ===")
    total_old = 0
    for old in _RENAMES:
        n = _count(conn, "fund_metric_timeseries", f"metric = '{old}'")
        print(f"  roll_ rows '{old}': {n:,}")
        total_old += n
    total_src = _count(conn, "fund_metric_timeseries", "1=1")
    fm_rolling = _count(conn, "fund_metrics", "horizon LIKE 'rolling_%'")
    print(f"  Total rows in fund_metric_timeseries: {total_src:,}")
    print(f"  fund_metrics rolling rows to migrate: {fm_rolling:,}")

    if total_old == 0 and fm_rolling == 0:
        print("\nNothing to do — already fully migrated.")
        conn.close()
        return

    if dry_run:
        print("\n[DRY RUN] No changes. Re-run without --dry-run to migrate.")
        conn.close()
        return

    # ── WAL checkpoint: flush stale uncommitted WAL frames before bulk read ─
    # The aborted UPDATE migration left a 1069MB uncommitted WAL. Without
    # checkpointing, every source-table read scans past all those WAL frames
    # (O(WAL_pages) per row lookup → ~8min for a single COUNT(*)).
    print("\n=== WAL checkpoint (flush stale frames) ===")
    t0 = time.time()
    busy, log, ckpt = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    print(f"  busy={busy}, log_frames={log}, checkpointed={ckpt} — {time.time()-t0:.1f}s")
    if busy != 0:
        print("  WARNING: checkpoint busy — another connection may be open; continuing anyway")

    # ── Step 1: Drop _new table if it exists from a previous aborted run ──
    conn.execute("DROP TABLE IF EXISTS fund_metric_timeseries_new")

    # ── Step 2: Create new table ───────────────────────────────────────────
    print("\n=== Step 1/5: Create new table ===")
    conn.execute(_NEW_TABLE_DDL)
    print("  fund_metric_timeseries_new created.")

    # ── Step 3: Insert all rows in PK order (sequential B-tree build) ─────
    print("\n=== Step 2/5: INSERT all rows in PK order ===")
    print("  (Inserting in sorted order for sequential B-tree build...)")
    t0 = time.time()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(_INSERT_SQL)
    conn.execute("COMMIT")
    elapsed = time.time() - t0
    new_count = _count(conn, "fund_metric_timeseries_new", "1=1")
    print(f"  Inserted {new_count:,} rows in {elapsed:.1f}s")
    if new_count != total_src:
        print(f"  ERROR: row count mismatch ({new_count} vs {total_src}). Aborting.",
              file=sys.stderr)
        conn.execute("DROP TABLE fund_metric_timeseries_new")
        conn.close()
        sys.exit(1)

    # ── Step 4: Swap tables ────────────────────────────────────────────────
    print("\n=== Step 3/5: Swap tables ===")
    t0 = time.time()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DROP TABLE fund_metric_timeseries")
    conn.execute("ALTER TABLE fund_metric_timeseries_new RENAME TO fund_metric_timeseries")
    conn.execute("COMMIT")
    print(f"  Swap done in {time.time()-t0:.1f}s")

    # ── Step 5: Recreate secondary indexes ────────────────────────────────
    print("\n=== Step 4/5: Recreate secondary indexes ===")
    for idx_name, idx_ddl in _INDEXES:
        print(f"  Creating {idx_name} ...")
        t0 = time.time()
        conn.execute(idx_ddl)
        print(f"    done in {time.time()-t0:.1f}s")

    # ── Step 6: Migrate fund_metrics rolling rows ─────────────────────────
    if fm_rolling > 0:
        print(f"\n=== Step 5/5: Migrate {fm_rolling:,} fund_metrics rolling rows ===")
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")
        for old, new in _FM_RENAMES.items():
            conn.execute("""
                INSERT OR IGNORE INTO fund_metric_timeseries
                    (isin, metric, window, date, value, real_flag,
                     ref_type, ref_value, source_rows)
                SELECT isin, ?, horizon, calculation_date,
                       value, real_flag, NULL, NULL, source_rows
                FROM fund_metrics
                WHERE metric = ? AND horizon LIKE 'rolling_%'
            """, (new, old))
        conn.execute("""
            DELETE FROM fund_metrics
            WHERE metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')
              AND horizon LIKE 'rolling_%'
        """)
        conn.execute("COMMIT")
        remaining = _count(conn, "fund_metrics", "horizon LIKE 'rolling_%'")
        print(f"  Migrated in {time.time()-t0:.1f}s | remaining rolling rows: {remaining}")
    else:
        print("\n=== Step 5/5: No fund_metrics rolling rows to migrate ===")

    # ── ANALYZE ────────────────────────────────────────────────────────────
    print("\n=== ANALYZE ===")
    print("  (Updates query planner statistics — may take several minutes)")
    t0 = time.time()
    conn.execute("ANALYZE fund_metric_timeseries")
    conn.execute("ANALYZE fund_metrics")
    print(f"  ANALYZE done in {time.time()-t0:.1f}s")

    # ── Final counts ───────────────────────────────────────────────────────
    print("\n=== Post-migration counts ===")
    for new in _RENAMES.values():
        n = _count(conn, "fund_metric_timeseries", f"metric = '{new}'")
        print(f"  '{new}': {n:,}")
    for old in _RENAMES:
        n = _count(conn, "fund_metric_timeseries", f"metric = '{old}'")
        if n:
            print(f"  WARNING: old name '{old}' still has {n:,} rows!")

    conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v29 table-rebuild rename migration")
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(Path(args.db), args.dry_run)
