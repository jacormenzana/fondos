# proyecto2/src/calculations/persistence.py
# -*- coding: utf-8 -*-
"""
Calculo de persistencia del alpha: mide si el fondo bate consistentemente
a su categoria en ventanas rolling de 3 anos.

Metodologia:
    1. Dividir la historia del fondo en ventanas rolling de WINDOW_MONTHS meses
       con paso de STEP_MONTHS meses entre ventanas.
    2. En cada ventana calcular la rentabilidad anualizada del fondo.
    3. Comparar con la rentabilidad media de fondos de la misma naturaleza
       en esa misma ventana.
    4. Contar el porcentaje de ventanas en que el fondo supera a la categoria.

Este ratio es mas informativo que el alpha simple porque distingue:
    - Fondos que baten consistentemente (alpha_persistence alto + buen alpha)
    - Fondos con un buen periodo pero inconsistentes (alpha bajo pero un pico)
    - Fondos que mejoran (persistence reciente > persistence historica)

Metricas generadas (horizon=since_inception, real_flag=0):
    alpha_persistence    % ventanas en que el fondo supera a su categoria [0,1]
    alpha_persistence_n  numero de ventanas evaluadas

Requisito: minimo MIN_WINDOWS ventanas validas para calcular la metrica.
"""

import numpy as np
import pandas as pd

from shared import config as _config
from shared.eur_nav import apply_eur_view_frame   # FND-0243
from shared.config import (
    PERSISTENCE_WINDOW_MONTHS as WINDOW_MONTHS,
    PERSISTENCE_STEP_MONTHS as STEP_MONTHS,
    PERSISTENCE_MIN_WINDOWS as MIN_WINDOWS,
)



# ============================================================
# Carga de rentabilidades de la categoria por ventana
# ============================================================

def _category_return_in_window(
    conn: "psycopg.Connection",
    fund_nature: str,
    exclude_isin: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> float | None:
    """
    Calcula la rentabilidad media anualizada de la categoria en una ventana.
    Excluye el propio fondo para evitar autocorrelacion.

    FND-0200: hasta ahora el retorno de cada par era MAX(NAV) / MIN(NAV) de la ventana, que no es el retorno del
    periodo (sesgo al alza: un fondo que cae de 100 a 80 contaba +25%). Con PERSISTENCE_FIRST_LAST_NAV_ENABLED se
    usa el NAV de la primera y de la ultima fecha, como el retorno del propio fondo en compute_persistence.
    """
    # Postgres migration Stage 9 (found live 2026-09-22): see momentum.py's identical note.
    ph = "%s"
    rows = conn.execute(f"""
        SELECT fnm.ISIN,
               MIN(fnm.NAV) AS nav_start,
               MAX(fnm.NAV) AS nav_end,
               COUNT(*)     AS n_months,
               (ARRAY_AGG(fnm.NAV ORDER BY fnm.Date ASC))[1]  AS nav_first,
               (ARRAY_AGG(fnm.NAV ORDER BY fnm.Date DESC))[1] AS nav_last,
               MIN(fnm.Date) AS d_first,
               MAX(fnm.Date) AS d_last
        FROM fund_nav_monthly fnm
        JOIN fund_master fm ON fm.ISIN = fnm.ISIN
        WHERE fm.Fund_Nature = {ph}
          AND fnm.ISIN != {ph}
          AND fnm.Date >= {ph}
          AND fnm.Date <= {ph}
        GROUP BY fnm.ISIN
        HAVING COUNT(*) >= {ph}
    """, (fund_nature, exclude_isin,
          start_date.strftime("%Y-%m-%d"),
          end_date.strftime("%Y-%m-%d"),
          WINDOW_MONTHS - 3)).fetchall()  # tolerar hasta 3 meses sin datos

    if not rows:
        return None

    if _config.EUR_NAV_CONVERSION_ENABLED:
        rows = _peer_rows_in_eur(conn, rows)

    returns = []
    first_last = _config.PERSISTENCE_FIRST_LAST_NAV_ENABLED
    for row in rows:
        isin, nav_s, nav_e, n, nav_f, nav_l = row[:6]   # [6:8] = window dates, used only by the EUR view
        if first_last:
            nav_s, nav_e = nav_f, nav_l
        if nav_s and nav_e and nav_s > 0:
            years = n / 12
            ret = (nav_e / nav_s) ** (1 / years) - 1 if years > 0 else None
            if ret is not None:
                returns.append(ret)

    return float(np.mean(returns)) if returns else None


def _peer_rows_in_eur(conn, rows: list) -> list:
    """FND-0243: the window's first and last NAV of each peer converted to EUR at their own dates. Peers whose
    class currency is not convertible (or lacks a rate at either date) leave the peer group. The EUR view needs
    the first/last path (PERSISTENCE_FIRST_LAST_NAV_ENABLED): MIN/MAX NAV carry no date, so they stay unconverted
    and the startup coupling check refuses that combination."""
    pts = pd.DataFrame(
        [(r[0], r[6], r[4], "f") for r in rows] + [(r[0], r[7], r[5], "l") for r in rows],
        columns=["isin", "date", "nav", "pt"])
    conv = apply_eur_view_frame(conn, pts)
    wide = conv.pivot_table(index="isin", columns="pt", values="nav", aggfunc="first")
    out = []
    for isin, nav_s, nav_e, n, _nav_f, _nav_l, d_f, d_l in rows:
        if isin in wide.index and {"f", "l"} <= set(wide.columns):
            f, l = wide.at[isin, "f"], wide.at[isin, "l"]
            if pd.notna(f) and pd.notna(l):
                out.append((isin, nav_s, nav_e, n, float(f), float(l), d_f, d_l))
    return out


# ============================================================
# Calculo de persistencia para un fondo
# ============================================================

def compute_persistence(
    isin: str,
    fund_nature: str,
    nav_df: pd.DataFrame,
    conn: "psycopg.Connection",
) -> list[tuple]:
    """
    Calcula la persistencia del alpha para un fondo.

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

    if len(nav) < WINDOW_MONTHS + STEP_MONTHS:
        return []

    # Generar ventanas rolling
    dates  = nav.index
    start  = dates[0]
    end    = dates[-1]

    beats      = 0
    total      = 0
    window_start = start

    while True:
        window_end = window_start + pd.DateOffset(months=WINDOW_MONTHS)
        if window_end > end:
            break

        # NAV del fondo en la ventana
        nav_w = nav[(nav.index >= window_start) & (nav.index <= window_end)]
        if len(nav_w) < WINDOW_MONTHS - 3:
            window_start += pd.DateOffset(months=STEP_MONTHS)
            continue

        years_w = len(nav_w) / 12
        if years_w <= 0:
            window_start += pd.DateOffset(months=STEP_MONTHS)
            continue

        ret_fondo = (float(nav_w.iloc[-1]) / float(nav_w.iloc[0])) ** (1 / years_w) - 1

        # Rentabilidad media de la categoria en la misma ventana
        ret_cat = _category_return_in_window(
            conn, fund_nature, isin, window_start, window_end
        )

        if ret_cat is not None:
            total += 1
            if ret_fondo > ret_cat:
                beats += 1

        window_start += pd.DateOffset(months=STEP_MONTHS)

    if total < MIN_WINDOWS:
        return []

    persistence = float(beats / total)

    return [
        ("alpha_persistence",   persistence,    0),
        ("alpha_persistence_n", float(total),   0),
    ]
