# proyecto2/tests/reports/test_rolling_dashboard_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 4) — regression tests for
rolling_dashboard.py's Postgres port: the window->window_label rename (fund_metric_timeseries AND
fund_metric_alerts) and the datetime.date-vs-string JSON-serialization bug (gotcha #6 — see
nav_discovery.py's companion fixes this same session for the other instances of this root cause).

_load_snapshot/_load_series/_load_alerts are tested against a real, hand-built schema (isolated
per-test via pg_conn_module_schema) rather than mocked, since the whole point is proving the SQL
text — including the {wc} substitution — is valid against a real server. _build_html's date
handling is tested directly with real datetime.date objects (not strings) as input, matching
exactly what a live psycopg3 read returns, to prove the str(dt) fix independent of whether the
loader functions are also exercised in the same test.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.reports.rolling_dashboard import (  # noqa: E402
    _load_alerts,
    _load_series,
    _load_snapshot,
    _build_html,
)


def _make_tables(conn):
    conn.execute("""
        CREATE TABLE fund_master (
            isin text PRIMARY KEY, fund_name text, fund_nature text
        )
    """)
    conn.execute("""
        CREATE TABLE fund_metric_timeseries (
            isin text NOT NULL, metric text NOT NULL, window_label text NOT NULL,
            date date NOT NULL, value double precision, real_flag smallint NOT NULL DEFAULT 0,
            PRIMARY KEY (isin, metric, window_label, date, real_flag)
        )
    """)
    conn.execute("""
        CREATE TABLE fund_metric_alerts (
            isin text NOT NULL, metric text NOT NULL, window_label text NOT NULL,
            level text NOT NULL, rule_code text, value double precision,
            reference_value double precision
        )
    """)


def test_load_snapshot_uses_window_label_column(pg_session_conn, pg_conn_module_schema):
    """The real port: window_label (not window) in FROM/JOIN/WHERE — proves the {wc} substitution
    produces valid SQL against Postgres's actual reserved-word-renamed column."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_master VALUES ('X1', 'Fund X', 'Renta Variable')")
    conn.execute(
        "INSERT INTO fund_metric_timeseries VALUES "
        "('X1', 'vol_ann', 'rolling_1y', '2026-06-30', 0.15, 0)"
    )
    conn.execute(
        "INSERT INTO fund_metric_timeseries VALUES "
        "('X1', 'return_ann', 'rolling_1y', '2026-06-30', 0.08, 0)"
    )

    rows = _load_snapshot(conn)
    assert len(rows) == 2
    isin, metric, window, dt, value, nature, name = rows[0]
    assert isin == "X1"
    assert window == "rolling_1y"
    assert isinstance(dt, date), "Postgres date column returns a real datetime.date object"


def test_load_series_uses_window_label_and_dynamic_in_list(pg_session_conn, pg_conn_module_schema):
    """Also proves the dynamic isin-IN-list placeholder fix (?->%s repeated N times)."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    for i, isin in enumerate(["X1", "X2", "X3"]):
        conn.execute(
            "INSERT INTO fund_metric_timeseries VALUES (%s, 'vol_ann', 'rolling_1y', %s, %s, 0)",
            (isin, f"2026-0{i+1}-28", 0.10 + i * 0.01),
        )

    rows = _load_series(conn, ["X1", "X3"])
    assert {r[0] for r in rows} == {"X1", "X3"}, "IN-list must select exactly the requested ISINs"

    assert _load_series(conn, []) == [], "empty isin list must short-circuit, not query"


def test_load_alerts_uses_window_label_on_alerts_table(pg_session_conn, pg_conn_module_schema):
    """fund_metric_alerts ALSO has the window->window_label rename — a separate table from
    fund_metric_timeseries, easy to miss if only one table's rename is checked."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_master VALUES ('X1', 'Fund X', 'Renta Variable')")
    conn.execute(
        "INSERT INTO fund_metric_alerts VALUES "
        "('X1', 'vol_ann', 'rolling_1y', 'ALARM', 'RULE1', 0.30, 0.15)"
    )

    rows = _load_alerts(conn)
    assert len(rows) == 1
    isin, metric, window, level, rule_code, value, ref_value, nature, name = rows[0]
    assert window == "rolling_1y"
    assert level == "ALARM"


# ============================================================
# _build_html — the datetime.date -> JSON serialization bug
# ============================================================

def test_build_html_json_serializes_real_date_objects_from_series_rows():
    """The actual bug: series_rows[i][3] (dt) is a real datetime.date on Postgres, not a string.
    json.dumps(all_dates) inside _build_html would raise TypeError on a raw date object —
    proving this test would have failed before the str(dt) fix, not just that it doesn't crash."""
    series_rows = [
        ("X1", "vol_ann", "rolling_1y", date(2026, 1, 31), 0.12, "Renta Variable", "Fund X"),
        ("X1", "vol_ann", "rolling_1y", date(2026, 2, 28), 0.13, "Renta Variable", "Fund X"),
        ("X1", "vol_ann", "rolling_1y", date(2026, 3, 31), 0.11, "Renta Variable", "Fund X"),
    ]
    snap_rows = [
        ("X1", "vol_ann", "rolling_1y", date(2026, 3, 31), 0.11, "Renta Variable", "Fund X"),
    ]
    # Must not raise — this is the regression proof.
    html = _build_html(
        snap_rows, series_rows, [], [],
        ["Renta Variable"], None, ["X1"], "2026-09-20",
    )
    assert "2026-01-31" in html, "date objects must serialize as plain 'YYYY-MM-DD' strings in the chart JSON"
