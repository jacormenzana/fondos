# proyecto2/tests/discovery/test_nav_discovery_freeze_20260915.py
# -*- coding: utf-8 -*-
"""
Tests for the FIX-FROZEN-NAV dead-code gap (2026-09-15).

Bug (see memory project_p2_audit_frozen_nav_deadcode_20260914 /
project_p0_p2_canonicalization_20260914): the STALE_FROZEN auto-freeze
detection (commit 36fa49f, 2026-08-22) lived entirely inside run_update(),
which the canonical launcher (P2_discoverLoadMetrics.bat) never calls —
only --mode discover and --mode load run in production. Live-verified
2026-09-14 and again 2026-09-15: 0/3,726 nav_sources rows ever flagged
STALE_FROZEN despite 7 known Russia/Emerging-Europe funds meeting the
freeze condition every cycle since 2026-08-22.

Fix: extracted the detection into _auto_freeze_stale_navs(conn, dry_run),
called from run_update() (unchanged behavior) AND from run_load() (the
mode that actually runs every cycle), plus an explicit STALE_FROZEN skip
branch added to run_load()'s two loops (workers>1 and sequential) — it
previously only special-cased RECALCULATE_MONTHLY and FORCE_REFRESH,
so even a correctly-flagged STALE_FROZEN fund would still have gone
through the normal download path.

These tests cover the pure, DB-only detection function directly — no
network mocking needed. The run_load()/run_update() skip-branch wiring
is exercised indirectly (self-evident from the diff) but not through a
full run_load() integration test, since that requires mocking the
Morningstar bearer-token/network layer that this module has no existing
test harness for.

R-7 compliant: no pipeline.py / core.io imports.
"""

import sqlite3
import sys
from datetime import date, timedelta
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

from proyecto2.src.discovery.nav_discovery import _auto_freeze_stale_navs, _FROZEN_NAV_DAYS


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _insert_source(
    conn,
    isin: str,
    last_nav_date: str,
    last_checked: str,
    data_status: str = "OK",
    status: str = "OK",
):
    conn.execute(
        "INSERT INTO nav_sources (isin, source, source_id, last_nav_date, "
        "last_checked, status, data_status) VALUES (?, 'MORNINGSTAR', ?, ?, ?, ?, ?)",
        (isin, f"ms_{isin}", last_nav_date, last_checked, status, data_status),
    )
    conn.commit()


class TestAutoFreezeStaleNavs:
    def test_freezes_a_fund_with_old_nav_and_recent_check(self):
        conn = _memory_conn()
        old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
        recent_check = (date.today() - timedelta(days=5)).isoformat()
        _insert_source(conn, "LU_FROZEN01", old_nav, recent_check)

        frozen = _auto_freeze_stale_navs(conn, dry_run=False)

        assert [r[0] for r in frozen] == ["LU_FROZEN01"]
        row = conn.execute(
            "SELECT data_status FROM nav_sources WHERE isin='LU_FROZEN01'"
        ).fetchone()
        assert row[0] == "STALE_FROZEN"

    def test_does_not_freeze_recently_updated_fund(self):
        conn = _memory_conn()
        recent_nav = (date.today() - timedelta(days=10)).isoformat()
        recent_check = (date.today() - timedelta(days=5)).isoformat()
        _insert_source(conn, "LU_ACTIVE01", recent_nav, recent_check)

        frozen = _auto_freeze_stale_navs(conn, dry_run=False)

        assert frozen == []
        row = conn.execute(
            "SELECT data_status FROM nav_sources WHERE isin='LU_ACTIVE01'"
        ).fetchone()
        assert row[0] == "OK"

    def test_does_not_freeze_when_last_checked_is_stale_too(self):
        """Old NAV + old last_checked means OUR pipeline stopped looking, not
        that the provider stopped publishing — a different problem, must not
        be silently reclassified as STALE_FROZEN."""
        conn = _memory_conn()
        old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
        old_check = (date.today() - timedelta(days=90)).isoformat()
        _insert_source(conn, "LU_UNCHECKED01", old_nav, old_check)

        frozen = _auto_freeze_stale_navs(conn, dry_run=False)

        assert frozen == []
        row = conn.execute(
            "SELECT data_status FROM nav_sources WHERE isin='LU_UNCHECKED01'"
        ).fetchone()
        assert row[0] == "OK"

    def test_idempotent_already_frozen_fund_not_reprocessed(self):
        conn = _memory_conn()
        old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
        recent_check = (date.today() - timedelta(days=5)).isoformat()
        _insert_source(conn, "LU_ALREADYFROZEN", old_nav, recent_check,
                        data_status="STALE_FROZEN")

        frozen = _auto_freeze_stale_navs(conn, dry_run=False)

        assert frozen == []  # not re-detected/re-logged — already flagged

    def test_dry_run_never_writes(self):
        conn = _memory_conn()
        old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
        recent_check = (date.today() - timedelta(days=5)).isoformat()
        _insert_source(conn, "LU_DRYRUN01", old_nav, recent_check)

        frozen = _auto_freeze_stale_navs(conn, dry_run=True)

        assert frozen == []
        row = conn.execute(
            "SELECT data_status FROM nav_sources WHERE isin='LU_DRYRUN01'"
        ).fetchone()
        assert row[0] == "OK"

    def test_multiple_qualifying_funds_all_frozen_in_one_call(self):
        conn = _memory_conn()
        old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
        recent_check = (date.today() - timedelta(days=5)).isoformat()
        for isin in ("LU_A", "LU_B", "LU_C"):
            _insert_source(conn, isin, old_nav, recent_check)

        frozen = _auto_freeze_stale_navs(conn, dry_run=False)

        assert sorted(r[0] for r in frozen) == ["LU_A", "LU_B", "LU_C"]
        rows = conn.execute(
            "SELECT isin FROM nav_sources WHERE data_status='STALE_FROZEN'"
        ).fetchall()
        assert sorted(r[0] for r in rows) == ["LU_A", "LU_B", "LU_C"]

    def test_ignores_sources_with_status_not_ok(self):
        """status != 'OK' (e.g. NOT_FOUND/ERROR) is a different lifecycle
        state entirely — must not be swept into STALE_FROZEN."""
        conn = _memory_conn()
        old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
        recent_check = (date.today() - timedelta(days=5)).isoformat()
        _insert_source(conn, "LU_NOTFOUND", old_nav, recent_check, status="NOT_FOUND")

        frozen = _auto_freeze_stale_navs(conn, dry_run=False)

        assert frozen == []
