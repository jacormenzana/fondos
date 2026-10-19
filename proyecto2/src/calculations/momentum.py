# proyecto2/src/calculations/momentum.py
# -*- coding: utf-8 -*-
"""
Calculo de momentum del fondo relativo a su categoria.

El momentum mide si el fondo ha tenido una rentabilidad reciente
superior o inferior a la media de fondos de su misma naturaleza.
Es uno de los predictores mas robustos documentados empiricamente
(Carhart 1997, Jegadeesh & Titman 1993).

Metricas generadas:
    momentum_1y    exceso de rentabilidad ANUALIZADA vs categoria en los ultimos 12 meses (FND-0302)
    momentum_3y    exceso de rentabilidad ANUALIZADA vs categoria en los ultimos 36 meses (FND-0302)
    momentum_rank  percentil del fondo dentro de su categoria (0=peor, 1=mejor)

Todas con horizon='since_inception' y real_flag=0.
El calculo requiere que haya al menos MIN_PEERS fondos en la misma categoria.
"""

import math

import numpy as np
import pandas as pd

from shared.config import MIN_NAV_ROWS, MIN_PEERS   # minimo de filas de una ventana / de fondos en la categoria para calcular percentil
from .returns import annualized_return, trailing_window   # FND-0302: la misma ventana y unidades que el rolling_Ny de los pares


def _mean(values: "pd.Series") -> float:
    """Media independiente del orden (FND-0297): suma exacta (math.fsum) sobre los valores ordenados."""
    v = sorted(float(x) for x in values)
    return math.fsum(v) / len(v)


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
        ORDER BY fmet.isin
    """, (horizon, fund_nature)).fetchall()      # FND-0297: a fixed row order, so the Series (and every sum over it) is reproducible

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

    nav_sorted = nav_df.sort_values("date")
    metrics = []

    # Momentum 1Y y 3Y: exceso de rentabilidad ANUALIZADA sobre la media de la categoria, en la MISMA ventana y las MISMAS unidades.
    # FND-0302: antes era la rentabilidad ACUMULADA de las ultimas 12/36 FILAS menos la rentabilidad ANUALIZADA rolling_Ny de los pares
    # (ventana por FECHAS): para 3 anos el 99.6% de los valores guardados estaba sesgado (mediana +0.39 en renta variable, ~0 esperado).
    # Ahora el fondo usa returns.trailing_window + annualized_return, exactamente lo que run_pipeline guarda como su propio rolling_Ny.
    for label, months in [("momentum_1y", 12), ("momentum_3y", 36)]:
        window = trailing_window(nav_sorted, months)
        covers = nav_sorted["date"].min() <= nav_sorted["date"].max() - pd.DateOffset(months=months)   # the history really spans N years
        if not covers or len(window) < MIN_NAV_ROWS:
            continue

        ret_fondo = annualized_return(window["nav"], 12, dates=window["date"])
        if not np.isfinite(ret_fondo):
            continue

        # Rentabilidad media de la categoria en el mismo horizonte rolling
        horizon = f"rolling_{months // 12}y"
        cat_rets = load_category_returns(conn, fund_nature, horizon)

        if len(cat_rets) < MIN_PEERS:
            continue

        ret_categoria = _mean(cat_rets)
        exceso        = ret_fondo - ret_categoria

        metrics.append((label, exceso, 0))

    # Percentil del fondo en su categoria (since_inception)
    cat_rets_si = load_category_returns(conn, fund_nature, "since_inception")
    if len(cat_rets_si) >= MIN_PEERS and isin in cat_rets_si.index:
        ret_fondo_si = cat_rets_si[isin]
        percentil    = float((cat_rets_si < ret_fondo_si).mean())
        metrics.append(("momentum_rank", percentil, 0))

    return metrics
