# proyecto2/tests/readers/test_preflight_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 1) — dedicated regression tests for
src/readers/db_readers.py::count_isins_with_new_nav() against real Postgres.

Companion to test_preflight.py (SQLite, in-memory) — same logical cases, ported per the migration's
"port function and test together" discipline. count_isins_with_new_nav() is read-only (no
conn.commit() inside it), so pg_conn's SAVEPOINT fixture would be sufficient on its own, but this
uses pg_conn_module_schema (isolated per-test schema) instead, matching the template already
established by test_portfolio_builder_persist_pg.py / test_fund_scorer_persist_pg.py — avoids any
dependency on or interference with the real seeded silver/gold/control schemas for what are
deliberately minimal, hand-built fixture tables.

R-7: imports ONLY db_readers — no pipeline.py, no core.io, no HTTP.
"""
from __future__ import annotations

import sys
from pathlib import Path

_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.readers.db_readers import count_isins_with_new_nav  # noqa: E402

MV = "v1"


def _make_tables(conn):
    conn.execute("""
        CREATE TABLE fund_master (
            isin text PRIMARY KEY,
            fund_name text NOT NULL,
            fund_nature text NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE fund_nav_monthly (
            isin text NOT NULL,
            date date NOT NULL,
            nav double precision,
            PRIMARY KEY (isin, date)
        )
    """)
    conn.execute("""
        CREATE TABLE fund_metric_state (
            isin text NOT NULL,
            metric_version text NOT NULL,
            input_hash text NOT NULL,
            calculated_at date NOT NULL,
            PRIMARY KEY (isin, metric_version)
        )
    """)
    conn.execute("""
        CREATE TABLE nav_sources (
            isin text PRIMARY KEY,
            last_nav_date date,
            status text
        )
    """)


def _add_fund(conn, isin="IE0001", nav_date="2024-06-30"):
    conn.execute(
        "INSERT INTO fund_master VALUES (%s, 'Test Fund', 'Renta Fija') ON CONFLICT DO NOTHING",
        (isin,),
    )
    conn.execute(
        "INSERT INTO fund_nav_monthly VALUES (%s, %s, 100.0) ON CONFLICT DO NOTHING",
        (isin, nav_date),
    )


def _add_state(conn, isin="IE0001", calculated_at="2024-06-30", mv=MV):
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s, %s, 'hash123', %s) "
        "ON CONFLICT (isin, metric_version) DO UPDATE SET calculated_at = excluded.calculated_at",
        (isin, mv, calculated_at),
    )


def _add_nav_source(conn, isin="IE0001", last_nav_date="2024-06-30"):
    conn.execute(
        "INSERT INTO nav_sources VALUES (%s, %s, 'OK') "
        "ON CONFLICT (isin) DO UPDATE SET last_nav_date = excluded.last_nav_date",
        (isin, last_nav_date),
    )


def test_empty_db_returns_zeros(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
    assert (n_new, n_never, n_total) == (0, 0, 0)


def test_nav_without_fund_master_excluded(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_nav_monthly VALUES ('LU9999', '2024-06-30', 100.0)")
    _, _, n_total = count_isins_with_new_nav(conn, MV)
    assert n_total == 0


def test_one_isin_no_state_counted_as_never(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    _add_fund(conn)
    n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
    assert (n_total, n_never, n_new) == (1, 1, 0)


def test_state_for_different_metric_version_is_ignored(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    _add_fund(conn)
    _add_state(conn, mv="v2")
    _, n_never, _ = count_isins_with_new_nav(conn, MV)
    assert n_never == 1


def test_nav_newer_than_calculated_at_counts_as_new(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    _add_fund(conn, nav_date="2024-07-31")
    _add_state(conn, calculated_at="2024-06-30")
    _add_nav_source(conn, last_nav_date="2024-07-31")
    n_new, n_never, _ = count_isins_with_new_nav(conn, MV)
    assert (n_new, n_never) == (1, 0)


def test_nav_older_than_calculated_at_not_new(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    _add_fund(conn, nav_date="2024-05-31")
    _add_state(conn, calculated_at="2024-06-30")
    _add_nav_source(conn, last_nav_date="2024-05-31")
    n_new, n_never, _ = count_isins_with_new_nav(conn, MV)
    assert (n_new, n_never) == (0, 0)


def test_null_last_nav_date_means_not_new(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    _add_fund(conn)
    _add_state(conn, calculated_at="2024-06-30")
    conn.execute("INSERT INTO nav_sources VALUES ('IE0001', NULL, 'PENDING')")
    n_new, n_never, _ = count_isins_with_new_nav(conn, MV)
    assert (n_new, n_never) == (0, 0)


def test_two_isins_one_never_one_new(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    _add_fund(conn, isin="IE0001")
    _add_fund(conn, isin="IE0002", nav_date="2024-07-31")
    _add_state(conn, isin="IE0002", calculated_at="2024-06-30")
    _add_nav_source(conn, isin="IE0002", last_nav_date="2024-07-31")

    n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
    assert (n_total, n_never, n_new) == (2, 1, 1)
