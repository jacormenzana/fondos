# proyecto1/tests/test_shadow_reconciliation.py
# -*- coding: utf-8 -*-
"""Tests unitarios de scripts/audit/shadow_reconciliation.py (FND-0137 Section D — Shadow
Reconciliation). Solo las funciones puras: las formulas clean-room y la comparacion
shadow-vs-produccion. Nada aqui toca una conexion a BD (R-7: sin pipeline.py ni core.io).

Los valores esperados de los dos fixtures NAV (`_DEGENERATE_NAV`, `_DRAWDOWN_NAV`) se derivaron
independientemente a mano (ver el commit que introdujo este archivo) y se confirmaron ejecutando
las propias funciones bajo prueba -- no se importan proyecto2.src.calculations.returns/drawdown
para "verificar" (eso solo probaria que este modulo coincide consigo mismo).
"""

import os
import sys

import numpy as np
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

import pandas as pd

from scripts.audit.shadow_reconciliation import (
    compute_shadow_metrics,
    reconcile_metrics,
    shadow_max_dd,
    shadow_monthly_returns,
    shadow_resolve_rf_rate,
    shadow_return_ann,
    shadow_sharpe,
    shadow_sortino,
    shadow_vol_ann,
)

_RF = 0.04

# 3 monthly NAV points, +10% each month, zero dispersion -- exercises the vol_ann==0 ->
# sharpe/sortino==NaN degenerate branches.
_DEGENERATE_NAV = np.array([100.0, 110.0, 121.0])

# 4 monthly NAV points with one down-month (a genuine peak-to-trough drawdown) -- exercises every
# branch (nonzero vol, nonzero downside deviation, a real max_dd).
_DRAWDOWN_NAV = np.array([100.0, 90.0, 99.0, 108.0])


class TestCleanRoomFormulas:
    def test_return_ann_geometric_annualization(self):
        # total = 121/100 = 1.21 over 3/12 years -> 1.21^4 - 1
        assert shadow_return_ann(_DEGENERATE_NAV) == pytest.approx(1.21 ** 4 - 1, rel=1e-9)

    def test_vol_ann_zero_on_constant_returns(self):
        # both periodic returns are exactly +10% -> sample stdev is exactly 0
        assert shadow_vol_ann(_DEGENERATE_NAV) == pytest.approx(0.0, abs=1e-12)

    def test_max_dd_zero_on_a_monotonic_series(self):
        assert shadow_max_dd(_DEGENERATE_NAV) == pytest.approx(0.0, abs=1e-12)

    def test_sharpe_and_sortino_nan_when_dispersion_is_zero(self):
        assert np.isnan(shadow_sharpe(_DEGENERATE_NAV, _RF))
        assert np.isnan(shadow_sortino(_DEGENERATE_NAV, _RF))

    def test_max_dd_captures_the_actual_trough(self):
        # nav drops 100 -> 90 before recovering -- the running peak stays at 100 through that
        # trough, so max_dd is exactly -10%, not the smaller dip from the (lower) later peak.
        assert shadow_max_dd(_DRAWDOWN_NAV) == pytest.approx(-0.10, rel=1e-9)

    def test_return_ann_vol_ann_sharpe_sortino_on_the_drawdown_fixture(self):
        # Values below were printed by running compute_shadow_metrics() itself on this fixture
        # and hand-verified against the documented formulas (returns.py's spec, not its code) --
        # see this file's module docstring.
        m = compute_shadow_metrics(_DRAWDOWN_NAV, risk_free_rate_ann=_RF)
        assert m["return_ann"] == pytest.approx(0.259712, rel=1e-5)
        assert m["vol_ann"] == pytest.approx(0.391226, rel=1e-5)
        assert m["max_dd"] == pytest.approx(-0.10, rel=1e-5)
        assert m["sharpe"] == pytest.approx(0.561599, rel=1e-5)
        assert m["sortino"] == pytest.approx(1.063123, rel=1e-5)

    def test_monthly_returns_is_simple_pct_change(self):
        rets = shadow_monthly_returns(_DRAWDOWN_NAV)
        assert rets == pytest.approx([-0.10, 0.10, 108.0 / 99.0 - 1.0], rel=1e-9)

    def test_too_few_observations_returns_nan_not_an_exception(self):
        one_point = np.array([100.0])
        m = compute_shadow_metrics(one_point, risk_free_rate_ann=_RF)
        assert all(np.isnan(v) for v in m.values())


