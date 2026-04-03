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
import sqlite3

MIN_PEERS = 5   # minimo de fondos en la categoria para calcular percentil


# ============================================================
# Carga de rentabilidades por categoria
# ============================================================

def load_category_returns(
    conn: sqlite3.Connection,
    fund_nature: str,
    horizon: str,
) -> pd.Series:
    """
    Carga las rentabilidades nominales anualizadas de todos los fondos
    de la misma naturaleza para un horizonte dado.
    Devuelve Series indexada por ISIN.
    """
    rows = conn.execute("""
        SELECT fmet.isin, fmet.value
        FROM fund_metrics fmet
        JOIN fund_master fm ON fm.ISIN = fmet.isin
        WHERE fmet.metric   = 'return_ann'
          AND fmet.horizon  = ?
          AND fmet.real_flag = 0
          AND fmet.value    IS NOT NULL
          AND fm.Fund_Nature = ?
    """, (horizon, fund_nature)).fetchall()

    if not rows:
        return pd.Series(dtype=float)

    return pd.Series({r[0]: float(r[1]) for r in rows})


# ============================================================
# Calculo de momentum
# ============================================================

def compute_momentum(
    isin: str,
    fund_nature: str,
    nav_df: pd.DataFrame,
    conn: sqlite3.Connection,
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
