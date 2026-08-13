# proyecto2/tests/readers/test_preflight.py
# -*- coding: utf-8 -*-
"""
Tests for src/readers/db_readers.count_isins_with_new_nav (P2-04 preflight).

R-7: imports ONLY db_readers — no pipeline.py, no core.io, no HTTP.
All tests use an in-memory SQLite DB with a minimal schema.

Run from repo root:
    python -m pytest proyecto2/tests/readers/test_preflight.py -v
"""

import sqlite3
import sys
from pathlib import Path

# proyecto2/ must be on sys.path (pytest.ini pythonpath=. sets this when run
# from proyecto2/); src/ is then reachable as the src package.
_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.readers.db_readers import count_isins_with_new_nav  # noqa: E402

MV = "v1"  # metric_version constant used in tests


# ============================================================
# Helpers
# ============================================================

def _make_db() -> sqlite3.Connection:
    """In-memory DB with the minimal schema required by count_isins_with_new_nav."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE fund_master (
            ISIN TEXT PRIMARY KEY,
            Fund_Name TEXT NOT NULL,
            Fund_Nature TEXT NOT NULL
        );
        CREATE TABLE fund_nav_monthly (
            ISIN TEXT NOT NULL,
            Date TEXT NOT NULL,
            NAV  REAL,
            PRIMARY KEY (ISIN, Date)
        );
        CREATE TABLE fund_metric_state (
            isin           TEXT NOT NULL,
            metric_version TEXT NOT NULL,
            input_hash     TEXT NOT NULL,
            calculated_at  TEXT NOT NULL,
            PRIMARY KEY (isin, metric_version)
        );
        CREATE TABLE nav_sources (
            isin           TEXT PRIMARY KEY,
            last_nav_date  DATE,
            status         TEXT
        );
    """)
    return conn


def _add_fund(conn, isin="IE0001", nav_date="2024-06-30"):
    """Insert a fund into fund_master + one NAV row."""
    conn.execute(
        "INSERT OR IGNORE INTO fund_master VALUES (?, 'Test Fund', 'Renta Fija')",
        (isin,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO fund_nav_monthly VALUES (?, ?, 100.0)",
        (isin, nav_date),
    )


def _add_state(conn, isin="IE0001", calculated_at="2024-06-30", mv=MV):
    conn.execute(
        "INSERT OR REPLACE INTO fund_metric_state VALUES (?, ?, 'hash123', ?)",
        (isin, mv, calculated_at),
    )


def _add_nav_source(conn, isin="IE0001", last_nav_date="2024-06-30"):
    conn.execute(
        "INSERT OR REPLACE INTO nav_sources VALUES (?, ?, 'OK')",
        (isin, last_nav_date),
    )


# ============================================================
# Tests — empty / trivial cases
# ============================================================

class TestEmpty:
    def test_empty_db_returns_zeros(self):
        conn = _make_db()
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_new == 0
        assert n_never == 0
        assert n_total == 0

    def test_nav_without_fund_master_excluded(self):
        """ISINs in fund_nav_monthly but NOT in fund_master must not appear."""
        conn = _make_db()
        conn.execute(
            "INSERT INTO fund_nav_monthly VALUES ('LU9999', '2024-06-30', 100.0)"
        )
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_total == 0


# ============================================================
# Tests — never calculated
# ============================================================

class TestNeverCalculated:
    def test_one_isin_no_state_counted_as_never(self):
        conn = _make_db()
        _add_fund(conn)
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_total == 1
        assert n_never == 1
        assert n_new == 0

    def test_state_for_different_metric_version_is_ignored(self):
        """State for 'v2' must not count as having been calculated for 'v1'."""
        conn = _make_db()
        _add_fund(conn)
        _add_state(conn, mv="v2")
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_never == 1  # no v1 state


# ============================================================
# Tests — NAV freshness
# ============================================================

class TestNavFreshness:
    def test_nav_same_date_as_calculated_at_not_new(self):
        conn = _make_db()
        _add_fund(conn, nav_date="2024-06-30")
        _add_state(conn, calculated_at="2024-06-30")
        _add_nav_source(conn, last_nav_date="2024-06-30")
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_new == 0
        assert n_never == 0
        assert n_total == 1

    def test_nav_newer_than_calculated_at_counts_as_new(self):
        conn = _make_db()
        _add_fund(conn, nav_date="2024-07-31")
        _add_state(conn, calculated_at="2024-06-30")
        _add_nav_source(conn, last_nav_date="2024-07-31")
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_new == 1
        assert n_never == 0

    def test_nav_older_than_calculated_at_not_new(self):
        conn = _make_db()
        _add_fund(conn, nav_date="2024-05-31")
        _add_state(conn, calculated_at="2024-06-30")
        _add_nav_source(conn, last_nav_date="2024-05-31")
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_new == 0
        assert n_never == 0

    def test_no_nav_source_row_means_not_new(self):
        """If nav_sources has no row for the ISIN, last_nav_date is NULL → not new."""
        conn = _make_db()
        _add_fund(conn)
        _add_state(conn, calculated_at="2024-06-30")
        # no _add_nav_source call
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_new == 0
        assert n_never == 0

    def test_null_last_nav_date_means_not_new(self):
        """nav_sources row exists but last_nav_date IS NULL → not new."""
        conn = _make_db()
        _add_fund(conn)
        _add_state(conn, calculated_at="2024-06-30")
        conn.execute(
            "INSERT INTO nav_sources VALUES ('IE0001', NULL, 'PENDING')"
        )
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_new == 0
        assert n_never == 0


# ============================================================
# Tests — mixed universes
# ============================================================

class TestMixedUniverse:
    def test_two_isins_one_new_one_current(self):
        conn = _make_db()
        _add_fund(conn, isin="IE0001", nav_date="2024-07-31")
        _add_state(conn, isin="IE0001", calculated_at="2024-06-30")
        _add_nav_source(conn, isin="IE0001", last_nav_date="2024-07-31")

        _add_fund(conn, isin="IE0002", nav_date="2024-06-30")
        _add_state(conn, isin="IE0002", calculated_at="2024-06-30")
        _add_nav_source(conn, isin="IE0002", last_nav_date="2024-06-30")

        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_total == 2
        assert n_new == 1
        assert n_never == 0

    def test_two_isins_one_never_one_new(self):
        conn = _make_db()
        # IE0001 — never calculated
        _add_fund(conn, isin="IE0001")

        # IE0002 — calculated, but NAV is newer
        _add_fund(conn, isin="IE0002", nav_date="2024-07-31")
        _add_state(conn, isin="IE0002", calculated_at="2024-06-30")
        _add_nav_source(conn, isin="IE0002", last_nav_date="2024-07-31")

        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_total == 2
        assert n_never == 1
        assert n_new == 1

    def test_all_up_to_date_returns_zeros_for_new_and_never(self):
        conn = _make_db()
        for isin in ["IE0001", "IE0002", "IE0003"]:
            _add_fund(conn, isin=isin, nav_date="2024-06-30")
            _add_state(conn, isin=isin, calculated_at="2024-06-30")
            _add_nav_source(conn, isin=isin, last_nav_date="2024-06-30")
        n_new, n_never, n_total = count_isins_with_new_nav(conn, MV)
        assert n_total == 3
        assert n_new == 0
        assert n_never == 0
