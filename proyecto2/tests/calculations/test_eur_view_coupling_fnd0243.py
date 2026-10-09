"""FND-0243 step 4: the EUR NAV view and the FND-0235 FX view are released together (owner decision D4).

- With EUR_NAV_CONVERSION_ENABLED the NAV reaching currency_factor is already in EUR: the FX view must not convert the class
  currency a second time (the exposure -- asset currency -- is unchanged).
- Every P2/P3 entry point refuses an inconsistent switch state BEFORE opening a connection.
- The switch is a P2 bundle flag on every metric family: enabling it recomputes every fund (EUR funds' peer metrics change too).
Fake series / stubs instead of a DB (R-7).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from shared import config
from shared import eur_nav
from src.calculations import currency_factor as cf
from src.utils import family_versions
from src.utils.fingerprint import effective_calc_version

_REPO = Path(__file__).resolve().parents[3]

N = 60
IDX = pd.date_range("2019-01-31", periods=N, freq="ME")


@pytest.fixture
def flags(monkeypatch):
    def _set(eur_nav_on, fx_view_on, first_last=True):
        monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", eur_nav_on, raising=False)
        monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", fx_view_on, raising=False)
        monkeypatch.setattr(config, "PERSISTENCE_FIRST_LAST_NAV_ENABLED", first_last, raising=False)
    return _set


# ---------------- the coupling rule (pure) ----------------

@pytest.mark.parametrize("eur_on, fx_on, first_last, ok", [
    (False, False, True, True),     # shipped state
    (True, True, True, True),       # the release
    (True, False, True, False),     # EUR NAV alone
    (False, True, True, False),     # FX view alone
    (True, True, False, False),     # EUR view with the legacy MIN/MAX peer return
    (False, False, False, True),    # MIN/MAX is fine while the EUR view is off
])
def test_release_coupling_rule(flags, eur_on, fx_on, first_last, ok):
    flags(eur_on, fx_on, first_last)
    assert (eur_nav.release_coupling_errors() == []) is ok
    if ok:
        eur_nav.assert_release_coupling()
    else:
        with pytest.raises(eur_nav.ReleaseCouplingError, match="FND-0243"):
            eur_nav.assert_release_coupling()


def test_shipped_defaults_are_consistent():
    assert eur_nav.release_coupling_errors(config) == []


# ---------------- no double conversion in the FX view ----------------

@pytest.fixture
def rates(monkeypatch):
    series = {"USD": pd.Series(1.0 * np.exp(0.008 * np.arange(N)), index=IDX, name="fx")}
    monkeypatch.setattr(cf, "load_fx_eur_divisa", lambda conn, ccy: series.get(ccy.upper()))
    return series


def _nav(monthly_log_return):
    return pd.DataFrame({"date": IDX, "nav": 100 * np.exp(monthly_log_return * np.arange(N))})


def test_with_eur_nav_the_fx_view_treats_a_usd_class_as_already_in_eur(flags, rates):
    nav_eur = _nav(0.004)                     # what load_nav returns once converted
    flags(True, True)
    usd_class = dict((m, v) for m, v, _ in cf.compute_currency_factor("X", "USD", None, nav_eur, None, asset_currency="USD"))
    eur_class = dict((m, v) for m, v, _ in cf.compute_currency_factor("X", "EUR", None, nav_eur, None, asset_currency="USD"))
    assert usd_class == pytest.approx(eur_class)
    # total in EUR = the NAV's own return (no second conversion): (1 + 0.004) ** 12 - 1
    ratio = usd_class["fx_contribution_ann"] / usd_class["fx_contribution_pct"]
    assert ratio == pytest.approx((1 + 0.004) ** 12 - 1)


def test_without_eur_nav_the_fx_view_still_converts_the_class_currency(flags, rates):
    # FND-0235 alone (pre-release behaviour kept for its own tests): a USD class NAV is converted inside the FX view.
    flags(False, True)
    nav_usd = _nav(0.004)
    out = dict((m, v) for m, v, _ in cf.compute_currency_factor("X", "USD", None, nav_usd, None, asset_currency="USD"))
    total = out["fx_contribution_ann"] / out["fx_contribution_pct"]
    assert total == pytest.approx((1 + 0.004 - 0.008) ** 12 - 1)


# ---------------- recompute scope ----------------

def test_eur_view_is_a_bundle_flag_on_every_family(flags):
    assert "EUR_NAV_CONVERSION_ENABLED" in config.P2_BUNDLE_FLAGS
    assert set(family_versions.flag_families("EUR_NAV_CONVERSION_ENABLED")) == set(family_versions.ALL_FAMILIES)
    flags(False, False)
    off = effective_calc_version("20261004")
    flags(True, True)
    assert effective_calc_version("20261004") != off


# ---------------- entry points refuse before connecting ----------------

def _explode(*a, **k):
    raise AssertionError("a connection was opened despite an inconsistent switch state")


def test_p2_run_refuses_before_connecting(flags, monkeypatch):
    import src.pipeline.run_pipeline as rp
    flags(True, False)
    monkeypatch.setattr(rp, "get_connection", _explode)
    # run() logs the refusal as a fatal error and returns its ERROR code (2); _explode proves no connection was opened.
    assert rp.run(dry_run=True) == 2


def test_p3_build_refuses_before_connecting(flags, monkeypatch):
    sys.path.insert(0, str(_REPO / "scripts" / "launch"))
    import p3_build_portfolio as p3b
    flags(False, True)
    monkeypatch.setattr(p3b, "get_connection", _explode)
    with pytest.raises(eur_nav.ReleaseCouplingError):
        p3b.main(dry_run=True)


def test_pit_backtest_checks_after_its_flag_overrides(flags, monkeypatch):
    sys.path.insert(0, str(_REPO / "scripts" / "launch"))
    import p3_pit_backtest as pit
    flags(False, False)
    monkeypatch.setattr(pit, "get_connection", _explode)
    with pytest.raises(eur_nav.ReleaseCouplingError):
        pit.main(["--flag", "FX_CONTRIBUTION_EUR_VIEW_ENABLED=1"])
    # both together is an accepted override (the release's PIT sensitivity run)
    state = pit.apply_flag_overrides(["FX_CONTRIBUTION_EUR_VIEW_ENABLED=1", "EUR_NAV_CONVERSION_ENABLED=1"])
    assert state["EUR_NAV_CONVERSION_ENABLED"] and state["FX_CONTRIBUTION_EUR_VIEW_ENABLED"]
    eur_nav.assert_release_coupling()
