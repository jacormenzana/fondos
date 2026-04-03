# proyecto2/src/calculations/returns.py
# -*- coding: utf-8 -*-
"""
Cálculo de métricas de retorno y eficiencia sobre series NAV.
"""

import numpy as np
import pandas as pd


def monthly_returns(series: pd.Series) -> pd.Series:
    """Retornos mensuales simples (pct_change)."""
    return series.pct_change().dropna()


def annualized_return(series: pd.Series, periods_per_year: int = 12) -> float:
    """
    Rentabilidad anualizada geométrica.
    Requiere al menos 2 observaciones.
    """
    if len(series) < 2:
        return np.nan
    total = series.iloc[-1] / series.iloc[0]
    years = len(series) / periods_per_year
    return float(total ** (1.0 / years) - 1.0)


def annualized_volatility(series: pd.Series, periods_per_year: int = 12) -> float:
    """
    Volatilidad anualizada (desviación estándar de retornos mensuales × √12).
    """
    r = monthly_returns(series)
    if len(r) < 2:
        return np.nan
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio(
    series: pd.Series,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
) -> float:
    """
    Ratio Sharpe anualizado.
        (Rentabilidad anualizada - tipo libre de riesgo) / Volatilidad anualizada

    risk_free_rate_ann: tipo libre de riesgo anual (ej. 0.04 para 4%)
    """
    ret  = annualized_return(series, periods_per_year)
    vol  = annualized_volatility(series, periods_per_year)

    if np.isnan(ret) or np.isnan(vol) or vol == 0:
        return np.nan

    return float((ret - risk_free_rate_ann) / vol)


def sortino_ratio(
    series: pd.Series,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
) -> float:
    """
    Ratio Sortino anualizado.
        (Rentabilidad anualizada - tipo libre de riesgo) / Downside deviation anualizada

    Solo penaliza la volatilidad negativa (retornos por debajo de 0).
    """
    ret = annualized_return(series, periods_per_year)
    if np.isnan(ret):
        return np.nan

    r = monthly_returns(series)
    negative_r = r[r < 0]

    if len(negative_r) < 2:
        return np.nan

    downside_std = float(negative_r.std(ddof=1) * np.sqrt(periods_per_year))

    if downside_std == 0:
        return np.nan

    return float((ret - risk_free_rate_ann) / downside_std)


def ret_vol_simple(series: pd.Series, periods_per_year: int = 12) -> float:
    """
    Ratio simple Rentabilidad / Volatilidad (sin descontar tipo libre de riesgo).
    """
    ret = annualized_return(series, periods_per_year)
    vol = annualized_volatility(series, periods_per_year)

    if np.isnan(ret) or np.isnan(vol) or vol == 0:
        return np.nan

    return float(ret / vol)
