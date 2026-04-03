# proyecto2/src/calculations/macro_sensitivity.py
# -*- coding: utf-8 -*-
"""
Calculo de sensibilidades individuales de cada fondo a factores macro
mediante regresion OLS (minimos cuadrados ordinarios).

Modelo:
    r_fondo(t) = alpha
               + b1*d_rate_eu(t)   variacion tipo deposito BCE
               + b2*m3_yoy(t)      crecimiento monetario M3 Eurozona
               + b3*ipc_yoy_es(t)  inflacion espanola
               + b4*ipc_yoy_eu(t)  inflacion eurozona
               + b5*ipc_yoy_us(t)  inflacion EEUU
               + b6*ipc_yoy_jp(t)  inflacion Japon
               + b7*ipc_yoy_cn(t)  inflacion China
               + b8*d_rate_us(t)   variacion Fed Funds
               + b9*d_rate_jp(t)   variacion tipo Banco de Japon
               + b10*d_rate_cn(t)  variacion tipo Banco Popular China
               + epsilon(t)

Donde r_fondo(t) = log(NAV(t)/NAV(t-1)) retorno mensual logaritmico.
Las variaciones de tipos (d_*) son diferencias mensuales del nivel.
Los ipc_yoy son variaciones interanuales calculadas desde el indice base 100.

Metricas generadas por fondo (horizon=since_inception, real_flag=0):
    beta_rate_eu         sensibilidad a variaciones tipo BCE
    beta_m3_yoy          sensibilidad a crecimiento M3 Eurozona
    beta_ipc_es          sensibilidad a inflacion ES
    beta_ipc_eu          sensibilidad a inflacion EU
    beta_ipc_us          sensibilidad a inflacion US
    beta_ipc_jp          sensibilidad a inflacion JP
    beta_ipc_cn          sensibilidad a inflacion CN
    beta_rate_us         sensibilidad a variaciones Fed Funds
    beta_rate_jp         sensibilidad a variaciones tipo BoJ
    beta_rate_cn         sensibilidad a variaciones tipo BPoC
    beta_oil             sensibilidad a variacion interanual precio petroleo WTI
    beta_copper          sensibilidad a variacion interanual precio cobre
    beta_cli_eu          sensibilidad a ciclo adelantado OCDE Eurozona
    beta_cli_us          sensibilidad a ciclo adelantado OCDE EEUU
    beta_dxy             sensibilidad a variacion interanual DXY
    beta_gold            sensibilidad a variacion interanual oro (PPICMM)
    beta_m2_global       sensibilidad a M2 Global YoY
    beta_spread_hy       sensibilidad al diferencial HY (nivel, %)
    beta_vix             sensibilidad a variacion interanual VIX
    beta_term_spread     sensibilidad a pendiente curva EEUU 10Y-2Y (nivel, %)
    beta_eur_jpy         sensibilidad a variacion interanual EUR/JPY
    beta_eur_gbp         sensibilidad a variacion interanual EUR/GBP
    beta_eur_cny         sensibilidad a variacion interanual EUR/CNY

Requisito: minimo MIN_OBS meses de datos solapados NAV e indicadores macro.
Se aplica filtro VIF para eliminar factores con multicolinealidad severa (VIF>10).
"""

import numpy as np
import pandas as pd
import sqlite3
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT))

MIN_OBS = 60


# ============================================================
# Carga de factores macro
# ============================================================

