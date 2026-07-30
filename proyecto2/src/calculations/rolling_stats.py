# proyecto2/src/calculations/rolling_stats.py
# -*- coding: utf-8 -*-
"""
Motor de indicadores rolling para fund_metric_timeseries (v26).

Métricas curadas (Hybrid model):
  - roll_vol_ann    : volatilidad anualizada sobre la ventana trailing
  - roll_max_dd     : máximo drawdown dentro de la ventana trailing
  - roll_return_ann : rentabilidad anualizada geométrica sobre la ventana

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
) -> list[dict]:
    """
    Calcula las series rolling de las 3 métricas curadas para un ISIN.

    Parameters
    ----------
    isin            : identificador del fondo
    nav_df          : DataFrame con columnas 'date' (DATE o str) y 'nav' (REAL),
                      ordenado cronológicamente, índice ignorado.
                      (Mismo contrato que load_nav() en db_readers.py.)
    rolling_windows : dict {nombre_ventana: tamaño_en_unidades}.
                      Unidades = periodos del nav_df (meses si mensual, días si diario).
    min_obs         : mínimo de observaciones para calcular (evita valores espurios).
    ipc_df          : opcional — DataFrame con columnas 'date' y 'ipc_index' (IPC, base 100
                      o ratio). Si se proporciona se calculan también las métricas reales.
                      (Mismo contrato que load_ipc() en db_readers.py.)
    real_flag_default : flag para la serie nominal (0). Real = 1.
    periods_per_year: 12 para serie mensual, 252 para diaria.

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
        for i in range(n):
            start = max(0, i - w + 1)
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

            for flag, nav_arr in nav_variants:
                if nav_arr is None or len(nav_arr) < min_obs:
                    continue
                for metric, fn in (
                    ("roll_vol_ann",    lambda a: _roll_vol_ann(a, periods_per_year)),
                    ("roll_max_dd",     _roll_max_dd),
                    ("roll_return_ann", lambda a: _roll_return_ann(a, periods_per_year)),
                ):
                    val = fn(nav_arr)
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
        pctile_cat    : posición percentil dentro de la categoría [0, 1]
        zscore_cat    : z-score vs media de la categoría
        cat_p10, cat_p50, cat_p90, cat_p97  : percentiles de la categoría
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
                "pctile_cat":  pctile,
                "zscore_cat":  zscore,
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

def compute_timeseries_snapshots(
    timeseries_df: pd.DataFrame,
    category_df: pd.DataFrame | None = None,
    min_self_obs: int = 12,
) -> list[dict]:
    """
    Extrae señales derivadas de fund_metric_timeseries para escribir en fund_metrics
    como métricas latest-scalar, horizon = window_name, metric_version = 'v1'.

    Señales por (isin, metric, window):
      <metric>_pctile_self : percentil temporal del último valor dentro del propio historial.
      <metric>_pctile_cat  : percentil cross-seccional (procedente de category_df).
      <metric>_zscore_cat  : z-score vs media de la categoría (procedente de category_df).

    Devuelve lista de dicts compatibles con _write_metrics:
        {metric, value, real_flag, source_rows}
    El caller añade isin y llama _write_metrics(conn, isin, rows, horizon, dry_run).

    Estructura de retorno alternativa (sin isin agrupado): devuelve dicts con
        {isin, metric, window, value, real_flag, source_rows}
    para que el caller pueda agrupar por (isin, window) antes de escribir.
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
