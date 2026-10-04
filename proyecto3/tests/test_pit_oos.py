# proyecto3/tests/test_pit_oos.py
# -*- coding: utf-8 -*-
"""
Weight no-trade band (FND-0207), bootstrap inference (FND-0219 part) and the out-of-sample harness (FND-0193).
Synthetic data with known answers; no DB (R-7).

    python -m pytest proyecto3/tests/test_pit_oos.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.backtest_inference import paired_sharpe_difference, sharpe_ratio, stationary_bootstrap_indices
from proyecto3.src.pit_oos import apply_weight_no_trade_band, oos_report, select_cell

M = pd.date_range("2010-01-31", periods=6, freq=pd.offsets.MonthEnd())


def _W(rows):
    return pd.DataFrame(rows, index=M[:len(rows)], columns=["A", "B", "C", "D"])


# ---------------- no-trade band ----------------

def test_delta_zero_returns_the_weights_unchanged():
    W = _W([[0.5, 0.5, np.nan, np.nan], [0.4, 0.6, np.nan, np.nan]])
    pd.testing.assert_frame_equal(apply_weight_no_trade_band(W, 0.0), W)


def test_a_small_change_in_a_held_fund_is_not_traded_and_a_large_one_is():
    nan = np.nan
    W = _W([[0.50, 0.50, nan, nan],
            [0.51, 0.40, 0.09, nan],            # A moves 1pp (inside the 2pp band), B moves 10pp, C enters
            ])
    out = apply_weight_no_trade_band(W, 0.02)
    assert out.iloc[1]["A"] == pytest.approx(0.50)         # frozen at last month's weight
    assert out.iloc[1]["B"] == pytest.approx(0.40)         # outside the band: goes to target
    assert out.iloc[1]["C"] == pytest.approx(0.09)         # a new fund enters at its target


def test_a_fund_that_leaves_is_sold_fully_and_an_empty_month_resets_the_holdings():
    nan = np.nan
    W = _W([[0.5, 0.5, nan, nan], [0.5, nan, 0.5, nan], [nan, nan, nan, nan], [0.5, nan, 0.5, nan]])
    out = apply_weight_no_trade_band(W, 0.5)               # a band wider than any move: only the holdings logic acts
    assert np.isnan(out.iloc[1]["B"]) and out.iloc[1]["C"] == pytest.approx(0.5)
    assert out.iloc[2].isna().all()
    assert out.iloc[3]["A"] == pytest.approx(0.5)          # re-entered from cash at the target


def test_the_book_is_never_levered_by_frozen_weights():
    nan = np.nan
    W = _W([[0.60, 0.40, nan, nan], [0.50, 0.50, nan, nan]])      # A would fall 10pp but stays inside a 15pp band
    out = apply_weight_no_trade_band(W, 0.15)
    assert out.iloc[1].sum() <= 1.0 + 1e-12


def test_traded_notional_does_not_increase_with_the_band():
    rng = np.random.default_rng(3)
    W = pd.DataFrame(rng.dirichlet(np.ones(4), size=40), index=pd.date_range("2010-01-31", periods=40, freq=pd.offsets.MonthEnd()),
                     columns=list("ABCD"))

    def turnover(x):
        return x.fillna(0.0).diff().abs().sum(axis=1).iloc[1:].mean()
    t = [turnover(apply_weight_no_trade_band(W, d)) for d in (0.0, 0.02, 0.05, 0.10)]
    assert t == sorted(t, reverse=True) and t[-1] < t[0]


# ---------------- inference ----------------

def test_stationary_bootstrap_indices_stay_in_range_and_keep_blocks():
    idx = stationary_bootstrap_indices(50, 200, mean_block=6.0, seed=1)
    assert idx.shape == (200, 50) and idx.min() >= 0 and idx.max() < 50
    consecutive = ((idx[:, 1:] - idx[:, :-1]) % 50 == 1).mean()
    assert consecutive > 0.7                                # mean block 6 -> about 5/6 of the steps continue a block


def test_sharpe_ratio_basics():
    assert np.isnan(sharpe_ratio([0.01, 0.01, 0.01]))
    r = np.array([0.02, -0.01, 0.015, 0.005, -0.005, 0.01])
    assert sharpe_ratio(r) == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(12))
    assert sharpe_ratio(r, cash=np.full(6, 0.001)) < sharpe_ratio(r)


def test_paired_difference_detects_a_real_edge_and_not_a_tie():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2005-01-31", periods=120, freq=pd.offsets.MonthEnd())
    base = pd.Series(rng.normal(0.003, 0.02, 120), index=idx)
    better = base + 0.006                                    # same noise, +60 bp a month
    edge = paired_sharpe_difference(better, base, n_boot=500)
    assert edge["diff"] > 0 and edge["ci_low"] > 0 and edge["prob_a_better"] > 0.95
    tie = paired_sharpe_difference(base, base, n_boot=500)
    assert tie["diff"] == pytest.approx(0.0) and tie["ci_low"] <= 0 <= tie["ci_high"]


def test_paired_difference_with_too_few_months_gives_no_interval():
    s = pd.Series(np.linspace(0.0, 0.02, 8), index=pd.date_range("2010-01-31", periods=8, freq=pd.offsets.MonthEnd()))
    out = paired_sharpe_difference(s, s * 0.5)
    assert np.isnan(out["ci_low"]) and out["n"] == 8


# ---------------- selection and the out-of-sample report ----------------

def test_select_cell_prefers_lower_turnover_among_near_ties():
    design = pd.DataFrame({"sharpe": [0.70, 0.69, 0.40], "mean_turnover": [0.30, 0.15, 0.05]},
                          index=pd.MultiIndex.from_tuples([(0.0, 0.0), (0.1, 0.0), (0.2, 0.0)]))
    assert select_cell(design, tolerance=0.02) == (0.1, 0.0)


def _table(idx, mean, turnover, seed):
    rng = np.random.default_rng(seed)
    r = pd.Series(rng.normal(mean, 0.02, len(idx)), index=idx)
    return pd.DataFrame({"net_turnover_1m": r, "cash_ret_1m": 0.001, "turnover": turnover,
                         "excess_12m": rng.normal(0.01, 0.02, len(idx))}, index=idx)


def test_oos_report_chooses_on_the_design_period_and_compares_on_the_blind_one():
    idx = pd.date_range("2008-01-31", periods=180, freq=pd.offsets.MonthEnd())
    base = _table(idx, 0.003, 0.30, 1)
    tables = {(0.0, 0.0): base, (0.1, 0.0): base.assign(net_turnover_1m=base["net_turnover_1m"] + 0.004, turnover=0.15),
              (0.2, 0.0): base.assign(net_turnover_1m=base["net_turnover_1m"] - 0.004, turnover=0.10)}
    rep = oos_report(tables, split="2016-12-31", n_boot=300)
    assert rep["chosen"] == (0.1, 0.0)
    g = rep["grid"]
    assert set(g["period"]) == {"design", "blind"} and len(g) == 6
    assert g[g["period"] == "blind"]["n_months"].max() == len(idx[idx > pd.Timestamp("2016-12-31")])
    b = rep["blind"].set_index(["hysteresis_band", "weight_band"])
    assert b.loc[(0.1, 0.0), "is_chosen"] and b.loc[(0.0, 0.0), "is_reference"]
    assert b.loc[(0.1, 0.0), "diff"] > 0 > b.loc[(0.2, 0.0), "diff"]
    assert b.loc[(0.0, 0.0), "diff"] == pytest.approx(0.0)
    d = rep["diagnostics"]
    assert d["n_trials"] == 3 and 0.0 <= d["design_deflated_sharpe"] <= d["design_psr_vs_zero"] <= 1.0
    assert 1 <= d["blind_effective_months"] <= d["blind_months"]


# ---------------- multiple testing and serial dependence (FND-0219) ----------------

def test_expected_max_sharpe_grows_with_trials_and_is_zero_for_one():
    from proyecto3.src.backtest_inference import expected_max_sharpe
    assert expected_max_sharpe(1, 0.01) == 0.0
    v = [expected_max_sharpe(n, 0.01) for n in (2, 10, 100, 1000)]
    assert v == sorted(v) and v[0] > 0
    assert expected_max_sharpe(10, 0.04) == pytest.approx(2 * expected_max_sharpe(10, 0.01))      # scales with the std


def test_probabilistic_sharpe_is_one_half_at_the_observed_sharpe_and_rises_with_length():
    from proyecto3.src.backtest_inference import probabilistic_sharpe_ratio
    rng = np.random.default_rng(2)
    r = rng.normal(0.01, 0.04, 400)
    sr = r.mean() / r.std(ddof=1)
    assert probabilistic_sharpe_ratio(r, sr) == pytest.approx(0.5, abs=0.02)
    assert probabilistic_sharpe_ratio(r, 0.0) > probabilistic_sharpe_ratio(r[:60], 0.0)            # more data, more certainty
    assert np.isnan(probabilistic_sharpe_ratio(np.full(30, 0.01), 0.0))                            # flat series


def test_deflated_sharpe_falls_as_the_number_of_trials_grows():
    from proyecto3.src.backtest_inference import deflated_sharpe_ratio, probabilistic_sharpe_ratio
    rng = np.random.default_rng(3)
    r = rng.normal(0.012, 0.04, 200)
    one = deflated_sharpe_ratio(r, 1, 0.0004)
    assert one == pytest.approx(probabilistic_sharpe_ratio(r, 0.0))
    many = [deflated_sharpe_ratio(r, n, 0.0004) for n in (5, 50, 500)]
    assert many == sorted(many, reverse=True) and many[-1] < one


def test_effective_sample_size_for_white_noise_and_for_autocorrelated_series():
    from proyecto3.src.backtest_inference import effective_sample_size
    rng = np.random.default_rng(4)
    white = rng.normal(size=600)
    assert effective_sample_size(white) > 0.7 * 600
    ar = np.zeros(600)
    for t in range(1, 600):
        ar[t] = 0.6 * ar[t - 1] + rng.normal()
    assert effective_sample_size(ar) < 0.6 * 600                       # theory: n (1-rho)/(1+rho) = n/4
    assert effective_sample_size(np.arange(3.0)) == 3.0
