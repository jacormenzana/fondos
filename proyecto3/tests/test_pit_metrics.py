# proyecto3/tests/test_pit_metrics.py
# -*- coding: utf-8 -*-
"""
PIT expanding-window metrics (proyecto3/src/pit_metrics.py) pinned to P2's own pure functions --
FND-0159, Wave B step d1a.

The oracle is P2's scalar path (returns / drawdown / deflation / srri, loaded by file path) evaluated on
the NAV TRUNCATED at every observation of every fund; the vectorized result must equal it at that point.
No DB, no pipeline imports (R-7).

    python -m pytest proyecto3/tests/test_pit_metrics.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_metrics import (
    METRICS, aligned_ipc, aligned_rf, expanding_risk_metrics, load_p2_calc,
)
from shared.config import RISK_FREE_RATE_ANN

returns = load_p2_calc("returns")
drawdown = load_p2_calc("drawdown")
deflation = load_p2_calc("deflation")
srri = load_p2_calc("srri")

N = 100
DATES = pd.date_range("2005-01-31", periods=N, freq=pd.offsets.MonthEnd())
TOL = dict(rel=1e-9, abs=1e-9)


def _panel() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    cols = {}
    for name, (start, mu, sd) in {
        "EQ":   (0, 0.006, 0.045),     # volatile equity-like
        "BOND": (10, 0.002, 0.008),    # calm
        "MM":   (25, 0.0003, 0.0002),  # money-market-like: tiny variance, nearly flat
        "GAP":  (5, 0.004, 0.03),      # internal gap of missing months
        "YOUNG": (80, 0.005, 0.02),    # only 20 observations
        "WILD": (40, 0.0, 0.40),       # vol above the SRRI sanity cap (5.0 annualized)
    }.items():
        r = rng.normal(mu, sd, N)
        nav = 100.0 * np.cumprod(1.0 + np.clip(r, -0.9, None))
        s = pd.Series(nav, index=DATES)
        s.iloc[:start] = np.nan
        cols[name] = s
    panel = pd.DataFrame(cols)
    panel.loc[DATES[30:36], "GAP"] = np.nan
    # WILD: alternating x10 / /10 moves -> annualized vol ~17 (> the SRRI sanity cap of 5.0)
    wild = pd.Series(100.0 * 10.0 ** (np.arange(N) % 2), index=DATES)
    wild.iloc[:40] = np.nan
    panel["WILD"] = wild
    return panel


def _ipc() -> pd.DataFrame:
    # starts 2 years BEFORE the panel: real IPC history predates every NAV. (If a fund's observations
    # all precede the first IPC date, P2's per-prefix bfill returns an empty deflated series -> NaN
    # real metrics, while pit_metrics back-fills the earliest IPC value; irrelevant with real data.)
    ipc_dates = pd.date_range("2003-01-31", periods=N + 24, freq=pd.offsets.MonthEnd())
    return pd.DataFrame({"date": ipc_dates,
                         "ipc_index": 100.0 * np.cumprod(1.0 + 0.002 + 0.001 * np.sin(np.arange(N + 24) / 5))})


def _rf() -> pd.DataFrame:
    rate = np.where(np.arange(N) < 50, 0.03, -0.004)                 # includes a negative-rate period
    return pd.DataFrame({"date": DATES, "rate": rate})


def _rf_oracle(date, rf):
    s = rf.sort_values("date")
    date = date + pd.offsets.MonthEnd(0)                             # resolve_rf_rate aligns to month-end
    prior = s[s["date"] <= date]
    return float(prior["rate"].iloc[-1]) if len(prior) else float(s["rate"].iloc[0])


def _oracle(nav: pd.Series, ipc, rf, lag=0) -> dict:
    """P2 scalar metrics for each prefix of the fund's own (dropna'd) observations."""
    s = nav.dropna()
    ipc_l = ipc.copy()
    if lag:
        ipc_l["date"] = ipc_l["date"] + pd.offsets.MonthEnd(lag)
    out = {}
    for p in range(len(s)):
        sub = s.iloc[: p + 1]
        d = sub.index[-1]
        sub_df = pd.DataFrame({"date": sub.index, "nav": sub.to_numpy()})
        sub_s = pd.Series(sub.to_numpy())
        real = deflation.deflate_nav(sub_df, ipc_l)["nav_real"].reset_index(drop=True)
        srri_res = srri.compute_srri(sub_s)
        out[d] = {
            "n_obs": float(len(sub)),
            "max_dd": drawdown.max_drawdown(drawdown.compute_drawdown(sub_s)),
            "return_ann": returns.annualized_return(sub_s),
            "vol_ann": returns.annualized_volatility(sub_s),
            "sharpe": returns.sharpe_ratio(sub_s, _rf_oracle(d, rf)),
            "return_ann_real": returns.annualized_return(real),
            "max_dd_real": drawdown.max_drawdown(drawdown.compute_drawdown(real)),
            "srri_nav": float(srri_res["srri"]),
        }
    return out


def _assert_equal(got: pd.DataFrame, expected: float, d, col, metric):
    g = got.loc[d, col]
    if expected is None or (isinstance(expected, float) and np.isnan(expected)):
        assert np.isnan(g), f"{metric} {col} {d.date()}: expected NaN, got {g}"
    else:
        assert g == pytest.approx(expected, **TOL), f"{metric} {col} {d.date()}: {g} != {expected}"


@pytest.fixture(scope="module")
def panel():
    return _panel()


@pytest.mark.parametrize("lag", [0, 2])
def test_matches_p2_scalar_path_on_every_truncation(panel, lag):
    ipc, rf = _ipc(), _rf()
    res = expanding_risk_metrics(panel, ipc, rf, ipc_lag_months=lag)
    checked = 0
    for col in panel.columns:
        for d, exp in _oracle(panel[col], ipc, rf, lag).items():
            for metric, value in exp.items():
                _assert_equal(res[metric], value, d, col, metric)
                checked += 1
    assert checked > 3000                                            # the loop really ran


def test_matches_p2_on_raw_non_month_end_dates(panel):
    # Real NAV dates are mostly mid/late month; P2 aligns IPC on the raw date and rf on its month-end.
    shifted = panel.copy()
    offsets = np.where(np.arange(N) % 3 == 0, 5, np.where(np.arange(N) % 3 == 1, 11, 2))
    shifted.index = DATES - pd.to_timedelta(offsets, unit="D")
    ipc, rf = _ipc(), _rf()
    res = expanding_risk_metrics(shifted, ipc, rf)
    checked = 0
    for col in ("EQ", "BOND", "GAP"):
        for d, exp in _oracle(shifted[col], ipc, rf).items():
            for metric, value in exp.items():
                _assert_equal(res[metric], value, d, col, metric)
                checked += 1
    assert checked > 1000


def test_values_only_where_the_fund_has_a_nav(panel):
    res = expanding_risk_metrics(panel, _ipc(), _rf())
    absent = panel.isna()
    for metric in METRICS:
        assert res[metric][absent].isna().all().all(), metric


def test_value_at_t_does_not_depend_on_later_data(panel):
    ipc, rf = _ipc(), _rf()
    full = expanding_risk_metrics(panel, ipc, rf, ipc_lag_months=2)
    for cut in (30, 60, 85):
        part = expanding_risk_metrics(panel.iloc[:cut], ipc, rf, ipc_lag_months=2)
        for metric in METRICS:
            pd.testing.assert_frame_equal(full[metric].iloc[:cut], part[metric], check_exact=False, rtol=1e-12, atol=1e-12)


def test_ipc_lag_uses_the_ipc_published_by_t_not_the_one_of_t(panel):
    ipc, rf = _ipc(), _rf()
    lagged = expanding_risk_metrics(panel, ipc, rf, ipc_lag_months=2)
    plain = expanding_risk_metrics(panel, ipc, rf, ipc_lag_months=0)
    d = DATES[70]
    assert lagged["return_ann_real"].loc[d, "EQ"] != pytest.approx(plain["return_ann_real"].loc[d, "EQ"], rel=1e-9)
    # nominal metrics are unaffected by the IPC lag
    pd.testing.assert_frame_equal(lagged["return_ann"], plain["return_ann"])
    ipc_t = aligned_ipc(DATES, ipc, 2)
    assert ipc_t[70] == pytest.approx(float(ipc.loc[ipc["date"] == DATES[68], "ipc_index"].iloc[0]))


def test_srri_gates_young_funds_and_anomalous_volatility(panel):
    res = expanding_risk_metrics(panel, _ipc(), _rf())
    young = res["srri_nav"]["YOUNG"].dropna()
    assert (young.iloc[:12] == 0).all() and young.iloc[12] > 0       # needs 13 NAV
    wild_vol = res["vol_ann"]["WILD"].dropna()
    assert (wild_vol > 5.0).any()
    flagged = res["srri_nav"]["WILD"][res["vol_ann"]["WILD"] > 5.0]
    assert (flagged == 0).all()                                      # ANOMALOUS_VOL -> 0


def test_without_ipc_real_metrics_are_nan_and_nominal_remain(panel):
    res = expanding_risk_metrics(panel, None, _rf())
    assert res["return_ann_real"].isna().all().all() and res["max_dd_real"].isna().all().all()
    assert res["return_ann"]["EQ"].notna().any()


def test_without_rf_series_falls_back_to_the_flat_default(panel):
    res = expanding_risk_metrics(panel, None, None)
    d, col = DATES[60], "EQ"
    expected = (res["return_ann"].loc[d, col] - RISK_FREE_RATE_ANN) / res["vol_ann"].loc[d, col]
    assert res["sharpe"].loc[d, col] == pytest.approx(expected, **TOL)


def test_rf_alignment_backfills_before_the_series_and_ffills_after():
    rf = pd.DataFrame({"date": DATES[10:20], "rate": np.linspace(0.01, 0.02, 10)})
    got = aligned_rf(DATES, rf)
    assert got[0] == pytest.approx(0.01) and got[5] == pytest.approx(0.01)       # bfill before the start
    assert got[50] == pytest.approx(0.02)                                        # ffill after the end


def test_constant_nav_has_zero_vol_and_nan_sharpe_without_crashing():
    nav = pd.DataFrame({"FLAT": np.full(N, 100.0)}, index=DATES)
    res = expanding_risk_metrics(nav, _ipc(), _rf())
    assert res["vol_ann"]["FLAT"].iloc[5] == pytest.approx(0.0, abs=1e-12)
    assert np.isnan(res["sharpe"]["FLAT"].iloc[5])
    assert res["max_dd"]["FLAT"].iloc[5] == 0.0
