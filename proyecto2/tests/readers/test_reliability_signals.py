# proyecto2/tests/readers/test_reliability_signals.py
# -*- coding: utf-8 -*-
"""
Tests for the three observability/reliability-signal helpers added in
FEAT-P2-RELIABILITY-SIGNALS-1:

  db_readers.load_ts_cohort(conn, metric_version, run_date_iso)
  db_readers.count_stale_nav_funds(conn, metric_version, run_date_iso, max_age_days)
  db_readers.coverage_snapshot(conn, metrics, horizon)

R-7: imports ONLY db_readers — no run_pipeline.py, no core.io, no HTTP.
All tests use an in-memory SQLite DB built from a minimal schema.

Run from repo root:
    python -m pytest proyecto2/tests/readers/test_reliability_signals.py -v
"""

import sqlite3
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


# ============================================================
# Minimal in-memory DB builder
# ============================================================

def _make_db() -> sqlite3.Connection:
    """Returns an in-memory SQLite DB with the tables these helpers read."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE fund_metric_state (
            isin           TEXT NOT NULL,
            metric_version TEXT NOT NULL,
            input_hash     TEXT,
            calculated_at  TEXT,
            PRIMARY KEY (isin, metric_version)
        );

        CREATE TABLE fund_nav_monthly (
            ISIN TEXT NOT NULL,
            Date TEXT NOT NULL,
            NAV  REAL,
            PRIMARY KEY (ISIN, Date)
        );

        CREATE TABLE fund_metrics (
            isin             TEXT NOT NULL,
            metric           TEXT NOT NULL,
            horizon          TEXT NOT NULL,
            value            REAL,
            real_flag        INTEGER NOT NULL DEFAULT 0,
            calculation_date TEXT,
            metric_version   TEXT NOT NULL DEFAULT 'v1',
            load_ts          TEXT,
            PRIMARY KEY (isin, metric, horizon, real_flag, metric_version)
        );
    """)
    return conn


# ============================================================
# load_ts_cohort tests
# ============================================================

