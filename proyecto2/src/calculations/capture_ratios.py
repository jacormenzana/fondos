# proyecto2/src/calculations/capture_ratios.py
# -*- coding: utf-8 -*-
"""
Calculo de ratios de captura upside/downside del fondo vs benchmark de categoria.

El upside capture ratio mide cuanto participa el fondo en las subidas
del benchmark. El downside capture ratio mide cuanto sufre en las bajadas.

Un fondo ideal tiene upside_capture > 1 y downside_capture < 1.
El ratio upside/downside (capture_ratio) resume ambos en un solo numero:
    > 1 = el fondo captura proporcionalmente mas subidas que bajadas (deseable)
    < 1 = el fondo captura mas bajadas que subidas (indeseable)

Metodologia:
    - Benchmark: media de retornos mensuales de fondos de la misma naturaleza
      (peer benchmark). Se usa cuando no hay un indice declarado disponible.
    - Periodos de subida: meses donde el benchmark sube (r_bench > 0)
    - Periodos de bajada: meses donde el benchmark baja (r_bench < 0)
    - Upside capture   = media_ret_fondo(subidas) / media_ret_bench(subidas)
    - Downside capture = media_ret_fondo(bajadas) / media_ret_bench(bajadas)

Metricas generadas:
    upside_capture    ratio de captura en periodos positivos del benchmark
    downside_capture  ratio de captura en periodos negativos del benchmark
    capture_ratio     upside_capture / downside_capture (ratio compuesto)

Todas con horizon='since_inception' y real_flag=0.
"""

import numpy as np
import pandas as pd
import sqlite3

MIN_PERIODS = 12  # minimo de periodos positivos/negativos para calcular


# ============================================================
# Construccion del benchmark de categoria (peer benchmark)
# ============================================================

def load_peer_benchmark(
    conn: sqlite3.Connection,
    fund_nature: str,
    exclude_isin: str,
) -> pd.Series:
    """
    Construye un benchmark mensual como media de retornos de todos los fondos
    de la misma naturaleza, excluyendo el propio fondo.

    Devuelve Series indexada por fecha con retorno mensual medio de la categoria.
    """
    rows = conn.execute("""
        SELECT fnm.ISIN, fnm.Date, fnm.NAV
        FROM fund_nav_monthly fnm
        JOIN fund_master fm ON fm.ISIN = fnm.ISIN
        WHERE fm.Fund_Nature = ?
          AND fnm.ISIN != ?
        ORDER BY fnm.ISIN, fnm.Date
    """, (fund_nature, exclude_isin)).fetchall()

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(rows, columns=["isin", "date", "nav"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["nav"]  = df["nav"].astype(float)

    # Retorno mensual por fondo
    df = df.sort_values(["isin", "date"])
    df["ret"] = df.groupby("isin")["nav"].pct_change()

    # Media de retornos por mes (peer benchmark)
    benchmark = df.groupby("date")["ret"].mean().dropna()
    return benchmark


# ============================================================
# Calculo de capture ratios
# ============================================================

def compute_capture_ratios(
    isin: str,
    fund_nature: str,
    nav_df: pd.DataFrame,
    conn: sqlite3.Connection,
) -> list[tuple]:
    """
    Calcula los ratios de captura upside/downside para un fondo.

    Parametros:
        isin:        ISIN del fondo
        fund_nature: naturaleza del fondo
        nav_df:      DataFrame con columnas ['date', 'nav']
        conn:        conexion sqlite3

    Devuelve lista de (metric, value, real_flag).
    """
    if nav_df.empty or not fund_nature:
        return []

    # Retornos mensuales del fondo
    nav = nav_df.set_index("date")["nav"].sort_index()
    r_fondo = nav.pct_change().dropna()

    if len(r_fondo) < MIN_PERIODS * 2:
        return []

    # Benchmark de categoria
    benchmark = load_peer_benchmark(conn, fund_nature, isin)
    if benchmark.empty:
        return []

    # Alinear fechas
    merged = pd.concat(
        [r_fondo.rename("r_fondo"), benchmark.rename("r_bench")],
        axis=1, join="inner"
    ).dropna()

    if len(merged) < MIN_PERIODS * 2:
        return []

    # Separar periodos de subida y bajada del benchmark
    up   = merged[merged["r_bench"] > 0]
    down = merged[merged["r_bench"] < 0]

    if len(up) < MIN_PERIODS or len(down) < MIN_PERIODS:
        return []

    # Calcular ratios
    upside_capture   = float(up["r_fondo"].mean()   / up["r_bench"].mean())
    downside_capture = float(down["r_fondo"].mean() / down["r_bench"].mean())

    # Evitar division por cero o valores extremos
    if abs(downside_capture) < 1e-6:
        return []

    capture_ratio = upside_capture / downside_capture

    # Clamp valores extremos (outliers por periodos muy cortos)
    def _clamp(v, lo=-5.0, hi=5.0):
        return max(lo, min(hi, v)) if not np.isnan(v) else np.nan

    return [
        ("upside_capture",   _clamp(upside_capture),   0),
        ("downside_capture", _clamp(downside_capture), 0),
        ("capture_ratio",    _clamp(capture_ratio),    0),
    ]
