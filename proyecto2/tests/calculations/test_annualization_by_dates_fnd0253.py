"""FND-0253: annualise by DATES, not by rows, behind ANNUALIZATION_BY_DATES_ENABLED (default OFF).

The safety proof first: for a DENSE monthly series (rows == consecutive months) every producer returns exactly what it returns today,
bit for bit, with the switch ON or OFF and in both states of the FND-0240 interval switch. Then the corrected behaviour on series with
gaps, and the audit's mirror (shared.statistical_audit.timeseries) so a stored value and its audit cannot disagree.
"""
import math

import numpy as np
import pandas as pd
import pytest

from shared import annualization, config
from shared.statistical_audit.timeseries import build_window_deflation_frame, scalar_window_cpi
from src.calculations import consistency, regime_returns, rolling_stats
from src.calculations.returns import (
    annualized_return, annualized_volatility, monthly_returns, ret_vol_simple, sharpe_ratio, sortino_ratio,
)
from src.calculations.risk_metrics import compute_risk_metrics

RF = 0.03


@pytest.fixture
def by_dates(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "ANNUALIZATION_BY_DATES_ENABLED", on, raising=False)


def _dates(n, start="2015-01-31", skip=()):
    full = pd.date_range(start, periods=n, freq="ME")
    return full[[i for i in range(n) if i not in set(skip)]]


def _nav(n, seed=0, drift=0.004, sigma=0.02):
    rets = np.random.default_rng(seed).normal(drift, sigma, n - 1)
    return 100.0 * np.concatenate([[1.0], np.cumprod(1 + rets)])


def _frame(n=60, seed=0, skip=()):
    """NAV on a monthly grid with the months in `skip` missing (a gap the row convention cannot see)."""
    full = _nav(n, seed)
    keep = [i for i in range(n) if i not in set(skip)]
    return pd.DataFrame({"date": _dates(n)[keep], "nav": full[keep]}).reset_index(drop=True)


def _same(a, b):
    return (math.isnan(a) and math.isnan(b)) or a == b


# ------------------------------------------------------------------ the switch and its registration
def test_switch_ships_off_and_is_in_the_bundle_and_the_family_map():
    from src.utils.family_versions import FLAG_FAMILIES
    assert config.ANNUALIZATION_BY_DATES_ENABLED is False
    assert "ANNUALIZATION_BY_DATES_ENABLED" in config.P2_BUNDLE_FLAGS
    assert set(FLAG_FAMILIES["ANNUALIZATION_BY_DATES_ENABLED"]) == {"risk", "rolling", "regime", "persistence"}


def test_the_helpers_on_a_dense_and_a_gappy_series():
    dense = annualization.month_ordinals(_dates(24))
    assert annualization.dense_equivalent_points(dense) == 24 and (annualization.gap_months(dense) == 1).all()
    gappy = annualization.month_ordinals(_dates(24, skip={5, 6, 7}))                 # a 4-month hole
    assert annualization.dense_equivalent_points(gappy) == 24 and annualization.gap_months(gappy).max() == 4
    assert len(gappy) == 21


def test_kweighted_std_is_exactly_the_legacy_expression_when_every_return_is_one_month():
    r = np.random.default_rng(3).normal(0.005, 0.02, 40)
    assert annualization.kweighted_std(r, np.ones(40)) == float(np.std(r, ddof=1) * np.sqrt(12))      # bit for bit
    k = np.ones(40)
    k[10] = 3.0
    assert annualization.kweighted_std(r, k) != annualization.kweighted_std(r, np.ones(40))


# ------------------------------------------------------------------ DENSE IDENTITY (the safety proof)
@pytest.mark.parametrize("interval", [True, False])
@pytest.mark.parametrize("seed,n", [(0, 13), (1, 37), (2, 120), (3, 200)])
def test_scalar_metrics_of_a_dense_series_are_bit_identical_with_the_switch_on(monkeypatch, by_dates, interval, seed, n):
    monkeypatch.setattr(config, "ANNUALIZATION_INTERVAL_ENABLED", interval, raising=False)
    df = _frame(n, seed)
    s, d = df["nav"], df["date"]
    by_dates(False)
    off = [annualized_return(s), annualized_volatility(s), sharpe_ratio(s, RF), sortino_ratio(s, RF), ret_vol_simple(s)]
    by_dates(True)
    on = [annualized_return(s, dates=d), annualized_volatility(s, dates=d), sharpe_ratio(s, RF, dates=d),
          sortino_ratio(s, RF, dates=d), ret_vol_simple(s, dates=d)]
    assert all(_same(a, b) for a, b in zip(off, on)), (off, on)


def test_compute_risk_metrics_of_a_dense_series_is_identical_with_the_switch_on(by_dates):
    df = _frame(80, 5)
    ipc = pd.DataFrame({"date": pd.date_range("2014-01-31", periods=110, freq="ME"),
                        "ipc_index": 100.0 * np.cumprod(np.full(110, 1.002))})
    by_dates(False)
    off = compute_risk_metrics(df, ipc, RF).set_index(["metric", "real_flag"])["value"]
    by_dates(True)
    on = compute_risk_metrics(df, ipc, RF).set_index(["metric", "real_flag"])["value"]
    assert off.index.equals(on.index)
    assert all(_same(a, b) for a, b in zip(off.to_numpy(), on.to_numpy()))


