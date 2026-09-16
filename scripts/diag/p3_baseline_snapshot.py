# scripts/diag/p3_baseline_snapshot.py
# -*- coding: utf-8 -*-
"""
P3 optimization plan (2026-09-16) -- Phase 0 frozen baseline.

Read-only. Captures the "before" state of the regime classifier, the scorer
and the current portfolio so every later phase (starting with Phase 1's
max_dd sign-bug fix, which reorders essentially every score) has a diff
target. Without this snapshot there is no way to distinguish "the fix
worked" from "something else broke".

Writes three CSVs to the given output directory (default: this session's
scratchpad):
  regime_history_baseline.csv   -- classify_historical() label vector + macro
                                    inputs, one row per month (~321 rows)
  fund_scores_baseline.csv      -- top-20 fund_scores per block, by score_total
  portfolio_weights_baseline.csv -- current portfolio_weights, all scenarios

Usage (from repo root):
    C:\\data\\envs\\des\\python.exe scripts/diag/p3_baseline_snapshot.py [out_dir]
"""

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH
from proyecto3.src.regime_classifier import RegimeClassifier

_DEFAULT_OUT_DIR = (
    Path.home() / "AppData" / "Local" / "Temp" / "claude" / "c--desarrollo-fondos"
    / "d94d9d53-0534-4408-8eb4-7b8ea3e5cfff" / "scratchpad" / "p3_baseline_20260916"
)


def _snapshot_regime_history(conn: sqlite3.Connection, out_dir: Path) -> int:
    clf = RegimeClassifier(conn)
    hist = clf.classify_historical()
    out_path = out_dir / "regime_history_baseline.csv"
    hist.to_csv(out_path, index=True, index_label="date")

    counts = hist["regime"].value_counts()
    print(f"[regime_history] {len(hist)} months -> {out_path}")
    print("  label counts:")
    for regime, n in counts.items():
        print(f"    {regime:<24} {n}")
    return len(hist)


def _snapshot_fund_scores(conn: sqlite3.Connection, out_dir: Path, top_n: int = 20) -> int:
    rows = conn.execute(
        """
        SELECT isin, block, score_version, score_total, eligible,
               calculated_at, notes
        FROM fund_scores
        ORDER BY block, score_total DESC
        """
    ).fetchall()
    cols = ["isin", "block", "score_version", "score_total", "eligible",
            "calculated_at", "notes"]

    # top_n per block, preserving the score_total DESC order from the query
    by_block: dict[str, list[tuple]] = {}
    for row in rows:
        by_block.setdefault(row[1], []).append(row)

    out_path = out_dir / "fund_scores_baseline.csv"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        n_written = 0
        for block, block_rows in sorted(by_block.items()):
            for row in block_rows[:top_n]:
                f.write(",".join(
                    "" if v is None else str(v).replace(",", ";") for v in row
                ) + "\n")
                n_written += 1

    print(f"[fund_scores] {len(rows)} total rows, {len(by_block)} blocks, "
          f"top {top_n}/block ({n_written} rows) -> {out_path}")
    return n_written


def _snapshot_portfolio_weights(conn: sqlite3.Connection, out_dir: Path) -> int:
    rows = conn.execute(
        """
        SELECT pw.scenario_id, ps.macro_regime, pw.isin, pw.block, pw.weight, pw.role
        FROM portfolio_weights pw
        JOIN portfolio_scenarios ps ON ps.scenario_id = pw.scenario_id
        ORDER BY pw.scenario_id, pw.block, pw.weight DESC
        """
    ).fetchall()
    cols = ["scenario_id", "macro_regime", "isin", "block", "weight", "role"]

    out_path = out_dir / "portfolio_weights_baseline.csv"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(",".join(cols) + "\n")
        for row in rows:
            f.write(",".join("" if v is None else str(v) for v in row) + "\n")

    print(f"[portfolio_weights] {len(rows)} rows -> {out_path}")
    return len(rows)


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        print(f"P3 baseline snapshot -> {out_dir}\n")
        _snapshot_regime_history(conn, out_dir)
        print()
        _snapshot_fund_scores(conn, out_dir)
        print()
        _snapshot_portfolio_weights(conn, out_dir)
    finally:
        conn.close()

    print("\nDone. Re-run after each phase and diff against these files.")


if __name__ == "__main__":
    main()
