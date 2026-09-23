# proyecto2/tests/calculations/test_calculations_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Stage 9 (2026-09-23) — dedicated regression tests for the four
proyecto2/src/calculations/ functions that issue their own direct SQL and were never covered by any
earlier stage's read-path port (only readers/db_readers.py was): momentum.load_category_returns,
capture_ratios.load_peer_benchmark, persistence._category_return_in_window and
currency_factor.load_fx_eur_divisa. Each used a bare `?` placeholder, which is a hard psycopg3
syntax error ("the query has 0 placeholders but 2 parameters were passed") — found only when the
first end-to-end `run_pipeline.py --backend postgres` rehearsal failed every fund.

All four are read-only, so the standard SAVEPOINT-based pg_conn fixture is safe. Each test builds
minimal scratch tables in a throwaway schema (Postgres DDL is transactional, so the savepoint
rollback removes them) instead of touching the live-seeded tables.
"""
from __future__ import annotations

import math

import pandas as pd

from shared.config import PERSISTENCE_WINDOW_MONTHS
from src.calculations.capture_ratios import load_peer_benchmark
from src.calculations.currency_factor import load_fx_eur_divisa
from src.calculations.momentum import load_category_returns
from src.calculations.persistence import _category_return_in_window


def _scratch(conn, schema: str) -> None:
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"SET search_path = {schema}")
    conn.execute("CREATE TABLE fund_master (isin text, fund_nature text)")
    conn.execute("CREATE TABLE fund_metrics (isin text, metric text, horizon text, "
                 "real_flag integer, value double precision)")
    conn.execute("CREATE TABLE fund_nav_monthly (isin text, date date, nav double precision)")
    conn.execute("CREATE TABLE series_macro (date date, indicator text, geography text, "
                 "value double precision)")


def test_load_category_returns_filters_by_horizon_nature_realflag_and_null(pg_conn):
    _scratch(pg_conn, "calc_momentum_t")
    pg_conn.execute("INSERT INTO fund_master VALUES ('A','Mixtos'),('B','Mixtos'),('C','Mixtos'),"
                    "('D','Renta Variable'),('E','Mixtos')")
    pg_conn.execute("""INSERT INTO fund_metrics VALUES
        ('A','return_ann','rolling_1y',0,0.10),
        ('B','return_ann','rolling_1y',0,0.20),
        ('C','return_ann','rolling_1y',0,NULL),          -- NULL value excluded
        ('D','return_ann','rolling_1y',0,0.50),          -- other Fund_Nature excluded
        ('E','return_ann','rolling_1y',1,0.30),          -- real_flag=1 (deflated) excluded
        ('A','return_ann','rolling_3y',0,0.99)           -- other horizon excluded
    """)
    s = load_category_returns(pg_conn, "Mixtos", "rolling_1y")
    assert dict(s) == {"A": 0.10, "B": 0.20}


def test_load_peer_benchmark_averages_peer_returns_and_excludes_self_and_other_natures(pg_conn):
    _scratch(pg_conn, "calc_capture_t")
    pg_conn.execute("INSERT INTO fund_master VALUES ('P1','Mixtos'),('P2','Mixtos'),"
                    "('X','Mixtos'),('Y','Renta Variable')")
    # P1: +10%, +10%   P2: -10%, +10%   -> monthly peer mean: 0.0 (Feb), 0.10 (Mar)
    pg_conn.execute("""INSERT INTO fund_nav_monthly VALUES
        ('P1','2026-01-31',100),('P1','2026-02-28',110),('P1','2026-03-31',121),
        ('P2','2026-01-31',100),('P2','2026-02-28', 90),('P2','2026-03-31', 99),
        ('X', '2026-01-31',100),('X', '2026-02-28',200),('X', '2026-03-31',400),   -- excluded self
        ('Y', '2026-01-31',100),('Y', '2026-02-28',300),('Y', '2026-03-31',900)    -- other nature
    """)
    b = load_peer_benchmark(pg_conn, "Mixtos", "X")
    assert len(b) == 2
    assert math.isclose(b.iloc[0], 0.0, abs_tol=1e-12)
    assert math.isclose(b.iloc[1], 0.10, rel_tol=1e-12)


def test_category_return_in_window_annualises_peer_returns_with_five_bound_params(pg_conn):
    """Five placeholders (nature, exclude_isin, start, end, min months) — the widest of the four."""
    _scratch(pg_conn, "calc_persist_t")
    n = PERSISTENCE_WINDOW_MONTHS
    dates = pd.date_range("2020-01-31", periods=n, freq="ME")
    pg_conn.execute("INSERT INTO fund_master VALUES ('P1','Mixtos'),('P2','Mixtos'),"
                    "('X','Mixtos'),('Y','Renta Variable')")
    steps = {"P1": 1.0, "P2": 2.0, "X": 50.0, "Y": 50.0}    # X (self) and Y (other nature) excluded
    for isin, step in steps.items():
        for k, d in enumerate(dates):
            pg_conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)",
                            (isin, d.date(), 100.0 + k * step))

    got = _category_return_in_window(pg_conn, "Mixtos", "X", dates[0], dates[-1])

    years = n / 12
    expected = sum(((100.0 + (n - 1) * st) / 100.0) ** (1 / years) - 1 for st in (1.0, 2.0)) / 2
    assert got is not None and math.isclose(got, expected, rel_tol=1e-12)


def test_load_fx_eur_divisa_crosses_a_non_usd_currency_via_the_bound_indicator(pg_conn):
    """JPY takes the parameterised `indicator = ?` path; USD takes the parameter-free one."""
    _scratch(pg_conn, "calc_fx_t")
    pg_conn.execute("""INSERT INTO series_macro VALUES
        ('2026-01-31','fx_usd_eur','GLOBAL',1.10),('2026-02-28','fx_usd_eur','GLOBAL',1.20),
        ('2026-01-31','fx_jpy_usd','GLOBAL',150.0),('2026-02-28','fx_jpy_usd','GLOBAL',140.0),
        ('2026-01-31','fx_usd_gbp','GLOBAL',1.30)        -- other indicator must not leak in
    """)
    usd = load_fx_eur_divisa(pg_conn, "USD")
    assert list(usd.round(6)) == [1.10, 1.20]

    jpy = load_fx_eur_divisa(pg_conn, "JPY")
    assert jpy is not None and len(jpy) == 2
    # EUR/JPY = (JPY per USD) * (USD per EUR)
    assert math.isclose(jpy.iloc[0], 150.0 * 1.10) and math.isclose(jpy.iloc[1], 140.0 * 1.20)
