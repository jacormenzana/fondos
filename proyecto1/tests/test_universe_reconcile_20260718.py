# proyecto1/tests/test_universe_reconcile_20260718.py
# -*- coding: utf-8 -*-
"""
Regression tests for FIX-UNIVERSE-RECON-1 (2026-07-18).

Tests reconcile_universe_membership() from sqlite_writer.py:
  - Basic flag setting (3 in-universe, 2 orphans)
  - Idempotency (same result on double call)
  - Re-entry: an orphan that comes back to the universe flips to 1
  - Empty universe: all rows flagged 0 (no crash)
  - Large universe: > 999 ISINs exercises the chunked IN() logic
  - Return value: tuple (in_universe, orphans) matches actual DB state

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sqlite3
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from sqlite_writer import reconcile_universe_membership


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_db(isins: list[str]) -> sqlite3.Connection:
    """Create an in-memory fund_master with a minimal schema and seed ISINs."""
    conn = sqlite3.connect(":memory:")
    conn.isolation_level = None  # manual transaction control
    conn.execute("""
        CREATE TABLE fund_master (
            ISIN TEXT PRIMARY KEY,
            Fund_Name TEXT NOT NULL DEFAULT '',
            Fund_Nature TEXT NOT NULL DEFAULT 'Restantes',
            Heuristic_Block TEXT NOT NULL DEFAULT 'RESTANTES',
            Heuristic_Core INTEGER NOT NULL DEFAULT 0,
            In_Current_Universe INTEGER NOT NULL DEFAULT 1
        )
    """)
    for isin in isins:
        conn.execute(
            "INSERT INTO fund_master (ISIN) VALUES (?)", (isin,)
        )
    return conn


def _flag_map(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {ISIN: In_Current_Universe} for all rows."""
    return {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT ISIN, In_Current_Universe FROM fund_master"
        ).fetchall()
    }


# ── Tests ────────────────────────────────────────────────────────────────────

class TestReconcileUniverseMembership:
    """Basic correctness tests."""

    def test_basic_in_and_out(self):
        """3 ISINs in universe → flag=1; 2 absent → flag=0."""
        all_isins = [f"LU{i:012d}" for i in range(5)]
        current = all_isins[:3]
        conn = _make_db(all_isins)
        in_u, orphans = reconcile_universe_membership(conn, current)

        flags = _flag_map(conn)
        for isin in current:
            assert flags[isin] == 1, f"{isin} should be in-universe (1)"
        for isin in all_isins[3:]:
            assert flags[isin] == 0, f"{isin} should be orphan (0)"

        assert in_u == 3, f"Expected 3 in-universe, got {in_u}"
        assert orphans == 2, f"Expected 2 orphans, got {orphans}"

    def test_all_in_universe(self):
        """All ISINs in universe → all flag=1, orphans=0."""
        isins = [f"FR{i:012d}" for i in range(4)]
        conn = _make_db(isins)
        in_u, orphans = reconcile_universe_membership(conn, isins)
        flags = _flag_map(conn)
        assert all(v == 1 for v in flags.values())
        assert in_u == 4 and orphans == 0

    def test_all_orphans(self):
        """Empty current universe → all flag=0, in_universe=0."""
        isins = [f"IE{i:012d}" for i in range(4)]
        conn = _make_db(isins)
        in_u, orphans = reconcile_universe_membership(conn, [])
        flags = _flag_map(conn)
        assert all(v == 0 for v in flags.values())
        assert in_u == 0 and orphans == 4

    def test_return_tuple_matches_db(self):
        """Return value (in_u, orphans) always matches actual DB counts."""
        isins = [f"LU{i:012d}" for i in range(10)]
        current = isins[:6]
        conn = _make_db(isins)
        in_u, orphans = reconcile_universe_membership(conn, current)
        db_in = conn.execute(
            "SELECT COUNT(*) FROM fund_master WHERE In_Current_Universe=1"
        ).fetchone()[0]
        db_out = conn.execute(
            "SELECT COUNT(*) FROM fund_master WHERE In_Current_Universe=0"
        ).fetchone()[0]
        assert in_u == db_in, f"Return {in_u} != DB {db_in}"
        assert orphans == db_out, f"Return {orphans} != DB {db_out}"


