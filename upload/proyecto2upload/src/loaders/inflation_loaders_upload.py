# proyecto2/src/loaders/inflation_loaders.py
# -*- coding: utf-8 -*-
"""
Carga de series de inflación (IPC) desde series_inflation.
"""

import sqlite3
import pandas as pd


def load_ipc(conn: sqlite3.Connection, geography: str = "ES") -> pd.DataFrame:
    """
    Carga el índice IPC mensual desde series_inflation.

    Parámetros:
        geography: código de geografía (ES / EU / US ...). Default: ES.

    Devuelve DataFrame con columnas:
        date (datetime64)  ipc_index (float)

    Ordenado por fecha ascendente.
    Devuelve DataFrame vacío si no hay datos para la geografía solicitada.
    """
    query = """
        SELECT
            date,
            ipc_index
        FROM series_inflation
        WHERE geography = ?
        ORDER BY date
    """
    rows = conn.execute(query, (geography,)).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "ipc_index"])

    df = pd.DataFrame(rows, columns=["date", "ipc_index"])
    df["date"]      = pd.to_datetime(df["date"])
    # Normalizar a fin de mes para alinear con las fechas NAV (que son fin de mes)
    df["date"]      = df["date"] + pd.offsets.MonthEnd(0)
    df["ipc_index"] = df["ipc_index"].astype(float)
    return df


def ipc_available(conn: sqlite3.Connection, geography: str = "ES") -> bool:
    """Devuelve True si hay datos IPC para la geografía indicada."""
    n = conn.execute(
        "SELECT COUNT(*) FROM series_inflation WHERE geography = ?",
        (geography,)
    ).fetchone()[0]
    return n > 0
