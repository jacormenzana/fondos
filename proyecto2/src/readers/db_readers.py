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


def count_isins_with_new_nav(
    conn: sqlite3.Connection,
    metric_version: str,
) -> tuple[int, int, int]:
    """
    Preflight check (P2-04): count ISINs that would NOT be hash-skipped.

    A fund needs recomputation when:
      (a) it has never been calculated (no fund_metric_state row), OR
      (b) nav_sources.last_nav_date > fund_metric_state.calculated_at
          (new NAV data since the last successful run).

    Note: CALC_VERSION / IPC changes are intentionally NOT detected here —
    use --force when bumping CALC_VERSION or after macro_discovery runs.

    Returns
    -------
    n_new_nav           ISINs with nav_sources.last_nav_date > calculated_at
    n_never_calculated  ISINs with no fund_metric_state row for metric_version
    n_universe          total ISINs in the fund_nav_monthly ∩ fund_master universe
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE fms.isin IS NULL)                       AS n_never,
            COUNT(*) FILTER (
                WHERE fms.isin IS NOT NULL
                  AND ns.last_nav_date IS NOT NULL
                  AND ns.last_nav_date > fms.calculated_at
            )                                                               AS n_new,
            COUNT(*)                                                        AS n_total
        FROM (
            SELECT DISTINCT n.ISIN
            FROM fund_nav_monthly n
            INNER JOIN fund_master m USING (ISIN)
        ) universe
        LEFT JOIN fund_metric_state fms
               ON universe.ISIN = fms.isin
              AND fms.metric_version = ?
        LEFT JOIN nav_sources ns ON universe.ISIN = ns.isin
        """,
        (metric_version,),
    ).fetchone()
    n_never, n_new, n_total = row
    return int(n_new or 0), int(n_never or 0), int(n_total or 0)


def get_isins_with_nav(conn: sqlite3.Connection) -> list[str]:
    """Devuelve la lista de ISINs con al menos una fila en fund_nav_monthly
    Y con entrada en fund_master (P2-01 / BUG-SIGNAL-DIAG).

    Incluye fondos de oferta antigua (In_Current_Universe=0): las metricas P2
    se calculan para todos los fondos porque los huerfanos pueden seguir en
    cartera y necesitan seguimiento de rendimiento.

    El INNER JOIN a fund_master es defensivo: ISINs sin registro en fund_master
    carecen de Fund_Nature y serían silenciosamente descartados por dropna() en
    compute_category_snapshot(), desinflando los peer groups por debajo del
    umbral min_peers=5 y produciendo cero señales de categoria.  Los ISINs
    ausentes de fund_master son invariablemente datos de NAV cargados antes de
    que el fondo haya pasado por el pipeline P1; la correcta accion es excluir
    esos ISINs del universo P2 hasta que P1 los clasifique.
    """
    rows = conn.execute(
        """SELECT DISTINCT n.ISIN
           FROM fund_nav_monthly n
           INNER JOIN fund_master m USING (ISIN)
           ORDER BY n.ISIN"""
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
    """Devuelve la lista de ISINs con al menos una fila en fund_nav_daily
    Y con entrada en fund_master (simetrico con get_isins_with_nav, P2-01).

    Incluye fondos huerfanos: misma razon que get_isins_with_nav (seguimiento
    de posiciones existentes).
    """
    rows = conn.execute(
        """SELECT DISTINCT n.ISIN
           FROM fund_nav_daily n
           INNER JOIN fund_master m USING (ISIN)
           ORDER BY n.ISIN"""
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
