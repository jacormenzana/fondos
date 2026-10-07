"""FND-0241: a monthly NAV looks the CPI up with its own calendar month-end, behind DEFLATION_MONTH_ALIGN_ENABLED.

The ES CPI is stamped on the calendar month-end, a monthly NAV on the last BUSINESS day. With the legacy lookup (merge_asof backward)
the NAVs dated 29/30 of a month took the PREVIOUS month's CPI: that month's real return equalled its nominal one. Default OFF must be
the stored behaviour bit-for-bit; ON gives every month its own CPI; a daily series is never aligned; the audit mirror follows the switch.
"""
import numpy as np
import pandas as pd
import pytest

from shared import config, deflation_alignment
from shared.statistical_audit.timeseries import nav_with_ipc
from src.calculations.consistency import consistency_metrics
from src.calculations.deflation import deflate_nav
from src.calculations.short_horizon import compute_short_horizon_metrics
from src.utils import family_versions


@pytest.fixture
def switch(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "DEFLATION_MONTH_ALIGN_ENABLED", on, raising=False)


def _last_business_day_navs(n, seed, start="2012-01-31"):
    """n monthly NAVs dated on the last BUSINESS day of each month (weekend month-ends move to Friday), like Morningstar's."""
    rng = np.random.default_rng(seed)
    ends = pd.date_range(start, periods=n, freq="ME")
    dates = [d - pd.offsets.BDay(0) if d.weekday() < 5 else d - pd.offsets.BDay(1) for d in ends]
    nav = 100.0 * np.cumprod(1 + rng.normal(0.004, 0.03, n))
    return pd.DataFrame({"date": pd.to_datetime(dates), "nav": nav}), ends


def _ipc(ends, seed):
    """CPI index stamped on the calendar month-end, with a non-trivial month-on-month path (incl. deflation months)."""
    rng = np.random.default_rng(seed)
    idx = 100.0 * np.cumprod(1 + rng.normal(0.002, 0.004, len(ends) + 3))
    return pd.DataFrame({"date": pd.date_range(ends[0] - pd.offsets.MonthEnd(2), periods=len(ends) + 3, freq="ME"), "ipc_index": idx})


# ---------------------------------------------------------------- the switch and its wiring
def test_switch_is_off_by_default_in_the_bundle_and_mapped_to_the_families_it_changes():
    assert config.DEFLATION_MONTH_ALIGN_ENABLED is False
    assert "DEFLATION_MONTH_ALIGN_ENABLED" in config.P2_BUNDLE_FLAGS
    assert family_versions.flag_families("DEFLATION_MONTH_ALIGN_ENABLED") == ("risk", "rolling")   # short_horizon is daily


def test_cpi_lookup_dates_moves_each_date_to_its_own_month_end_only_when_on(switch):
    d = pd.Series(pd.to_datetime(["2016-01-29", "2016-02-29", "2016-03-31", "2016-04-15"]))
    assert list(deflation_alignment.cpi_lookup_dates(d, align=False)) == list(d)
    assert list(deflation_alignment.cpi_lookup_dates(d, align=True)) == list(pd.to_datetime(["2016-01-31", "2016-02-29", "2016-03-31", "2016-04-30"]))
    switch(False)
    assert list(deflation_alignment.cpi_lookup_dates(d)) == list(d)
    switch(True)
    assert deflation_alignment.cpi_lookup_dates(d).iloc[0] == pd.Timestamp("2016-01-31")


# ---------------------------------------------------------------- the legacy defect, pinned
def test_legacy_lookup_gives_a_business_day_month_end_the_previous_months_cpi(switch):
    """The defect: 2016-01-29 (01-31 was a Sunday) takes December's CPI, so January's real return == nominal."""
    switch(False)
    nav = pd.DataFrame({"date": pd.to_datetime(["2015-12-31", "2016-01-29", "2016-02-29"]), "nav": [100.0, 90.0, 95.0]})
    ipc = pd.DataFrame({"date": pd.to_datetime(["2015-11-30", "2015-12-31", "2016-01-31", "2016-02-29"]), "ipc_index": [99.0, 100.0, 98.0, 99.0]})
    real = deflate_nav(nav, ipc)["nav_real"].pct_change()
    assert real.iloc[1] == pytest.approx(90.0 / 100.0 - 1)                      # January: no deflation at all (CPI Dec -> Dec)
    assert real.iloc[2] == pytest.approx((95.0 / 90.0) / (99.0 / 100.0) - 1)    # February absorbs the two months


