# proyecto2/tests/calculations/test_risk_metrics.py
# -*- coding: utf-8 -*-
"""
Tests para risk_metrics.py -- el orquestador del path escalar (fund_metrics).

Cumple R-7: sin importar pipeline.py ni core.io.

Pin del default risk_free_rate_ann (P0, 2026-09-15): antes de este test,
risk_metrics.py:31 duplicaba RISK_FREE_RATE_ANN como literal 0.04 en lugar
de importarlo desde shared.config -- un shadow silencioso sin impacto en
produccion (run_pipeline.py siempre pasa el valor explicitamente) pero que
diverge en silencio si RISK_FREE_RATE_ANN cambia. Este test fija que el
default coincide con la fuente unica tras eliminar el literal.
"""

import inspect
import math

import numpy as np
import pandas as pd
import pytest

from shared.config import RISK_FREE_RATE_ANN
from src.calculations.risk_metrics import compute_risk_metrics
from src.calculations.returns import (
    annualized_return,
    annualized_volatility,
    sharpe_ratio,
    sortino_ratio,
)


def _nav_df(navs: list[float], start: str = "2020-01-31") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(navs), freq="ME")
    return pd.DataFrame({"date": dates, "nav": navs})


def _synthetic_navs(n: int = 40, seed: int = 7) -> list[float]:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.004, 0.025, n)
    return list(100.0 * np.cumprod(1 + rets))


class TestDefaultRiskFreeRate:
    def test_default_matches_config(self):
        default = inspect.signature(compute_risk_metrics).parameters["risk_free_rate_ann"].default
        assert default == pytest.approx(RISK_FREE_RATE_ANN)


class TestComputeRiskMetricsScalarPath:
    def _metrics_dict(self, df: pd.DataFrame, real_flag: int = 0) -> dict:
        sub = df[df["real_flag"] == real_flag]
        return dict(zip(sub["metric"], sub["value"]))

    def test_nominal_metrics_match_canonical_returns_functions(self):
        navs = _synthetic_navs()
        nav_df = _nav_df(navs)
        rfr = 0.04
        result = compute_risk_metrics(nav_df, ipc_df=None, risk_free_rate_ann=rfr)
        m = self._metrics_dict(result, real_flag=0)

        nav_series = nav_df["nav"]
        assert m["return_ann"] == pytest.approx(annualized_return(nav_series), rel=1e-9)
        assert m["vol_ann"] == pytest.approx(annualized_volatility(nav_series), rel=1e-9)
        assert m["sharpe"] == pytest.approx(sharpe_ratio(nav_series, rfr), rel=1e-9)
        assert m["sortino"] == pytest.approx(sortino_ratio(nav_series, rfr), rel=1e-9)

    def test_returns_expected_columns(self):
        nav_df = _nav_df(_synthetic_navs())
        result = compute_risk_metrics(nav_df, ipc_df=None, risk_free_rate_ann=0.04)
        assert set(result.columns) >= {"metric", "value", "real_flag"}

    def test_no_ipc_df_emits_only_nominal(self):
        nav_df = _nav_df(_synthetic_navs())
        result = compute_risk_metrics(nav_df, ipc_df=None, risk_free_rate_ann=0.04)
        assert set(result["real_flag"].unique()) == {0}

    def test_nominal_metric_names_present(self):
        nav_df = _nav_df(_synthetic_navs())
        result = compute_risk_metrics(nav_df, ipc_df=None, risk_free_rate_ann=0.04)
        names = set(result.loc[result["real_flag"] == 0, "metric"])
        expected = {
            "max_dd", "drawdown_duration", "time_to_recovery",
            "return_ann", "vol_ann", "sharpe", "sortino", "ret_vol_simple",
            "srri_nav", "srri_volatility",
        }
        assert expected <= names

    def test_uses_default_when_rfr_not_passed(self):
        """Calling without risk_free_rate_ann must fall back to RISK_FREE_RATE_ANN,
        matching the explicit-rfr call -- guards the default wiring itself,
        not just its declared value."""
        nav_df = _nav_df(_synthetic_navs())
        result_default = compute_risk_metrics(nav_df, ipc_df=None)
        result_explicit = compute_risk_metrics(
            nav_df, ipc_df=None, risk_free_rate_ann=RISK_FREE_RATE_ANN
        )
        m_default = self._metrics_dict(result_default, real_flag=0)
        m_explicit = self._metrics_dict(result_explicit, real_flag=0)
        assert m_default["sharpe"] == pytest.approx(m_explicit["sharpe"], rel=1e-12)
        assert m_default["sortino"] == pytest.approx(m_explicit["sortino"], rel=1e-12)
