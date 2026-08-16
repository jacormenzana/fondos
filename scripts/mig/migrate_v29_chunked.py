# scripts/mig/migrate_v29_chunked.py
# -*- coding: utf-8 -*-
"""
Migración v29 — Chunked INSERT+DELETE approach.

Renames roll_vol_ann→vol_ann, roll_max_dd→max_dd, roll_return_ann→return_ann
in fund_metric_timeseries, one metric at a time, in ISIN-batched commits.

Why this works on 8 GB RAM + HDD + 11 GB DB:
  Every previous approach created a 1–2 GB WAL in a single transaction, which
  then required writing that back to the DB via HDD — saturating I/O and causing
  swapping.  This script keeps each commit to ~50 ISINs (~22 MB WAL), which
  checkpoints in a few seconds before the next batch starts.

  No ORDER BY → no sort buffer → peak memory ≈ 50 MB (one batch in RAM).
  Progress is printed per batch and is resumable: rerun on partial migration is
  safe because INSERT OR IGNORE skips already-renamed rows.

Usage:
  python scripts/mig/migrate_v29_chunked.py [--db path] [--dry-run] [--batch-size N]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "db" / "fondos.sqlite"

_RENAMES = [
    ("roll_vol_ann",    "vol_ann"),
    ("roll_max_dd",     "max_dd"),
    ("roll_return_ann", "return_ann"),
]

_DEFAULT_BATCH = 50   # ISINs per commit; ~22 MB WAL at typical row density


def _count(conn, metric):
    return conn.execute(
        "SELECT COUNT(*) FROM fund_metric_timeseries WHERE metric=?", (metric,)
    ).fetchone()[0]


def _get_isins(conn, metric):
    rows = conn.execute(
        "SELECT DISTINCT isin FROM fund_metric_timeseries WHERE metric=? ORDER BY isin",
        (metric,)
    ).fetchall()
    return [r[0] for r in rows]


def _batch(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def run(db_path: Path, dry_run: bool, batch_size: int) -> None:
    print(f"DB       : {db_path}")
    print(f"Mode     : {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Batch    : {batch_size} ISINs/commit\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")   # 64 MB page cache
    conn.execute("PRAGMA temp_store=FILE")

    # Truncate the stale WAL before any work.  Prior aborted migrations left a
    # 2.3 GB WAL containing uncommitted frames.  Without this, every subsequent
    # connection scans the full WAL to rebuild the SHM hash table (HDD at 100%
    # for 30–120 s per connection).  This checkpoint commits any pending frames,
    # then shrinks the WAL file to near zero.
    print("=== WAL checkpoint (flush + truncate stale WAL) ===")
    t0 = time.time()
    try:
        busy, log_frames, ckpt_frames = conn.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        ).fetchone()
        print(f"  busy={busy}  log={log_frames}  checkpointed={ckpt_frames}  {time.time()-t0:.1f}s")
        if busy != 0:
            print("  WARNING: checkpoint busy — another reader may be open.")
    except Exception as e:
        print(f"  WARNING: checkpoint failed ({e}) — continuing anyway")

    # ── Preflight ──────────────────────────────────────────────────────────
    print("=== Preflight ===")
    any_work = False
    for old, new in _RENAMES:
        n_old = _count(conn, old)
        n_new = _count(conn, new)
        status = "DONE" if n_old == 0 else "TODO"
        print(f"  [{status}] '{old}': {n_old:>10,}  →  '{new}': {n_new:>10,}")
        if n_old > 0:
            any_work = True

    fm_rolling = conn.execute(
        "SELECT COUNT(*) FROM fund_metrics WHERE horizon LIKE 'rolling_%'"
    ).fetchone()[0]
    print(f"  fund_metrics rolling rows to migrate: {fm_rolling:,}")

    if not any_work and fm_rolling == 0:
        print("\nNothing to do — already fully migrated.")
        conn.close()
        return

    if dry_run:
        print("\n[DRY RUN] No changes. Re-run without --dry-run to migrate.")
        conn.close()
        return

    # ── Cleanup: drop stale _new table from any previous aborted rebuild ───
    stale = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='fund_metric_timeseries_new'"
    ).fetchone()[0]
    if stale:
        n_stale = conn.execute(
            "SELECT COUNT(*) FROM fund_metric_timeseries_new"
        ).fetchone()[0]
        print(f"\n  Dropping stale fund_metric_timeseries_new ({n_stale:,} rows)...")
        conn.execute("DROP TABLE fund_metric_timeseries_new")
        print("  Dropped.")

    # ── Per-metric chunked rename ──────────────────────────────────────────
    grand_start = time.time()

    for old, new in _RENAMES:
        n_old = _count(conn, old)
        if n_old == 0:
            print(f"\n  '{old}' already done — skipping.")
            continue

        print(f"\n=== Renaming '{old}' → '{new}' ({n_old:,} rows) ===")
        isins = _get_isins(conn, old)
        total_isins = len(isins)
        batches = list(_batch(isins, batch_size))
        n_batches = len(batches)
        print(f"  {total_isins:,} ISINs → {n_batches} batches of ≤{batch_size}")

        rows_done = 0
        metric_start = time.time()

        for bi, isin_batch in enumerate(batches, 1):
            placeholders = ",".join("?" * len(isin_batch))
            t0 = time.time()

            conn.execute("BEGIN IMMEDIATE")

            # 1. INSERT new-named rows for this ISIN batch
            conn.execute(f"""
                INSERT OR IGNORE INTO fund_metric_timeseries
                    (isin, metric, window, date, value, real_flag,
                     ref_type, ref_value, source_rows)
                SELECT isin, ?, window, date, value, real_flag,
                       ref_type, ref_value, source_rows
                FROM fund_metric_timeseries
                WHERE metric = ?
                  AND isin IN ({placeholders})
            """, [new, old] + list(isin_batch))
            inserted = conn.execute("SELECT changes()").fetchone()[0]

            # 2. DELETE old-named rows for this ISIN batch
            conn.execute(f"""
                DELETE FROM fund_metric_timeseries
                WHERE metric = ?
                  AND isin IN ({placeholders})
            """, [old] + list(isin_batch))
            deleted = conn.execute("SELECT changes()").fetchone()[0]

            conn.execute("COMMIT")

            rows_done += deleted
            elapsed = time.time() - t0
            pct = rows_done / n_old * 100
            eta = (time.time() - metric_start) / rows_done * (n_old - rows_done) if rows_done else 0
            print(f"  batch {bi:>4}/{n_batches}  +{inserted:>7,} ins  -{deleted:>7,} del"
                  f"  {elapsed:>5.1f}s  {pct:>5.1f}%  ETA {eta/60:.1f}min")

        remaining = _count(conn, old)
        print(f"  Done. '{old}' remaining: {remaining}")

    # ── fund_metrics rolling rows ──────────────────────────────────────────
    fm_rolling = conn.execute(
        "SELECT COUNT(*) FROM fund_metrics WHERE horizon LIKE 'rolling_%'"
    ).fetchone()[0]

    if fm_rolling > 0:
        print(f"\n=== Migrating {fm_rolling:,} fund_metrics rolling rows ===")
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")
        for old, new in _RENAMES:
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
        print(f"  Done in {time.time()-t0:.1f}s")
    else:
        print("\n=== No fund_metrics rolling rows to migrate ===")

    # ── ANALYZE ────────────────────────────────────────────────────────────
    print("\n=== ANALYZE (updates query planner stats) ===")
    t0 = time.time()
    conn.execute("ANALYZE fund_metric_timeseries")
    conn.execute("ANALYZE fund_metrics")
    print(f"  Done in {time.time()-t0:.1f}s")

    # ── Final verification ─────────────────────────────────────────────────
    print("\n=== Post-migration counts ===")
    for old, new in _RENAMES:
        n_old = _count(conn, old)
        n_new = _count(conn, new)
        ok = "OK" if n_old == 0 else "WARN — old rows remain!"
        print(f"  [{ok}] '{new}': {n_new:,}  ('{old}': {n_old:,})")

    conn.close()
    print(f"\nMigration complete in {(time.time()-grand_start)/60:.1f} min.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v29 chunked rename migration")
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=_DEFAULT_BATCH)
    args = parser.parse_args()
    run(Path(args.db), args.dry_run, args.batch_size)
