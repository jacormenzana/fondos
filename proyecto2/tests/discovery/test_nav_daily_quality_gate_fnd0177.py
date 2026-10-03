# -*- coding: utf-8 -*-
"""FND-0177: the daily-NAV quality gate (_filter_daily_anomalies) on the three real defect shapes. R-7: no pipeline/core.io."""
import sys
from datetime import date, timedelta
from pathlib import Path

_P2 = Path(__file__).resolve().parents[2]
for _p in (str(_P2), str(_P2.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto2.src.discovery.nav_discovery import _filter_daily_anomalies  # noqa: E402


def _rows(navs, start=date(2010, 1, 1)):
    return [{"Date": start + timedelta(days=i), "NAV": v} for i, v in enumerate(navs)]


def test_isolated_low_print_is_dropped():          # IE00B3L10570: 1.0 among ~100
    kept, bad = _filter_daily_anomalies(_rows([100, 100, 1.0, 100, 100.2]))
    assert [r["NAV"] for r in kept] == [100, 100, 100, 100.2] and len(bad) == 1 and "spike" in bad[0][1]


def test_isolated_high_print_is_dropped():         # LU0052474979: 9.16 among ~41 (dip) and inflated peaks
    kept, bad = _filter_daily_anomalies(_rows([41.2, 9.1562, 41.4, 41.3]))
    assert [r["NAV"] for r in kept] == [41.2, 41.4, 41.3] and len(bad) == 1
    kept, bad = _filter_daily_anomalies(_rows([41.2, 80.0, 41.4]))
    assert len(kept) == 2


def test_scale_seam_drops_the_pre_seam_segment():  # LU1291108998: 133,009 -> 99.75, then stable
    kept, bad = _filter_daily_anomalies(_rows([133009.12, 133009.12, 133009.12, 99.75, 99.75, 100.51]))
    assert [r["NAV"] for r in kept] == [99.75, 99.75, 100.51] and len(bad) == 3 and "seam" in bad[0][1]


def test_real_persistent_moves_are_untouched():
    for navs in ([100, 101, 60, 61, 62], [100, 101, 160, 161, 162], [100, 101, 102]):   # -40%, +60% that stay: not defects
        kept, bad = _filter_daily_anomalies(_rows(navs))
        assert len(kept) == len(navs) and not bad


def test_small_batches_pass_through():
    assert _filter_daily_anomalies(_rows([100]))[1] == [] and _filter_daily_anomalies([])[0] == []


def test_good_row_between_two_bad_prints_is_kept():   # IE00B3L10570 2010-11-24..26: 1.0, 100.0, 1.0
    kept, bad = _filter_daily_anomalies(_rows([100, 100, 1.0, 100, 1.0, 100, 100]))
    assert [r["NAV"] for r in kept] == [100, 100, 100, 100, 100] and len(bad) == 2
