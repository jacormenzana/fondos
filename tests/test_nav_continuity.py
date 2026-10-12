# tests/test_nav_continuity.py
# -*- coding: utf-8 -*-
"""
shared/nav_continuity.py -- the single NAV-continuity predicate shared by the live scorer (SQL path) and the PIT backtest (panel path),
FND-0251 item C / FND-0254. Pure: no DB, no config read (R-7).

    python -m pytest tests/test_nav_continuity.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.nav_continuity import gap_reason, reasons_by_date  # noqa: E402

MIN_COV, MAX_RUN = 0.80, 6


def _ordinal(year: int, month: int) -> int:
    return year * 12 + month - 1


# ---------------- gap_reason: the predicate and its text ----------------

def test_the_reason_text_is_the_documented_audit_format():
    # the exact example of fund_scorer.load_eligibility_inputs' docstring: the text is audited from fund_scores.exclusion_reason
    got = gap_reason(35, _ordinal(2018, 3), _ordinal(2026, 10), 5, 27, MIN_COV, MAX_RUN)
    assert got == "nav_gap: coverage 34% (35/104 months, 2018-03..2026-10), longest gap 5 months, 27 gaps"


@pytest.mark.parametrize("n_rows, first, last, longest, n_gaps, expect_flag", [
    (36, _ordinal(2023, 1), _ordinal(2025, 12), 0, 0, False),      # dense
    (37, _ordinal(2023, 1), _ordinal(2026, 4), 3, 1, False),       # 37/40 = 92%, hole of 3: tolerated
    (34, _ordinal(2023, 1), _ordinal(2026, 4), 6, 1, True),        # coverage 85% but a 6-month outage: flagged (>= MAX_RUN)
    (33, _ordinal(2023, 1), _ordinal(2026, 4), 5, 1, False),       # 33/40 = 82.5%, longest 5 < 6: tolerated
    (31, _ordinal(2023, 1), _ordinal(2026, 4), 2, 4, True),        # 31/40 = 77.5% < 80%: flagged although no long hole
    (32, _ordinal(2023, 1), _ordinal(2026, 4), 2, 4, False),       # 32/40 = 80.0% exactly: NOT below the threshold
])
def test_flag_boundaries(n_rows, first, last, longest, n_gaps, expect_flag):
    assert (gap_reason(n_rows, first, last, longest, n_gaps, MIN_COV, MAX_RUN) is not None) is expect_flag


def test_a_gapless_or_one_month_series_is_never_flagged_even_with_extreme_numbers():
    assert gap_reason(1, _ordinal(2024, 5), _ordinal(2024, 5), 0, 0, MIN_COV, MAX_RUN) is None     # span < 2 months
    assert gap_reason(10, _ordinal(2024, 1), _ordinal(2024, 10), 99, 0, MIN_COV, MAX_RUN) is None  # no gap at all


# ---------------- reasons_by_date: structure ----------------

def _panel(spec: dict, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """spec: isin -> iterable of month indexes (into `dates`) that HAVE a NAV."""
    cols = {}
    for isin, present in spec.items():
        s = pd.Series(np.nan, index=dates)
        s.iloc[sorted(present)] = 100.0
        cols[isin] = s
    return pd.DataFrame(cols)


DATES = pd.date_range("2018-01-31", periods=96, freq=pd.offsets.MonthEnd())


def test_dense_series_are_never_flagged_at_any_date():
    panel = _panel({"A": range(96)}, DATES)
    got = reasons_by_date(panel, DATES[[10, 50, 95]], MIN_COV, MAX_RUN)
    assert got.isna().all().all()


def test_the_verdict_changes_only_once_the_outage_is_observable():
    present = [i for i in range(96) if not 30 <= i < 38]            # an 8-month hole: months 30..37 missing
    panel = _panel({"A": present}, DATES)
    at = DATES[[29, 38, 60, 95]]                                    # before the hole, right after its end, later, last
    got = reasons_by_date(panel, at, MIN_COV, MAX_RUN)["A"]
    assert pd.isna(got.iloc[0])                                     # before the hole: dense so far
    assert got.iloc[1].startswith("nav_gap:") and "longest gap 8 months, 1 gaps" in got.iloc[1]
    assert "longest gap 8 months" in got.iloc[3]                    # the outage stays on record (live: permanent mark)


def test_a_fund_with_fewer_than_two_observations_or_all_nan_is_never_flagged():
    panel = _panel({"ONE": [40], "NONE": []}, DATES)
    got = reasons_by_date(panel, DATES[[50, 95]], MIN_COV, MAX_RUN)
    assert got.isna().all().all()


def test_two_rows_in_the_same_calendar_month_count_once():
    # business-day and calendar-month-end NAV of the SAME month must not inflate coverage (the live SQL groups by month)
    idx = list(pd.date_range("2020-01-31", periods=12, freq=pd.offsets.MonthEnd()))
    idx.insert(5, idx[5] - pd.Timedelta(days=2))                   # an extra row two days before the month-end
    panel = pd.DataFrame({"A": 100.0}, index=pd.DatetimeIndex(sorted(idx)))
    assert reasons_by_date(panel, pd.DatetimeIndex([idx[-1]]), MIN_COV, MAX_RUN).isna().all().all()


def test_output_is_indexed_by_the_evaluation_dates_and_the_funds_columns():
    panel = _panel({"A": range(96), "B": range(0, 96, 4)}, DATES)
    at = DATES[[20, 60]]
    got = reasons_by_date(panel, at, MIN_COV, MAX_RUN)
    assert list(got.index) == list(at) and list(got.columns) == ["A", "B"]
    assert pd.isna(got["A"]).all() and got["B"].notna().all()       # B: one row a quarter = 25-33% coverage


# ---------------- reasons_by_date vs a from-scratch oracle ----------------

def _oracle(panel: pd.DataFrame, t: pd.Timestamp, isin: str):
    """Independent, loop-based implementation of the documented rule on the rows observable at t."""
    s = panel[isin].dropna()
    s = s[s.index <= t]
    months = sorted({d.year * 12 + d.month - 1 for d in s.index})
    if len(months) < 2:
        return None
    first, last = months[0], months[-1]
    expected = last - first + 1
    runs = [b - a - 1 for a, b in zip(months, months[1:]) if b - a > 1]
    if not runs:
        return None
    coverage = len(months) / expected
    if coverage < MIN_COV or max(runs) >= MAX_RUN:
        ym = lambda m: f"{m // 12:04d}-{m % 12 + 1:02d}"                # text written out here, NOT via gap_reason: the oracle is independent
        return (f"nav_gap: coverage {coverage:.0%} ({len(months)}/{expected} months, {ym(first)}..{ym(last)}), "
                f"longest gap {max(runs)} months, {len(runs)} gaps")
    return None


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_matches_the_oracle_on_random_sparse_panels_at_every_date(seed):
    rng = np.random.default_rng(seed)
    spec = {}
    for k in range(14):
        density = rng.choice([1.0, 0.95, 0.85, 0.6, 0.35])
        keep = rng.random(96) < density
        if k % 4 == 0:                                              # an outage block on a mostly dense fund
            start = int(rng.integers(10, 70))
            keep[start:start + int(rng.integers(3, 12))] = False
        start_row = int(rng.integers(0, 30))
        keep[:start_row] = False
        spec[f"F{k}"] = np.flatnonzero(keep)
    panel = _panel(spec, DATES)
    at = DATES[::7]
    got = reasons_by_date(panel, at, MIN_COV, MAX_RUN)
    for t in at:
        for isin in panel.columns:
            want = _oracle(panel, t, isin)
            have = got.loc[t, isin]
            assert (want is None and pd.isna(have)) or want == have, (seed, str(t.date()), isin, want, have)


def test_adding_future_rows_never_changes_an_earlier_verdict():
    base = _panel({"A": [i for i in range(60) if not 20 <= i < 29]}, DATES)
    extended = _panel({"A": [i for i in range(96) if not 20 <= i < 29 and not 70 <= i < 80]}, DATES)
    at = DATES[[15, 25, 35, 55]]
    a = reasons_by_date(base, at, MIN_COV, MAX_RUN)
    b = reasons_by_date(extended, at, MIN_COV, MAX_RUN)
    pd.testing.assert_frame_equal(a, b)                              # look-ahead guard: only rows <= t decide the verdict at t


@pytest.mark.parametrize("hole, flagged", [(5, False), (6, True), (7, True)])
def test_the_panel_path_has_the_same_run_boundary_as_the_predicate(hole, flagged):
    # 90 observed months of a 96-month span keeps coverage >= 80% in every case, so ONLY the run length decides
    present = [i for i in range(96) if not 40 <= i < 40 + hole]
    got = reasons_by_date(_panel({"A": present}, DATES), DATES[[95]], MIN_COV, MAX_RUN)["A"].iloc[0]
    assert (isinstance(got, str)) is flagged
    if flagged:
        assert f"longest gap {hole} months, 1 gaps" in got
