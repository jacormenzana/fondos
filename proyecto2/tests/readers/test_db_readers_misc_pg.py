# proyecto2/tests/readers/test_db_readers_misc_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 1) — regression tests for the
remaining src/readers/db_readers.py functions that had ZERO prior test coverage on either dialect
before this port: load_nav, load_nav_daily, get_isins_with_nav, get_isins_with_nav_daily, load_ipc,
ipc_available, load_rf_rate, load_fund_attributes. New coverage, not a port of an existing SQLite
test (there wasn't one) — same pg_conn_module_schema pattern as the rest of this tranche.

R-7: imports ONLY db_readers — no run_pipeline.py, no core.io, no HTTP.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.readers.db_readers import (  # noqa: E402
    load_nav,
    load_nav_daily,
    get_isins_with_nav,
    get_isins_with_nav_daily,
    load_ipc,
    ipc_available,
    load_rf_rate,
    load_fund_attributes,
)


def _make_nav_tables(conn):
    conn.execute("""
        CREATE TABLE fund_master (isin text PRIMARY KEY, fund_nature text)
    """)
    conn.execute("""
        CREATE TABLE fund_nav_monthly (
            isin text NOT NULL, date date NOT NULL, nav double precision,
            PRIMARY KEY (isin, date)
        )
    """)
    conn.execute("""
        CREATE TABLE fund_nav_daily (
            isin text NOT NULL, date date NOT NULL, nav double precision,
            PRIMARY KEY (isin, date)
        )
    """)
    conn.execute("""
        CREATE TABLE nav_sources (isin text PRIMARY KEY, data_status text)
    """)


def test_load_nav_returns_sorted_series(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_tables(conn)
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('X1', '2024-02-29', 105.0)")
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('X1', '2024-01-31', 100.0)")

    df = load_nav(conn, "X1")
    assert list(df["nav"]) == [100.0, 105.0], "must be ordered by date ascending"
    assert df["date"].iloc[0] == pd.Timestamp("2024-01-31")


def test_load_nav_empty_returns_empty_frame(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_tables(conn)
    df = load_nav(conn, "NOPE")
    assert df.empty
    assert list(df.columns) == ["date", "nav"]


def test_load_nav_daily_returns_sorted_series(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_tables(conn)
    conn.execute("INSERT INTO fund_nav_daily VALUES ('X1', '2024-01-02', 101.0)")
    conn.execute("INSERT INTO fund_nav_daily VALUES ('X1', '2024-01-01', 100.0)")
    df = load_nav_daily(conn, "X1")
    assert list(df["nav"]) == [100.0, 101.0]


def test_get_isins_with_nav_excludes_orphans_and_inactive(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_tables(conn)
    conn.execute("INSERT INTO fund_master VALUES ('X1', 'Renta Variable')")
    conn.execute("INSERT INTO fund_master VALUES ('X2', 'Renta Variable')")
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('X1', '2024-01-31', 100.0)")
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('X2', '2024-01-31', 100.0)")
    # orphan NAV: no fund_master row -> must be excluded
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('X3', '2024-01-31', 100.0)")
    conn.execute("INSERT INTO nav_sources VALUES ('X2', 'INACTIVE')")

    isins = get_isins_with_nav(conn)
    assert isins == ["X1"], f"expected only X1 (X2 INACTIVE, X3 orphan), got {isins}"


def test_get_isins_with_nav_daily(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_tables(conn)
    conn.execute("INSERT INTO fund_master VALUES ('X1', 'Renta Variable')")
    conn.execute("INSERT INTO fund_nav_daily VALUES ('X1', '2024-01-01', 100.0)")
    conn.execute("INSERT INTO fund_nav_daily VALUES ('X9', '2024-01-01', 100.0)")  # orphan
    assert get_isins_with_nav_daily(conn) == ["X1"]


def _make_macro_tables(conn):
    conn.execute("""
        CREATE TABLE series_inflation (date date NOT NULL, geography text NOT NULL, ipc_index double precision)
    """)
    conn.execute("""
        CREATE TABLE series_macro (
            date date NOT NULL, indicator text NOT NULL, geography text NOT NULL,
            value double precision
        )
    """)


def test_load_ipc_month_end_alignment(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_macro_tables(conn)
    conn.execute("INSERT INTO series_inflation VALUES ('2024-01-15', 'ES', 105.2)")
    df = load_ipc(conn, "ES")
    assert df["date"].iloc[0] == pd.Timestamp("2024-01-31"), "must normalize to month-end"
    assert df["ipc_index"].iloc[0] == 105.2


def test_ipc_available(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_macro_tables(conn)
    assert ipc_available(conn, "ES") is False
    conn.execute("INSERT INTO series_inflation VALUES ('2024-01-15', 'ES', 105.2)")
    assert ipc_available(conn, "ES") is True


def test_load_rf_rate_converts_pct_to_decimal(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_macro_tables(conn)
    conn.execute(
        "INSERT INTO series_macro VALUES ('2024-01-15', 'rate_deposit', 'EU', 4.0)"
    )
    df = load_rf_rate(conn, "rate_deposit", "EU")
    assert df["rate"].iloc[0] == 0.04
    assert df["date"].iloc[0] == pd.Timestamp("2024-01-31")


def test_load_fund_attributes(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("""
        CREATE TABLE fund_master (
            isin text PRIMARY KEY, fund_nature text, strategy text, geography text,
            development_status text, credit_quality text, duration_profile text,
            investment_focus text, hedging_policy text, asset_currency text,
            fund_currency text, leverage_used text, sfdr_article text,
            in_current_universe smallint
        )
    """)
    conn.execute("""
        INSERT INTO fund_master VALUES
        ('X1', 'Renta Variable', 'Growth', 'Global', 'Established', 'Investment Grade',
         'N/A', 'Large Cap', 'Unhedged', 'EUR', 'EUR', 'No', 'Article 8', 1)
    """)
    df = load_fund_attributes(conn)
    assert df.index.name == "ISIN"
    assert df.loc["X1", "Fund_Nature"] == "Renta Variable"
    assert df.loc["X1", "In_Current_Universe"] == 1
