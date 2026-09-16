# proyecto3/tests/test_fund_scorer_normalize.py
# -*- coding: utf-8 -*-
"""
Pins the sign convention of _normalize_metric / compute_base_scores and, most
importantly, is the fence for the max_dd inversion sign bug (P3 optimization
plan defect #1, fund_scorer.py:461 _INVERTED_METRICS).

max_dd is stored strictly <= 0 in fund_metrics (verified live: 57,166 rows,
min -0.97, max 0.0, zero positive values), so a milder (less negative) value
is already the better outcome under a plain ascending percentile rank.
_INVERTED_METRICS currently includes "max_dd", which flips that ranking and
rewards the WORST drawdown -- on the metric carrying the largest single
weight in Defensiva (30%). test_max_dd_inversion_bug_defensiva below FAILS on
today's code and must pass once _INVERTED_METRICS no longer contains max_dd
(Phase 1a).

R-7: imports ONLY fund_scorer -- no pipeline.py, no core.io, no DB.
Run from repo root:
    python -m pytest proyecto3/tests/test_fund_scorer_normalize.py -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.fund_scorer import _normalize_metric, compute_base_scores


# ============================================================
# 1. _normalize_metric primitive -- the invert parameter itself is correct
#    and general-purpose; the bug is in which call site passes invert=True,
#    not in this function's semantics. Pinned here as documentation.
# ============================================================

def test_normalize_metric_no_invert_ranks_ascending():
    s = pd.Series([-0.60, -0.20, -0.02])
    result = _normalize_metric(s, invert=False)
    assert result.iloc[0] < result.iloc[1] < result.iloc[2]


def test_normalize_metric_invert_ranks_descending():
    s = pd.Series([-0.60, -0.20, -0.02])
    result = _normalize_metric(s, invert=True)
    assert result.iloc[0] > result.iloc[1] > result.iloc[2]


# ============================================================
# 2. The fence: compute_base_scores must rank a mild drawdown above a severe
#    one. Three funds, identical Fund_Nature (a synthetic value absent from
#    NATURE_PROFILE_BONUS so the multiplicative nature bonus is exactly 1.0
#    and cannot mask the ordering), differing only in max_dd. No other
#    weighted metric column is supplied, so compute_base_scores's per-metric
#    loop skips every other term (fund_scorer.py: "if metric not in
#    subset_n.columns: continue") and the score is driven by max_dd alone --
#    isolating the defect precisely.
# ============================================================

def _three_fund_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "isin": ["FUND_MILD", "FUND_MED", "FUND_SEVERE"],
        "Fund_Nature": ["Test_Nature_No_Bonus"] * 3,
        "max_dd": [-0.02, -0.20, -0.60],
    })


@pytest.mark.parametrize("subportfolio", ["Defensiva", "Equilibrada", "Dinamica"])
def test_mild_drawdown_outscores_severe_drawdown(subportfolio):
    df = _three_fund_frame()
    scores = compute_base_scores(df, subportfolio=subportfolio)
    assert scores.loc[0] > scores.loc[1] > scores.loc[2], (
        f"[{subportfolio}] expected FUND_MILD > FUND_MED > FUND_SEVERE, got "
        f"{scores.to_dict()} -- max_dd is being inverted (worst drawdown "
        f"scoring highest)"
    )


def test_defensiva_mild_vs_severe_advantage_matches_weight():
    # Defensiva weights max_dd at 0.30. With 3 distinct raw values, pandas
    # rank(pct=True) assigns exactly {1/3, 2/3, 3/3} (method='average', no
    # ties). No other metric column is present, so the score gap between the
    # best and worst fund is exactly weight * (3/3 - 1/3) = 0.30 * 2/3 = 0.20.
    df = _three_fund_frame()
    scores = compute_base_scores(df, subportfolio="Defensiva")
    advantage = scores.loc[0] - scores.loc[2]
    assert advantage == pytest.approx(0.20, abs=1e-9)
