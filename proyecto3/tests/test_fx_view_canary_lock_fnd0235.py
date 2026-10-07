# proyecto3/tests/test_fx_view_canary_lock_fnd0235.py
# -*- coding: utf-8 -*-
"""FND-0235: P3 refuses to score when fx_contribution_ann is still the legacy (opposite-sign) metric while FX_CONTRIBUTION_EUR_VIEW_ENABLED is on.

A presence check cannot tell the two apart (same metric name), so the lock recomputes the EUR-view value for a canary sample and compares it
with what P2 stored. R-7: fake conn / fake series, no DB.
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto2.src.calculations import currency_factor as cf
from proyecto3.src import data_freshness as df
from shared import config


@pytest.fixture
def switch(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", on, raising=False)


# ---------------------------------------------------------------- the pure verdict
def _pairs(stored_sign=1.0, n=20, noise=0.0005, weak=0):
    rng = np.random.default_rng(0)
    r = rng.uniform(0.012, 0.03, n) * rng.choice([-1, 1], n)
    out = [(f"S{i}", float(stored_sign * x + rng.normal(0, noise)), float(x)) for i, x in enumerate(r)]
    return out + [(f"W{i}", 0.001, -0.001) for i in range(weak)]


def test_eur_view_values_pass_even_with_small_drift_from_new_navs():
    c = df.fx_view_canary_check(_pairs(+1.0, noise=0.001))
    assert c.ok and c.name == "fx_view_canary" and not c.show_age


def test_legacy_opposite_sign_values_fail_with_an_actionable_message():
    c = df.fx_view_canary_check(_pairs(-1.0))
    assert not c.ok and "legacy" in c.detail and "BEFORE P3" in c.detail


def test_weak_funds_are_not_canaries():
    """|fx| < 1 pp: the legacy sign flip is within the tolerance, so such funds prove nothing either way."""
    assert df.fx_view_canary_check(_pairs(+1.0, n=10, weak=50)).ok
    c = df.fx_view_canary_check(_pairs(+1.0, n=3, weak=100))
    assert not c.ok and "inconclusive" in c.detail                       # fail closed


def test_missing_stored_values_count_as_mismatches():
    pairs = [(f"S{i}", None, 0.02) for i in range(12)]
    assert not df.fx_view_canary_check(pairs).ok


def test_a_few_stale_funds_are_tolerated_but_not_many():
    good = _pairs(+1.0, n=20)
    assert df.fx_view_canary_check(good[:-1] + [(good[-1][0], -good[-1][1], good[-1][2])]).ok           # 1 of 20
    bad = [(i, -s, r) for i, s, r in good[:6]] + good[6:]
    assert not df.fx_view_canary_check(bad).ok                                                       # 6 of 20


# ---------------------------------------------------------------- end to end on synthetic series (stored by P2, recomputed by P3)
N = 60
IDX = pd.date_range("2019-01-31", periods=N, freq="ME")


class _Conn:
    """candidates query -> rows; NAV query -> that fund's series."""
    def __init__(self, funds, stored):
        self.funds, self.stored = funds, stored

    def execute(self, sql, params=None):
        outer = self
        class R:
            def fetchall(self_inner):
                if "FROM fund_metrics" in sql:
                    return [(i, "EUR", None, "USD", outer.stored[i]) for i in outer.funds]
                nav = outer.funds[params[0]]
                return list(zip(IDX.to_pydatetime(), nav))
        return R()


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(cf, "load_fx_eur_divisa", lambda conn, ccy: pd.Series(1.0 * np.exp(0.008 * np.arange(N)), index=IDX, name="fx"))
    funds = {f"F{i}": list(100.0 * np.exp(np.cumsum(np.full(N, -0.003 + 0.0004 * i)))) for i in range(12)}
    return funds


def _store(monkeypatch, funds, eur_view):
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", eur_view, raising=False)
    out = {}
    for isin, nav in funds.items():
        nav_df = pd.DataFrame({"date": IDX, "nav": nav})
        out[isin] = {m: v for m, v, _ in cf.compute_currency_factor(isin, "EUR", None, nav_df, None, asset_currency="USD")}["fx_contribution_ann"]
    return out


def test_p3_lock_rejects_a_legacy_store_and_accepts_a_recomputed_one(monkeypatch, world):
    legacy = _store(monkeypatch, world, eur_view=False)
    new = _store(monkeypatch, world, eur_view=True)
    assert any(abs(legacy[i] - new[i]) > 0.05 for i in world)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", True, raising=False)       # the flag P3 runs under
    assert not df.fx_view_canary_check(df.load_fx_canary_pairs(_Conn(world, legacy)), min_canaries=8).ok
    assert df.fx_view_canary_check(df.load_fx_canary_pairs(_Conn(world, new)), min_canaries=8).ok


# ---------------------------------------------------------------- wiring into the gate
class _Clf:
    def input_last_dates(self):
        return {}


def test_the_gate_adds_the_canary_only_when_the_flag_is_on(monkeypatch, switch):
    monkeypatch.setattr(df, "load_freshness_inputs", lambda conn: ([], None, []))
    monkeypatch.setattr(df, "evaluate_freshness", lambda *a, **k: [])
    seen = []
    monkeypatch.setattr(df, "load_fx_canary_pairs", lambda conn: seen.append(1) or [])
    switch(False)
    assert df.check_universe_freshness(None, _Clf(), today=date(2026, 10, 7)) == [] and not seen
    switch(True)
    checks = df.check_universe_freshness(None, _Clf(), today=date(2026, 10, 7))
    assert [c.name for c in checks] == ["fx_view_canary"] and seen and not checks[0].ok               # no canaries -> fail closed
    assert df.stale_checks(checks) == checks
