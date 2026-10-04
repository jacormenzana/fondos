# proyecto3/tests/test_pit_macro.py
# -*- coding: utf-8 -*-
"""
PIT scorer group B, macro part (proyecto3/src/pit_macro.py) -- FND-0224.

Publication lags, no look-ahead, the two variants, and the flow into the scorer (a crisis beta that the
production thresholds react to). No DB (R-7).

    python -m pytest proyecto3/tests/test_pit_macro.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_candidates import pit_scores
from proyecto3.src.pit_macro import (MACRO_METRICS, VARIANTS, expanding_macro_metrics, factor_lag_months,
                                     variant_signature, visible_macro)
from proyecto3.tests.test_pit_candidates import AT, DATES, N, _inputs, _universe

LAGS = {"ipc_index": 2, "cli": 2, "m3_yoy": 2}
IDX = pd.date_range("2005-01-31", periods=200, freq=pd.offsets.MonthEnd())


def _macro(n=200, seed=5):
    """Five factors; spread_ig is a near-duplicate of spread_hy so the production single pass drops both."""
    rng = np.random.default_rng(seed)
    hy = rng.normal(0, 1.0, n)
    return pd.DataFrame({
        "d_rate_eu": rng.normal(0, 0.1, n), "oil_yoy": rng.normal(0, 5.0, n), "m3_yoy": rng.normal(0, 1.0, n),
        "ipc_yoy_eu": rng.normal(2, 1.0, n), "spread_hy": hy, "spread_ig": hy * 0.8 + rng.normal(0, 0.1, n),
    }, index=IDX[:n])


def _nav_panel(macro, n_funds=3, seed=9):
    rng = np.random.default_rng(seed)
    cols = {}
    for k in range(n_funds):
        r = 0.003 * macro["spread_hy"].values + rng.normal(0.004, 0.01, len(macro))
        cols[f"F{k}"] = pd.Series(100.0 * np.exp(np.cumsum(r)), index=macro.index)
    return pd.DataFrame(cols)


def test_factor_lag_months_follows_the_regime_publication_table():
    assert factor_lag_months("ipc_yoy_es", LAGS) == 2
    assert factor_lag_months("cli_yoy_us", LAGS) == 2
    assert factor_lag_months("m3_yoy", LAGS) == 2
    assert factor_lag_months("spread_hy", LAGS) == 0 and factor_lag_months("oil_yoy", LAGS) == 0


def test_visible_macro_masks_unpublished_months_and_drops_the_future():
    macro = _macro()
    t = IDX[100]
    vis = visible_macro(macro, t, LAGS)
    assert vis.index.max() == t
    assert vis["ipc_yoy_eu"].iloc[-2:].isna().all() and vis["ipc_yoy_eu"].iloc[-3:-2].notna().all()
    assert vis["m3_yoy"].iloc[-2:].isna().all()
    assert vis["spread_hy"].iloc[-1:].notna().all()          # a market series is known at t


def test_values_at_t_do_not_change_when_later_data_is_added():
    macro = _macro()
    nav = _nav_panel(macro)
    at = pd.DatetimeIndex([IDX[i] for i in (101, 104, 107)])
    base = expanding_macro_metrics(nav.loc[:IDX[107]], macro.loc[:IDX[107]], at, lags=LAGS)
    # extend both the NAV and the macro history with different data after the last evaluation date
    macro2, nav2 = _macro(n=200, seed=99), _nav_panel(_macro(n=200, seed=99), seed=77)
    macro2.loc[:IDX[107]] = macro.loc[:IDX[107]]
    nav2.loc[:IDX[107]] = nav.loc[:IDX[107]]
    ext = expanding_macro_metrics(nav2, macro2, at, lags=LAGS)
    for m in MACRO_METRICS:
        pd.testing.assert_frame_equal(base[m], ext[m])


def test_only_quarter_end_dates_are_evaluated_by_default():
    macro = _macro()
    nav = _nav_panel(macro)
    at = pd.DatetimeIndex([IDX[i] for i in range(100, 112)])
    out = expanding_macro_metrics(nav.loc[:at[-1]], macro.loc[:at[-1]], at, lags=LAGS)
    assert set(out["macro_r2"].index.month) <= {3, 6, 9, 12}
    assert out["macro_r2"].notna().any().any()


def test_control_drops_the_collinear_pair_and_iterative_variant_keeps_spread_hy():
    macro = _macro()
    nav = _nav_panel(macro)
    at = pd.DatetimeIndex([IDX[119]])
    ctl = expanding_macro_metrics(nav.loc[:at[-1]], macro.loc[:at[-1]], at, lags=LAGS, variant="control")
    itv = expanding_macro_metrics(nav.loc[:at[-1]], macro.loc[:at[-1]], at, lags=LAGS, variant="iterative_hy")
    assert ctl["beta_spread_hy"].isna().all().all()
    assert itv["beta_spread_hy"].notna().all().all()


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError):
        expanding_macro_metrics(pd.DataFrame(), pd.DataFrame(), pd.DatetimeIndex([]), variant="nope")


def test_iterative_variant_cleans_infinite_macro_cells():
    macro = _macro()
    macro.loc[IDX[50], "ipc_yoy_eu"] = np.inf
    nav = _nav_panel(macro)
    at = pd.DatetimeIndex([IDX[119]])
    out = expanding_macro_metrics(nav.loc[:at[-1]], macro.loc[:at[-1]], at, lags=LAGS, variant="iterative_hy")
    assert np.isfinite(out["macro_r2"].to_numpy(dtype=float)[~np.isnan(out["macro_r2"].to_numpy(dtype=float))]).all()
    assert "clean_inf" in VARIANTS["iterative_hy"]


def test_a_crisis_beta_reaches_the_scorer_and_moves_the_multiplier():
    """beta_vix above the production threshold in Crisis_Financiera applies the x0.6 malus; without macro it is neutral."""
    panel, nature, attrs, regimes = _universe()
    risk, peers, mom = _inputs(panel, nature, AT)
    t_crisis = DATES[75]
    at = pd.DatetimeIndex([t_crisis])
    base, _ = pit_scores(at, risk, peers, attrs, regimes, momentum=mom)
    victim = base["isin"].iloc[0]
    vix = pd.DataFrame(0.0, index=at, columns=panel.columns)
    vix[victim] = 0.05                                        # > VIX_CRISIS_THRESHOLD (0.02)
    macro = {"beta_vix": vix}
    with_macro, _ = pit_scores(at, risk, peers, attrs, regimes, momentum=mom, macro=macro)
    m0 = base.set_index(["isin", "subportfolio"])["multiplier"]
    m1 = with_macro.set_index(["isin", "subportfolio"])["multiplier"]
    assert regimes.loc[t_crisis] == "Crisis_Financiera"
    hit = m1[m1.index.get_level_values(0) == victim]
    assert (hit < m0.loc[hit.index]).all()
    others = m1[m1.index.get_level_values(0) != victim]
    assert np.allclose(others.to_numpy(), m0.loc[others.index].to_numpy())


def test_variant_signature_is_deterministic_and_reflects_the_settings():
    sig = variant_signature("iterative_hy")
    assert sig["extra_priority"] == ["spread_hy", "vix_yoy"] and sig["exclude"] == ["spread_ig"]
    assert variant_signature("control") == {"name": "control"}
    assert variant_signature("iterative_hy") == sig
    with pytest.raises(ValueError):
        variant_signature("nope")
