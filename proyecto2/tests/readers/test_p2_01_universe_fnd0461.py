# proyecto2/tests/readers/test_p2_01_universe_fnd0461.py
# -*- coding: utf-8 -*-
"""FND-0461: the P2-01 universe check warned 'ISINs without fund_master' on every run, but the difference was the STALE_FROZEN funds that
get_isins_with_nav excludes on purpose. The exclusions are now counted by cause; only a missing fund_master row (or counts that do not
reconcile) is a warning.

R-7: imports ONLY db_readers -- no pipeline.py, no core.io, no HTTP, no database (a fake connection answers the one query).
"""
import sys
from pathlib import Path

_P2 = Path(__file__).resolve().parent.parent.parent
if str(_P2) not in sys.path:
    sys.path.insert(0, str(_P2))

from src.readers.db_readers import nav_universe_exclusions, p2_01_check  # noqa: E402


class _Conn:
    def __init__(self, row):
        self.row, self.sql = row, None

    def execute(self, sql, *_a):
        self.sql = sql
        return self

    def fetchone(self):
        return self.row


def test_exclusions_are_read_by_cause():
    conn = _Conn((3753, 0, 7))
    assert nav_universe_exclusions(conn) == {"nav_all": 3753, "no_master": 0, "stale_frozen": 7}
    assert "STALE_FROZEN" in conn.sql and "LEFT JOIN fund_master" in conn.sql
    assert nav_universe_exclusions(_Conn((None, None, None))) == {"nav_all": 0, "no_master": 0, "stale_frozen": 0}


def test_stale_frozen_only_is_not_a_warning():
    # the live state of 2026-11-27: 3,753 ISINs with NAV, 7 STALE_FROZEN, none without fund_master
    warn, msg = p2_01_check({"nav_all": 3753, "no_master": 0, "stale_frozen": 7}, 3746)
    assert warn is False
    assert "OK" in msg and "7 STALE_FROZEN" in msg and "Ejecuta P1" not in msg


def test_missing_fund_master_is_a_warning_with_its_own_count():
    warn, msg = p2_01_check({"nav_all": 3755, "no_master": 2, "stale_frozen": 7}, 3746)
    assert warn is True
    assert msg.startswith("[P2-01] 2 ISINs") and "Ejecuta P1" in msg


def test_counts_that_do_not_reconcile_are_a_warning():
    warn, msg = p2_01_check({"nav_all": 3753, "no_master": 0, "stale_frozen": 7}, 3740)
    assert warn is True and "no cuadran" in msg
