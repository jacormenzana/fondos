# proyecto2/src/calculations/momentum.py
# -*- coding: utf-8 -*-
"""
Calculo de momentum del fondo relativo a su categoria.

El momentum mide si el fondo ha tenido una rentabilidad reciente
superior o inferior a la media de fondos de su misma naturaleza.
Es uno de los predictores mas robustos documentados empiricamente
(Carhart 1997, Jegadeesh & Titman 1993).

Metricas generadas:
    momentum_1y    exceso de rentabilidad vs categoria en los ultimos 12 meses
    momentum_3y    exceso de rentabilidad vs categoria en los ultimos 36 meses
    momentum_rank  percentil del fondo dentro de su categoria (0=peor, 1=mejor)

Todas con horizon='since_inception' y real_flag=0.
El calculo requiere que haya al menos MIN_PEERS fondos en la misma categoria.
"""

import numpy as np
import pandas as pd

from shared.config import MIN_PEERS   # minimo de fondos en la categoria para calcular percentil


# ============================================================
# Carga de rentabilidades por categoria (FND-0064: cacheada por ejecucion)
# ============================================================
# run_pipeline.py procesa los fondos uno a uno, ESCRIBIENDO return_ann en fund_metrics a medida que
# avanza. Sin cache, un fondo procesado a mitad de tanda ve el return_ann RECIEN escrito de los pares
# de su categoria ya procesados en ESTE MISMO run, mientras que uno procesado antes vio el valor de
# la tanda anterior -- el mismo universo de fondos, en dos ordenes de proceso distintos, produce
# momentum/percentiles distintos para el mismo fondo. La cache congela el snapshot de cada
# (fund_nature, horizon) la PRIMERA vez que se consulta en la tanda: todos los fondos de la misma
# categoria ven entonces exactamente el mismo peer set, sea el primero o el ultimo en procesarse.
_category_returns_cache: dict[tuple[str, str], pd.Series] = {}


def reset_category_returns_cache() -> None:
    """Llamar UNA vez al principio de cada ejecucion del pipeline (run_pipeline.py), antes del bucle
    por fondo. Sin esto, un proceso de larga vida (tests, un scheduler) reutilizaria el snapshot de
    una tanda anterior."""
    _category_returns_cache.clear()


def load_category_returns(
    conn: "psycopg.Connection",
    fund_nature: str,
    horizon: str,
) -> pd.Series:
    """
    Carga las rentabilidades nominales anualizadas de todos los fondos
    de la misma naturaleza para un horizonte dado.
    Devuelve Series indexada por ISIN.

    Cacheada por (fund_nature, horizon) durante la ejecucion -- ver reset_category_returns_cache().
    """
    key = (fund_nature, horizon)
    if key in _category_returns_cache:
        return _category_returns_cache[key]

    # Postgres migration Stage 9 (found live 2026-09-22): proyecto2/src/calculations/*.py issue
    # their own direct SQL and were never in scope for any prior stage's read-path port (only
    # readers/db_readers.py was ported) — surfaced by the first-ever end-to-end run_pipeline.py
    # --backend postgres rehearsal, not any per-function unit test.
    ph = "%s"
    rows = conn.execute(f"""
        SELECT fmet.isin, fmet.value
        FROM fund_metrics fmet
        JOIN fund_master fm ON fm.ISIN = fmet.isin
        WHERE fmet.metric   = 'return_ann'
          AND fmet.horizon  = {ph}
          AND fmet.real_flag = 0
          AND fmet.value    IS NOT NULL
          AND fm.Fund_Nature = {ph}
    """, (horizon, fund_nature)).fetchall()

    result = pd.Series(dtype=float) if not rows else pd.Series({r[0]: float(r[1]) for r in rows})
    _category_returns_cache[key] = result
    return result


# ============================================================
# Calculo de momentum
# ============================================================

def compute_momentum(
    isin: str,
    fund_nature: str,
    nav_df: pd.DataFrame,
    conn: "psycopg.Connection",
) -> list[tuple]:
    """
    Calcula metricas de momentum para un fondo.

    Parametros:
        isin:        ISIN del fondo
        fund_nature: naturaleza del fondo (Fund_Nature en fund_master)
        nav_df:      DataFrame con columnas ['date', 'nav']
        conn:        conexion sqlite3

    Devuelve lista de (metric, value, real_flag).
    """
    if nav_df.empty or not fund_nature:
        return []

    nav = nav_df.set_index("date")["nav"].sort_index()
    metrics = []

    # Momentum 1Y y 3Y: rentabilidad del fondo en la ventana
    for label, months in [("momentum_1y", 12), ("momentum_3y", 36)]:
        if len(nav) < months + 1:
            continue

        nav_window = nav.iloc[-months - 1:]
        ret_fondo  = float(nav_window.iloc[-1] / nav_window.iloc[0] - 1)

        # Rentabilidad media de la categoria en el mismo horizonte rolling
        horizon = f"rolling_{months // 12}y"
        cat_rets = load_category_returns(conn, fund_nature, horizon)

        if len(cat_rets) < MIN_PEERS:
            continue

        ret_categoria = float(cat_rets.mean())
        exceso        = ret_fondo - ret_categoria

        metrics.append((label, exceso, 0))

    # Percentil del fondo en su categoria (since_inception)
    cat_rets_si = load_category_returns(conn, fund_nature, "since_inception")
    if len(cat_rets_si) >= MIN_PEERS and isin in cat_rets_si.index:
        ret_fondo_si = cat_rets_si[isin]
        percentil    = float((cat_rets_si < ret_fondo_si).mean())
        metrics.append(("momentum_rank", percentil, 0))

    return metrics
