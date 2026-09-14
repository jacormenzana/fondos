# scripts/mig/backfill_pre_v26_lineage.py
# -*- coding: utf-8 -*-
"""
One-shot backfill — stamp pre-v26 audit-column NULLs with the sentinel
'PRE_V26_UNKNOWN' (P#10: sentinel for "indeterminate by nature", never NULL
for "not discovered").

Root cause (see memory project_p2_audit_frozen_nav_deadcode_20260914 /
session 2026-09-14): the v29 table-rebuild migration
(scripts/mig/migrate_v29_rebuild_rename.py, run 2026-08-16) rebuilt
fund_metric_timeseries via CREATE+INSERT+SWAP before algorithm_version/
batch_id existed. Those columns were added five days later
(shared/migrate_schema_v26.py, 2026-08-21) via plain ALTER TABLE ADD COLUMN,
which defaults every pre-existing row to NULL. No backfill UPDATE
accompanied it. Since then, _write_timeseries() only stamps newly-inserted
dates (INSERT OR IGNORE) — it never touches a pre-existing row. Result:
31,541,300 fund_metric_timeseries rows (98% of the table) and 4,121
fund_metrics rows are permanently unstamped unless explicitly backfilled.

This script stamps exactly those rows with a sentinel meaning "known to
predate CALC_VERSION tracking, original version not recoverable" — distinct
from NULL, which after this backfill means "not yet computed by any run."

No index touches either column (verified live) — this is a plain row
rewrite, not a B-tree/index rebuild.

Usage:
  python scripts/mig/backfill_pre_v26_lineage.py [--db path] [--dry-run]
"""

import argparse
import sqlite3
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "db" / "fondos.sqlite"

_SENTINEL = "PRE_V26_UNKNOWN"

_TARGETS = [
    ("fund_metric_timeseries", "algorithm_version IS NULL AND batch_id IS NULL"),
    ("fund_metrics", "algorithm_version IS NULL"),
]


def run(db_path: Path, dry_run: bool) -> None:
    print(f"DB  : {db_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-131072")  # 128 MB page cache

    print("=== Preflight ===")
    counts = {}
    for table, where in _TARGETS:
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        counts[table] = n
        print(f"  {table}: {n:,} rows to stamp ({where})")

    if sum(counts.values()) == 0:
        print("\nNothing to do — already backfilled.")
        conn.close()
        return

    if dry_run:
        print("\n[DRY RUN] No changes. Re-run without --dry-run to backfill.")
        conn.close()
        return

    for table, where in _TARGETS:
        if counts[table] == 0:
            continue
        print(f"\n=== Stamping {table} ({counts[table]:,} rows) ===")
        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"UPDATE {table} SET algorithm_version = ?, batch_id = ? WHERE {where}",
            (_SENTINEL, _SENTINEL),
        )
        conn.execute("COMMIT")
        print(f"  Done in {time.time() - t0:.1f}s")

    print("\n=== Post-backfill verification ===")
    for table, where in _TARGETS:
        remaining = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
        stamped = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE algorithm_version = ?", (_SENTINEL,)
        ).fetchone()[0]
        print(f"  {table}: remaining NULL matching original predicate={remaining} | "
              f"stamped {_SENTINEL}={stamped:,}")
        if remaining != 0:
            print(f"  WARNING: {remaining} rows still unstamped — investigate before trusting this backfill.")

    conn.close()
    print("\nBackfill complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill PRE_V26_UNKNOWN lineage sentinel")
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(Path(args.db), args.dry_run)
