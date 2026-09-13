# proyecto2/src/calculations/rolling_stats.py
# -*- coding: utf-8 -*-
"""
Motor de indicadores rolling para fund_metric_timeseries (v29).

Métricas curadas (Hybrid model — full history in fund_metric_timeseries):
  - vol_ann    : volatilidad anualizada sobre la ventana trailing
  - max_dd     : máximo drawdown dentro de la ventana trailing
  - return_ann : rentabilidad anualizada geométrica sobre la ventana
  - sharpe     : ratio Sharpe rolling = (return_ann - rfr) / vol_ann
  - sortino    : ratio Sortino rolling = (return_ann - rfr) / downside_deviation_ann

Tanto la variante nominal (real_flag=0) como la real deflactada (real_flag=1)
se calculan para todas las métricas cuando se provee ipc_df.

Horizontes: todos los ROLLING_WINDOWS (mensual, v1) y SHORT_WINDOWS (diario, d1).

Diseño incremental:
  compute_rolling_rows() devuelve *todas* las filas de la serie histórica.
  El escritor (_write_timeseries en run_pipeline) filtra las fechas ya
  presentes en fund_metric_timeseries con INSERT OR IGNORE → solo inserta
  las nuevas → comportamiento incremental sin reescribir la historia.

Cumplimiento R-7: este módulo NO importa pipeline.py ni core.io.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd


# ============================================================
# Constantes internas (reflejo de config — sin importar config
# para cumplir R-7 en tests; el caller pasa los valores)
# ============================================================

_PERIODS_YEAR_MONTHLY: int = 12
_PERIODS_YEAR_DAILY:   int = 252


# ============================================================
# Helpers estadísticos de bajo nivel
# ============================================================

def _roll_vol_ann(nav_window: np.ndarray, periods_per_year: int) -> float:
    """Volatilidad anualizada sobre un segmento NAV (array 1D)."""
    if len(nav_window) < 3:
        return math.nan
    rets = np.diff(nav_window) / nav_window[:-1]
    if len(rets) < 2:
        return math.nan
    return float(np.std(rets, ddof=1) * math.sqrt(periods_per_year))


def resolve_rf_rate(
    date: "pd.Timestamp | str",
    rf_series: pd.DataFrame | None,
    fallback: float,
) -> float:
    """Single-point date-aligned risk-free rate lookup (§4g semantics),
    for callers that need ONE rate at ONE point (a horizon's end date) rather
    than the vectorized per-row resolution `compute_rolling_rows` does
    internally for its whole date index. Same algorithm, expressed for one
    date: month-end align, forward-fill from the latest rate at-or-before
    `date`, back-fill if `date` precedes the series (so an unusually early
    horizon still gets the earliest known rate instead of falling back to
    the flat default), and only fall back to `fallback` when `rf_series` is
    None/empty (no historical series available at all).

    Added 2026-09-13 (AUDITORIA_ESTADISTICA.md §2.9) to close a real formula
    asymmetry: run_pipeline.py's scalar risk_metrics call used a flat
    RISK_FREE_RATE_ANN for sharpe/sortino while compute_rolling_rows' own
    vectorized alignment (lines below) already used the date-aligned curve —
    the two write paths computed genuinely different sharpe/sortino values
    for the same fund/horizon, not just stale-data divergence. Kept as a
    separate function rather than refactoring compute_rolling_rows to call
    this in a per-row loop: that vectorized block runs over the full NAV
    history for every ISIN and looping a per-point lookup there would be a
    real performance regression; test_rolling_stats.py pins both
    implementations to agree on the same inputs instead.
    """
    if rf_series is None or rf_series.empty:
        return fallback

    s = rf_series.copy()
    s["date"] = pd.to_datetime(s["date"]) + pd.offsets.MonthEnd(0)
    s = s.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["rate"]

    target = pd.Timestamp(date).normalize() + pd.offsets.MonthEnd(0)
    aligned = s.reindex(s.index.union([target])).sort_index().ffill().bfill()
    value = aligned.get(target)
    return fallback if value is None or pd.isna(value) else float(value)


def _roll_max_dd(nav_window: np.ndarray) -> float:
    """Máximo drawdown (≤ 0) dentro de un segmento NAV."""
    if len(nav_window) < 2:
        return math.nan
    running_max = np.maximum.accumulate(nav_window)
    dd = nav_window / running_max - 1.0
    return float(dd.min())


def _roll_return_ann(nav_window: np.ndarray, periods_per_year: int) -> float:
    """Rentabilidad anualizada geométrica sobre un segmento NAV."""
    if len(nav_window) < 2:
        return math.nan
    total = nav_window[-1] / nav_window[0]
    if total <= 0:
        return math.nan
    years = len(nav_window) / periods_per_year
    return float(total ** (1.0 / years) - 1.0)


def _roll_sharpe(
    nav_window: np.ndarray,
    periods_per_year: int,
    risk_free_rate_ann: float,
) -> float:
    """Ratio Sharpe rolling = (return_ann - rfr) / vol_ann.

    Returns NaN if vol_ann = 0 or < 3 observations.
    """
    if len(nav_window) < 3:
        return math.nan
    ret_ann = _roll_return_ann(nav_window, periods_per_year)
    vol_ann = _roll_vol_ann(nav_window, periods_per_year)
    if math.isnan(ret_ann) or math.isnan(vol_ann) or vol_ann == 0.0:
        return math.nan
    return float((ret_ann - risk_free_rate_ann) / vol_ann)


def _roll_sortino(
    nav_window: np.ndarray,
    periods_per_year: int,
    risk_free_rate_ann: float,
) -> float:
    """Ratio Sortino rolling = (return_ann - rfr) / downside_deviation_ann.

    Downside deviation uses a MAR of rfr/periods_per_year per period.
    Only negative-excess periods contribute (semi-variance approach).
    Returns NaN if downside_dev = 0 or < 3 observations.
    """
    if len(nav_window) < 3:
        return math.nan
    rets = np.diff(nav_window) / nav_window[:-1]
    if len(rets) < 2:
        return math.nan
    mar_per_period = risk_free_rate_ann / periods_per_year
    downside = rets - mar_per_period
    downside_sq = np.where(downside < 0, downside ** 2, 0.0)
    dd_var = float(downside_sq.mean())
    if dd_var <= 0:
        return math.nan
    downside_dev_ann = math.sqrt(dd_var) * math.sqrt(periods_per_year)
    ret_ann = _roll_return_ann(nav_window, periods_per_year)
    if math.isnan(ret_ann):
        return math.nan
    return float((ret_ann - risk_free_rate_ann) / downside_dev_ann)


# ============================================================
# Función principal
# ============================================================

def compute_rolling_rows(
    isin: str,
    nav_df: pd.DataFrame,
    rolling_windows: dict[str, int],
    *,
    min_obs: int = 5,
    ipc_df: pd.DataFrame | None = None,
    real_flag_default: int = 0,
    periods_per_year: int = _PERIODS_YEAR_MONTHLY,
    risk_free_rate: float = 0.0,
    rf_series: pd.DataFrame | None = None,
) -> list[dict]:
    """
    Calcula las series rolling de las 5 métricas curadas para un ISIN.

    Parameters
    ----------
    isin            : identificador del fondo
    nav_df          : DataFrame con columnas 'date' (DATE o str) y 'nav' (REAL),
                      ordenado cronológicamente, índice ignorado.
                      (Mismo contrato que load_nav() en db_readers.py.)
    rolling_windows : dict {nombre_ventana: meses_trailing}.
                      Ventana = fecha > (fecha_punto - DateOffset(months=meses_trailing)),
                      el mismo criterio calendario que usa run_pipeline.py para las métricas
                      escalares en fund_metrics — no un recuento fijo de filas (ver nota abajo).
    min_obs         : mínimo de observaciones para calcular (evita valores espurios).
    ipc_df          : opcional — DataFrame con columnas 'date' y 'ipc_index' (IPC, base 100
                      o ratio). Si se proporciona se calculan también las métricas reales.
                      (Mismo contrato que load_ipc() en db_readers.py.)
    real_flag_default : flag para la serie nominal (0). Real = 1.
    periods_per_year: 12 para serie mensual, 252 para diaria.
    risk_free_rate  : tasa libre de riesgo anualizada (decimal, fallback estático).
                      Usado cuando rf_series no cubre la fecha del punto.
                      Por defecto 0.0. Pasar RISK_FREE_RATE_ANN de shared/config.py.
    rf_series       : opcional — DataFrame con columnas 'date' y 'rate' (decimal, % / 100).
                      Cargado vía load_rf_rate() en db_readers.py. Cuando se proporciona,
                      cada punto rolling usa la tasa activa en esa fecha (forward-fill),
                      eliminando la distorsión histórica del Sharpe/Sortino con tipo plano.
                      (§4g del plan de re-ingeniería 2026-08.)

    Returns
    -------
    Lista de dicts con claves:
        isin, metric, window, date (str ISO), value, real_flag,
        ref_type (None), ref_value (None), source_rows
    """
    if nav_df is None or nav_df.empty:
        return []

    nav_df = nav_df.copy()
    nav_df["date"] = pd.to_datetime(nav_df["date"])
    nav_df = nav_df.sort_values("date").reset_index(drop=True)
    nav_series = nav_df["nav"].to_numpy(dtype=float)
    dates = nav_df["date"].tolist()
    n = len(nav_series)

    # §4g: build date-aligned RF rate vector (one value per NAV row, forward-filled).
    # When rf_series is provided each window-end date uses its historically correct rate.
    # Fall back to the static risk_free_rate scalar for dates outside the series range.
    if rf_series is not None and not rf_series.empty:
        _rf_s = rf_series.copy()
        _rf_s["date"] = pd.to_datetime(_rf_s["date"]) + pd.offsets.MonthEnd(0)
        _rf_s = (
            _rf_s.sort_values("date")
            .drop_duplicates("date", keep="last")
            .set_index("date")["rate"]
        )
        _nav_idx = pd.DatetimeIndex(dates).normalize() + pd.offsets.MonthEnd(0)
        # reindex + ffill ensures every NAV date gets the closest preceding rate
        _rf_aligned = _rf_s.reindex(_nav_idx).ffill().bfill()
        _rf_vals: np.ndarray = np.where(
            _rf_aligned.isna(),
            risk_free_rate,
            _rf_aligned.to_numpy(dtype=float),
        )
    else:
        _rf_vals = np.full(n, risk_free_rate, dtype=float)

    # Deflactar si hay IPC
    nav_real: np.ndarray | None = None
    if ipc_df is not None and not ipc_df.empty:
        ipc_df = ipc_df.copy()
        ipc_df["date"] = pd.to_datetime(ipc_df["date"])
        merged = nav_df[["date"]].merge(
            ipc_df[["date", "ipc_index"]].rename(columns={"ipc_index": "ipc"}),
            on="date", how="left"
        )
        ipc_vals = merged["ipc"].to_numpy(dtype=float)
        if not np.all(np.isnan(ipc_vals)):
            # Rellenar NaN por forward-fill (datos macroeconómicos con retardo)
            mask = np.isnan(ipc_vals)
            if mask.any():
                df_tmp = pd.Series(ipc_vals)
                ipc_vals = df_tmp.ffill().bfill().to_numpy(dtype=float)
            # Deflactar NAV: NAV_real[t] = NAV[t] / (IPC[t] / IPC[0])
            if ipc_vals[0] != 0 and not np.isnan(ipc_vals[0]):
                nav_real = nav_series / (ipc_vals / ipc_vals[0])

    rows: list[dict] = []

    for window_name, w in rolling_windows.items():
        # Calendar-date window (months trailing), not a fixed row count: matches
        # run_pipeline.py's `nav_df[nav_df["date"] > cutoff]` slicing for the scalar
        # metrics in fund_metrics, so "rolling_1y" means the same thing in both
        # fund_metrics and fund_metric_timeseries. `dates` is sorted ascending, so the
        # cutoff is monotonic non-decreasing in i and `left` only ever moves forward.
        left = 0
        for i in range(n):
            cutoff = dates[i] - pd.DateOffset(months=w)
            while left <= i and dates[left] <= cutoff:
                left += 1
            start = left
            window_nav = nav_series[start : i + 1]
            n_obs = len(window_nav)
            if n_obs < min_obs:
                continue
            date_str = dates[i].date().isoformat()

            nav_variants: list[tuple[int, np.ndarray]] = [
                (real_flag_default, window_nav)
            ]
            if nav_real is not None:
                nav_variants.append((1, nav_real[start : i + 1]))

            # Date-appropriate RF rate for this window-end point
            _rf = _rf_vals[i]

            for flag, nav_arr in nav_variants:
                if nav_arr is None or len(nav_arr) < min_obs:
                    continue

                # Compute all 5 curated metrics for this window slice.
                # Base metrics computed once; Sharpe/Sortino reuse them internally.
                metric_vals = (
                    ("vol_ann",    _roll_vol_ann(nav_arr, periods_per_year)),
                    ("max_dd",     _roll_max_dd(nav_arr)),
                    ("return_ann", _roll_return_ann(nav_arr, periods_per_year)),
                    ("sharpe",     _roll_sharpe(nav_arr, periods_per_year, _rf)),
                    ("sortino",    _roll_sortino(nav_arr, periods_per_year, _rf)),
                )
                for metric, val in metric_vals:
                    rows.append({
                        "isin":        isin,
                        "metric":      metric,
                        "window":      window_name,
                        "date":        date_str,
                        "value":       None if (isinstance(val, float) and math.isnan(val)) else val,
                        "real_flag":   flag,
                        "ref_type":    None,
                        "ref_value":   None,
                        "source_rows": n_obs,
                    })

    return rows


# ============================================================
# Snapshot percentil / zscore de categoría (cross-seccional)
# ============================================================

def compute_category_snapshot(
    timeseries_df: pd.DataFrame,
    min_peers: int = 5,
) -> pd.DataFrame:
    """
    Calcula percentiles cross-seccionales de categoría para la última fecha
    disponible en fund_metric_timeseries (tras cargar todos los ISINs).

    Parameters
    ----------
    timeseries_df : DataFrame con columnas:
        isin, metric, window, date, value, real_flag, Fund_Nature (join externo).
    min_peers     : mínimo de fondos con datos para calcular percentil.

    Returns
    -------
    DataFrame con columnas:
        isin, metric, window, real_flag, Fund_Nature,
        value         : valor del fondo en esta fila (AUDITORIA_ESTADISTICA.md
                         §2.5 finding D1 — compute_alerts() lee esta columna
                         para poblar fund_metric_alerts.value; antes de este
                         fix no existía y esa columna quedaba NULL al 100%)
        pctile_cat    : posición percentil dentro de la categoría [0, 1]
        zscore_cat    : z-score vs media de la categoría
        cat_p03, cat_p05, cat_p10, cat_p50, cat_p90, cat_p97  : percentiles
                         de la categoría (p03/p05 añadidos por finding D2 —
                         DD_CAT_P03/RET_CAT_P05 en ALERT_RULES las necesitan
                         y nunca existían, dejando reference_value NULL en
                         todas las filas ALARM de esas dos reglas)
        cat_n         : nº fondos con dato

    Solo contiene filas para la fecha más reciente por (metric, window, real_flag).
    """
    if timeseries_df is None or timeseries_df.empty:
        return pd.DataFrame()

    required = {"isin", "metric", "window", "date", "value", "real_flag", "Fund_Nature"}
    missing = required - set(timeseries_df.columns)
    if missing:
        raise ValueError(f"compute_category_snapshot: columnas faltantes: {missing}")

    df = timeseries_df.dropna(subset=["value", "Fund_Nature"]).copy()
    if df.empty:
        return pd.DataFrame()

    # Última fila por fondo (isin, metric, window, real_flag) — per-fund latest.
    # Using global MAX(date) per (metric, window, real_flag) is WRONG: funds have
    # different last-date stamps; the global peak date matches only a handful of
    # funds, starving min_peers. Per-fund latest is correct for peer ranking.
    df = (
        df.sort_values("date")
        .groupby(["isin", "metric", "window", "real_flag"], as_index=False)
        .last()
    )

    if df.empty:
        return pd.DataFrame()

    records = []
    for (metric, window, real_flag, fund_nature), grp in df.groupby(
        ["metric", "window", "real_flag", "Fund_Nature"]
    ):
        vals = grp["value"].dropna()
        n = len(vals)
        if n < min_peers:
            continue
        mu    = float(vals.mean())
        sigma = float(vals.std(ddof=1)) if n > 1 else 0.0
        p03   = float(np.percentile(vals, 3))
        p05   = float(np.percentile(vals, 5))
        p10   = float(np.percentile(vals, 10))
        p50   = float(np.percentile(vals, 50))
        p90   = float(np.percentile(vals, 90))
        p97   = float(np.percentile(vals, 97))

        for _, row in grp.iterrows():
            v = row["value"]
            pctile = float((vals < v).mean()) if not math.isnan(v) else math.nan
            zscore = float((v - mu) / sigma) if sigma > 0 and not math.isnan(v) else math.nan
            records.append({
                "isin":        row["isin"],
                "metric":      metric,
                "window":      window,
                "real_flag":   real_flag,
                "Fund_Nature": fund_nature,
                "value":       v,
                "pctile_cat":  pctile,
                "zscore_cat":  zscore,
                "cat_p03":     p03,
                "cat_p05":     p05,
                "cat_p10":     p10,
                "cat_p50":     p50,
                "cat_p90":     p90,
                "cat_p97":     p97,
                "cat_n":       n,
            })

    return pd.DataFrame(records) if records else pd.DataFrame()


# ============================================================
# Alarm engine
# ============================================================

def compute_alerts(
    category_df: pd.DataFrame,
    alert_rules: list[dict],
    min_peers: int = 5,
) -> list[dict]:
    """
    Genera filas para fund_metric_alerts a partir del snapshot de categoría.

    Parameters
    ----------
    category_df  : salida de compute_category_snapshot().
    alert_rules  : lista de reglas de ALERT_RULES en config.py.
    min_peers    : mínimo fondos con dato para emitir alerta (fail-open si < min).

    Returns
    -------
    Lista de dicts con claves:
        isin, metric, window, level, rule_code, value, reference_value,
        ref_type ('category').

    Una regla en nivel WARN no genera fila ALARM (ALARM supercede WARN).
    """
    if category_df is None or category_df.empty:
        return []

    # Indexar por (isin, metric, window, real_flag) para lookup rápido
    df = category_df.set_index(["isin", "metric", "window", "real_flag"])

    # Acumular la alerta más severa por (isin, metric, window)
    # severity: OK=0, WARN=1, ALARM=2
    _SEVERITY = {"OK": 0, "WARN": 1, "ALARM": 2}
    best: dict[tuple, dict] = {}

    for rule in alert_rules:
        rule_code   = rule["rule_code"]
        metric      = rule["metric"]
        window      = rule["window"]
        ref_type    = rule.get("ref_type", "category")
        direction   = rule["direction"]   # 'above' | 'below'
        pctile_key  = rule["threshold_pctile"]
        level       = rule["level"]

        # Solo flag=0 (nominal) para las reglas de categoría por defecto
        real_flag = rule.get("real_flag", 0)

        subset = df.loc[df.index.get_level_values("metric") == metric]
        subset = subset.loc[subset.index.get_level_values("window") == window]
        subset = subset.loc[subset.index.get_level_values("real_flag") == real_flag]

        for idx, row in subset.reset_index().iterrows():
            isin = row["isin"]
            n    = row.get("cat_n", 0)
            if n < min_peers:
                continue   # fail-open: sin base estadística → no alertar

            val     = row.get("pctile_cat")
            if val is None or (isinstance(val, float) and math.isnan(val)):
                continue

            # Determinar si se activa esta regla
            if direction == "above":
                # Alerta si el percentil del fondo está por encima del umbral
                triggered = val > pctile_key
                # Referencia = valor de la categoría en ese percentil
                ref_col = f"cat_p{int(pctile_key * 100):02d}"
            else:  # 'below'
                # Alerta si el percentil del fondo está por debajo del umbral
                triggered = val < pctile_key
                ref_col = f"cat_p{int(pctile_key * 100):02d}"

            actual_value     = row.get("value", None) if "value" in row.index else None
            reference_value  = row.get(ref_col) if ref_col in row.index else None

            key = (isin, metric, window)
            alert = {
                "isin":            isin,
                "metric":          metric,
                "window":          window,
                "level":           level if triggered else "OK",
                "rule_code":       rule_code,
                "value":           actual_value,
                "reference_value": reference_value,
                "ref_type":        ref_type,
            }

            existing = best.get(key)
            if existing is None:
                best[key] = alert
            else:
                if _SEVERITY.get(alert["level"], 0) > _SEVERITY.get(existing["level"], 0):
                    best[key] = alert

    return list(best.values())


# ============================================================
# P2 latest-scalar snapshot → fund_metrics
# ============================================================

# (metric, real_flag) pairs for which a normalized slope is computed.
# Slope = OLS b / std(y) → dimensionless, sign-meaningful (+ = improving).
# Add (metric, flag) here to enable; no other code change needed.
_SLOPE_TARGETS: frozenset[tuple[str, int]] = frozenset([
    ("sharpe",     0),   # rolling Sharpe trend (nominal)
    ("return_ann", 1),   # rolling real-return trend (deflated)
])


def _linear_slope_normalized(y: np.ndarray) -> float:
    """Normalized OLS slope: b / std(y), in std-devs per time-step.

    Returns NaN when n < 3, all values equal, or series has no variance.
    Sign: positive = improving trend; negative = deteriorating.
    """
    n = len(y)
    if n < 3:
        return math.nan
    t = np.arange(n, dtype=float)
    t_c = t - t.mean()
    denom = float((t_c ** 2).sum())
    if denom == 0:
        return math.nan
    b = float((t_c * (y - y.mean())).sum()) / denom
    std_y = float(y.std())
    if std_y == 0:
        return math.nan
    return b / std_y


def compute_timeseries_snapshots(
    timeseries_df: pd.DataFrame,
    category_df: pd.DataFrame | None = None,
    min_self_obs: int = 12,
    slope_n_points: int = 36,
) -> list[dict]:
    """
    Extrae señales derivadas de fund_metric_timeseries para escribir en fund_metrics
    como métricas latest-scalar, horizon = window_name, metric_version = 'v1'.

    Señales por (isin, metric, window):
      <metric>_pctile_self : percentil temporal del último valor dentro del propio historial.
      <metric>_slope       : tendencia normalizada (solo para _SLOPE_TARGETS).
      <metric>_pctile_cat  : percentil cross-seccional (procedente de category_df).
      <metric>_zscore_cat  : z-score vs media de la categoría (procedente de category_df).

    slope_n_points: número máximo de observaciones recientes para el cálculo de pendiente.
                    Por defecto 36 (3 años de datos mensuales).

    Devuelve dicts con {isin, metric, window, value, real_flag, source_rows}.
    """
    if timeseries_df is None or timeseries_df.empty:
        return []

    df = timeseries_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["isin", "metric", "window", "real_flag", "date"])

    # Índice de categoría si se proporciona
    cat_index: dict = {}
    if category_df is not None and not category_df.empty:
        for _, row in category_df.iterrows():
            key = (row["isin"], row["metric"], row["window"], int(row["real_flag"]))
            cat_index[key] = row

    records: list[dict] = []

    for (isin, metric, window, real_flag), grp in df.groupby(
        ["isin", "metric", "window", "real_flag"]
    ):
        vals = grp["value"].dropna()
        if vals.empty:
            continue

        latest_val = float(vals.iloc[-1])
        n_obs = len(vals)

        # pctile_self: percentil temporal del último valor
        if n_obs >= min_self_obs:
            pctile_self = float((vals.iloc[:-1] < latest_val).mean()) if n_obs > 1 else 0.5
            records.append({
                "isin":        isin,
                "metric":      f"{metric}_pctile_self",
                "window":      window,
                "value":       pctile_self,
                "real_flag":   int(real_flag),
                "source_rows": n_obs,
            })

        # slope: tendencia normalizada para métricas en _SLOPE_TARGETS
        if (metric, int(real_flag)) in _SLOPE_TARGETS and n_obs >= 3:
            y_slope = vals.to_numpy(dtype=float)[-slope_n_points:]
            slope_val = _linear_slope_normalized(y_slope)
            if not math.isnan(slope_val):
                records.append({
                    "isin":        isin,
                    "metric":      f"{metric}_slope",
                    "window":      window,
                    "value":       slope_val,
                    "real_flag":   int(real_flag),
                    "source_rows": len(y_slope),
                })

        # pctile_cat y zscore_cat: de category_df
        cat_row = cat_index.get((isin, metric, window, int(real_flag)))
        if cat_row is not None:
            pctile_cat = cat_row.get("pctile_cat")
            zscore_cat = cat_row.get("zscore_cat")

            if pctile_cat is not None and not (isinstance(pctile_cat, float) and math.isnan(pctile_cat)):
                records.append({
                    "isin":        isin,
                    "metric":      f"{metric}_pctile_cat",
                    "window":      window,
                    "value":       float(pctile_cat),
                    "real_flag":   int(real_flag),
                    "source_rows": int(cat_row.get("cat_n", n_obs)),
                })
            if zscore_cat is not None and not (isinstance(zscore_cat, float) and math.isnan(zscore_cat)):
                records.append({
                    "isin":        isin,
                    "metric":      f"{metric}_zscore_cat",
                    "window":      window,
                    "value":       float(zscore_cat),
                    "real_flag":   int(real_flag),
                    "source_rows": int(cat_row.get("cat_n", n_obs)),
                })

    return records


# ============================================================
# Category signals from snapshot (no full-table read)
# ============================================================

def cat_signals_from_snapshot(cat_df: pd.DataFrame) -> list[dict]:
    """
    Convierte cat_df (salida de compute_category_snapshot) en filas listas para
    _write_metrics — una fila pctile_cat y una zscore_cat por (isin, metric, window).

    cat_df ya contiene solo la última fecha (compute_category_snapshot filtra
    internamente); este helper no hace ninguna lectura adicional de la DB ni
    materializa la tabla fund_metric_timeseries.

    Reemplaza el uso de compute_timeseries_snapshots sobre la tabla completa
    para extraer las señales de categoría (RC-2 fix, v28).

    Cumplimiento R-7: no importa pipeline.py ni core.io.

    Parameters
    ----------
    cat_df : salida de compute_category_snapshot() (o DataFrame vacío).

    Returns
    -------
    Lista de dicts con claves:
        isin, metric, window, value, real_flag, source_rows
    """
    if cat_df is None or cat_df.empty:
        return []

    records: list[dict] = []
    for _, row in cat_df.iterrows():
        isin      = row["isin"]
        metric    = row["metric"]
        window    = row["window"]
        real_flag = int(row["real_flag"])
        cat_n     = int(row.get("cat_n", 0))

        pctile_cat = row.get("pctile_cat")
        if pctile_cat is not None and not (
            isinstance(pctile_cat, float) and math.isnan(pctile_cat)
        ):
            records.append({
                "isin":        isin,
                "metric":      f"{metric}_pctile_cat",
                "window":      window,
                "value":       float(pctile_cat),
                "real_flag":   real_flag,
                "source_rows": cat_n,
            })

        zscore_cat = row.get("zscore_cat")
        if zscore_cat is not None and not (
            isinstance(zscore_cat, float) and math.isnan(zscore_cat)
        ):
            records.append({
                "isin":        isin,
                "metric":      f"{metric}_zscore_cat",
                "window":      window,
                "value":       float(zscore_cat),
                "real_flag":   real_flag,
                "source_rows": cat_n,
            })

    return records
