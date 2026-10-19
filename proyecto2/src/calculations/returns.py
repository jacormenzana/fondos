# proyecto2/src/calculations/returns.py
# -*- coding: utf-8 -*-
"""
Cálculo de métricas de retorno y eficiencia sobre series NAV.
"""

import numpy as np
import pandas as pd

from shared.annualization import (
    by_dates_enabled, dense_equivalent_points, gap_months, kweighted_std, month_ordinals, standardised_shortfalls, years_spanned,
)
from shared.sortino_reliability import downside_count_reliable

# FND-0075: floor below which an annualized downside deviation is treated as a numerical artifact
# (near-flat-fund near-zero denominator), not a real risk signal — see downside_deviation_ann().
_MIN_DOWNSIDE_DEV_ANN: float = 0.001   # 0.1% annualized


def trailing_window(nav_df: pd.DataFrame, months: int) -> pd.DataFrame:
    """The rows of `nav_df` (columns date, nav) inside the trailing `months` calendar months: date > last date - DateOffset(months).

    THE definition of a "rolling_Ny" window (REL-3: date based, because tail(N) can exclude months with a one-off publication delay). The
    risk family's rolling horizons and the momentum family both call it, so a fund's window is the one its peers' stored rolling_Ny
    return_ann was computed on (FND-0302). Index reset; an empty frame stays empty."""
    if nav_df.empty:
        return nav_df
    cutoff = nav_df["date"].max() - pd.DateOffset(months=months)
    return nav_df[nav_df["date"] > cutoff].reset_index(drop=True)


def _month_ords(series: pd.Series, dates) -> "np.ndarray | None":
    """Calendar-month ordinals aligned with `series`, or None (= the stored row convention) when ANNUALIZATION_BY_DATES_ENABLED is off,
    no dates were given, or they do not match the series (FND-0253)."""
    if dates is None or not by_dates_enabled():
        return None
    mo = month_ordinals(dates)
    return mo if len(mo) == len(series) and len(mo) >= 2 else None


def _returns_and_gaps(series: pd.Series, dates):
    """(simple returns as a Series, months each return spans as an array or None). None = legacy row convention."""
    rr = series.pct_change()
    valid = rr.notna().to_numpy()
    r = rr[valid]
    mo = _month_ords(series, dates)
    if mo is None:
        return r, None
    k_full = np.concatenate(([1], gap_months(mo)))
    return r, k_full[valid]


def monthly_returns(series: pd.Series, dates=None, one_month_only: bool = False) -> pd.Series:
    """Retornos mensuales simples (pct_change). FND-0253: with `one_month_only` and the by-dates switch ON, returns that span more than
    one calendar month (a gap in the NAV) are dropped: they are not monthly returns (consistency / regime statistics)."""
    r, k = _returns_and_gaps(series, dates if one_month_only else None)
    if one_month_only and k is not None:
        return r[k == 1]
    return r


def annualized_return(series: pd.Series, periods_per_year: int = 12, dates=None) -> float:
    """
    Rentabilidad anualizada geométrica.
    Requiere al menos 2 observaciones.

    FND-0253: with `dates` (one per row) and ANNUALIZATION_BY_DATES_ENABLED the horizon is the dense-equivalent number of points of
    the first..last calendar month, through the same years_spanned(): identical for a dense series, correct for one with gaps.
    """
    if len(series) < 2:
        return np.nan
    total = series.iloc[-1] / series.iloc[0]
    n_points = len(series)
    mo = _month_ords(series, dates)
    if mo is not None and dense_equivalent_points(mo) >= 2:
        n_points = dense_equivalent_points(mo)
    years = years_spanned(n_points, periods_per_year)        # FND-0240: N points span N - 1 periods when the switch is on
    return float(total ** (1.0 / years) - 1.0)


def annualized_volatility(series: pd.Series, periods_per_year: int = 12, dates=None) -> float:
    """
    Volatilidad anualizada (desviación estándar de retornos mensuales × √12).
    FND-0253: with `dates` and the switch ON, a return spanning k months enters as a k-month return (shared.annualization.kweighted_std);
    a series whose returns are all one month long takes the legacy expression untouched.
    """
    r, k = _returns_and_gaps(series, dates)
    if len(r) < 2:
        return np.nan
    if k is not None and not (k == 1).all():
        return kweighted_std(r.to_numpy(), k, periods_per_year)
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio(
    series: pd.Series,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
    dates=None,
) -> float:
    """
    Ratio Sharpe anualizado.
        (Rentabilidad anualizada - tipo libre de riesgo) / Volatilidad anualizada

    risk_free_rate_ann: tipo libre de riesgo anual (ej. 0.04 para 4%)
    """
    ret  = annualized_return(series, periods_per_year, dates)
    vol  = annualized_volatility(series, periods_per_year, dates)

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
    dd_ann = float(np.sqrt(var) * np.sqrt(periods_per_year))

    # FND-0075 (2026-09-27): a near-flat fund (Monetario/RF Corto) dips below the MAR in only a
    # handful of periods, by a tiny amount each time -- var > 0 but vanishingly small, which the
    # `var <= 0` guard above does not catch. The Sortino ratio this feeds then divides by that near-
    # zero denominator and explodes to a numerically-derived but meaningless magnitude (measured
    # live: up to 2505 for a real Monetario fund, when a genuinely good fund rarely exceeds single
    # digits). _MIN_DOWNSIDE_DEV_ANN is deliberately far below any real fund's downside deviation
    # (SRRI's own bucket 1, "negligible volatility", starts at 0.5% *total* vol; downside deviation
    # is a strict subset of that) -- this only catches the numerical-artifact case, never a
    # genuinely low-risk fund's real ratio.
    if dd_ann < _MIN_DOWNSIDE_DEV_ANN:
        return np.nan
    return dd_ann


def sortino_ratio(
    series: pd.Series,
    risk_free_rate_ann: float,
    periods_per_year: int = 12,
    dates=None,
) -> float:
    """
    Ratio Sortino anualizado.
        (Rentabilidad anualizada - tipo libre de riesgo) / Downside deviation anualizada

    Downside deviation: ver downside_deviation_ann() — semi-desviación frente a
    un MAR igual al tipo libre de riesgo por periodo, sobre todos los periodos.
    """
    ret = annualized_return(series, periods_per_year, dates)
    if np.isnan(ret):
        return np.nan

    r, k = _returns_and_gaps(series, dates)
    mar_per_period = risk_free_rate_ann / periods_per_year
    if k is not None and not (k == 1).all():
        # FND-0253: shortfalls of k-month returns are measured against k * MAR and scaled by sqrt(k) (identical to the line below for k = 1)
        z = standardised_shortfalls(r.to_numpy(), k, mar_per_period)
        if not downside_count_reliable(z, 0.0):
            return np.nan
        downside_std = downside_deviation_ann(z, 0.0, periods_per_year)
    else:
        # FND-0242: a denominator built from 2-4 shortfalls is noise (shared/sortino_reliability.py); off = stored behaviour.
        if not downside_count_reliable(r.to_numpy(), mar_per_period):
            return np.nan
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


def ret_vol_simple(series: pd.Series, periods_per_year: int = 12, dates=None) -> float:
    """
    Ratio simple Rentabilidad / Volatilidad (sin descontar tipo libre de riesgo).
    """
    ret = annualized_return(series, periods_per_year, dates)
    vol = annualized_volatility(series, periods_per_year, dates)

    if np.isnan(ret) or np.isnan(vol) or vol == 0:
        return np.nan

    return float(ret / vol)
