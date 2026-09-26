# proyecto2/tests/discovery/test_nav_monthly_write_20260913.py
# -*- coding: utf-8 -*-
"""
Tests for FIX-NAV-OPEN-MONTH-1 (2026-09-13).

Bug: _write_nav_rows() used INSERT OR IGNORE keyed on the table's PK
(ISIN, Date), but the true semantic key for a monthly row is (ISIN,
YYYY-MM). During an open calendar month, each successive ingestion run
resamples to a later "latest day so far" and inserted it as a NEW row
instead of replacing the prior provisional one for that month — verified
in production: 6,876 (ISIN, month) pairs with duplicate rows, 13,308
removable rows, 99% concentrated in the two most recent months. Because the
write was OR IGNORE (not OR REPLACE), even a closed month could stay stuck
forever on its first-ever provisional value.

Fix: _write_nav_rows() now DELETEs any existing row(s) for the exact
(ISIN, YYYY-MM) being written before inserting — the same DELETE+INSERT
idea already used by _overwrite_nav_rows_monthly() (whole-ISIN scope, used
in RECALCULATE_MONTHLY), scoped down to just the affected months.

R-7 compliant: no pipeline.py / core.io imports.
"""

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_P2_ROOT = _HERE.parent.parent  # proyecto2/
_REPO = _P2_ROOT.parent         # c:/desarrollo/fondos
_CORE_DIR = _REPO / "proyecto1" / "core"
for _p in (str(_P2_ROOT), str(_REPO), str(_CORE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery.nav_discovery import _resample_to_monthly, _write_nav_rows


_PG = {}


@pytest.fixture(autouse=True)
def _pg_connection(pg_app_conn):
    """Hand the per-test savepoint-wrapped Postgres connection (real DDL, app search_path) to the module's DB-building helper."""
    _PG["conn"] = pg_app_conn
    # These tests exercise NAV logic, not referential integrity: skip FK triggers for this test only
    # (transactional -> undone with the SAVEPOINT).
    pg_app_conn.execute("SET LOCAL session_replication_role = replica")
    yield
    _PG.clear()



def _make_row(isin: str, date: str, nav: float, source: str = "MORNINGSTAR_CHART") -> dict:
    return {
        "ISIN": isin,
        "Date": date,
        "NAV": nav,
        "NAV_Currency": "EUR",
        "NAV_Type": "TotalReturn",
        "Is_Estimated": 0,
        "Data_Source": source,
    }


def _memory_conn():
    """Conexion Postgres del test (DDL real de db/pg, revertida al acabar)."""
    return _PG["conn"]


def _nav_rows(conn, isin: str) -> list[tuple]:
    """(YYYY-MM-DD string, NAV) tuples: Postgres returns real date objects, the assertions use ISO text."""
    rows = conn.execute(
        "SELECT Date, NAV FROM fund_nav_monthly WHERE ISIN=%s ORDER BY Date", (isin,)
    ).fetchall()
    return [(str(d), nav) for d, nav in rows]


class TestWriteNavRowsOpenMonthFix:
    def test_second_write_for_same_open_month_replaces_not_accumulates(self):
        conn = _memory_conn()
        isin = "IE00TEST0001"

        # First ingestion run mid-month: provisional value as of the 11th.
        _write_nav_rows(conn, [_make_row(isin, "2026-08-11", 100.0)], dry_run=False)
        # Second ingestion run, later in the same still-open month: as of the 19th.
        _write_nav_rows(conn, [_make_row(isin, "2026-08-19", 103.5)], dry_run=False)

        rows = _nav_rows(conn, isin)
        assert len(rows) == 1, f"expected exactly one row for the open month, got {rows}"
        assert rows[0] == ("2026-08-19", 103.5)

    def test_closed_month_stuck_on_stale_provisional_value_is_corrected(self):
        """The old bug's worse consequence: OR IGNORE meant a closed month
        could stay wrong forever. A later write for that same month (e.g. a
        RECALCULATE-style re-ingestion) must now win, not be ignored.
        """
        conn = _memory_conn()
        isin = "IE00TEST0001"

        _write_nav_rows(conn, [_make_row(isin, "2026-07-12", 50.0)], dry_run=False)
        # Month has since closed; a later run resamples the true month-end value.
        _write_nav_rows(conn, [_make_row(isin, "2026-07-31", 52.25)], dry_run=False)

        rows = _nav_rows(conn, isin)
        assert rows == [("2026-07-31", 52.25)]

    def test_different_months_for_same_isin_both_survive(self):
        conn = _memory_conn()
        isin = "IE00TEST0001"

        _write_nav_rows(conn, [_make_row(isin, "2026-06-30", 90.0)], dry_run=False)
        _write_nav_rows(conn, [_make_row(isin, "2026-07-31", 91.0)], dry_run=False)

        rows = _nav_rows(conn, isin)
        assert rows == [("2026-06-30", 90.0), ("2026-07-31", 91.0)]

    def test_different_isins_same_month_do_not_cross_delete(self):
        conn = _memory_conn()

        _write_nav_rows(conn, [_make_row("IE00TEST0001", "2026-08-11", 100.0)], dry_run=False)
        _write_nav_rows(conn, [_make_row("IE00TEST0002", "2026-08-19", 200.0)], dry_run=False)

        assert _nav_rows(conn, "IE00TEST0001") == [("2026-08-11", 100.0)]
        assert _nav_rows(conn, "IE00TEST0002") == [("2026-08-19", 200.0)]

    def test_rewriting_identical_final_row_is_a_noop(self):
        conn = _memory_conn()
        isin = "IE00TEST0001"

        _write_nav_rows(conn, [_make_row(isin, "2026-05-29", 75.0)], dry_run=False)
        _write_nav_rows(conn, [_make_row(isin, "2026-05-29", 75.0)], dry_run=False)

        assert _nav_rows(conn, isin) == [("2026-05-29", 75.0)]

    def test_dry_run_writes_nothing(self):
        conn = _memory_conn()
        isin = "IE00TEST0001"

        _write_nav_rows(conn, [_make_row(isin, "2026-08-11", 100.0)], dry_run=True)

        assert _nav_rows(conn, isin) == []


class TestResampleToMonthly:
    def test_keeps_latest_date_per_month(self):
        rows = [
            _make_row("X", "2026-08-11", 100.0),
            _make_row("X", "2026-08-19", 103.5),
            _make_row("X", "2026-08-14", 101.0),
        ]
        result = _resample_to_monthly(rows)
        assert len(result) == 1
        assert result[0]["Date"] == "2026-08-19"
        assert result[0]["NAV"] == 103.5

    def test_handles_multiple_months(self):
        rows = [
            _make_row("X", "2026-06-15", 90.0),
            _make_row("X", "2026-06-30", 92.0),
            _make_row("X", "2026-07-05", 93.0),
        ]
        result = _resample_to_monthly(rows)
        dates = [r["Date"] for r in result]
        assert dates == ["2026-06-30", "2026-07-05"]
