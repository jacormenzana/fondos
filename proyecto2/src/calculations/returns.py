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
from shared.ratio_floor import vol_is_degenerate        # FND-0405: no ratio on a near-zero volatility denominator
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


def window_span_ok(first_date, end_date, months: int, tolerance_days: "int | None" = None) -> bool:
    """FND-0427 (a), span: the history really reaches back `months` calendar months from `end_date`, give or take `ROLLING_WINDOW_COVERAGE_TOLERANCE_DAYS`
    (35: one monthly close, because monthly rows are last-NAV-of-month and the newest one can be mid-month). `first_date` is the fund's FIRST NAV."""
    from shared import config
    tol = config.ROLLING_WINDOW_COVERAGE_TOLERANCE_DAYS if tolerance_days is None else int(tolerance_days)
    return pd.Timestamp(first_date) <= pd.Timestamp(end_date) - pd.DateOffset(months=months) + pd.Timedelta(days=tol)


def window_continuity_thresholds() -> tuple:
    """(min_coverage, max_gap_run): the ONE continuity rule of `shared.nav_continuity`, with the values the P3 eligibility already uses."""
    from shared import config
    return float(config.P3_FUND_NAV_MIN_COVERAGE), int(config.P3_FUND_NAV_MAX_GAP_RUN_MONTHS)


def window_coverage_ok(nav_df: pd.DataFrame, months: int, tolerance_days: "int | None" = None) -> bool:
    """FND-0427: does the history behind `trailing_window(nav_df, months)` really cover that horizon? Two conditions, both required:

    (a) span, `window_span_ok`: the fund's first NAV is at most `end - months + ROLLING_WINDOW_COVERAGE_TOLERANCE_DAYS`; a 3-year-old fund has no rolling_10y;
    (b) continuity: the rows INSIDE the window show no hole beyond the existing NAV continuity thresholds (`shared.nav_continuity.reason_for_dates`, over the
        window's own first..last month), so a suspended fund does not publish a long window built from little data.

    Pure (the thresholds are read at call time, like the other P2 switches). `nav_df` has columns date, nav; an empty frame is not covered."""
    if nav_df is None or nav_df.empty:
        return False
    from shared.nav_continuity import reason_for_dates
    if not window_span_ok(nav_df["date"].min(), nav_df["date"].max(), months, tolerance_days):
        return False
    min_coverage, max_gap_run = window_continuity_thresholds()
    return reason_for_dates(trailing_window(nav_df, months)["date"], min_coverage, max_gap_run) is None


def metric_window(nav_df: pd.DataFrame, horizon: str) -> "dict | None":
    """FND-0428: the ACTUAL window a stored metric of `horizon` covers, from the NAV, with the same definitions P2 uses:
    'since_inception' (first..last NAV), 'ytd' (`ytd_return`: base..last) and 'rolling_Ny' (`trailing_window`). Returns {start, end, span_days, span_months}
    or None. Monthly rows are last-NAV-of-month and the newest one can be mid-month, so a "1Y" window can span 11.3 months: the report shows this span
    on the label instead of letting the name hide it."""
    if nav_df is None or nav_df.empty:
        return None
    df = nav_df.assign(date=pd.to_datetime(nav_df["date"])).sort_values("date")
    if horizon == "since_inception":
        start, end = df["date"].iloc[0], df["date"].iloc[-1]
    elif horizon == "ytd":
        y = ytd_return(df)
        if y is None:
            return None
        start, end = y["base_date"], y["end_date"]
    elif horizon.startswith("rolling_") and horizon.endswith("y") and horizon[8:-1].isdigit():
        win = trailing_window(df, int(horizon[8:-1]) * 12)
        if win.empty:
            return None
        start, end = win["date"].iloc[0], win["date"].iloc[-1]
    else:
        return None
    days = int((end - start).days)
    return {"start": start, "end": end, "span_days": days, "span_months": round(days / 30.4375, 1)}


def rolling_window_admissible(nav_df: pd.DataFrame, months: int, min_rows: int) -> bool:
    """The gate of a rolling window in the risk family and in momentum: at least `min_rows` rows inside the window and, with `ROLLING_WINDOW_COVERAGE_ENABLED`,
    the coverage rule. With the switch OFF it is the legacy gate (rows only), bit for bit."""
    from shared import config
    if len(trailing_window(nav_df, months)) < min_rows:
        return False
    return window_coverage_ok(nav_df, months) if getattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", False) else True


def ytd_return(nav_df: pd.DataFrame, as_of=None, max_staleness_bdays: "int | None" = None) -> "dict | None":
    """FND-0426: the cumulative (never annualized) return of the calendar year to `as_of` (default: the fund's last NAV date).

    Base = the last NAV on or before the prior Dec 31, which must lie within `YTD_BASE_MAX_STALENESS_BDAYS` business days of it; a staler base writes no row.
    A fund whose first NAV is in the current year has no Dec 31 base: it gets the return since its FIRST NAV and `partial=True` (stored as metric_version
    'ytd_partial', so the report never presents it as a full-year YTD). Returns {value, base_date, end_date, partial} or None (empty, one NAV, stale base,
    non-positive base). The same function serves the nominal series and, given the deflated NAV as `nav`, the real one."""
    if nav_df is None or nav_df.empty:
        return None
    from shared import config
    limit = config.YTD_BASE_MAX_STALENESS_BDAYS if max_staleness_bdays is None else int(max_staleness_bdays)
    df = nav_df.assign(date=pd.to_datetime(nav_df["date"])).sort_values("date")
    end_ts = pd.Timestamp(as_of) if as_of is not None else df["date"].max()
    upto = df[df["date"] <= end_ts]
    if upto.empty:
        return None
    end_row = upto.iloc[-1]
    end_date = pd.Timestamp(end_row["date"])
    prior_dec31 = pd.Timestamp(year=end_date.year - 1, month=12, day=31)
    before = upto[upto["date"] <= prior_dec31]
    if before.empty:
        base_row, partial = upto.iloc[0], True                   # launched this year: since the first NAV
    else:
        base_row, partial = before.iloc[-1], False
        if int(np.busday_count(pd.Timestamp(base_row["date"]).date(), prior_dec31.date())) > limit:
            return None                                          # the Dec 31 base is too stale to call this a year-to-date return
    base_date = pd.Timestamp(base_row["date"])
    if base_date >= end_date:
        return None                                              # one observation: no return
    base_nav, end_nav = float(base_row["nav"]), float(end_row["nav"])
    if not (np.isfinite(base_nav) and np.isfinite(end_nav)) or base_nav <= 0:
        return None
    return {"value": end_nav / base_nav - 1.0, "base_date": base_date, "end_date": end_date, "partial": partial}


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

    if np.isnan(ret) or np.isnan(vol) or vol == 0 or vol_is_degenerate(vol):     # FND-0405: below RATIO_MIN_VOL_ANN the quotient is arithmetic on a near-zero denominator
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

    if np.isnan(ret) or np.isnan(vol) or vol == 0 or vol_is_degenerate(vol):     # FND-0405
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

    if np.isnan(ret) or np.isnan(vol) or vol == 0 or vol_is_degenerate(vol):     # FND-0405
        return np.nan

    return float(ret / vol)