class TestResolveRfRate:
    def test_falls_back_to_the_scalar_when_no_series_available(self):
        assert shadow_resolve_rf_rate("2020-06-30", None, 0.04) == 0.04
        assert shadow_resolve_rf_rate("2020-06-30", pd.DataFrame(columns=["date", "rate"]), 0.04) == 0.04

    def test_resolves_the_exact_month_end_match(self):
        rf = pd.DataFrame({
            "date": pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"]),
            "rate": [0.01, 0.02, 0.03],
        })
        assert shadow_resolve_rf_rate("2020-02-29", rf, 0.04) == pytest.approx(0.02)

    def test_forward_fills_a_date_between_two_known_points(self):
        rf = pd.DataFrame({
            "date": pd.to_datetime(["2020-01-31", "2020-03-31"]),
            "rate": [0.01, 0.03],
        })
        # 2020-02-15 has no exact match; the last KNOWN (Jan) rate carries forward -- this is the
        # "genuinely covered, just not exact-date-aligned" case, not the leading-gap case below.
        assert shadow_resolve_rf_rate("2020-02-15", rf, 0.04) == pytest.approx(0.01)

    def test_leading_date_before_any_coverage_falls_back_to_the_earliest_known_value(self):
        rf = pd.DataFrame({
            "date": pd.to_datetime(["2020-06-30", "2020-07-31"]),
            "rate": [0.05, 0.06],
        })
        assert shadow_resolve_rf_rate("2019-01-31", rf, 0.04) == pytest.approx(0.05)


class TestReconcileMetrics:
    def test_matching_values_within_tolerance(self):
        shadow = {"return_ann": 0.05, "vol_ann": 0.10, "max_dd": -0.2, "sharpe": 0.5, "sortino": 0.6}
        prod = dict(shadow)  # identical
        rows = reconcile_metrics(shadow, prod)
        assert all(r["verdict"] == "match" for r in rows)

    def test_a_real_divergence_is_flagged(self):
        shadow = {"return_ann": 0.05, "vol_ann": 0.10, "max_dd": -0.2, "sharpe": 0.5, "sortino": 0.6}
        prod = dict(shadow)
        prod["sharpe"] = 0.5 + 0.01   # 2% relative gap, well past a 1e-6 tolerance
        rows = {r["metric"]: r for r in reconcile_metrics(shadow, prod)}
        assert rows["sharpe"]["verdict"] == "DIVERGE"
        assert rows["return_ann"]["verdict"] == "match"

    def test_nan_on_one_side_is_its_own_verdict_not_silently_skipped(self):
        shadow = {"return_ann": float("nan"), "vol_ann": 0.10, "max_dd": -0.2,
                  "sharpe": 0.7, "sortino": float("nan")}
        # sharpe/sortino intentionally absent from prod -- exercises "prod has no value at all"
        # (distinct from "prod computed NaN") without contradicting the both_nan case below.
        prod = {"return_ann": 0.05, "vol_ann": 0.10, "max_dd": -0.2}
        rows = {r["metric"]: r for r in reconcile_metrics(shadow, prod)}
        assert rows["return_ann"]["verdict"] == "shadow_nan_prod_has_value"
        assert rows["sharpe"]["verdict"] == "prod_nan_shadow_has_value"
        assert rows["sortino"]["verdict"] == "both_nan"  # shadow NaN AND prod absent
        assert rows["vol_ann"]["verdict"] == "match"

    def test_nan_on_both_sides_is_both_nan_not_a_match(self):
        shadow = {"sharpe": float("nan")}
        prod = {"sharpe": float("nan")}
        rows = reconcile_metrics(shadow, prod)
        sharpe_row = next(r for r in rows if r["metric"] == "sharpe")
        assert sharpe_row["verdict"] == "both_nan"

    def test_tolerance_is_relative_not_a_bare_absolute_epsilon(self):
        # A 1e-6 absolute epsilon would wrongly flag two large, genuinely-equal-within-precision
        # values as diverging; the relative component must scale with magnitude.
        shadow = {"return_ann": 123.456789}
        prod = {"return_ann": 123.456789 + 1e-4}   # ~8e-7 relative -- should still match
        rows = reconcile_metrics(shadow, prod, rel_tol=1e-6, abs_tol=1e-9)
        assert rows[0]["verdict"] == "match"
