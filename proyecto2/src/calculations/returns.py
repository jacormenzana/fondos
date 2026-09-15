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


def downside_deviation_ann(
    rets,
    mar_per_period: float,
    periods_per_year: int = 12,
) -> float:
    """
    Semi-desviación anualizada de retornos frente a un MAR (Minimum Acceptable
    Return), definición estándar de Sortino: semi-varianza poblacional sobre
    TODOS los periodos (cero para los que igualan o superan el MAR), no la
    varianza de solo el subconjunto negativo.

    Canonical — única implementación compartida por el path escalar
    (sortino_ratio, fund_metrics) y el path rolling (rolling_stats._roll_sortino,
    fund_metric_timeseries). Antes de unificarse aquí, los dos paths usaban
    fórmulas estructuralmente distintas (MAR=0 sobre solo negativos vs.
    MAR=rfr/periodo sobre todos los periodos) y escribían valores divergentes
    bajo el mismo nombre de métrica 'sortino' (root-caused 2026-09-14, ISIN
    BE0058182792: denominadores 0.133 vs 0.096 para el mismo input).
    """
    arr = np.asarray(rets, dtype=float)
    if len(arr) < 2:
        return np.nan
    downside = arr - mar_per_period
    downside_sq = np.where(downside < 0, downside ** 2, 0.0)
    var = float(downside_sq.mean())
    if var <= 0:
        return np.nan
    return float(np.sqrt(var) * np.sqrt(periods_per_year))


def sortino_ratio(
    series: pd.Series,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
) -> float:
    """
    Ratio Sortino anualizado.
        (Rentabilidad anualizada - tipo libre de riesgo) / Downside deviation anualizada

    Downside deviation: ver downside_deviation_ann() — semi-desviación frente a
    un MAR igual al tipo libre de riesgo por periodo, sobre todos los periodos.
    """
    ret = annualized_return(series, periods_per_year)
    if np.isnan(ret):
        return np.nan

    r = monthly_returns(series)
    mar_per_period = risk_free_rate_ann / periods_per_year
    downside_std = downside_deviation_ann(r.to_numpy(), mar_per_period, periods_per_year)

    if np.isnan(downside_std) or downside_std == 0:
        return np.nan

    return float((ret - risk_free_rate_ann) / downside_std)


def annualized_return_from_returns(rets, periods_per_year: int = 12) -> float:
    """
    Rentabilidad anualizada geometrica desde un array de retornos periodicos
    simples, no necesariamente contiguos (p.ej. solo los meses de un
    regimen macro dado, que no forman una serie NAV valida).

    Mismo criterio de anualizacion geometrica que annualized_return(), pero
    expresado sobre retornos ya extraidos en lugar de niveles NAV -- unico
    punto de entrada canonico para consumidores que no disponen de una
    serie NAV contigua (ver regime_returns.py, P0 2026-09-15).
    """
    arr = np.asarray(rets, dtype=float)
    n = len(arr)
    if n < 1:
        return np.nan
    total = float(np.prod(1.0 + arr))
    return float(total ** (periods_per_year / n) - 1.0)


def annualized_volatility_from_returns(rets, periods_per_year: int = 12) -> float:
    """Volatilidad anualizada desde un array de retornos periodicos simples."""
    arr = np.asarray(rets, dtype=float)
    if len(arr) < 2:
        return np.nan
    return float(np.std(arr, ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio_from_returns(
    rets,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
) -> float:
    """Ratio Sharpe anualizado desde un array de retornos periodicos simples.
    Misma formula que sharpe_ratio() -- ver esa docstring."""
    ret = annualized_return_from_returns(rets, periods_per_year)
    vol = annualized_volatility_from_returns(rets, periods_per_year)

    if np.isnan(ret) or np.isnan(vol) or vol == 0:
        return np.nan

    return float((ret - risk_free_rate_ann) / vol)


def sortino_ratio_from_returns(
    rets,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
) -> float:
    """Ratio Sortino anualizado desde un array de retornos periodicos simples.
    Reutiliza downside_deviation_ann() sin duplicar la formula -- ver esa
    docstring para la definicion canonica de la semi-desviacion."""
    ret = annualized_return_from_returns(rets, periods_per_year)
    if np.isnan(ret):
        return np.nan

    arr = np.asarray(rets, dtype=float)
    mar_per_period = risk_free_rate_ann / periods_per_year
    downside_std = downside_deviation_ann(arr, mar_per_period, periods_per_year)

    if np.isnan(downside_std) or downside_std == 0:
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
