# proyecto2/tests/calculations/test_m2_global_builder_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression test for
src/calculations/m2_global_builder.py::build_m2_global(). Commits internally (two DELETE +
executemany-upsert blocks against series_macro), so this uses pg_conn_module_schema/pg_session_conn
-- never bare pg_conn/SAVEPOINT -- same discipline as every other commit-owning function in this
migration. This module had zero test coverage (SQLite or Postgres) before this port.
"""
from __future__ import annotations

from src.calculations.m2_global_builder import build_m2_global


def _make_series_macro(conn):
    conn.execute("""
        CREATE TABLE series_macro (
            date        date NOT NULL,
            indicator   text NOT NULL,
            geography   text NOT NULL,
            value       double precision,
            unit        text,
            source      text,
            load_ts     timestamptz DEFAULT now(),
            PRIMARY KEY (date, indicator, geography)
        )
    """)


def _seed_minimal_m2_inputs(conn):
    """US + EU levels + fx, enough for build_m2_global to compute a global YoY series (needs >=13
    months of core components to produce a first pct_change(12) row)."""
    # 14 consecutive month-end dates (avoids day-in-month edge cases).
    dates = [
        "2024-01-31", "2024-02-29", "2024-03-31", "2024-04-30", "2024-05-31", "2024-06-30",
        "2024-07-31", "2024-08-31", "2024-09-30", "2024-10-31", "2024-11-30", "2024-12-31",
        "2025-01-31", "2025-02-28",
    ]
    for idx, d in enumerate(dates):
        us_level = 21000.0 + idx * 10
        eu_level = 15000.0 + idx * 8   # millones EUR
        fx = 1.08
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'm2_level', 'US', %s, 'usd_bn', 'FRED')",
            (d, us_level),
        )
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'm2_level', 'EU', %s, 'eur_mn', 'BCE')",
            (d, eu_level),
        )
        conn.execute(
            "INSERT INTO series_macro (date, indicator, geography, value, unit, source) "
            "VALUES (%s, 'fx_usd_eur', 'GLOBAL', %s, 'ratio', 'FRED')",
            (d, fx),
        )


def test_build_m2_global_persists_and_is_rerunnable(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)
    _seed_minimal_m2_inputs(conn)

    n1 = build_m2_global(conn, dry_run=False)
    assert n1 > 0

    rows = conn.execute(
        "SELECT date, value, unit, source FROM series_macro "
        "WHERE indicator='m2_global_yoy' AND geography='GLOBAL' ORDER BY date"
    ).fetchall()
    assert len(rows) >= 1
    assert all(r[2] == "pct" and r[3] == "CALC" for r in rows)

    us_yoy = conn.execute(
        "SELECT COUNT(*) FROM series_macro WHERE indicator='m2_yoy' AND geography='US' AND source='CALC'"
    ).fetchone()[0]
    assert us_yoy >= 1

    # Rerun (DELETE + reinsert) must not duplicate or error — idempotent at the row-count level.
    n2 = build_m2_global(conn, dry_run=False)
    assert n2 == n1
    rows2 = conn.execute(
        "SELECT COUNT(*) FROM series_macro WHERE indicator='m2_global_yoy' AND geography='GLOBAL'"
    ).fetchone()[0]
    assert rows2 == len(rows)


def test_build_m2_global_dry_run_writes_nothing(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_series_macro(conn)
    _seed_minimal_m2_inputs(conn)

    n = build_m2_global(conn, dry_run=True)
    assert n > 0
    count = conn.execute(
        "SELECT COUNT(*) FROM series_macro WHERE indicator='m2_global_yoy'"
    ).fetchone()[0]
    assert count == 0
