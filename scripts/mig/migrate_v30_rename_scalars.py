# scripts/mig/migrate_v30_rename_scalars.py
# -*- coding: utf-8 -*-
"""
Migración v30 — Rename scalar metrics in fund_metrics for naming consistency.

  volatility_ann  →  vol_ann   (since_inception rows, nominal + real)
  max_drawdown    →  max_dd    (since_inception rows, nominal + real)

These names now match fund_metric_timeseries (rolling windows), eliminating
the P#11 DRY violation introduced between risk_metrics.py and rolling_stats.py.

Row count: ~3,700 funds × 2 real_flags × 2 metrics ≈ ~15,000 rows.
UPDATE on PK column = delete+reinsert in B-tree, but at 15K rows this is fast.

Usage:
  python scripts/mig/migrate_v30_rename_scalars.py [--db path] [--dry-run]
"""

import argparse
import sqlite3
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _REPO_ROOT / "db" / "fondos.sqlite"

_RENAMES = [
    ("volatility_ann", "vol_ann"),
    ("max_drawdown",   "max_dd"),
]


def run(db_path: Path, dry_run: bool) -> None:
    print(f"DB  : {db_path}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    print("=== Preflight ===")
    for old, new in _RENAMES:
        n_old = conn.execute(
            "SELECT COUNT(*) FROM fund_metrics WHERE metric=?", (old,)
        ).fetchone()[0]
        n_new = conn.execute(
            "SELECT COUNT(*) FROM fund_metrics WHERE metric=?", (new,)
        ).fetchone()[0]
        status = "DONE" if n_old == 0 else "TODO"
        print(f"  [{status}] '{old}': {n_old:,}  →  '{new}': {n_new:,}")

    if dry_run:
        print("\n[DRY RUN] No changes. Re-run without --dry-run to apply.")
        conn.close()
        return

    print("\n=== Renaming ===")
    t0_total = time.time()
    for old, new in _RENAMES:
        n = conn.execute(
            "SELECT COUNT(*) FROM fund_metrics WHERE metric=?", (old,)
        ).fetchone()[0]
        if n == 0:
            print(f"  '{old}' → already done, skipping.")
            continue
        print(f"  '{old}' → '{new}' ({n:,} rows) ...", end="", flush=True)
        t0 = time.time()
        # SQLite UPDATE on PK column = DELETE old B-tree entry + INSERT new one.
        # With only ~7,500 rows per metric this completes in <1s.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            UPDATE fund_metrics SET metric=? WHERE metric=?
        """, (new, old))
        conn.execute("COMMIT")
        print(f" done in {time.time()-t0:.2f}s")

    print("\n=== Post-migration counts ===")
    for old, new in _RENAMES:
        n_old = conn.execute(
            "SELECT COUNT(*) FROM fund_metrics WHERE metric=?", (old,)
        ).fetchone()[0]
        n_new = conn.execute(
            "SELECT COUNT(*) FROM fund_metrics WHERE metric=?", (new,)
        ).fetchone()[0]
        flag = "OK" if n_old == 0 else "WARN — old rows remain!"
        print(f"  [{flag}] '{new}': {n_new:,}  (old '{old}': {n_old:,})")

    conn.execute("ANALYZE fund_metrics")
    conn.close()
    print(f"\nMigration v30 complete in {time.time()-t0_total:.1f}s.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v30 scalar metric rename in fund_metrics")
    parser.add_argument("--db", default=str(_DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(Path(args.db), args.dry_run)
