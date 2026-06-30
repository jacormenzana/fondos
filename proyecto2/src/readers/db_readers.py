# proyecto2/src/loaders/db_readers.py
# -*- coding: utf-8 -*-
"""
Lectores de BD para el pipeline P2.

Funciones de solo lectura que sirven DataFrames a run_pipeline
desde las tablas internas. No realizan llamadas externas.

  NAV
    load_nav(conn, isin)          -> DataFrame[date, nav]
    get_isins_with_nav(conn)      -> list[str]

  IPC
    load_ipc(conn, geography)     -> DataFrame[date, ipc_index]
    ipc_available(conn, geography) -> bool
"""

import sqlite3
import pandas as pd


# ============================================================
# NAV
# ============================================================

def load_nav(conn: sqlite3.Connection, isin: str) -> pd.DataFrame:
    """
    Carga la serie NAV mensual de un fondo desde fund_nav_monthly.

    Devuelve DataFrame con columnas:
        date (datetime64)  nav (float)

    Ordenado por fecha ascendente.
    Devuelve DataFrame vacío si el fondo no tiene datos.
    """
    rows = conn.execute("""
        SELECT Date AS date, NAV AS nav
        FROM fund_nav_monthly
        WHERE ISIN = ?
        ORDER BY Date
    """, (isin,)).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "nav"])

    df = pd.DataFrame(rows, columns=["date", "nav"])
    df["date"] = pd.to_datetime(df["date"])
    df["nav"]  = df["nav"].astype(float)
    return df


def get_isins_with_nav(conn: sqlite3.Connection) -> list[str]:
    """Devuelve la lista de ISINs con al menos una fila en fund_nav_monthly."""
    rows = conn.execute(
        "SELECT DISTINCT ISIN FROM fund_nav_monthly ORDER BY ISIN"
    ).fetchall()
    return [r[0] for r in rows]


# ============================================================
# IPC
# ============================================================

def load_ipc(conn: sqlite3.Connection, geography: str = "ES") -> pd.DataFrame:
    """
    Carga el índice IPC mensual desde series_inflation.

    Parámetros:
        geography: código de geografía (ES / EU / US ...). Default: ES.

    Devuelve DataFrame con columnas:
        date (datetime64)  ipc_index (float)

    Fechas normalizadas a fin de mes para alinear con las fechas NAV.
    Devuelve DataFrame vacío si no hay datos para la geografía solicitada.
    """
    rows = conn.execute("""
        SELECT date, ipc_index
        FROM series_inflation
        WHERE geography = ?
        ORDER BY date
    """, (geography,)).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "ipc_index"])

    df = pd.DataFrame(rows, columns=["date", "ipc_index"])
    df["date"]      = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["ipc_index"] = df["ipc_index"].astype(float)
    return df


def ipc_available(conn: sqlite3.Connection, geography: str = "ES") -> bool:
    """Devuelve True si hay datos IPC para la geografía indicada."""
    n = conn.execute(
        "SELECT COUNT(*) FROM series_inflation WHERE geography = ?",
        (geography,)
    ).fetchone()[0]
    return n > 0
