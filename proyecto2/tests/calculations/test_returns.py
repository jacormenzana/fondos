# proyecto2/tests/calculations/test_returns.py
# -*- coding: utf-8 -*-
"""
Tests para returns.py -- la capa canonica de estadisticas P2.

Cumple R-7: sin importar pipeline.py ni core.io.

Hasta este fichero, returns.py (capa canonica compartida por el path escalar
y el path rolling desde 2b45416) no tenia ningun test propio -- solo se
cubria de forma transitiva via la paridad escalar/rolling en
test_rolling_stats.py::TestSortinoScalarRollingAgree. Estos tests fijan el
comportamiento exacto de cada formula ANTES de canonicalizar
regime_returns.py sobre esta capa (P0, 2026-09-15), incluyendo guardas de
regresion explicitas frente a las formulas divergentes que regime_returns.py
usaba (subconjunto negativo con ddof=1 en vez de semi-varianza poblacional
sobre todos los periodos; media de exceso / std de exceso en vez de
(ret_ann - rfr) / vol_ann).
"""

import math

import numpy as np
import pandas as pd
import pytest

from src.calculations.returns import (
    monthly_returns,
    annualized_return,
    annualized_volatility,
    sharpe_ratio,
    downside_deviation_ann,
    sortino_ratio,
    annualized_return_from_returns,
    annualized_volatility_from_returns,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
)


def _nav(navs: list[float], start: str = "2020-01-31") -> pd.Series:
    idx = pd.date_range(start=start, periods=len(navs), freq="ME")
    return pd.Series(navs, index=idx)


# Vector NAV compartido: 5 retornos [0.02, -0.03, 0.01, -0.01, 0.00]
_NAVS = [100.0, 102.0, 98.94, 99.9294, 98.930106, 98.930106]


class TestMonthlyReturns:
    def test_simple_pct_change(self):
        r = monthly_returns(_nav([100.0, 110.0, 99.0]))
        assert list(np.round(r.to_numpy(), 10)) == [0.10, -0.10]


class TestAnnualizedReturn:
    def test_matches_geometric_formula(self):
        s = _nav(_NAVS)
        result = annualized_return(s)
        total = _NAVS[-1] / _NAVS[0]
        years = len(_NAVS) / 12
        expected = total ** (1.0 / years) - 1.0
        assert result == pytest.approx(expected, rel=1e-9)

    def test_too_short_returns_nan(self):
        assert math.isnan(annualized_return(_nav([100.0])))


class TestAnnualizedVolatility:
    def test_matches_std_formula(self):
        s = _nav(_NAVS)
        result = annualized_volatility(s)
        r = pd.Series(_NAVS).pct_change().dropna()
        expected = r.std(ddof=1) * math.sqrt(12)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_flat_series_zero_vol(self):
        assert annualized_volatility(_nav([100.0] * 6)) == pytest.approx(0.0, abs=1e-12)


class TestSharpeRatio:
    def test_matches_ret_minus_rf_over_vol(self):
        s = _nav(_NAVS)
        rfr = 0.04
        result = sharpe_ratio(s, rfr)
        expected = (annualized_return(s) - rfr) / annualized_volatility(s)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_not_mean_excess_over_std_excess(self):
        """Regression guard: sharpe_ratio must NOT equal
        mean(excess)/std(excess)*sqrt(12) -- that was regime_returns.py's
        divergent local formula (P0, removed 2026-09-15)."""
        s = _nav(_NAVS)
        rfr = 0.04
        canonical = sharpe_ratio(s, rfr)
        r = pd.Series(_NAVS).pct_change().dropna()
        excess = r - rfr / 12
        wrong = (excess.mean() / excess.std(ddof=1)) * math.sqrt(12)
        assert canonical != pytest.approx(wrong, rel=1e-6)


