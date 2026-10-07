"""FND-0235: the FX contribution as seen by a EUR investor, behind FX_CONTRIBUTION_EUR_VIEW_ENABLED.

Stored metric (off): fx = mean log change of FOREIGN UNITS PER EUR (the opposite sign of a EUR investor's contribution), divided into the
fund's own-currency return -- 579 of 711 funds are non-EUR classes whose NAV is already in the foreign currency -- and a ratio clamped to
+-5 that writes a fake 0.0 when the total is ~0. On (EUR view): signed pp, asset currency = exposure, total converted to EUR, ratio only
where defined. Fake series instead of a DB (R-7).
"""
import numpy as np
import pandas as pd
import pytest

from shared import config
from src.calculations import currency_factor as cf
from src.utils import family_versions

N = 60
IDX = pd.date_range("2019-01-31", periods=N, freq="ME")


@pytest.fixture
def switch(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", on, raising=False)


@pytest.fixture
def rates(monkeypatch):
    """USD per EUR rising 0.8% (log) a month; GBP per EUR flat; load_fx_eur_divisa replaced (no DB)."""
    series = {"USD": pd.Series(1.0 * np.exp(0.008 * np.arange(N)), index=IDX, name="fx"),
              "GBP": pd.Series(np.full(N, 0.85), index=IDX, name="fx")}
    monkeypatch.setattr(cf, "load_fx_eur_divisa", lambda conn, ccy: series.get(ccy.upper()))
    return series


def _nav(monthly_log_ret):
    return pd.DataFrame({"date": IDX, "nav": 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(np.full(N - 1, monthly_log_ret))]))})


def _run(nav, fc, ac, hp=None):
    return {m: v for m, v, _ in cf.compute_currency_factor("X", fc, hp, nav, None, asset_currency=ac)}


# ---------------------------------------------------------------- wiring
def test_switch_is_off_by_default_in_the_bundle_and_mapped_to_the_fx_family():
    assert config.FX_CONTRIBUTION_EUR_VIEW_ENABLED is False
    assert "FX_CONTRIBUTION_EUR_VIEW_ENABLED" in config.P2_BUNDLE_FLAGS
    assert family_versions.flag_families("FX_CONTRIBUTION_EUR_VIEW_ENABLED") == ("fx",)
    assert (config.FX_CONTRIBUTION_PP_LIMIT, config.FX_RATIO_MIN_TOTAL_ANN) == (0.02, 0.01)


@pytest.mark.parametrize("fc, ac, expected", [
    ("EUR", "USD", ("USD", None)), ("USD", "USD", ("USD", "USD")), ("USD", "", ("USD", "USD")), ("USD", "MCY", ("USD", "USD")),
    ("USD", "EUR", (None, "USD")), ("EUR", "EUR", (None, None)), ("EUR", "", (None, None)), ("GBP", "USD", ("USD", "GBP")),
])
def test_exposure_and_class_currencies(fc, ac, expected):
    assert cf.eur_view_currencies(fc, ac) == expected


# ---------------------------------------------------------------- the sign
def test_eur_class_holding_flat_usd_assets_loses_what_the_euro_gains(switch, rates):
    """USD assets flat in USD, EUR up 0.8%/month: the EUR NAV falls 0.8%/month and ALL of it is FX."""
    nav = _nav(-0.008)
    switch(False)
    legacy = _run(nav, "EUR", "USD")
    assert legacy["fx_contribution_ann"] > 0                     # the stored sign: a GAIN, for a loss
    switch(True)
    new = _run(nav, "EUR", "USD")
    assert new["fx_contribution_ann"] == pytest.approx(-legacy["fx_contribution_ann"] / (1 + legacy["fx_contribution_ann"]) , rel=0.02)
    assert new["fx_contribution_ann"] < -0.08
    assert new["fx_contribution_pct"] == pytest.approx(1.0, abs=1e-6)


def test_usd_class_is_converted_to_eur_before_the_ratio(switch, rates):
    """A USD class (NAV in USD) flat in USD: its own return is 0, the EUR investor loses the FX. Legacy: ratio 0.0 (undefined, written as 0)."""
    nav = _nav(0.0)
    switch(False)
    assert _run(nav, "USD", "USD")["fx_contribution_pct"] == 0.0
    switch(True)
    new = _run(nav, "USD", "USD")
    assert new["fx_contribution_ann"] < -0.08
    assert new["fx_contribution_pct"] == pytest.approx(1.0, abs=1e-6)


def test_usd_class_of_eur_assets_has_no_exposure_for_a_eur_investor(switch, rates):
    switch(False)
    assert _run(_nav(0.002), "USD", "EUR")                       # legacy: reports the class currency as exposure
    switch(True)
    assert _run(_nav(0.002), "USD", "EUR") == {}


# ---------------------------------------------------------------- the ratio
def test_ratio_is_not_written_when_the_eur_total_is_below_the_floor(switch, rates):
    """USD class whose own return offsets the FX drift: EUR total ~ 0 -> no ratio (legacy clamps a huge number), the pp figure stays."""
    switch(True)
    out = _run(_nav(0.008), "USD", "USD")
    assert "fx_contribution_pct" not in out and out["fx_contribution_ann"] < -0.08
    assert out["fx_volatility_ann"] == pytest.approx(0.0, abs=1e-9)


def test_ratio_is_clamped_when_defined(switch, rates):
    switch(True)
    out = _run(_nav(0.0), "USD", "USD")
    assert abs(out["fx_contribution_pct"]) <= config.FX_CONTRIBUTION_PCT_CLAMP


def test_gbp_flat_has_no_contribution(switch, rates):
    switch(True)
    out = _run(_nav(0.004), "GBP", "GBP")
    assert out["fx_contribution_ann"] == pytest.approx(0.0, abs=1e-9)
    assert out["fx_contribution_pct"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------- unchanged guards
@pytest.mark.parametrize("on", [False, True])
def test_hedged_and_unsupported_currencies_return_nothing(switch, rates, on):
    switch(on)
    assert _run(_nav(0.004), "USD", "USD", hp="Hedged") == {}
    assert _run(_nav(0.004), "CHF", "CHF") == {}
    assert cf.compute_currency_factor("X", "USD", None, _nav(0.004).iloc[:10], None, asset_currency="USD") == []     # < MIN_OBS


def test_off_is_the_stored_formula(switch, rates):
    """Off: fx = (1 + mean log change of USD-per-EUR)^12 - 1 over total = (1 + mean log NAV return)^12 - 1, no conversion."""
    switch(False)
    out = _run(_nav(0.003), "USD", "USD")
    fx = (1 + 0.008) ** 12 - 1
    tot = (1 + 0.003) ** 12 - 1
    assert out["fx_contribution_ann"] == pytest.approx(fx) and out["fx_contribution_pct"] == pytest.approx(fx / tot)


def test_the_pure_decomposition_is_signed_and_converts_non_eur_classes():
    d = cf.eur_view_decomposition(np.full(24, 0.004), np.full(24, 0.002), np.full(24, 0.002))
    assert d["fx_contribution_ann"] == pytest.approx((1 - 0.002) ** 12 - 1)
    assert d["total_ann"] == pytest.approx((1 + 0.002) ** 12 - 1)
    eur = cf.eur_view_decomposition(np.full(24, 0.004), np.full(24, 0.002))
    assert eur["total_ann"] == pytest.approx((1 + 0.004) ** 12 - 1)