def load_macro_factors(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Carga y construye el DataFrame de factores macro mensuales.
    Devuelve DataFrame indexado por fecha (fin de mes) con columnas:
        d_rate_eu, m3_yoy, ipc_yoy_es, ipc_yoy_eu, ipc_yoy_us,
        ipc_yoy_jp, ipc_yoy_cn, d_rate_us, d_rate_jp, d_rate_cn
    """
    query = """
        SELECT date, indicator, geography, value
        FROM series_macro
        WHERE (indicator = 'rate_deposit' AND geography = 'EU')
           OR (indicator = 'rate_policy'  AND geography IN ('US','JP','CN'))
           OR (indicator = 'm3_yoy'       AND geography = 'EU')
           OR (indicator = 'ipc_index'    AND geography IN ('ES','EU','US','JP','CN'))
           OR (indicator = 'oil_wti'      AND geography = 'GLOBAL')
           OR (indicator = 'copper'       AND geography = 'GLOBAL')
           OR (indicator = 'cli'          AND geography IN ('EU','US'))
           OR (indicator = 'dxy'          AND geography = 'GLOBAL')
           OR (indicator = 'gold'         AND geography = 'GLOBAL')
           OR (indicator = 'm2_global_yoy' AND geography = 'GLOBAL')
           OR (indicator = 'spread_hy'   AND geography = 'GLOBAL')
           OR (indicator = 'vix'         AND geography = 'GLOBAL')
           OR (indicator = 'term_spread' AND geography = 'US')
           OR (indicator IN ('fx_usd_eur','fx_jpy_usd','fx_usd_gbp','fx_cny_usd')
               AND geography = 'GLOBAL')
        ORDER BY date
    """
    rows = conn.execute(query).fetchall()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "indicator", "geography", "value"])
    df["date"]  = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["value"] = df["value"].astype(float)
    df["key"]   = df["indicator"] + "_" + df["geography"]

    wide = df.pivot_table(index="date", columns="key",
                          values="value", aggfunc="last")
    wide.columns.name = None

    # Variaciones mensuales de tipos (nivel -> cambio)
    for col, new_col in [
        ("rate_deposit_EU", "d_rate_eu"),
        ("rate_policy_US",  "d_rate_us"),
        ("rate_policy_JP",  "d_rate_jp"),
        ("rate_policy_CN",  "d_rate_cn"),
    ]:
        if col in wide.columns:
            wide[new_col] = wide[col].diff()

    # IPC variacion interanual desde indice base 100
    for col, new_col in [
        ("ipc_index_ES", "ipc_yoy_es"),
        ("ipc_index_EU", "ipc_yoy_eu"),
        ("ipc_index_US", "ipc_yoy_us"),
        ("ipc_index_JP", "ipc_yoy_jp"),
    ]:
        if col in wide.columns:
            wide[new_col] = wide[col].pct_change(12, fill_method=None) * 100

    # IPC China desde indice base (igual que el resto)
    if "ipc_index_CN" in wide.columns:
        wide["ipc_yoy_cn"] = wide["ipc_index_CN"].pct_change(12, fill_method=None) * 100

    # M3 ya es variacion interanual
    if "m3_yoy_EU" in wide.columns:
        wide["m3_yoy"] = wide["m3_yoy_EU"]

    # Petroleo y cobre: variacion interanual desde nivel (precio)
    if "oil_wti_GLOBAL" in wide.columns:
        wide["oil_yoy"] = wide["oil_wti_GLOBAL"].pct_change(12, fill_method=None) * 100
    if "copper_GLOBAL" in wide.columns:
        wide["copper_yoy"] = wide["copper_GLOBAL"].pct_change(12, fill_method=None) * 100

    # CLI OCDE: variacion interanual (valores > 100 = expansion)
    if "cli_EU" in wide.columns:
        wide["cli_yoy_eu"] = wide["cli_EU"].pct_change(12, fill_method=None) * 100
    if "cli_US" in wide.columns:
        wide["cli_yoy_us"] = wide["cli_US"].pct_change(12, fill_method=None) * 100

    # DXY: variacion interanual (dolar fuerte = positivo)
    if "dxy_GLOBAL" in wide.columns:
        wide["dxy_yoy"] = wide["dxy_GLOBAL"].pct_change(12, fill_method=None) * 100

    # Oro: variacion interanual (USD/oz)
    if "gold_GLOBAL" in wide.columns:
        wide["gold_yoy"] = wide["gold_GLOBAL"].pct_change(12, fill_method=None) * 100

    # M2 Global YoY: ya viene calculado
    if "m2_global_yoy_GLOBAL" in wide.columns:
        wide["m2_global_yoy"] = wide["m2_global_yoy_GLOBAL"]

    # Spread HY: se usa como nivel (ya es diferencial en %, media mensual)
    # Valores altos = mayor aversion al riesgo / estres crediticio
    if "spread_hy_GLOBAL" in wide.columns:
        wide["spread_hy"] = wide["spread_hy_GLOBAL"]

    # VIX: variacion interanual (el nivel en puntos no es estacionario)
    if "vix_GLOBAL" in wide.columns:
        wide["vix_yoy"] = wide["vix_GLOBAL"].pct_change(12, fill_method=None) * 100

    # Term spread: se usa como nivel (ya es diferencial en %)
    # Captura pendiente de curva: negativo = inversion / señal recesion
    if "term_spread_US" in wide.columns:
        wide["term_spread"] = wide["term_spread_US"]

    # Tipos de cambio vs EUR -- perspectiva del inversor europeo
    # Todos los pares se construyen desde los ratios USD disponibles en BD.
    # Convencion: unidades de divisa extranjera por 1 EUR (subida = EUR fuerte).
    # Se usa variacion interanual para estacionariedad en el modelo OLS.
    #
    # fx_usd_eur  = USD/EUR  (directo)
    # fx_jpy_usd  = JPY/USD  -> EUR/JPY = fx_jpy_usd * fx_usd_eur
    # fx_usd_gbp  = USD/GBP  -> EUR/GBP = fx_usd_gbp / fx_usd_eur
    # fx_cny_usd  = CNY/USD  -> EUR/CNY = fx_cny_usd / fx_usd_eur
    #
    # NOTA: NO se añade EUR/USD explicitamente -- DXY ya captura la fortaleza
    # global del dolar (~57% USD en la cesta). Incluir EUR/USD y DXY juntos
    # generaria multicolinealidad elevada (el VIF lo eliminaria de todos modos).
    if "fx_usd_eur_GLOBAL" in wide.columns and "fx_jpy_usd_GLOBAL" in wide.columns:
        eur_jpy = wide["fx_jpy_usd_GLOBAL"] * wide["fx_usd_eur_GLOBAL"]
        wide["eur_jpy_yoy"] = eur_jpy.pct_change(12, fill_method=None) * 100

    if "fx_usd_eur_GLOBAL" in wide.columns and "fx_usd_gbp_GLOBAL" in wide.columns:
        eur_gbp = wide["fx_usd_gbp_GLOBAL"] / wide["fx_usd_eur_GLOBAL"]
        wide["eur_gbp_yoy"] = eur_gbp.pct_change(12, fill_method=None) * 100

    if "fx_usd_eur_GLOBAL" in wide.columns and "fx_cny_usd_GLOBAL" in wide.columns:
        eur_cny = wide["fx_cny_usd_GLOBAL"] / wide["fx_usd_eur_GLOBAL"]
        wide["eur_cny_yoy"] = eur_cny.pct_change(12, fill_method=None) * 100

    # Seleccionar factores finales
    factors = [
        "d_rate_eu", "m3_yoy",
        "ipc_yoy_es", "ipc_yoy_eu", "ipc_yoy_us", "ipc_yoy_jp", "ipc_yoy_cn",
        "d_rate_us",  "d_rate_jp",  "d_rate_cn",
        "oil_yoy",    "copper_yoy",
        "cli_yoy_eu", "cli_yoy_us",
        "dxy_yoy",    "gold_yoy",   "m2_global_yoy",
        "spread_hy",  "vix_yoy",    "term_spread",
        "eur_jpy_yoy", "eur_gbp_yoy", "eur_cny_yoy",
    ]
    available = [f for f in factors if f in wide.columns]
    return wide[available].dropna()


# ============================================================
# OLS sin dependencias externas
# ============================================================

def _ols(y: np.ndarray, X: np.ndarray) -> dict:
    """Regresion OLS. X incluye columna de unos para el intercepto."""
    try:
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        y_hat  = X @ beta
        ss_res = float(np.sum((y - y_hat) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = max(0.0, min(1.0, 1.0 - ss_res / ss_tot if ss_tot > 1e-10 else 0.0))
        return {"beta": beta, "r2": r2, "n": len(y)}
    except Exception:
        return None


# ============================================================
# Calculo de sensibilidades para un fondo
# ============================================================

# Mapa factor -> nombre de metrica
_FACTOR_TO_METRIC = {
    "d_rate_eu":   "beta_rate_eu",
    "m3_yoy":      "beta_m3_yoy",
    "ipc_yoy_es":  "beta_ipc_es",
    "ipc_yoy_eu":  "beta_ipc_eu",
    "ipc_yoy_us":  "beta_ipc_us",
    "ipc_yoy_jp":  "beta_ipc_jp",
    "ipc_yoy_cn":  "beta_ipc_cn",
    "d_rate_us":   "beta_rate_us",
    "d_rate_jp":   "beta_rate_jp",
    "d_rate_cn":   "beta_rate_cn",
    "oil_yoy":     "beta_oil",
    "copper_yoy":  "beta_copper",
    "cli_yoy_eu":    "beta_cli_eu",
    "cli_yoy_us":    "beta_cli_us",
    "dxy_yoy":       "beta_dxy",
    "gold_yoy":      "beta_gold",
    "m2_global_yoy": "beta_m2_global",
    "spread_hy":     "beta_spread_hy",
    "vix_yoy":       "beta_vix",
    "term_spread":   "beta_term_spread",
    "eur_jpy_yoy":   "beta_eur_jpy",
    "eur_gbp_yoy":   "beta_eur_gbp",
    "eur_cny_yoy":   "beta_eur_cny",
}


def _compute_vif(X_cols: np.ndarray) -> np.ndarray:
    """
    Calcula el VIF (Variance Inflation Factor) para cada columna de X.
    X_cols NO incluye la columna de unos del intercepto.
    VIF > 10 indica multicolinealidad severa.
    """
    n, k = X_cols.shape
    vif = np.ones(k)
    for i in range(k):
        y_i = X_cols[:, i]
        X_rest = np.delete(X_cols, i, axis=1)
        X_rest_c = np.column_stack([np.ones(n), X_rest])
        try:
            beta, _, _, _ = np.linalg.lstsq(X_rest_c, y_i, rcond=None)
            y_hat = X_rest_c @ beta
            ss_res = np.sum((y_i - y_hat) ** 2)
            ss_tot = np.sum((y_i - np.mean(y_i)) ** 2)
            r2_i = 1 - ss_res / ss_tot if ss_tot > 1e-10 else 0.0
            vif[i] = 1 / (1 - r2_i) if r2_i < 0.9999 else 999.0
        except Exception:
            vif[i] = 999.0
    return vif


VIF_THRESHOLD = 10.0  # eliminar factores con VIF > umbral


def compute_macro_sensitivity(
    nav_df: pd.DataFrame,
    macro_df: pd.DataFrame,
) -> list[tuple]:
    """
    Calcula las betas macro para un fondo.

    Parametros:
        nav_df:   DataFrame con columnas [date, nav] (fechas fin de mes)
        macro_df: DataFrame indexado por fecha con factores macro

    Devuelve lista de (metric, value, real_flag).
    Devuelve lista vacia si no hay suficientes datos solapados.
    """
    if nav_df.empty or macro_df.empty:
        return []

    nav      = nav_df.set_index("date")["nav"].sort_index()
    r_fondo  = np.log(nav / nav.shift(1)).dropna()
    merged   = pd.concat([r_fondo.rename("r_fondo"), macro_df],
                         axis=1, join="inner").dropna()

    if len(merged) < MIN_OBS:
        return []

    y           = merged["r_fondo"].values
    factor_cols = [c for c in macro_df.columns if c in merged.columns]
    X_raw       = merged[factor_cols].values

    # Filtrar factores con alta multicolinealidad (VIF > umbral)
    if X_raw.shape[1] > 1:
        vif_vals = _compute_vif(X_raw)
        keep_mask = vif_vals <= VIF_THRESHOLD
        # Siempre mantener al menos d_rate_eu y oil_yoy si estan presentes
        priority = {"d_rate_eu", "oil_yoy", "m3_yoy"}
        for i, col in enumerate(factor_cols):
            if col in priority:
                keep_mask[i] = True
        factor_cols = [c for c, k in zip(factor_cols, keep_mask) if k]
        X_raw       = merged[factor_cols].values

    X = np.column_stack([np.ones(len(y)), X_raw])

    result = _ols(y, X)
    if result is None:
        return []

    beta  = result["beta"]
    r2    = result["r2"]
    n_obs = result["n"]

    alpha_ann = (1 + float(beta[0])) ** 12 - 1

    metrics = [
        ("macro_r2",    r2,             0),
        ("macro_alpha", alpha_ann,      0),
        ("macro_n_obs", float(n_obs),   0),
    ]

    for i, col in enumerate(factor_cols):
        metric_name = _FACTOR_TO_METRIC.get(col)
        if metric_name:
            metrics.append((metric_name, float(beta[i + 1]), 0))

    return metrics
