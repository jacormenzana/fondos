# proyecto2/tests/discovery/test_nav_discovery_date_types_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 4) — regression tests for a class
of bug found while porting nav_discovery.py's remaining reads: Postgres returns genuine
datetime.date objects for `date` columns (fund_nav_daily.Date, nav_sources.last_nav_date, ...),
while SQLite returns the stored text as-is. Several functions in this file assumed a string
(`last_daily[:10]`, `datetime.strptime(last_d_stored, "%Y-%m-%d")`, dict keys used as `new_by_date`
lookups) — silently wrong or outright crashing under Postgres, never caught by any SQLite-only
test because the bug is specific to the value's Python TYPE, not its content.

test_is_stale_handles_both_str_and_date_pg is a pure-Python test (_is_stale takes no DB connection
at all) — it needs no live Postgres, but lives alongside the connection-requiring test below since
both regression-test the same root cause. test_splice_anchor_date_as_date_object_pg needs a real
connection because _splice_new_chart_batch() queries fund_nav_daily directly.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.discovery.nav_discovery import _is_stale, _splice_new_chart_batch  # noqa: E402


# ============================================================
# _is_stale — pure function, no DB connection needed
# ============================================================

def test_is_stale_handles_both_str_and_date_pg():
    """The actual bug: last_daily/last_monthly can arrive as either a 'YYYY-MM-DD' string
    (SQLite) or a real datetime.date object (Postgres) — _is_stale() must produce the identical
    answer either way. Before the fix, a date object silently made every fund look stale
    (TypeError from `last_daily[:10]`, caught by a pre-existing except clause meant for malformed
    text, returning True unconditionally) instead of raising or degrading loudly."""
    cutoff = date(2026, 8, 1)
    ref_month_end = date(2026, 7, 31)

    fresh_str  = "2026-08-15"
    fresh_date = date(2026, 8, 15)
    stale_str  = "2026-06-01"
    stale_date = date(2026, 6, 1)

    # Daily-grain gate: fresh (>= cutoff) -> not stale, for both input types
    assert _is_stale("OK", fresh_str, None, cutoff, ref_month_end, monthly_grain=False) is False
    assert _is_stale("OK", fresh_date, None, cutoff, ref_month_end, monthly_grain=False) is False

    # Daily-grain gate: stale (< cutoff) -> stale, for both input types
    assert _is_stale("OK", stale_str, None, cutoff, ref_month_end, monthly_grain=False) is True
    assert _is_stale("OK", stale_date, None, cutoff, ref_month_end, monthly_grain=False) is True

    # Monthly-grain gate exercises the same code path for last_monthly
    assert _is_stale("OK", None, fresh_str, cutoff, ref_month_end, monthly_grain=True) is False
    assert _is_stale("OK", None, fresh_date, cutoff, ref_month_end, monthly_grain=True) is False
    assert _is_stale("OK", None, stale_str, cutoff, ref_month_end, monthly_grain=True) is True
    assert _is_stale("OK", None, stale_date, cutoff, ref_month_end, monthly_grain=True) is True


def test_is_stale_still_correct_for_non_date_edge_cases():
    """Confirms the fix didn't disturb the function's other branches."""
    cutoff = date(2026, 8, 1)
    ref_month_end = date(2026, 7, 31)
    assert _is_stale("FORCE_REFRESH", "2026-08-15", None, cutoff, ref_month_end, False) is True
    assert _is_stale("STALE_FROZEN", "2020-01-01", None, cutoff, ref_month_end, False) is False
    assert _is_stale("OK", None, None, cutoff, ref_month_end, False) is True  # no prior data


# ============================================================
# _splice_new_chart_batch — anchor_date dict-key lookup
# ============================================================

def _make_daily_table(conn):
    conn.execute("""
        CREATE TABLE fund_nav_daily (
            isin varchar(12) NOT NULL,
            date date NOT NULL,
            nav double precision,
            nav_currency varchar(3),
            nav_type text,
            is_estimated smallint,
            data_source text,
            PRIMARY KEY (isin, date)
        )
    """)


def test_splice_anchor_date_as_date_object_matches_string_keyed_batch(pg_conn):
    """The bug: overlap's anchor_date comes back from Postgres as a datetime.date object; the
    function looks it up in new_by_date, which is keyed by the API response's own 'YYYY-MM-DD'
    strings — a raw (unconverted) date-object key would never match a string key, so
    new_by_date[anchor_date] would KeyError even though the exact same calendar date is present
    under its string form. This proves the str() fix resolves that mismatch."""
    _make_daily_table(pg_conn)
    isin = "TESTSPLICE01"  # exactly 12 chars, fund_nav_daily.isin is varchar(12)
    # Existing history at a seam-worthy scale (existing NAV ~100, matching a "new" batch at ~800 —
    # ratio 8x, well past the default jump_threshold=8.0 boundary... use a very high ratio instead
    # to be unambiguous).
    pg_conn.execute(
        "INSERT INTO fund_nav_daily VALUES (%s, %s, %s, 'EUR', 'NAV', 0, 'MORNINGSTAR_CHART')",
        (isin, "2026-06-15", 100.0),
    )

    # New batch overlaps on the exact same date (2026-06-15), with a NAV suggesting a >8x
    # rebase seam versus the existing 100.0 anchor.
    rows = [
        {"ISIN": isin, "Date": "2026-06-15", "NAV": 900.0, "Data_Source": "MORNINGSTAR_CHART"},
        {"ISIN": isin, "Date": "2026-06-16", "NAV": 905.0, "Data_Source": "MORNINGSTAR_CHART"},
    ]

    spliced = _splice_new_chart_batch(pg_conn, isin, rows, jump_threshold=8.0)

    # Before the fix this line would raise KeyError inside _splice_new_chart_batch itself (anchor_date
    # a date object, new_by_date keyed by strings) rather than returning a result at all.
    assert spliced[0]["NAV"] < rows[0]["NAV"], (
        "seam detected (ratio 9x > threshold 8.0) -> carry_factor must rescale the new batch down"
    )
    # carry_factor = 100.0 / 900.0 -> new NAV values scaled by ~0.1111
    assert abs(spliced[0]["NAV"] - 100.0) < 0.01
    assert abs(spliced[1]["NAV"] - (905.0 * 100.0 / 900.0)) < 0.01