def test_rolling_rows_of_a_dense_series_are_identical_with_the_switch_on(by_dates):
    df = _frame(70, 6)
    kw = dict(rolling_windows={"rolling_1y": 12, "rolling_3y": 36}, min_obs=5, risk_free_rate=RF)
    by_dates(False)
    off = rolling_stats.compute_rolling_rows("X", df, **kw)
    by_dates(True)
    on = rolling_stats.compute_rolling_rows("X", df, **kw)
    assert len(off) == len(on) > 0
    for a, b in zip(off, on):
        assert a["metric"] == b["metric"] and a["date"] == b["date"] and a["source_rows"] == b["source_rows"]
        assert (a["value"] is None and b["value"] is None) or a["value"] == b["value"]


def test_consistency_of_a_dense_series_is_identical_with_the_switch_on(by_dates):
    df = _frame(48, 7)
    by_dates(False)
    off = consistency.consistency_metrics(df)
    by_dates(True)
    assert consistency.consistency_metrics(df) == off


def test_regime_returns_of_a_dense_series_are_identical_with_the_switch_on(by_dates):
    df = _frame(96, 8)
    regimes = pd.DataFrame({"regime": ["Expansion"] * 40 + ["Contraccion"] * 30 + ["Estanflacion"] * 30}, index=_dates(100)[:100] + pd.offsets.MonthEnd(0))
    regimes = regimes[regimes.index.isin(df["date"] + pd.offsets.MonthEnd(0))]
    by_dates(False)
    off = regime_returns.compute_regime_returns(df, regimes)
    by_dates(True)
    assert regime_returns.compute_regime_returns(df, regimes) == off


# ------------------------------------------------------------------ the corrected behaviour on a series with gaps
def test_a_gap_no_longer_inflates_the_annualised_return(by_dates):
    skip = set(range(20, 50))                                                        # 30 missing months of 72
    df = _frame(72, 11, skip=skip)
    s, d = df["nav"], df["date"]
    total = s.iloc[-1] / s.iloc[0]
    by_dates(False)
    rows_based = annualized_return(s)
    by_dates(True)
    dated = annualized_return(s, dates=d)
    months = (d.iloc[-1].year - d.iloc[0].year) * 12 + d.iloc[-1].month - d.iloc[0].month
    assert dated == pytest.approx(total ** (12.0 / months) - 1.0)                     # (N-1)/12 with N-1 = months spanned
    assert rows_based == pytest.approx(total ** (12.0 / (len(df) - 1)) - 1.0)        # the stored convention: rows as months
    assert abs(rows_based) > abs(dated) or rows_based > dated


def test_the_lu1663960000_shape_is_corrected(by_dates):
    """35 rows over 104 months: the stored row convention annualised a 1.7x total return over 2.8 years instead of 8.7."""
    n = 104
    keep = np.unique(np.round(np.linspace(0, n - 1, 35)).astype(int))
    nav = 100.0 * np.linspace(1.0, 1.712, len(keep))
    d = _dates(n)[keep]
    s = pd.Series(nav)
    by_dates(False)
    stored = annualized_return(s)
    by_dates(True)
    fixed = annualized_return(s, dates=d)
    assert stored == pytest.approx(1.712 ** (12.0 / (len(keep) - 1)) - 1.0)
    assert fixed == pytest.approx(1.712 ** (12.0 / (n - 1)) - 1.0)
    assert stored - fixed > 0.10                                                     # >10 pp of inflation removed


def test_a_gap_return_enters_the_volatility_as_a_multi_month_return(by_dates):
    df = _frame(60, 12, skip={30, 31, 32})
    s, d = df["nav"], df["date"]
    by_dates(False)
    row_vol = annualized_volatility(s)
    by_dates(True)
    vol = annualized_volatility(s, dates=d)
    r = s.pct_change().dropna().to_numpy()
    k = annualization.gap_months(annualization.month_ordinals(d)).astype(float)
    assert vol == annualization.kweighted_std(r, k)
    assert vol < row_vol                                                              # the 4-month return no longer counts as a month


def test_one_month_statistics_drop_the_gap_returns(by_dates):
    df = _frame(40, 13, skip={10, 11})
    by_dates(False)
    assert len(monthly_returns(df["nav"], dates=df["date"], one_month_only=True)) == len(df) - 1
    by_dates(True)
    r = monthly_returns(df["nav"], dates=df["date"], one_month_only=True)
    assert len(r) == len(df) - 2                                                      # the one return that spans the 2-month hole is gone


