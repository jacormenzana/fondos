# proyecto2/tests/discovery/test_nav_discovery_splice_20260915.py
# -*- coding: utf-8 -*-
"""
Tests for the durable MORNINGSTAR_CHART ingestion-time splice fix (2026-09-15).

Bug (see memory project_nav_scale_rebasing_fix_20260913 /
project_p0_p2_canonicalization_20260914): MORNINGSTAR_CHART is a charting
endpoint that rebases its index to an arbitrary base on every fetch window.
The only splice fix that existed was retroactive
(scripts/mig/repair_nav_scale_20260719.py::_splice_rebased_index_batches),
applied once by hand — a routine NAV Load re-introduced the identical >8x
seam on the same 3 ISINs (LU2473381015, LU2536453348, LU2536454403) hours
after being manually spliced, proving this recurs on ordinary operational
runs, not just rare edge cases.

Fix: _splice_new_chart_batch(conn, isin, rows) re-anchors a freshly-fetched
batch onto whatever MORNINGSTAR_CHART history already exists in
fund_nav_daily BEFORE it is ever written — same carry-factor idea as the
retroactive script, but scoped to the one boundary a single ingestion call
can introduce, computed live against the DB instead of a full historical
re-walk. Wired into all 4 call sites where nav_discovery.py writes freshly
downloaded NAV data (run_load's two worker-pool branches, run_load's
sequential branch, run_update).

R-7 compliant: no pipeline.py / core.io imports.
"""

import sqlite3
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

from sqlite_writer import create_schema

from proyecto2.src.discovery.nav_discovery import _splice_new_chart_batch


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _insert_existing_daily(conn, isin: str, date: str, nav: float,
                            source: str = "MORNINGSTAR_CHART"):
    conn.execute(
        "INSERT INTO fund_nav_daily (ISIN, Date, NAV, NAV_Currency, NAV_Type, "
        "Is_Estimated, Data_Source) VALUES (?, ?, ?, 'EUR', 'TOTAL_RETURN_IDX', 0, ?)",
        (isin, date, nav, source),
    )
    conn.commit()


def _new_row(isin: str, date: str, nav: float, source: str = "MORNINGSTAR_CHART") -> dict:
    return {
        "ISIN": isin, "Date": date, "NAV": nav, "NAV_Currency": "EUR",
        "NAV_Type": "TOTAL_RETURN_IDX", "Is_Estimated": 0, "Data_Source": source,
    }


class TestSpliceNewChartBatch:
    def test_no_existing_history_returns_rows_unchanged(self):
        """First-ever load: nothing to splice against."""
        conn = _memory_conn()
        rows = [_new_row("LU_FIRST", "2026-09-01", 1.5),
                _new_row("LU_FIRST", "2026-09-02", 1.51)]

        result = _splice_new_chart_batch(conn, "LU_FIRST", rows)

        assert result == rows

    def test_reproduces_the_documented_regression_seam(self):
        """Reproduces the exact real-world case: LU2473381015-style seam,
        NAV jumping from ~0.0065 (correctly spliced history) to 1.5264
        (fresh rebase) -- a ~235x jump, matching the documented regression."""
        conn = _memory_conn()
        isin = "LU2473381015"
        _insert_existing_daily(conn, isin, "2026-08-31", 0.006490)
        rows = [_new_row(isin, "2026-09-10", 1.5250),
                _new_row(isin, "2026-09-11", 1.5264)]

        result = _splice_new_chart_batch(conn, isin, rows)

        # Anchor = nearest prior existing date (08-31), no exact overlap.
        # abs=1e-6 accounts for the implementation's intentional round(..., 6).
        carry = 0.006490 / 1.5250
        assert result[0]["NAV"] == pytest.approx(1.5250 * carry, abs=1e-6)
        assert result[1]["NAV"] == pytest.approx(1.5264 * carry, abs=1e-6)
        # Post-splice, the boundary is continuous (ratio ~1, not >8x).
        assert result[0]["NAV"] / 0.006490 < 8.0

    def test_exact_date_overlap_preferred_over_nearest_prior(self):
        """When the new batch re-fetches a date that's already stored, that
        exact-date pair is the most reliable anchor -- use it even if an
        earlier prior date also exists."""
        conn = _memory_conn()
        isin = "LU_OVERLAP"
        _insert_existing_daily(conn, isin, "2026-09-01", 0.01)
        _insert_existing_daily(conn, isin, "2026-09-05", 0.0102)  # overlap anchor
        rows = [
            _new_row(isin, "2026-09-05", 2.0),   # rebased value at the SAME date
            _new_row(isin, "2026-09-06", 2.04),
        ]

        result = _splice_new_chart_batch(conn, isin, rows)

        carry = 0.0102 / 2.0
        assert result[0]["NAV"] == pytest.approx(0.0102, rel=1e-9)  # == existing anchor
        assert result[1]["NAV"] == pytest.approx(2.04 * carry, rel=1e-9)

    def test_ordinary_price_move_left_untouched(self):
        """A normal day-to-day move (well under the 8x threshold) must not
        be rescaled -- only genuine rebasing seams."""
        conn = _memory_conn()
        isin = "LU_NORMAL"
        _insert_existing_daily(conn, isin, "2026-09-01", 1.00)
        rows = [_new_row(isin, "2026-09-02", 1.03),  # +3%, ordinary
                _new_row(isin, "2026-09-03", 1.05)]

        result = _splice_new_chart_batch(conn, isin, rows)

        assert result == rows  # untouched

    def test_non_chart_rows_pass_through_unscaled(self):
        conn = _memory_conn()
        isin = "LU_MIXED"
        _insert_existing_daily(conn, isin, "2026-09-01", 0.01)
        rows = [
            _new_row(isin, "2026-09-02", 5.0),  # CHART, seam
            {**_new_row(isin, "2026-09-02", 999.0), "Data_Source": "MORNINGSTAR"},
        ]

        result = _splice_new_chart_batch(conn, isin, rows)

        chart_row = [r for r in result if r["Data_Source"] == "MORNINGSTAR_CHART"][0]
        other_row = [r for r in result if r["Data_Source"] == "MORNINGSTAR"][0]
        assert chart_row["NAV"] != 5.0  # rescaled
        assert other_row["NAV"] == 999.0  # untouched

    def test_empty_rows_returns_empty(self):
        conn = _memory_conn()
        assert _splice_new_chart_batch(conn, "LU_EMPTY", []) == []

    def test_downward_seam_also_corrected(self):
        """The rebase can land on either side -- a new base that's much
        SMALLER than history must be corrected too, not just larger."""
        conn = _memory_conn()
        isin = "LU_DOWN"
        _insert_existing_daily(conn, isin, "2026-09-01", 100.0)
        rows = [_new_row(isin, "2026-09-02", 0.5)]  # 200x smaller

        result = _splice_new_chart_batch(conn, isin, rows)

        carry = 100.0 / 0.5
        assert result[0]["NAV"] == pytest.approx(0.5 * carry, rel=1e-9)
        assert result[0]["NAV"] == pytest.approx(100.0, rel=1e-9)