# ---------------------------------------------------------------- ON: every month gets its own CPI
@pytest.mark.parametrize("seed", range(8))
def test_on_every_monthly_real_return_is_nominal_deflated_by_that_months_cpi(switch, seed):
    switch(True)
    nav, ends = _last_business_day_navs(60, seed)
    ipc = _ipc(ends, seed + 100)
    real = deflate_nav(nav, ipc)["nav_real"].pct_change().iloc[1:].to_numpy()
    nom = nav["nav"].pct_change().iloc[1:].to_numpy()
    cpi = ipc.set_index("date")["ipc_index"].reindex(ends).to_numpy()
    expected = (1 + nom) / (cpi[1:] / cpi[:-1]) - 1
    np.testing.assert_allclose(real, expected, rtol=0, atol=1e-12)


@pytest.mark.parametrize("seed", range(4))
def test_on_equals_off_when_every_nav_is_already_a_calendar_month_end(switch, seed):
    nav, ends = _last_business_day_navs(48, seed)
    nav["date"] = ends
    ipc = _ipc(ends, seed + 7)
    switch(False)
    off = deflate_nav(nav, ipc)
    switch(True)
    on = deflate_nav(nav, ipc)
    pd.testing.assert_frame_equal(off, on)


def test_off_is_the_stored_behaviour_bit_for_bit(switch):
    """The off path must equal the pre-FND-0241 implementation (merge_asof on the raw date)."""
    switch(False)
    nav, ends = _last_business_day_navs(60, 3)
    ipc = _ipc(ends, 9)
    merged = pd.merge_asof(nav.sort_values("date"), ipc.sort_values("date"), on="date", direction="backward")
    legacy = merged["nav"].to_numpy() / (merged["ipc_index"].to_numpy() / merged["ipc_index"].iloc[0])
    np.testing.assert_array_equal(deflate_nav(nav, ipc)["nav_real"].to_numpy(), legacy)


def test_explicit_false_overrides_the_switch_for_daily_series(switch):
    switch(True)
    nav, ends = _last_business_day_navs(30, 1)
    ipc = _ipc(ends, 2)
    switch(False)
    off = deflate_nav(nav, ipc)
    switch(True)
    pd.testing.assert_frame_equal(deflate_nav(nav, ipc, align_month_end=False), off)


def test_short_horizon_daily_series_ignores_the_switch(switch):
    days = pd.bdate_range("2024-01-02", periods=140)
    nav = pd.DataFrame({"date": days, "nav": 100.0 * np.cumprod(1 + np.random.default_rng(0).normal(0.0003, 0.004, len(days)))})
    ipc = pd.DataFrame({"date": pd.date_range("2023-10-31", periods=12, freq="ME"), "ipc_index": 100.0 * 1.002 ** np.arange(12)})
    switch(False)
    off = compute_short_horizon_metrics(nav, ipc)
    switch(True)
    assert compute_short_horizon_metrics(nav, ipc) == off


# ---------------------------------------------------------------- the stored metric that exposed it
def test_worst_month_real_is_deflated_when_the_worst_month_ends_on_a_business_day(switch):
    """Jan-2016: NAV 01-29. Legacy real == nominal for that month; aligned, it is deflated by January's CPI move."""
    nav = pd.DataFrame({"date": pd.to_datetime(["2015-12-31", "2016-01-29", "2016-02-29", "2016-03-31"]), "nav": [100.0, 88.0, 90.0, 91.0]})
    ipc = pd.DataFrame({"date": pd.to_datetime(["2015-11-30", "2015-12-31", "2016-01-31", "2016-02-29", "2016-03-31"]),
                        "ipc_index": [100.0, 100.0, 102.0, 102.5, 103.0]})
    worst = lambda: dict(((m, f), v) for m, v, f in consistency_metrics(nav, ipc))[("worst_month", 1)]
    switch(False)
    assert worst() == pytest.approx(88.0 / 100.0 - 1)
    switch(True)
    assert worst() == pytest.approx((88.0 / 100.0) / (102.0 / 100.0) - 1)


# ---------------------------------------------------------------- the audit mirror follows the producer
@pytest.mark.parametrize("on", [False, True])
def test_audit_mirror_attaches_the_same_cpi_deflate_nav_uses(switch, on):
    switch(on)
    nav, ends = _last_business_day_navs(60, 5)
    ipc = _ipc(ends, 11)
    produced = deflate_nav(nav, ipc)
    implied_cpi = (nav["nav"] / produced["nav_real"]).to_numpy()          # = ipc / ipc_base
    mirror = nav_with_ipc(nav.assign(isin="X"), ipc)
    mirrored = mirror["ipc_index"].to_numpy() / mirror["ipc_index"].iloc[0]
    np.testing.assert_allclose(mirrored, implied_cpi, rtol=0, atol=1e-12)
