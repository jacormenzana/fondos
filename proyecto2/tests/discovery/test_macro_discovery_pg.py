# proyecto2/tests/discovery/test_macro_discovery_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
src/discovery/macro_discovery.py's two DB-writing functions (_write_inflation, _write_macro).
Both commit internally, so this uses pg_conn_module_schema/pg_session_conn -- never bare
pg_conn/SAVEPOINT -- same discipline as every other commit-owning function in this migration.
This module had zero test coverage (SQLite or Postgres) before this port.
"""
from __future__ import annotations

from src.discovery.macro_discovery import _write_inflation, _write_macro


def _make_series_inflation(conn):
    conn.execute("""
        CREATE TABLE series_inflation (
            date      date NOT NULL,
            geography text NOT NULL DEFAULT 'ES',
            ipc_index double precision NOT NULL,
            source    text,
            load_ts   timestamptz DEFAULT now(),
            PRIMARY KEY (date, geography)
        )
    """)


def _make_series_macro(conn):
    conn.execute("""
        CREATE TABLE series_macro (
            date      date NOT NULL,
            indicator text NOT NULL,
            geography text NOT NULL,
            value     double precision,
            unit      text,
            source    text,
            load_ts   timestamptz DEFAULT now(),
            PRIMARY KEY (date, indicator, geography)
        )
    """)


def test_write_inflation_upsert_and_dry_run(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_inflation(conn)

    rows = [{"date": "2026-01-31", "geography": "ES", "ipc_index": 118.5, "source": "INE"}]
    n = _write_inflation(conn, rows, dry_run=False)
    assert n == 1

    # Rerun with a revised index value must overwrite (INSERT OR REPLACE parity).
    rows2 = [{"date": "2026-01-31", "geography": "ES", "ipc_index": 118.9, "source": "INE"}]
    _write_inflation(conn, rows2, dry_run=False)
    value = conn.execute(
        "SELECT ipc_index FROM series_inflation WHERE date='2026-01-31' AND geography='ES'"
    ).fetchone()[0]
    assert value == 118.9

    assert _write_inflation(conn, rows, dry_run=True) == 0
    assert _write_inflation(conn, [], dry_run=False) == 0


def test_write_macro_upsert_and_dry_run(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)

    rows = [{
        "date": "2026-01-31", "indicator": "m3_yoy", "geography": "EU",
        "value": 3.2, "unit": "pct", "source": "BCE",
    }]
    n = _write_macro(conn, rows, dry_run=False)
    assert n == 1

    rows2 = [{
        "date": "2026-01-31", "indicator": "m3_yoy", "geography": "EU",
        "value": 3.5, "unit": "pct", "source": "BCE",
    }]
    _write_macro(conn, rows2, dry_run=False)
    value = conn.execute(
        "SELECT value FROM series_macro WHERE date='2026-01-31' AND indicator='m3_yoy' AND geography='EU'"
    ).fetchone()[0]
    assert value == 3.5

    assert _write_macro(conn, rows, dry_run=True) == 0
    assert _write_macro(conn, [], dry_run=False) == 0
