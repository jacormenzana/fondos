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

Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 1) — read before editing further.
Ported in place, per is_postgres_connection() branch, same convention as every other ported P#
module in this codebase (shared/db.py's module docstring explains why: a factory swap alone can't
work because psycopg3 doesn't accept `?` placeholders at all).

Two things worth knowing before touching this file again:
  - Unquoted mixed-case identifiers (ISIN, Fund_Nature, ...) need NO rewrite: Postgres folds every
    unquoted identifier to lowercase at parse time, which is exactly db/pg/rename_map.yaml's target
    spelling for every renamed column here. Verified empirically 2026-09-20 (`SELECT Fund_Nature
    FROM t` resolves against a real `fund_nature` column with zero error). So the real dialect
    differences below are only: `?`->`%s`, and the two SQLite-only date functions this file used
    (`julianday()`, bare `DATE()`) — see count_stale_nav_funds() and load_ts_cohort().
  - `fund_nav_monthly.date` / `fund_nav_daily.date` are genuine `date` columns on Postgres (not
    text) — `db/pg/10_bronze.sql`. `fund_metrics.load_ts` is `timestamptz`. Both let date arithmetic
    use native operators instead of `julianday()`/`DATE()`.
"""

import sqlite3
import pandas as pd

from shared.db import is_postgres_connection


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
    ph = "%s" if is_postgres_connection(conn) else "?"
    rows = conn.execute(f"""
        SELECT Date AS date, NAV AS nav
        FROM fund_nav_monthly
        WHERE ISIN = {ph}
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
    ph = "%s" if is_postgres_connection(conn) else "?"
    row = conn.execute(
        f"""
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
              AND fms.metric_version = {ph}
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

    Excluye fondos con nav_sources.data_status='INACTIVE': fondos cuyo NAV
    esta permanentemente congelado (p.ej. liquidados sin fecha de cierre
    registrada) y cuya recomputacion metrica seria silenciosa perdida de CPU.
    Sus filas existentes en fund_metrics se conservan para seguimiento de
    posiciones en cartera. El COALESCE('OK') garantiza que ISINs sin fila en
    nav_sources (fondos con NAV pero sin descubrimiento previo) siguen incluidos.
    """
    rows = conn.execute(
        """SELECT DISTINCT n.ISIN
           FROM fund_nav_monthly n
           INNER JOIN fund_master m USING (ISIN)
           LEFT JOIN nav_sources s USING (ISIN)
           WHERE COALESCE(s.data_status, 'OK') <> 'INACTIVE'
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
    ph = "%s" if is_postgres_connection(conn) else "?"
    rows = conn.execute(f"""
        SELECT Date AS date, NAV AS nav
        FROM fund_nav_daily
        WHERE ISIN = {ph}
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
    ph = "%s" if is_postgres_connection(conn) else "?"
    rows = conn.execute(f"""
        SELECT date, ipc_index
        FROM series_inflation
        WHERE geography = {ph}
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
    ph = "%s" if is_postgres_connection(conn) else "?"
    n = conn.execute(
        f"SELECT COUNT(*) FROM series_inflation WHERE geography = {ph}",
        (geography,)
    ).fetchone()[0]
    return n > 0


def load_rf_rate(
    conn: sqlite3.Connection,
    indicator: str = "rate_deposit",
    geography: str = "EU",
) -> pd.DataFrame:
    """
    Carga la tasa libre de riesgo mensual histórica desde series_macro.

    Usa por defecto rate_deposit/EU (tipo de depósito BCE, proxy del €STR).
    La serie cubre 2000-presente con resolución mensual y refleja los tipos
    reales de cada periodo (incluidos tipos negativos 2014-2022).

    Parámetros:
        indicator : nombre del indicador en series_macro (default: rate_deposit)
        geography : código de geografía                  (default: EU)

    Devuelve DataFrame con columnas:
        date  (datetime64, normalizado a fin de mes)
        rate  (float, decimal — e.g. 0.04 = 4.0%; -0.003 = -0.3%)

    Devuelve DataFrame vacío si no hay datos en la BD.
    """
    ph = "%s" if is_postgres_connection(conn) else "?"
    rows = conn.execute(
        f"SELECT date, value FROM series_macro "
        f"WHERE indicator = {ph} AND geography = {ph} ORDER BY date",
        (indicator, geography),
    ).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "rate"])

    df = pd.DataFrame(rows, columns=["date", "rate"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["rate"] = df["rate"].astype(float) / 100.0  # % → decimal
    return df


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
    _cols = ["ISIN", "Fund_Nature", "Strategy", "Geography", "Development_Status",
             "Credit_Quality", "Duration_Profile", "Investment_Focus", "Hedging_Policy",
             "Asset_Currency", "Fund_Currency", "Leverage_Used", "Sfdr_Article",
             "In_Current_Universe"]
    # fetchall() + manual DataFrame, not pd.read_sql(sql, conn): pandas only formally supports a
    # SQLAlchemy engine/connection or a sqlite3 DBAPI2 connection for the `con` argument — a raw
    # psycopg3 Connection works (verified live 2026-09-20) but emits a UserWarning on every call.
    # Matches every other function in this file, which already builds its DataFrame this way.
    rows = conn.execute(f"SELECT {', '.join(_cols)} FROM fund_master").fetchall()
    df = pd.DataFrame(rows, columns=_cols)
    df = df.set_index("ISIN")
    return df


# ============================================================
# Observability / reliability-signal helpers
# ============================================================

def load_ts_cohort(
    conn: sqlite3.Connection,
    metric_version: str,
    run_start_iso: str,
) -> list[tuple[str, int]]:
    """Groups fund_metrics rows by DATE(load_ts) for ISINs processed this run.

    Used by run_pipeline.py to emit the [RUN COHORT] log line and by the
    pipelineP2Audit / pipelineP1P2Audit skills for the load_ts cohort check.

    Parameters
    ----------
    metric_version : e.g. 'v1' — filters fund_metric_state rows.
    run_start_iso  : 'YYYY-MM-DD' of the run-start calendar day (the date at
                     the moment run() was invoked).  Overnight P2 runs begin
                     ~21:00 and finish ~02:40, so they stamp ``calculated_at``
                     on two consecutive calendar days.  Using a lower-bound
                     (``>=``) instead of equality ensures all funds touched by
                     the same run are counted regardless of when they finished.
                     Funds from earlier runs (``calculated_at < run_start_iso``)
                     are correctly excluded.

    Returns
    -------
    List of (ts_date_str, count) tuples ordered by ts_date ascending.
    Empty list if no rows (run produced no output, or DB empty).

    Two-timestamp model: load_ts = per-value change stamp (data lineage);
    calculated_at = per-fund run stamp (orchestration). See run_pipeline
    module docstring for the full classification rule (EXPECTED vs ANOMALY).
    """
    pg = is_postgres_connection(conn)
    ph = "%s" if pg else "?"
    # SQLite's DATE(x) extracts the date part of a text/datetime value; Postgres has no 1-arg
    # DATE() function — fund_metrics.load_ts is `timestamptz` there, so ::date does the same job.
    ts_date_expr = "fm.load_ts::date" if pg else "DATE(fm.load_ts)"
    rows = conn.execute(
        f"""
        SELECT {ts_date_expr} AS ts_date, COUNT(*) AS cnt
        FROM fund_metrics fm
        WHERE fm.isin IN (
            SELECT isin FROM fund_metric_state
            WHERE metric_version = {ph} AND calculated_at >= {ph}
        )
        GROUP BY {ts_date_expr}
        ORDER BY ts_date
        """,
        (metric_version, run_start_iso),
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows]


def count_stale_nav_funds(
    conn: sqlite3.Connection,
    metric_version: str,
    run_start_iso: str,
    max_age_days: int = 60,
    as_of_iso: str | None = None,
) -> int:
    """Count funds recomputed this run whose newest NAV is older than max_age_days.

    Fresh metrics computed on stale price data are silent accuracy risk.
    The typical cause is nav_discovery not having run recently enough;
    cross-reference nav_sources.data_status for the affected ISINs.

    Parameters
    ----------
    metric_version : e.g. 'v1'.
    run_start_iso  : 'YYYY-MM-DD' of the run-start calendar day.  Used as a
                     lower bound (``>=``) on ``calculated_at`` so that overnight
                     runs whose stamps span two calendar days are counted in full.
    max_age_days   : threshold in calendar days (default 60 ≈ 2 months).
    as_of_iso      : 'YYYY-MM-DD' anchor for the NAV-age calculation
                     (``julianday(as_of_iso) - julianday(newest_nav) > max_age_days``).
                     Defaults to ``run_start_iso`` when omitted, which preserves
                     the original single-date behaviour for callers that do not
                     need the two-date separation.  Pass ``date.today().isoformat()``
                     from the pipeline so the age is measured against the real
                     wall-clock end-of-run date, not the run-start date.

    Returns
    -------
    Integer count of stale-NAV ISINs (0 = all up to date).
    """
    _as_of = as_of_iso if as_of_iso is not None else run_start_iso
    pg = is_postgres_connection(conn)
    ph = "%s" if pg else "?"
    # SQLite has no native date subtraction; julianday(a) - julianday(b) is its idiom for "days
    # between". Postgres date - date already yields an integer day count directly — no julianday
    # equivalent needed, and nav_latest.max_nav_date is a genuine `date` column there (10_bronze.sql).
    age_expr = (f"({ph}::date - nav_latest.max_nav_date)" if pg
                else f"(julianday({ph}) - julianday(nav_latest.max_nav_date))")
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT fms.isin)
        FROM fund_metric_state fms
        JOIN (
            SELECT ISIN, MAX(Date) AS max_nav_date
            FROM fund_nav_monthly
            GROUP BY ISIN
        ) nav_latest ON fms.isin = nav_latest.ISIN
        WHERE fms.metric_version = {ph}
          AND fms.calculated_at  >= {ph}
          AND {age_expr} > {ph}
        """,
        (metric_version, run_start_iso, _as_of, max_age_days),
    ).fetchone()
    return int(row[0] or 0)


def coverage_snapshot(
    conn: sqlite3.Connection,
    metrics: list[str],
    horizon: str = "since_inception",
) -> list[tuple[str, int]]:
    """Return distinct non-NULL ISIN count per metric for the given horizon.

    Provides the run-over-run coverage baseline for P3-consumed metrics.
    A drop of more than ~2 % versus the prior run signals a possible upstream
    NAV loss or calc regression and should be triaged immediately.

    Parameters
    ----------
    metrics : list of metric names to check (e.g. _P3_CONSUMED_METRICS).
    horizon : horizon filter (default 'since_inception').

    Returns
    -------
    List of (metric, count) in the same order as `metrics`.
    """
    ph = "%s" if is_postgres_connection(conn) else "?"
    result = []
    for metric in metrics:
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT isin)
            FROM fund_metrics
            WHERE metric = {ph} AND horizon = {ph} AND value IS NOT NULL
            """,
            (metric, horizon),
        ).fetchone()
        result.append((metric, int(row[0] or 0)))
    return result