class TestDownsideDeviationAnn:
    def test_semi_variance_over_all_periods(self):
        """Population semi-variance over ALL periods, zero for non-breaching --
        the canonical Sortino denominator (root-caused 2026-09-14, ISIN
        BE0058182792)."""
        rets = np.array([0.02, -0.03, 0.01, -0.01, 0.00])
        result = downside_deviation_ann(rets, mar_per_period=0.0, periods_per_year=12)
        downside_sq = np.where(rets < 0.0, rets ** 2, 0.0)
        expected = math.sqrt(downside_sq.mean()) * math.sqrt(12)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_not_sample_std_of_negative_subset(self):
        """Regression guard: must NOT equal np.std(negatives, ddof=1)*sqrt(12)
        -- that was regime_returns.py's divergent local Sortino denominator
        (subset-only, ddof=1) removed in P0 (2026-09-15). Vector chosen with
        asymmetric negative magnitudes so the two quantities are not
        numerically coincident."""
        rets = np.array([0.03, -0.08, 0.01, -0.01, 0.00, 0.02])
        result = downside_deviation_ann(rets, mar_per_period=0.0, periods_per_year=12)
        negatives = rets[rets < 0.0]
        wrong = np.std(negatives, ddof=1) * math.sqrt(12)
        assert result != pytest.approx(wrong, rel=1e-6)


class TestSortinoRatio:
    def test_matches_ret_minus_rf_over_downside(self):
        s = _nav(_NAVS)
        rfr = 0.04
        result = sortino_ratio(s, rfr)
        r = pd.Series(_NAVS).pct_change().dropna()
        dd = downside_deviation_ann(r.to_numpy(), rfr / 12, periods_per_year=12)
        expected = (annualized_return(s) - rfr) / dd
        assert result == pytest.approx(expected, rel=1e-9)


class TestFromReturnsVariants:
    """*_from_returns operate on a bag of periodic returns rather than a
    NAV-level series -- needed for regime_returns.py, whose per-regime sample
    is a scattered, non-contiguous subset of months (no valid NAV series to
    reconstruct). Same formulas as the *_ratio series-based siblings; the
    Sortino variant reuses downside_deviation_ann() directly (zero
    duplication)."""

    def test_annualized_return_from_returns_geometric_compounding(self):
        r = np.array([0.02, -0.03, 0.01, -0.01, 0.00])
        result = annualized_return_from_returns(r, periods_per_year=12)
        total = np.prod(1.0 + r)
        expected = total ** (12 / len(r)) - 1.0
        assert result == pytest.approx(expected, rel=1e-9)

    def test_annualized_return_from_returns_nan_below_min_length(self):
        assert math.isnan(annualized_return_from_returns(np.array([]), periods_per_year=12))

    def test_annualized_volatility_from_returns(self):
        r = np.array([0.02, -0.03, 0.01, -0.01, 0.00])
        result = annualized_volatility_from_returns(r, periods_per_year=12)
        expected = r.std(ddof=1) * math.sqrt(12)
        assert result == pytest.approx(expected, rel=1e-9)

    def test_sharpe_ratio_from_returns_matches_composition(self):
        r = np.array([0.02, -0.03, 0.01, -0.01, 0.00])
        rfr = 0.04
        result = sharpe_ratio_from_returns(r, rfr, periods_per_year=12)
        ret_ann = annualized_return_from_returns(r, periods_per_year=12)
        vol_ann = annualized_volatility_from_returns(r, periods_per_year=12)
        expected = (ret_ann - rfr) / vol_ann
        assert result == pytest.approx(expected, rel=1e-9)

    def test_sortino_ratio_from_returns_reuses_downside_deviation_ann(self):
        r = np.array([0.02, -0.03, 0.01, -0.01, 0.00])
        rfr = 0.04
        result = sortino_ratio_from_returns(r, rfr, periods_per_year=12)
        ret_ann = annualized_return_from_returns(r, periods_per_year=12)
        dd = downside_deviation_ann(r, rfr / 12, periods_per_year=12)
        expected = (ret_ann - rfr) / dd
        assert result == pytest.approx(expected, rel=1e-9)

    def test_sortino_ratio_from_returns_nan_when_no_downside(self):
        r = np.full(12, 0.01)
        result = sortino_ratio_from_returns(r, risk_free_rate_ann=0.0, periods_per_year=12)
        assert math.isnan(result)

    def test_sharpe_ratio_from_returns_nan_on_zero_vol(self):
        r = np.zeros(6)
        result = sharpe_ratio_from_returns(r, risk_free_rate_ann=0.04, periods_per_year=12)
        assert math.isnan(result)
