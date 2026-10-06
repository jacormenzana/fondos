"""FND-0240: annualise a NAV window over its intervals (N points span N - 1 periods), behind ANNUALIZATION_INTERVAL_ENABLED.

Default OFF must be the stored convention bit-for-bit; ON must agree with the regime path (annualized_return_from_returns,
which already counts returns), which is the property the two paths never had. The audit mirrors the convention through the same
helper, so a stored value and its audit cannot disagree.
"""
import math

import numpy as np
import pandas as pd
import pytest

from shared import annualization, config
from shared.statistical_audit.timeseries import build_window_deflation_frame, scalar_window_cpi
from src.calculations import rolling_stats
from src.calculations.returns import (
    annualized_return, annualized_return_from_returns, annualized_volatility, sharpe_ratio, sortino_ratio,
)


@pytest.fixture
def switch(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "ANNUALIZATION_INTERVAL_ENABLED", on, raising=False)


def _nav(n, seed=0, drift=0.004, sigma=0.02):
    rets = np.random.default_rng(seed).normal(drift, sigma, n - 1)
    return pd.Series(100.0 * np.concatenate([[1.0], np.cumprod(1 + rets)]))


# ---------------------------------------------------------------- the helper and the default
def test_switch_is_off_by_default_and_listed_in_the_bundle_so_fingerprints_follow_it():
    assert config.ANNUALIZATION_INTERVAL_ENABLED is False
    assert "ANNUALIZATION_INTERVAL_ENABLED" in config.P2_BUNDLE_FLAGS


def test_years_spanned_off_counts_points_on_counts_intervals(switch):
    switch(False)
    assert annualization.years_spanned(12) == 1.0
    switch(True)
    assert annualization.years_spanned(12) == pytest.approx(11 / 12)
    assert annualization.years_spanned(pd.Series([13, 25])).tolist() == pytest.approx([1.0, 2.0])
    assert annualization.years_spanned(12, interval_correct=False) == 1.0          # explicit argument wins over the switch


# ---------------------------------------------------------------- annualized_return
def test_off_is_the_stored_formula_bit_for_bit(switch):
    switch(False)
    s = _nav(13)
    assert annualized_return(s) == (s.iloc[-1] / s.iloc[0]) ** (1.0 / (len(s) / 12)) - 1.0


def test_on_counts_intervals(switch):
    switch(True)
    s = _nav(13)                                                                    # 12 monthly returns = exactly one year
    assert annualized_return(s) == pytest.approx(s.iloc[-1] / s.iloc[0] - 1.0)


@pytest.mark.parametrize("n", [12, 13, 37, 120, 323])
def test_on_agrees_with_the_regime_path_which_already_counts_returns(switch, n):
    s = _nav(n, seed=n)
    switch(True)
    assert annualized_return(s) == pytest.approx(annualized_return_from_returns(s.pct_change().dropna().to_numpy()), rel=1e-12)


def test_off_disagrees_with_the_regime_path_on_short_windows(switch):
    """The defect, stated as a test: same fund, same window, two answers."""
    switch(False)
    s = _nav(13, seed=3, drift=0.02)                                                # ~27% a year: the gap is then visible
    old, correct = annualized_return(s), annualized_return_from_returns(s.pct_change().dropna().to_numpy())
    assert abs(old - correct) > 0.01 and old < correct


def test_the_change_is_about_one_over_n_of_the_exponent(switch):
    s = _nav(13, seed=4, drift=0.01)
    switch(False)
    old = annualized_return(s)
    switch(True)
    new = annualized_return(s)
    assert new == pytest.approx((1 + old) ** (13 / 12) - 1, rel=1e-9)               # ret_new = (1 + ret_old)^(n/(n-1)) - 1 for n = 13


def test_volatility_does_not_depend_on_the_convention(switch):
    s = _nav(37, seed=5)
    switch(False)
    off = annualized_volatility(s)
    switch(True)
    assert annualized_volatility(s) == off


