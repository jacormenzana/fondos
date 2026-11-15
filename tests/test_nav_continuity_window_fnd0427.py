# tests/test_nav_continuity_window_fnd0427.py
# -*- coding: utf-8 -*-
"""FND-0427: `shared.nav_continuity.reason_for_months / reason_for_dates`, the third caller of the one NAV continuity rule (P2 rolling windows).

Pure and hermetic (R-7): numpy / pandas only. The thresholds are the ones P3 already uses (min coverage 0.80, max gap run 6 months)."""
import numpy as np
import pandas as pd

from shared.annualization import month_ordinals
from shared.nav_continuity import gap_reason, reason_for_dates, reason_for_months

MIN_COV, MAX_GAP = 0.80, 6


def _months(first="2020-01-31", periods=60, drop=()):
    idx = pd.date_range(first, periods=periods, freq="ME")
    return idx[~idx.isin(pd.to_datetime(list(drop)))] if len(drop) else idx


def test_a_dense_series_is_never_flagged():
    assert reason_for_dates(_months(), MIN_COV, MAX_GAP) is None


def test_fewer_than_two_months_is_never_flagged():
    assert reason_for_months([month_ordinals(_months(periods=1))[0]], MIN_COV, MAX_GAP) is None
    assert reason_for_months([], MIN_COV, MAX_GAP) is None


def test_one_missing_month_is_tolerated():
    assert reason_for_dates(_months(drop=["2022-06-30"]), MIN_COV, MAX_GAP) is None


def test_a_long_run_of_missing_months_is_flagged_with_the_shared_reason_text():
    holes = pd.date_range("2022-03-31", periods=7, freq="ME")
    reason = reason_for_dates(_months(drop=list(holes)), MIN_COV, MAX_GAP)
    assert reason is not None and reason.startswith("nav_gap:") and "longest gap 7" in reason


def test_many_scattered_holes_fail_on_coverage_alone():
    scattered = pd.to_datetime(["2020-03-31", "2020-06-30", "2020-09-30", "2020-12-31", "2021-03-31", "2021-06-30",
                                "2021-09-30", "2021-12-31", "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31"])
    reason = reason_for_dates(_months(periods=36, drop=list(scattered)), MIN_COV, MAX_GAP)      # span 2020-01..2022-11: 24 of 35 months present, 69%
    assert reason is not None and "coverage 69%" in reason and "longest gap 1" in reason


def test_two_rows_in_one_calendar_month_count_once_and_order_is_irrelevant():
    base = _months(periods=24)
    doubled = base.append(base[5:6] - pd.Timedelta(days=3)).sort_values()[::-1]               # a repeated month, unsorted
    assert reason_for_dates(doubled, MIN_COV, MAX_GAP) is None


def test_it_agrees_with_gap_reason_on_the_same_statistics():
    months = month_ordinals(_months(drop=pd.date_range("2022-03-31", periods=7, freq="ME")))
    direct = gap_reason(len(months), months[0], months[-1], 7, 1, MIN_COV, MAX_GAP)
    assert reason_for_months(months, MIN_COV, MAX_GAP) == direct
