# proyecto1/tests/test_universe_nav_quarantine_20261104.py
# -*- coding: utf-8 -*-
"""
Owner directive 2026-11-04 (FND-0233 / FND-0383): a fund whose NAV source is dead leaves the universe BY RULE, in P1, every pass (reconcile_universe_membership):

  * data_status STALE_FROZEN (the Russia / Eastern Europe cohort, frozen since 2022-02-28), or
  * last NAV older than UNIVERSE_NAV_QUARANTINE_DAYS while the source was checked within UNIVERSE_NAV_QUARANTINE_RECENT_CHECK_DAYS (the provider publishes nothing).

No ISIN is named anywhere (P#5); the flag is reversible by itself (a resumed NAV re-opens the fund on the next pass); the kill switch UNIVERSE_NAV_QUARANTINE_ENABLED restores the old behaviour.
R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import datetime
import os
import sys

import pytest

_CORE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from fund_writer import classify_nav_quarantine, reconcile_universe_membership, universe_nav_exclusions

TODAY = datetime.date(2026, 11, 4)
DAYS, RECENT = 180, 30


def _rule(status, last_nav, checked):
    return classify_nav_quarantine(status, last_nav, checked, TODAY, DAYS, RECENT)


def test_a_frozen_source_is_quarantined_whatever_its_dates():
    assert _rule("STALE_FROZEN", datetime.date(2022, 2, 28), datetime.date(2026, 10, 18)) == "STALE_FROZEN"
    assert _rule("STALE_FROZEN", None, None) == "STALE_FROZEN"


def test_a_stale_nav_that_was_checked_recently_is_quarantined_and_the_reason_carries_the_lag():
    assert _rule("OK", datetime.date(2025, 12, 31), datetime.date(2026, 11, 4)) == "NAV_STALE_308d"
    assert _rule("OK", datetime.date(2026, 3, 12), datetime.date(2026, 10, 25)) == "NAV_STALE_237d"


def test_the_edges_are_exclusive_for_the_lag_and_inclusive_for_the_recent_check():
    assert _rule("OK", TODAY - datetime.timedelta(days=DAYS), TODAY) is None             # exactly 180 d: stays
    assert _rule("OK", TODAY - datetime.timedelta(days=DAYS + 1), TODAY) is not None     # 181 d: goes
    assert _rule("OK", TODAY - datetime.timedelta(days=300), TODAY - datetime.timedelta(days=RECENT)) is not None
    assert _rule("OK", TODAY - datetime.timedelta(days=300), TODAY - datetime.timedelta(days=RECENT + 1)) is None   # nobody looked: OUR gap, not the fund's


def test_a_fresh_fund_or_one_without_dates_stays_and_iso_strings_are_accepted():
    assert _rule("OK", TODAY - datetime.timedelta(days=40), TODAY) is None
    assert _rule("OK", None, TODAY) is None and _rule("OK", TODAY, None) is None
    assert _rule("OK", "2025-12-31", "2026-11-04") == "NAV_STALE_308d"


@pytest.fixture()
def universe(pg_conn):
    """Toy fund_master as a TEMP table (shadows the real one) and rows in the real control.nav_sources; everything is rolled back with the test savepoint."""
    pg_conn.execute("CREATE TEMP TABLE fund_master (ISIN TEXT PRIMARY KEY, In_Current_Universe INTEGER NOT NULL DEFAULT 1)")
    today = datetime.date.today()
    rows = {                                    # isin: (status, data_status, days since last NAV, days since last check)
        "AA0000000001": ("OK", "STALE_FROZEN", 1700, 20),
        "AA0000000002": ("OK", "OK", 237, 1),
        "AA0000000003": ("OK", "OK", 40, 1),
        "AA0000000004": ("OK", "OK", 2, 1),
        "AA0000000005": ("OK", "OK", 300, 90),      # nobody checked it: not the provider's silence
        "AA0000000006": ("NOT_FOUND", "OK", 400, 1),  # no source at all: a different problem (FND-0295)
    }
    for isin, (st, ds, lag, chk) in rows.items():
        pg_conn.execute("INSERT INTO fund_master (ISIN) VALUES (%s)", (isin,))
        pg_conn.execute("INSERT INTO control.nav_sources (isin, status, data_status, last_nav_date, last_checked) VALUES (%s,%s,%s,%s,%s)",
                        (isin, st, ds, today - datetime.timedelta(days=lag), today - datetime.timedelta(days=chk)))
    return pg_conn, sorted(rows)


def _flags(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT ISIN, In_Current_Universe FROM fund_master").fetchall()}


def test_reconcile_quarantines_dead_nav_funds_and_returns_the_counts(universe, capsys):
    conn, isins = universe
    in_u, orphans = reconcile_universe_membership(conn, isins)
    f = _flags(conn)
    assert (f["AA0000000001"], f["AA0000000002"]) == (0, 0)
    assert all(f[i] == 1 for i in ("AA0000000003", "AA0000000004", "AA0000000005", "AA0000000006"))
    assert (in_u, orphans) == (4, 2)
    out = capsys.readouterr().out
    assert "[UNIVERSE-QUARANTINE] AA0000000001 STALE_FROZEN" in out and "[UNIVERSE-QUARANTINE] AA0000000002 NAV_STALE_237d" in out
    assert "2 en cuarentena por NAV" in out


def test_a_fund_outside_the_current_universe_is_not_touched_by_the_quarantine(universe):
    conn, isins = universe
    in_u, orphans = reconcile_universe_membership(conn, [i for i in isins if i not in ("AA0000000002",)])
    assert _flags(conn)["AA0000000002"] == 0 and (in_u, orphans) == (4, 2)


def test_the_quarantine_is_idempotent_and_reversible_when_the_nav_resumes(universe):
    conn, isins = universe
    first = reconcile_universe_membership(conn, isins)
    assert reconcile_universe_membership(conn, isins) == first
    conn.execute("UPDATE control.nav_sources SET last_nav_date = CURRENT_DATE WHERE isin = 'AA0000000002'")
    reconcile_universe_membership(conn, isins)
    assert _flags(conn)["AA0000000002"] == 1 and _flags(conn)["AA0000000001"] == 0


def test_the_kill_switch_restores_the_previous_behaviour(universe, monkeypatch):
    conn, isins = universe
    from shared import config
    monkeypatch.setattr(config, "UNIVERSE_NAV_QUARANTINE_ENABLED", False)
    assert universe_nav_exclusions(conn) == {}
    in_u, orphans = reconcile_universe_membership(conn, isins)
    assert (in_u, orphans) == (6, 0)
