# proyecto3/tests/test_pit_inputs_pg.py
# -*- coding: utf-8 -*-
"""
Postgres readers of the PIT backtester (proyecto3/src/pit_inputs.py) -- FND-0159 d3.

Hermetic: throwaway schema with only the columns the readers touch (same pattern as test_p3_reads_pg.py).
Checks the dialect points that bit this project before (lowercase result columns, date objects, ANY(%s)) and
that retired funds (In_Current_Universe=0) ARE returned, since the PIT universe includes them.

    python scripts/ops/run_pg_tests.py proyecto3/tests/test_pit_inputs_pg.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_inputs import (
    ATTRIBUTE_FRAME_COLUMNS, iter_daily_chunks, load_attributes, load_ipc, load_nav_panel, load_rate_deposit,
)


def _schema(conn, schema):
    conn.execute(f"SET search_path = {schema}")
    conn.execute("""
        CREATE TABLE fund_master (
            isin text PRIMARY KEY, fund_name text, fund_nature text, srri smallint, investment_focus text,
            credit_quality text, ongoing_charge_recurrent double precision, srri_quality_flag text,
            fund_family_id text, management_company text, in_current_universe smallint)
    """)
    conn.execute("CREATE TABLE fund_nav_monthly (isin text, date date, nav double precision, PRIMARY KEY (isin, date))")
    conn.execute("CREATE TABLE fund_nav_daily (isin text, date date, nav double precision, PRIMARY KEY (isin, date))")
    conn.execute("CREATE TABLE series_inflation (geography text, date date, ipc_index double precision)")
    conn.execute("""CREATE TABLE series_macro (date date, indicator text, geography text, value double precision)""")
    conn.execute("""
        INSERT INTO fund_master VALUES
        ('A1', 'Fund A', 'Renta Variable', 4, 'Global', 'Investment Grade', 0.012, 'OK', 'FAM1', 'MgrA', 1),
        ('B1', 'Fund B', 'Monetario',      1, 'Global', 'Investment Grade', 0.003, 'OK', NULL,   'MgrB', 0),
        ('C1', 'Fund C', 'Mixtos',         3, 'Global', 'High Yield',       0.010, 'OK', 'FAM2', 'MgrC', 1)
    """)
    conn.execute("""
        INSERT INTO fund_nav_monthly VALUES
        ('A1', '2020-01-31', 100), ('A1', '2020-02-27', 101), ('B1', '2020-01-31', 50), ('B1', '2020-02-29', 50.1),
        ('C1', '2020-01-31', 10)
    """)
    conn.execute("""
        INSERT INTO fund_nav_daily VALUES
        ('A1', '2020-01-02', 100), ('A1', '2020-01-03', 100.5), ('B1', '2020-01-02', 50), ('C1', '2020-01-02', 10),
        ('C1', '2020-01-03', 10.1)
    """)
    conn.execute("""
        INSERT INTO series_inflation VALUES ('ES', '2020-01-01', 100), ('ES', '2020-02-01', 100.2), ('EU', '2020-01-01', 5)
    """)
    conn.execute("""
        INSERT INTO series_macro VALUES
        ('2020-01-31', 'rate_deposit', 'EU', -0.5), ('2020-02-29', 'rate_deposit', 'EU', 4.0),
        ('2020-01-31', 'rate_deposit', 'US', 1.0), ('2020-01-31', 'oil_wti', 'GLOBAL', 55)
    """)


def test_nav_panel_is_wide_raw_dated_and_sql_scoped(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _schema(conn, pg_conn_module_schema)
    full = load_nav_panel(conn)
    assert set(full.columns) == {"A1", "B1", "C1"}
    assert date(2020, 2, 27) in {d.date() for d in full.index}                # RAW date kept, not month-end normalised
    assert full.loc[pd.Timestamp("2020-02-27"), "A1"] == 101 and pd.isna(full.loc[pd.Timestamp("2020-02-27"), "B1"])
    scoped = load_nav_panel(conn, ["A1", "C1"])
    assert list(scoped.columns) == ["A1", "C1"]
    assert load_nav_panel(conn, ["NOPE"]).empty


def test_attributes_include_retired_funds_and_use_the_scorer_column_names(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _schema(conn, pg_conn_module_schema)
    attrs = load_attributes(conn)
    assert attrs.index.name == "isin" and set(attrs.index) == {"A1", "B1", "C1"}
    assert list(attrs.columns) == [c for c in ATTRIBUTE_FRAME_COLUMNS if c != "ISIN"]
    assert attrs.loc["B1", "In_Current_Universe"] == 0                          # retired fund returned (FND-0198)
    assert attrs.loc["A1", "Ongoing_Charge"] == 0.012 and attrs.loc["A1", "srri_kiid"] == 4
    assert attrs.loc["A1", "Management_Company"] == "MgrA"
    assert list(load_attributes(conn, ["C1"]).index) == ["C1"]


def test_ipc_is_month_end_dated_and_geography_filtered(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _schema(conn, pg_conn_module_schema)
    ipc = load_ipc(conn)
    assert list(ipc["date"]) == [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29")]
    assert list(ipc["ipc_index"]) == [100.0, 100.2]
    assert list(load_ipc(conn, "EU")["ipc_index"]) == [5.0]


def test_rate_deposit_is_decimal_month_end_and_eu_only(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _schema(conn, pg_conn_module_schema)
    rate = load_rate_deposit(conn)
    assert list(rate["rate"]) == [-0.005, 0.04]                                  # percent -> decimal, negative preserved
    assert list(rate["date"]) == [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29")]


def test_daily_chunks_cover_every_isin_once_in_bounded_chunks(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _schema(conn, pg_conn_module_schema)
    chunks = list(iter_daily_chunks(conn, ["C1", "A1", "B1"], chunk_size=2))
    assert [sorted(c["isin"].unique()) for c in chunks] == [["A1", "B1"], ["C1"]]
    assert sum(len(c) for c in chunks) == 5
    a = chunks[0][chunks[0]["isin"] == "A1"]
    assert list(a["nav"]) == [100.0, 100.5] and a["date"].is_monotonic_increasing
