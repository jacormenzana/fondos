#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feasibility study F of the drawdown-management RFC (Package L, PREREG_P3_10_FEAS, FND-0430). READ-ONLY on the sealed PIT run (hash-verified), no database, no production change.

  --stage design   the DESIGN-POWER count only: stress months and distinct stress episodes of the equal-weight eligible pool, and the share of dates at which a stress beta can exist.
                   It is a property of the design, not an outcome of the hypothesis, so it may be read BEFORE the preregistration is committed (plan rev. 3, Step 0).

The outcome stages (F1 census, F1b gate, F2) are added to this script only after PREREG_P3_10_FEAS.md is committed with the owner's approval; the pure pieces are in proyecto3/src/pit_cdar.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from proyecto3.src import pit_cdar as C  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402
from proyecto3.src.ranking_levers import month_end_rows  # noqa: E402

DEFAULT_RUN = ROOT / "out" / "reports" / "pit_backtest" / RUN_STAMP


def load_sealed(run_dir: Path):
    """Verify the seal, then return (scores [as_of, isin, eligible], month-end returns frame dates x isin)."""
    verified = verify_seal(run_dir)
    scores = pd.read_parquet(run_dir / "scores_long.parquet", columns=["as_of", "isin", "eligible"])
    nav = month_end_rows(pd.read_parquet(run_dir / "inputs" / "nav.parquet"))
    return verified, scores, nav.pct_change(fill_method=None)


def pool_series(scores: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Equal-weight return OVER month t of the funds that were ELIGIBLE at the previous month-end of the sealed run (any sub-portfolio): the pool actually available then, so the series at t uses
    only information known at t. A fund without a return that month leaves that month's mean."""
    elig = scores[scores["eligible"].astype(bool)].groupby("as_of")["isin"].agg(lambda s: sorted(set(s)))
    dates = sorted(elig.index)
    out = {}
    for prev, t in zip(dates[:-1], dates[1:]):
        if t in returns.index:
            cols = [i for i in elig[prev] if i in returns.columns]
            if cols:
                out[t] = float(returns.loc[t, cols].mean())
    return pd.Series(out, dtype=float)


def stage_design(run_dir: Path) -> int:
    verified, scores, returns = load_sealed(run_dir)
    print(f"[SEAL] {len(verified)} artifacts verified for run {run_dir.name}")
    pool = pool_series(scores, returns)
    stress = C.stress_months(pool)
    print(f"[POOL] {len(pool)} months {pool.index.min().date()}..{pool.index.max().date()}")
    rc = 0
    for gap in (0, 2):
        p = C.design_power(stress, gap=gap)
        ep = C.stress_episodes(stress, gap)
        print(f"[DESIGN gap={gap}] stress months {p['n_stress_months']} of {p['n_months']} | episodes {p['n_episodes']} (lengths {p['episode_lengths']}) | "
              f"enough episodes (>= {C.MIN_EPISODES}): {p['enough_episodes']} | dates where a stress beta can exist (>= {C.MIN_STRESS} stress months in {C.WINDOW}): {p['share_dates_with_beta']:.1%}")
        for k in sorted(set(ep[ep > 0])):
            m = ep[ep == k].index
            print(f"  episode {k}: {m.min().date()} .. {m.max().date()} ({int((ep == k).sum())} stress months)")
        rc = rc or (0 if p["enough_episodes"] else 3)
    return rc


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["design"], default="design")
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return stage_design(args.run_dir)
    except SealMismatch as exc:
        print(f"[REFUSED] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
