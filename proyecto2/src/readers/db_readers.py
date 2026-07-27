# proyecto2/src/readers/db_readers.py
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
    """Devuelve la lista de ISINs con al menos una fila en fund_nav_monthly.

    Incluye fondos de oferta antigua (In_Current_Universe=0): las metricas P2
    se calculan para todos los fondos porque los huerfanos pueden seguir en
    cartera y necesitan seguimiento de rendimiento.
    """
    rows = conn.execute(
        "SELECT DISTINCT ISIN FROM fund_nav_monthly ORDER BY ISIN"
    ).fetchall()
    return [r[0] for r in rows]


def load_nav_daily(conn: sqlite3.Connection, isin: str) -> pd.DataFrame:
    """
    Carga la serie NAV diaria de un fondo desde fund_nav_daily (v24).

    Usada por proyecto2/src/calculations/short_horizon.py para métricas
    de horizonte corto (rolling_1m / rolling_3m / rolling_6m).

    Devuelve DataFrame con columnas:
        date (datetime64)  nav (float)

    Ordenado por fecha ascendente.
    Devuelve DataFrame vacío si el fondo no tiene datos diarios.
    """
    rows = conn.execute("""
        SELECT Date AS date, NAV AS nav
        FROM fund_nav_daily
        WHERE ISIN = ?
        ORDER BY Date
    """, (isin,)).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "nav"])

    df = pd.DataFrame(rows, columns=["date", "nav"])
    df["date"] = pd.to_datetime(df["date"])
    df["nav"]  = df["nav"].astype(float)
    return df


def get_isins_with_nav_daily(conn: sqlite3.Connection) -> list[str]:
    """Devuelve la lista de ISINs con al menos una fila en fund_nav_daily.

    Incluye fondos huerfanos: misma razon que get_isins_with_nav (seguimiento
    de posiciones existentes).
    """
    rows = conn.execute(
        "SELECT DISTINCT ISIN FROM fund_nav_daily ORDER BY ISIN"
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


# ============================================================
# P1 Fund Attributes (P1→P2 integration interface)
# ============================================================

def load_fund_attributes(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Carga los atributos de clasificación P1 relevantes para el pipeline P2.

    Formaliza la dependencia P2→P1: los módulos de cálculo que necesitan
    atributos del fondo (Fund_Nature para benchmarks de categoría,
    Hedging_Policy/Asset_Currency para currency_factor, Geography/Credit_Quality
    para macro_sensitivity) deben obtenerlos de aquí, no inline.

    Incluye fondos de cualquier In_Current_Universe: P2 calcula métricas
    para todos los fondos con NAV, incluyendo huérfanos en cartera.

    Returns
    -------
    DataFrame indexado por ISIN con columnas:
        Fund_Nature, Strategy, Geography, Development_Status,
        Credit_Quality, Duration_Profile, Investment_Focus,
        Hedging_Policy, Asset_Currency, Fund_Currency,
        Leverage_Used, Sfdr_Article, In_Current_Universe
    """
    df = pd.read_sql("""
        SELECT ISIN,
               Fund_Nature, Strategy, Geography, Development_Status,
               Credit_Quality, Duration_Profile, Investment_Focus,
               Hedging_Policy, Asset_Currency, Fund_Currency,
               Leverage_Used, Sfdr_Article, In_Current_Universe
        FROM fund_master
    """, conn)
    df = df.set_index("ISIN")
    return df