class TestReconcileIdempotency:
    """Calling twice with the same list must give the same result."""

    def test_idempotent_same_universe(self):
        """Double call with same list → same flags and counts."""
        isins = [f"LU{i:012d}" for i in range(6)]
        current = isins[:4]
        conn = _make_db(isins)
        r1 = reconcile_universe_membership(conn, current)
        r2 = reconcile_universe_membership(conn, current)
        assert r1 == r2, f"Not idempotent: {r1} != {r2}"
        flags = _flag_map(conn)
        for isin in current:
            assert flags[isin] == 1
        for isin in isins[4:]:
            assert flags[isin] == 0

    def test_reentry_flips_orphan_back_to_in_universe(self):
        """An orphan that re-enters the universe must flip from 0 → 1."""
        isins = [f"LU{i:012d}" for i in range(4)]
        conn = _make_db(isins)

        # First call: last ISIN is orphan
        reconcile_universe_membership(conn, isins[:3])
        assert _flag_map(conn)[isins[3]] == 0, "Last ISIN should be orphan after first call"

        # Second call: last ISIN is back in universe
        reconcile_universe_membership(conn, isins[:4])
        assert _flag_map(conn)[isins[3]] == 1, "Last ISIN should be in-universe after re-entry"

    def test_shrink_universe_marks_new_orphans(self):
        """Shrinking the universe mid-run correctly marks new orphans."""
        isins = [f"LU{i:012d}" for i in range(6)]
        conn = _make_db(isins)

        reconcile_universe_membership(conn, isins[:6])
        assert all(v == 1 for v in _flag_map(conn).values())

        # Universe shrinks to first 3
        in_u, orphans = reconcile_universe_membership(conn, isins[:3])
        flags = _flag_map(conn)
        assert all(flags[i] == 1 for i in isins[:3])
        assert all(flags[i] == 0 for i in isins[3:])
        assert in_u == 3 and orphans == 3


class TestReconcileChunking:
    """Exercises the chunked IN(...) path for > 900 ISINs."""

    def test_large_universe_all_flagged(self):
        """1500 ISINs (> 999 SQLite limit) — all correctly flagged in-universe."""
        n = 1500
        isins = [f"LU{i:012d}" for i in range(n)]
        conn = _make_db(isins)
        in_u, orphans = reconcile_universe_membership(conn, isins)
        assert in_u == n, f"Expected {n} in-universe, got {in_u}"
        assert orphans == 0, f"Expected 0 orphans, got {orphans}"

    def test_large_universe_partial(self):
        """1500 ISINs seeded; only 1200 in current universe."""
        n_seed, n_current = 1500, 1200
        isins = [f"LU{i:012d}" for i in range(n_seed)]
        current = isins[:n_current]
        conn = _make_db(isins)
        in_u, orphans = reconcile_universe_membership(conn, current)
        assert in_u == n_current
        assert orphans == n_seed - n_current


class TestReconcileGuards:
    """Safety guards: the flag must match the passed set exactly."""

    def test_flag_count_equals_passed_set_size(self):
        """Count of In_Current_Universe=1 must equal len(set(current_isins))."""
        isins = [f"LU{i:012d}" for i in range(10)]
        # pass 5, including duplicates — dedup must occur
        current_with_dups = isins[:5] + isins[:3]  # 8 items, 5 unique
        conn = _make_db(isins)
        in_u, _ = reconcile_universe_membership(conn, current_with_dups)
        assert in_u == 5, (
            f"Expected 5 (after dedup), got {in_u} — duplication inflated the count"
        )

    def test_isins_not_in_master_are_silently_ignored(self):
        """ISINs in current_isins but not in fund_master don't crash or create rows."""
        isins = [f"LU{i:012d}" for i in range(4)]
        conn = _make_db(isins)
        ghost = "LU9999999999"  # not in fund_master
        in_u, orphans = reconcile_universe_membership(conn, isins[:3] + [ghost])
        # ghost has no row → in_u counts only real fund_master hits
        assert in_u == 3
        assert orphans == 1
        # no phantom row was created
        n = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
        assert n == 4, f"No row should have been created for ghost ISIN; total={n}"