class TestLoadTsCohort:

    def test_empty_db_returns_empty_list(self):
        conn = _make_db()
        assert load_ts_cohort(conn, MV, "2026-08-14") == []

    def test_groups_by_date(self):
        """Funds with two different load_ts dates produce two cohort rows."""
        conn = _make_db()
        # Two funds processed today
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_A", MV, "hash_a", "2026-08-14"),
        )
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_B", MV, "hash_b", "2026-08-14"),
        )
        # ISIN_A: all metrics fresh (load_ts = today)
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_A", "sharpe", "since_inception", 1.2, 0, "2026-08-14", MV, "2026-08-14 10:00:00"),
        )
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_A", "beta_rate_eu", "since_inception", -0.3, 0, "2026-07-31", MV, "2026-07-31 08:00:00"),
        )
        # ISIN_B: all fresh
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_B", "sharpe", "since_inception", 0.8, 0, "2026-08-14", MV, "2026-08-14 11:00:00"),
        )
        conn.commit()

        result = load_ts_cohort(conn, MV, "2026-08-14")
        result_dict = dict(result)
        # Two distinct load_ts dates: 2026-07-31 (1 row) and 2026-08-14 (2 rows)
        assert "2026-07-31" in result_dict
        assert "2026-08-14" in result_dict
        assert result_dict["2026-07-31"] == 1
        assert result_dict["2026-08-14"] == 2

    def test_only_counts_funds_processed_today(self):
        """Funds processed on a different date are excluded from the cohort."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_OLD", MV, "hash_old", "2026-07-31"),  # processed last month
        )
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_OLD", "sharpe", "since_inception", 0.5, 0, "2026-07-31", MV, "2026-07-31 09:00:00"),
        )
        conn.commit()

        result = load_ts_cohort(conn, MV, "2026-08-14")  # asking for today
        assert result == []

    def test_different_metric_version_excluded(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_A", "v2", "hash_a", "2026-08-14"),  # different version
        )
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_A", "sharpe", "since_inception", 1.0, 0, "2026-08-14", "v2", "2026-08-14 10:00:00"),
        )
        conn.commit()

        result = load_ts_cohort(conn, MV, "2026-08-14")  # asking for v1
        assert result == []


# ============================================================
# count_stale_nav_funds tests
# ============================================================

class TestCountStaleNavFunds:

    def test_empty_db_returns_zero(self):
        conn = _make_db()
        assert count_stale_nav_funds(conn, MV, "2026-08-14") == 0

    def test_fresh_nav_not_counted(self):
        """Fund with NAV dated 2026-08-01 is fresh relative to 2026-08-14 (13d < 60d)."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_A", MV, "h", "2026-08-14"),
        )
        conn.execute(
            "INSERT INTO fund_nav_monthly VALUES (?,?,?)",
            ("ISIN_A", "2026-08-01", 100.0),
        )
        conn.commit()
        assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 0

    def test_stale_nav_counted(self):
        """Fund with NAV dated 2026-05-01 is stale relative to 2026-08-14 (105d > 60d)."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_A", MV, "h", "2026-08-14"),
        )
        conn.execute(
            "INSERT INTO fund_nav_monthly VALUES (?,?,?)",
            ("ISIN_A", "2026-05-01", 100.0),
        )
        conn.commit()
        assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 1

    def test_mixed_fresh_and_stale(self):
        """One fresh + one stale → count 1."""
        conn = _make_db()
        for isin, nav_date in [("ISIN_A", "2026-08-01"), ("ISIN_B", "2026-04-01")]:
            conn.execute(
                "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
                (isin, MV, "h", "2026-08-14"),
            )
            conn.execute(
                "INSERT INTO fund_nav_monthly VALUES (?,?,?)",
                (isin, nav_date, 100.0),
            )
        conn.commit()
        assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 1

    def test_respects_max_age_days_boundary(self):
        """Exactly at the threshold is NOT stale (julianday diff == max_age_days, not >)."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_A", MV, "h", "2026-08-14"),
        )
        # Exactly 60 days before 2026-08-14 is 2026-06-15
        conn.execute(
            "INSERT INTO fund_nav_monthly VALUES (?,?,?)",
            ("ISIN_A", "2026-06-15", 100.0),
        )
        conn.commit()
        # 60 days exactly → NOT stale (condition is > not >=)
        assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 0

    def test_only_funds_processed_today(self):
        """Stale fund processed last month should not be counted."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metric_state VALUES (?,?,?,?)",
            ("ISIN_OLD", MV, "h", "2026-07-31"),  # not today
        )
        conn.execute(
            "INSERT INTO fund_nav_monthly VALUES (?,?,?)",
            ("ISIN_OLD", "2026-01-01", 100.0),   # very stale NAV
        )
        conn.commit()
        assert count_stale_nav_funds(conn, MV, "2026-08-14", max_age_days=60) == 0


# ============================================================
# coverage_snapshot tests
# ============================================================

class TestCoverageSnapshot:

    def test_empty_db_returns_zeros(self):
        conn = _make_db()
        result = coverage_snapshot(conn, ["sharpe", "max_drawdown"])
        assert result == [("sharpe", 0), ("max_drawdown", 0)]

    def test_counts_distinct_isins_with_non_null_value(self):
        conn = _make_db()
        # Two ISINs with sharpe, one with max_drawdown
        for isin, metric, value in [
            ("ISIN_A", "sharpe", 1.2),
            ("ISIN_B", "sharpe", 0.8),
            ("ISIN_A", "max_drawdown", -0.15),
        ]:
            conn.execute(
                "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
                (isin, metric, "since_inception", value, 0, "2026-08-14", MV, "2026-08-14"),
            )
        conn.commit()

        result = coverage_snapshot(conn, ["sharpe", "max_drawdown"])
        result_dict = dict(result)
        assert result_dict["sharpe"] == 2
        assert result_dict["max_drawdown"] == 1

    def test_null_values_not_counted(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_A", "sharpe", "since_inception", None, 0, "2026-08-14", MV, "2026-08-14"),
        )
        conn.commit()
        result = coverage_snapshot(conn, ["sharpe"])
        assert result == [("sharpe", 0)]

    def test_preserves_input_metric_order(self):
        """Output order matches input order regardless of DB retrieval order."""
        conn = _make_db()
        for metric in ["momentum_rank", "alpha_persistence", "capture_ratio"]:
            conn.execute(
                "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
                ("ISIN_A", metric, "since_inception", 1.0, 0, "2026-08-14", MV, "2026-08-14"),
            )
        conn.commit()

        metrics_in = ["capture_ratio", "alpha_persistence", "momentum_rank"]
        result = coverage_snapshot(conn, metrics_in)
        assert [r[0] for r in result] == metrics_in

    def test_horizon_filter(self):
        """Only rows matching the given horizon are counted."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_metrics VALUES (?,?,?,?,?,?,?,?)",
            ("ISIN_A", "sharpe", "rolling_3y", 1.0, 0, "2026-08-14", MV, "2026-08-14"),
        )
        conn.commit()
        # Asking for since_inception → 0 (the row is rolling_3y)
        result = coverage_snapshot(conn, ["sharpe"], horizon="since_inception")
        assert result == [("sharpe", 0)]
        # Asking for rolling_3y → 1
        result = coverage_snapshot(conn, ["sharpe"], horizon="rolling_3y")
        assert result == [("sharpe", 1)]
