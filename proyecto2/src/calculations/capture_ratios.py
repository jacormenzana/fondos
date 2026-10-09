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

from shared import config as _config
from shared.eur_nav import apply_eur_view_frame   # FND-0243
from shared.config import CAPTURE_MIN_PERIODS as MIN_PERIODS  # minimo de periodos positivos/negativos para calcular



# ============================================================
# Construccion del benchmark de categoria (peer benchmark)
# ============================================================

def load_peer_benchmark(
    conn: "psycopg.Connection",
    fund_nature: str,
    exclude_isin: str,
) -> pd.Series:
    """
    Construye un benchmark mensual como media de retornos de todos los fondos
    de la misma naturaleza, excluyendo el propio fondo.

    Devuelve Series indexada por fecha con retorno mensual medio de la categoria.
    """
    # Postgres migration Stage 9 (found live 2026-09-22): see momentum.py's identical note —
    # proyecto2/src/calculations/*.py were never in scope for any prior stage's read-path port.
    ph = "%s"
    rows = conn.execute(f"""
        SELECT fnm.ISIN, fnm.Date, fnm.NAV
        FROM fund_nav_monthly fnm
        JOIN fund_master fm ON fm.ISIN = fnm.ISIN
        WHERE fm.Fund_Nature = {ph}
          AND fnm.ISIN != {ph}
        ORDER BY fnm.ISIN, fnm.Date
    """, (fund_nature, exclude_isin)).fetchall()

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(rows, columns=["isin", "date", "nav"])
    df["nav"]  = df["nav"].astype(float)
    # FND-0243: peers in EUR at their own NAV dates (before the month-end stamp); funds without a convertible
    # class currency drop out of the peer group. Identity while EUR_NAV_CONVERSION_ENABLED is off.
    df = apply_eur_view_frame(conn, df)
    if df.empty:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)

    if _config.CAPTURE_MONTH_END_ENABLED:
        # FND-0202: a month-end grid per fund (last NAV of the month), and a return only between CONSECUTIVE months:
        # a gap no longer turns into a multi-month return that is then averaged with 1-month returns.
        wide = df.pivot_table(index="date", columns="isin", values="nav", aggfunc="last").sort_index()
        wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq=pd.offsets.MonthEnd()))
        return wide.pct_change(fill_method=None).mean(axis=1).dropna()

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
    conn: "psycopg.Connection",
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
    if _config.CAPTURE_MONTH_END_ENABLED:
        # FND-0202: same month-end grid as the peer benchmark. The raw dates mixed month-end and mid-month points, so the
        # inner join with the (month-end) benchmark silently kept only the month-end ones.
        nav = nav.copy()
        nav.index = pd.DatetimeIndex(nav.index) + pd.offsets.MonthEnd(0)
        nav = nav[~nav.index.duplicated(keep="last")]
        nav = nav.reindex(pd.date_range(nav.index.min(), nav.index.max(), freq=pd.offsets.MonthEnd()))
        r_fondo = nav.pct_change(fill_method=None).dropna()
    else:
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