def test_sharpe_and_sortino_numerators_follow_the_return(switch):
    s = _nav(37, seed=6, drift=0.006)
    rf = 0.025
    switch(False)
    sharpe_off, sortino_off, ret_off = sharpe_ratio(s, rf), sortino_ratio(s, rf), annualized_return(s)
    switch(True)
    ret_on = annualized_return(s)
    vol = annualized_volatility(s)
    assert sharpe_ratio(s, rf) == pytest.approx((ret_on - rf) / vol)
    assert sharpe_ratio(s, rf) != sharpe_off
    assert math.copysign(1, sortino_ratio(s, rf)) == math.copysign(1, sortino_off) or abs(ret_on - rf) < 1e-3


def test_rolling_stats_uses_the_same_helper(switch):
    nav = _nav(13, seed=7).to_numpy()
    switch(False)
    off = rolling_stats._roll_return_ann(nav, 12)
    switch(True)
    on = rolling_stats._roll_return_ann(nav, 12)
    assert off == pytest.approx((nav[-1] / nav[0]) ** (12 / 13) - 1)
    assert on == pytest.approx(nav[-1] / nav[0] - 1)
    assert on == pytest.approx(annualized_return(pd.Series(nav)))                   # scalar and rolling paths agree (ON)


# ---------------------------------------------------------------- the audit mirrors the producers
def _stored_pair(nav, ipc_end, ipc_start, interval_correct):
    """What the producers would have stored for a window of len(nav) points, under a given convention."""
    years = annualization.years_spanned(len(nav), interval_correct=interval_correct)
    nominal = (nav[-1] / nav[0]) ** (1 / years) - 1
    real = ((nav[-1] / ipc_end) / (nav[0] / ipc_start)) ** (1 / years) - 1
    return nominal, real


def _audit_inputs(interval_correct):
    dates = pd.date_range("2024-01-31", periods=13, freq="ME")
    navs = np.linspace(100, 108, 13)
    ipc = pd.DataFrame({"date": dates, "ipc_index": np.linspace(100, 103, 13)})
    nominal, real = _stored_pair(navs, ipc["ipc_index"].iloc[-1], ipc["ipc_index"].iloc[0], interval_correct)
    nav_dates = pd.DataFrame({"isin": "A", "date": dates, "nav": navs})
    ts = pd.DataFrame([{"isin": "A", "date": dates[-1], "window_label": "rolling_1y", "w_return_nominal": nominal,
                        "w_return_real": real, "w_n_obs": 13}])
    return ts, nav_dates, ipc


@pytest.mark.parametrize("stored_on, switch_on, consistent", [(False, False, True), (True, True, True),
                                                               (True, False, False), (False, True, False)])
def test_audit_frame_identities_hold_only_when_producer_and_audit_share_the_convention(switch, stored_on, switch_on, consistent):
    ts, nav_dates, ipc = _audit_inputs(interval_correct=stored_on)
    switch(switch_on)
    row = build_window_deflation_frame(ts, nav_dates, ipc).iloc[0]
    assert bool(row["w_nominal_gap"] <= 1e-9) is consistent
    assert bool(abs(row["w_deflator_implied"] / row["w_deflator_expected"] - 1) <= 1e-9) is consistent


def test_scalar_window_cpi_follows_the_convention(switch):
    dates = pd.date_range("2024-01-31", periods=13, freq="ME")
    nav = pd.DataFrame({"isin": "A", "date": dates, "nav": np.linspace(100, 108, 13)})
    ipc = pd.DataFrame({"date": dates, "ipc_index": np.linspace(100, 103, 13)})
    rows = pd.DataFrame([("A", "since_inception", "v1", 13)], columns=["isin", "horizon", "metric_version", "n_obs"])
    switch(False)
    off = scalar_window_cpi(rows, nav, ipc)["window_cpi_ann"].iloc[0]
    switch(True)
    on = scalar_window_cpi(rows, nav, ipc)["window_cpi_ann"].iloc[0]
    assert off == pytest.approx(1.03 ** (12 / 13) - 1)
    assert on == pytest.approx(0.03)
