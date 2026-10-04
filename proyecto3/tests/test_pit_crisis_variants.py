# proyecto3/tests/test_pit_crisis_variants.py
# -*- coding: utf-8 -*-
"""
Sign / scale variants of the crisis multiplier (proyecto3/src/pit_crisis_variants.py) -- FND-0224 phase B, FND-0225.
Synthetic worlds with a known answer; no DB (R-7).

    python -m pytest proyecto3/tests/test_pit_crisis_variants.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_crisis_variants import (CRISIS_VARIANTS, evaluate_crisis_variants, forward_max_drawdown,
                                               penalised, signal)
from proyecto3.src.pit_macro import expanding_macro_metrics
from proyecto3.tests.test_pit_macro import IDX, LAGS, _macro, _nav_panel

N_FUNDS = 60
ISINS = [f"F{i:02d}" for i in range(N_FUNDS)]


def _group(seed=1, scale=1e-4):
    rng = np.random.default_rng(seed)
    vol = rng.uniform(0.02, 0.2, N_FUNDS)
    return pd.DataFrame({"beta_vix": -scale * vol / vol.mean() + rng.normal(0, scale * 0.3, N_FUNDS),
                         "beta_spread_hy": rng.normal(0, 0.005, N_FUNDS), "vol_ann": vol}, index=ISINS)


def test_sign_raw_stays_silent_at_the_stored_scale_and_fires_on_a_large_negative_beta():
    g = _group()
    assert not penalised(g, "sign_raw", 1.0).any()                       # |beta| ~ 1e-4 vs the 0.02 threshold
    g.loc["F00", "beta_vix"] = -0.05
    assert penalised(g, "sign_raw", 1.0).loc["F00"] and penalised(g, "sign_raw", 1.0).sum() == 1


def test_z_vix_penalises_the_most_negative_betas_only():
    g = _group()
    flag = penalised(g, "z_vix", 1.0)
    assert 0.05 < flag.mean() < 0.30
    assert g.loc[flag, "beta_vix"].max() < g.loc[~flag, "beta_vix"].median()


def test_signal_needs_a_minimum_pool_and_ignores_missing_values():
    g = _group().head(5)
    assert signal(g, "z_vix").isna().all()
    g = _group()
    g.loc[ISINS[:10], "beta_spread_hy"] = np.nan
    s = signal(g, "z_spread")
    assert s.iloc[:10].isna().all() and s.iloc[10:].notna().all()


def test_residual_signal_removes_the_dependence_on_volatility():
    g = _group()
    raw = signal(g, "z_vix")
    res = signal(g, "z_vix_resid")
    vol = g["vol_ann"]
    assert abs(raw.corr(vol)) > 0.8 and abs(res.corr(vol)) < 0.1


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError):
        signal(_group(), "nope")


def _world(effect, seed=4, n_dates=4):
    """Crisis dates, one sub-portfolio; the 12m forward return depends on beta_vix with strength `effect`."""
    rng = np.random.default_rng(seed)
    dates = pd.DatetimeIndex([IDX[100 + 3 * k] for k in range(n_dates)])
    beta = pd.DataFrame({i: rng.normal(0, 1, n_dates) for i in ISINS}, index=dates)
    vol = pd.DataFrame(rng.uniform(0.02, 0.2, (n_dates, N_FUNDS)), index=dates, columns=ISINS)
    spread = pd.DataFrame(rng.normal(0, 1, (n_dates, N_FUNDS)), index=dates, columns=ISINS)
    fwd = effect * beta + pd.DataFrame(rng.normal(0, 0.5, (n_dates, N_FUNDS)), index=dates, columns=ISINS)
    rows = [{"as_of": t, "regime": "Crisis_Financiera", "isin": i, "subportfolio": "Defensiva",
             "score_final": float(rng.uniform(0.1, 1.0)), "eligible": True} for t in dates for i in ISINS]
    run = SimpleNamespace(scores=pd.DataFrame(rows), macro={"beta_vix": beta, "beta_spread_hy": spread}, vol_ann=vol)
    return run, fwd, dates


def test_the_evaluation_detects_a_real_signal():
    run, fwd, dates = _world(effect=1.0)
    out = evaluate_crisis_variants(run, fwd, dates, variants=("z_vix",), z_cuts=(1.0,))
    row = out[(out["scope"] == "crisis") & (out["variant"] == "z_vix")].iloc[0]
    assert row["ic_signal"] > 0.5 and row["flagged_minus_unflagged"] < -0.5
    assert 0.05 < row["share_flagged"] < 0.30


def test_the_evaluation_finds_nothing_in_noise():
    run, fwd, dates = _world(effect=0.0)
    out = evaluate_crisis_variants(run, fwd, dates, variants=("z_vix",), z_cuts=(1.0,))
    row = out[(out["scope"] == "crisis") & (out["variant"] == "z_vix")].iloc[0]
    assert abs(row["ic_signal"]) < 0.2


def test_every_variant_runs_and_non_crisis_dates_only_count_in_the_all_scope():
    run, fwd, dates = _world(effect=0.5)
    run.scores.loc[run.scores["as_of"] == dates[0], "regime"] = "Expansion"
    out = evaluate_crisis_variants(run, fwd, dates)
    assert set(out["variant"]) == set(CRISIS_VARIANTS) and set(out["scope"]) == {"crisis", "all"}
    g = out.groupby("scope")["groups"].first()
    assert g["all"] == len(dates) and g["crisis"] == len(dates) - 1


def test_a_run_without_group_b_is_rejected():
    run, fwd, dates = _world(effect=0.5)
    run.macro = None
    with pytest.raises(ValueError):
        evaluate_crisis_variants(run, fwd, dates)


def test_parallel_macro_metrics_match_the_single_process_result():
    macro = _macro()
    nav = _nav_panel(macro, n_funds=4)
    at = pd.DatetimeIndex([IDX[i] for i in (101, 104, 107)])
    one = expanding_macro_metrics(nav.loc[:at[-1]], macro.loc[:at[-1]], at, lags=LAGS)
    two = expanding_macro_metrics(nav.loc[:at[-1]], macro.loc[:at[-1]], at, lags=LAGS, workers=2)
    for m in one:
        pd.testing.assert_frame_equal(one[m], two[m])


def test_forward_max_drawdown_measures_the_worst_fall_inside_the_window():
    idx = pd.date_range("2020-01-31", periods=30, freq=pd.offsets.MonthEnd())
    nav = pd.DataFrame({"A": 100.0, "B": 100.0}, index=idx)
    nav.loc[idx[3]:idx[5], "A"] = 90.0                       # -10% from the running peak, then back
    nav.loc[idx[6]:, "A"] = 100.0
    nav["B"] = 100.0 * 1.01 ** np.arange(30)                 # never falls
    out = forward_max_drawdown(nav, pd.DatetimeIndex([idx[0], idx[10]]), months=12)
    assert out.loc[idx[0], "A"] == pytest.approx(-0.10) and out.loc[idx[10], "A"] == pytest.approx(0.0)
    assert (out["B"] == 0).all()


def test_the_evaluation_reports_the_drawdown_yardstick_when_given():
    run, fwd, dates = _world(effect=1.0)
    out = evaluate_crisis_variants(run, fwd, dates, variants=("z_vix",), z_cuts=(1.0,), forward_dd=fwd * 0.1)
    row = out[(out["scope"] == "crisis") & (out["variant"] == "z_vix")].iloc[0]
    assert row["ic_signal_dd"] > 0.5 and row["dd_flagged_minus_unflagged"] < 0
