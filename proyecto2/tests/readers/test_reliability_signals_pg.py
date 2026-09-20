# proyecto2/tests/readers/test_reliability_signals_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 1) — dedicated regression tests for
src/readers/db_readers.py's three observability helpers against real Postgres:

  load_ts_cohort(conn, metric_version, run_start_iso)
  count_stale_nav_funds(conn, metric_version, run_start_iso, max_age_days, as_of_iso)
  coverage_snapshot(conn, metrics, horizon)

Companion to test_reliability_signals.py (SQLite, in-memory). These two functions are the reason
this file exists: they're the only two SQLite-only-syntax sites in db_readers.py (DATE(), julianday())
so this is the real regression coverage for that rewrite, not just a dialect smoke test. Uses
pg_conn_module_schema like every other read-only reader test ported in this tranche — see
test_preflight_pg.py's docstring for why.

R-7: imports ONLY db_readers — no run_pipeline.py, no core.io, no HTTP.
"""
from __future__ import annotations

import sys
from pathlib import Path

_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.readers.db_readers import (  # noqa: E402
    load_ts_cohort,
    count_stale_nav_funds,
    coverage_snapshot,
)

MV = "v1"


def _make_tables(conn):
    conn.execute("""
        CREATE TABLE fund_metric_state (
            isin text NOT NULL,
            metric_version text NOT NULL,
            input_hash text,
            calculated_at date,
            PRIMARY KEY (isin, metric_version)
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
        CREATE TABLE fund_metrics (
            isin text NOT NULL,
            metric text NOT NULL,
            horizon text NOT NULL,
            value double precision,
            real_flag smallint NOT NULL DEFAULT 0,
            calculation_date date,
            metric_version text NOT NULL DEFAULT 'v1',
            load_ts timestamptz,
            PRIMARY KEY (isin, metric, horizon, real_flag, metric_version)
        )
    """)


# ============================================================
# load_ts_cohort
# ============================================================

def test_load_ts_cohort_groups_by_date(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)",
        ("ISIN_A", MV, "hash_a", "2026-08-14"),
    )
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)",
        ("ISIN_B", MV, "hash_b", "2026-08-14"),
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_A", "sharpe", "since_inception", 1.2, 0, "2026-08-14", MV, "2026-08-14 10:00:00"),
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_A", "beta_rate_eu", "since_inception", -0.3, 0, "2026-07-31", MV,
         "2026-07-31 08:00:00"),
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_B", "sharpe", "since_inception", 0.8, 0, "2026-08-14", MV, "2026-08-14 11:00:00"),
    )

    result_dict = dict(load_ts_cohort(conn, MV, "2026-08-14"))
    assert result_dict["2026-07-31"] == 1
    assert result_dict["2026-08-14"] == 2


def test_load_ts_cohort_excludes_other_runs_and_versions(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    # processed last month -> excluded when asking about today
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)",
        ("ISIN_OLD", MV, "hash_old", "2026-07-31"),
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_OLD", "sharpe", "since_inception", 0.5, 0, "2026-07-31", MV,
         "2026-07-31 09:00:00"),
    )
    assert load_ts_cohort(conn, MV, "2026-08-14") == []

    # different metric_version -> excluded
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)",
        ("ISIN_A", "v2", "hash_a", "2026-08-14"),
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_A", "sharpe", "since_inception", 1.0, 0, "2026-08-14", "v2", "2026-08-14 10:00:00"),
    )
    assert load_ts_cohort(conn, MV, "2026-08-14") == []


def test_load_ts_cohort_midnight_boundary(pg_session_conn, pg_conn_module_schema):
    """Overnight P2 runs stamp calculated_at across two calendar days — both must be counted."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("PRE", MV, "h1", "2026-08-13")
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("PRE", "sharpe", "since_inception", 1.0, 0, "2026-08-13", MV, "2026-08-13 23:00:00"),
    )
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("POST", MV, "h2", "2026-08-14")
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("POST", "sharpe", "since_inception", 1.1, 0, "2026-08-14", MV, "2026-08-14 01:00:00"),
    )
    conn.execute(
        "INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("OLD", MV, "h3", "2026-07-01")
    )
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("OLD", "sharpe", "since_inception", 0.5, 0, "2026-07-01", MV, "2026-07-01 12:00:00"),
    )

    result_dict = dict(load_ts_cohort(conn, MV, "2026-08-13"))
    assert result_dict.get("2026-08-13") == 1
    assert result_dict.get("2026-08-14") == 1
    assert sum(result_dict.values()) == 2, "OLD fund must not appear"


# ============================================================
# count_stale_nav_funds — the julianday() -> date-subtraction rewrite
# ============================================================

def test_stale_nav_counted(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("ISIN_A", MV, "h", "2026-08-14"))
    conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)", ("ISIN_A", "2026-05-01", 100.0))
    assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 1


def test_fresh_nav_not_counted(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("ISIN_A", MV, "h", "2026-08-14"))
    conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)", ("ISIN_A", "2026-08-01", 100.0))
    assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 0


def test_respects_max_age_days_boundary(pg_session_conn, pg_conn_module_schema):
    """Exactly at the threshold is NOT stale (date-diff == max_age_days, not >)."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("ISIN_A", MV, "h", "2026-08-14"))
    # Exactly 60 days before 2026-08-14 is 2026-06-15
    conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)", ("ISIN_A", "2026-06-15", 100.0))
    assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 0


def test_count_stale_nav_spans_two_days(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("PRE", MV, "h1", "2026-08-13"))
    conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)", ("PRE", "2026-02-01", 100.0))
    conn.execute("INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("POST", MV, "h2", "2026-08-14"))
    conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)", ("POST", "2026-01-01", 100.0))
    conn.execute("INSERT INTO fund_metric_state VALUES (%s,%s,%s,%s)", ("OLD", MV, "h3", "2026-07-01"))
    conn.execute("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s)", ("OLD", "2025-01-01", 100.0))

    n = count_stale_nav_funds(conn, MV, "2026-08-13", max_age_days=60, as_of_iso="2026-08-14")
    assert n == 2, "Expected 2 stale funds (PRE + POST)"


# ============================================================
# coverage_snapshot
# ============================================================

def test_coverage_snapshot_counts_and_order(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    for isin, metric, value in [
        ("ISIN_A", "sharpe", 1.2), ("ISIN_B", "sharpe", 0.8), ("ISIN_A", "max_dd", -0.15),
    ]:
        conn.execute(
            "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (isin, metric, "since_inception", value, 0, "2026-08-14", MV, "2026-08-14"),
        )
    # NULL value must not be counted
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_C", "sharpe", "since_inception", None, 0, "2026-08-14", MV, "2026-08-14"),
    )

    result = coverage_snapshot(conn, ["max_dd", "sharpe"])
    assert result == [("max_dd", 1), ("sharpe", 2)], "output order must match input order"


def test_coverage_snapshot_horizon_filter(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute(
        "INSERT INTO fund_metrics VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("ISIN_A", "sharpe", "rolling_3y", 1.0, 0, "2026-08-14", MV, "2026-08-14"),
    )
    assert coverage_snapshot(conn, ["sharpe"], horizon="since_inception") == [("sharpe", 0)]
    assert coverage_snapshot(conn, ["sharpe"], horizon="rolling_3y") == [("sharpe", 1)]
