# proyecto2/src/loaders/nav_loader.py
# -*- coding: utf-8 -*-
"""
Carga de series NAV mensuales desde fund_nav_monthly.
"""

import sqlite3
import pandas as pd


def load_nav(conn: sqlite3.Connection, isin: str) -> pd.DataFrame:
    """
    Carga la serie NAV mensual de un fondo desde fund_nav_monthly.

    Devuelve DataFrame con columnas:
        date (datetime64)  NAV (float)

    Ordenado por fecha ascendente.
    Devuelve DataFrame vacío si el fondo no tiene datos.
    """
    query = """
        SELECT
            Date  AS date,
            NAV   AS nav
        FROM fund_nav_monthly
        WHERE ISIN = ?
        ORDER BY Date
    """
    rows = conn.execute(query, (isin,)).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "nav"])

    df = pd.DataFrame(rows, columns=["date", "nav"])
    df["date"] = pd.to_datetime(df["date"])
    df["nav"]  = df["nav"].astype(float)
    return df


def get_isins_with_nav(conn: sqlite3.Connection) -> list[str]:
    """
    Devuelve la lista de ISINs que tienen al menos una fila en fund_nav_monthly.
    """
    rows = conn.execute(
        "SELECT DISTINCT ISIN FROM fund_nav_monthly ORDER BY ISIN"
    ).fetchall()
    return [r[0] for r in rows]