def test_sortino_uses_standardised_shortfalls_on_a_gappy_series(by_dates):
    df = _frame(60, 14, skip={25, 26, 27, 28})
    s, d = df["nav"], df["date"]
    by_dates(True)
    on = sortino_ratio(s, RF, dates=d)
    by_dates(False)
    off = sortino_ratio(s, RF, dates=d)                                               # dates ignored with the switch off
    assert off == sortino_ratio(s, RF)
    assert not _same(on, off) or math.isnan(on)


def test_without_dates_the_switch_cannot_change_anything(by_dates):
    s = pd.Series(_nav(30, 9))
    by_dates(False)
    off = (annualized_return(s), annualized_volatility(s), sharpe_ratio(s, RF), sortino_ratio(s, RF))
    by_dates(True)
    assert (annualized_return(s), annualized_volatility(s), sharpe_ratio(s, RF), sortino_ratio(s, RF)) == off


# ------------------------------------------------------------------ rolling windows on a series with a gap
def test_rolling_return_of_a_gappy_window_uses_the_calendar_span(by_dates):
    df = _frame(40, 15, skip={14, 15, 16, 17, 18})                                    # 5 missing months inside a 12-month window
    kw = dict(rolling_windows={"rolling_1y": 12}, min_obs=5, risk_free_rate=RF)
    by_dates(False)
    off = {(r["date"], r["metric"], r["real_flag"]): r["value"] for r in rolling_stats.compute_rolling_rows("X", df, **kw)}
    by_dates(True)
    on = {(r["date"], r["metric"], r["real_flag"]): r["value"] for r in rolling_stats.compute_rolling_rows("X", df, **kw)}
    assert set(off) == set(on)
    changed = [key for key in off if off[key] != on[key] and key[1] == "return_ann"]
    assert changed                                                                    # windows spanning the hole move ...
    far = [key for key in off if key[1] == "return_ann" and key[0] < df["date"].iloc[10].date().isoformat()]
    assert far and all(off[key] == on[key] for key in far)                            # ... earlier (dense) windows do not


# ------------------------------------------------------------------ the audit mirrors the convention
def _audit_inputs(skip):
    df = _frame(48, 21, skip=skip)
    nav_dates = pd.DataFrame({"isin": "X", "date": df["date"], "nav": df["nav"]})
    ipc = pd.DataFrame({"date": pd.date_range("2014-01-31", periods=80, freq="ME"),
                        "ipc_index": 100.0 * np.cumprod(np.full(80, 1.002))})
    return df, nav_dates, ipc


def test_scalar_window_cpi_follows_the_producer_convention(by_dates):
    df, nav_dates, ipc = _audit_inputs(skip={10, 11, 12, 13})
    rows = pd.DataFrame({"isin": ["X"], "horizon": ["since_inception"], "metric_version": ["v1"], "n_obs": [len(df)]})
    by_dates(False)
    off = scalar_window_cpi(rows, nav_dates, ipc)["window_cpi_ann"].iloc[0]
    by_dates(True)
    on = scalar_window_cpi(rows, nav_dates, ipc)["window_cpi_ann"].iloc[0]
    # the CPI grows 0.2% a month: by dates the annualised CPI of the window is ~(1.002)^12 - 1, by rows it is overstated
    assert on == pytest.approx(1.002 ** 12 - 1.0, rel=1e-6)
    assert off > on


def test_a_dense_window_has_the_same_audit_cpi_with_the_switch_on(by_dates):
    df, nav_dates, ipc = _audit_inputs(skip=())
    rows = pd.DataFrame({"isin": ["X"], "horizon": ["since_inception"], "metric_version": ["v1"], "n_obs": [len(df)]})
    by_dates(False)
    off = scalar_window_cpi(rows, nav_dates, ipc)["window_cpi_ann"].iloc[0]
    by_dates(True)
    assert scalar_window_cpi(rows, nav_dates, ipc)["window_cpi_ann"].iloc[0] == off


def test_the_fisher_identity_holds_on_a_gappy_series_when_producer_and_audit_agree(by_dates):
    """Stored nominal / real return_ann (by dates) fed to the audit frame: the implied deflator equals the expected one."""
    by_dates(True)
    df, nav_dates, ipc = _audit_inputs(skip={20, 21, 22, 23, 24, 25})
    from src.calculations.deflation import deflate_nav
    real = deflate_nav(df[["date", "nav"]], ipc.assign(date=pd.to_datetime(ipc["date"])))
    nom_ret = annualized_return(df["nav"], dates=df["date"])
    real_ret = annualized_return(real["nav_real"].reset_index(drop=True), dates=real["date"].reset_index(drop=True))
    ts = pd.DataFrame({"isin": ["X"], "date": [df["date"].iloc[-1]], "window_label": ["since_inception"],
                       "w_return_nominal": [nom_ret], "w_return_real": [real_ret], "w_n_obs": [len(df)]})
    frame = build_window_deflation_frame(ts, nav_dates, ipc)
    assert frame["w_deflator_implied"].iloc[0] == pytest.approx(frame["w_deflator_expected"].iloc[0], rel=1e-9)
    assert frame["w_nominal_gap"].iloc[0] < 1e-9
