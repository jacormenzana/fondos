# shared/nav_continuity.py
# -*- coding: utf-8 -*-
"""
NAV continuity -- the ONE definition of "this fund's monthly NAV history is too sparse to be scored" (P#11).

P2 annualises a monthly series by the first..last calendar month and weights a k-month return as k months (FND-0253), so
a sparse series is no longer MIS-computed, but a fund with large holes still carries metrics built from little data. The
build-time eligibility rule (FND-0251 item C, `FUND_DATA_ELIGIBILITY_ENABLED`) therefore excludes a fund when, over its
own first..last monthly NAV span:

    coverage = distinct months with a NAV / months in the span   <  P3_FUND_NAV_MIN_COVERAGE, or
    its longest run of consecutive missing months                >= P3_FUND_NAV_MAX_GAP_RUN_MONTHS.

Two callers, one rule and one reason text (the exclusion is audited from `fund_scores.exclusion_reason`):

  * live   `proyecto3/src/fund_scorer._nav_gap_reasons`: aggregates `fund_nav_monthly` in SQL and calls `gap_reason`;
  * PIT    `reasons_by_date`: evaluates the same rule at every evaluation date from the wide NAV panel, using only the
           rows observable at that date (FND-0254).

Months are `shared.annualization.month_ordinals` (year * 12 + month - 1): month-end alignment is implicit, so a NAV
dated on the last business day and one dated on the calendar month-end are the same month, exactly as in P2.
Pure (numpy / pandas only): no DB, no config read -- the thresholds are arguments, so the live and the PIT callers can
never read different values by accident.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.annualization import month_ordinals


def _ym(month_ordinal: int) -> str:
    year, month0 = divmod(int(month_ordinal), 12)
    return f"{year:04d}-{month0 + 1:02d}"


def gap_reason(n_rows, first_month, last_month, longest_gap, n_gaps, min_coverage: float, max_gap_run: int) -> "str | None":
    """The exclusion reason, or None when the series is continuous enough.

    n_rows       distinct calendar months with a NAV in [first_month, last_month]
    first_month  month ordinal of the first NAV;   last_month  month ordinal of the last NAV (as of the evaluation date)
    longest_gap  longest run of consecutive missing months inside the span;   n_gaps  number of gaps (runs) inside the span
    A series with no gap, or a span shorter than two months, is never flagged."""
    expected = int(last_month) - int(first_month) + 1
    if expected < 2 or int(n_gaps) == 0:
        return None
    coverage = int(n_rows) / expected
    if coverage < min_coverage or int(longest_gap) >= max_gap_run:
        return (f"nav_gap: coverage {coverage:.0%} ({int(n_rows)}/{expected} months, {_ym(first_month)}..{_ym(last_month)}), "
                f"longest gap {int(longest_gap)} months, {int(n_gaps)} gaps")
    return None


def reasons_by_date(nav: pd.DataFrame, at, min_coverage: float, max_gap_run: int) -> pd.DataFrame:
    """Point-in-time continuity: DataFrame indexed by `at`, columns = the funds of `nav` (raw-date DatetimeIndex x isin), object
    dtype -- the `gap_reason` text where the fund is too sparse AS OF that date, NaN otherwise.

    A fund's row at date t uses only its NAV observations dated <= t, so the verdict at t is what the live rule would have said
    on t (retired funds keep the verdict of their last observation, like any other fund-date). Two NAV rows in the same calendar
    month count once (the live SQL groups by month)."""
    at = pd.DatetimeIndex(at)
    out = pd.DataFrame(np.nan, index=at, columns=nav.columns, dtype=object)
    if nav.empty or len(at) == 0:
        return out
    at_ns = at.to_numpy(dtype="datetime64[ns]")
    for col_pos, isin in enumerate(nav.columns):
        s = nav[isin].dropna()
        if len(s) < 2:
            continue
        months = month_ordinals(s.index)
        first_of_month = np.r_[True, np.diff(months) != 0]            # index is sorted: keep the first row of each month
        dates = s.index.to_numpy(dtype="datetime64[ns]")[first_of_month]
        months = months[first_of_month]
        if len(months) < 2:
            continue
        gap_before = np.r_[0, np.diff(months) - 1]                    # missing months right before each row
        longest = np.maximum.accumulate(gap_before)
        n_gaps = np.cumsum(gap_before > 0)
        k = np.searchsorted(dates, at_ns, side="right") - 1           # last row observable at each evaluation date
        usable = k >= 1
        if not usable.any():
            continue
        kk = np.clip(k, 0, None)
        expected = months[kk] - months[0] + 1
        coverage = (kk + 1) / np.maximum(expected, 1)
        flagged = usable & (n_gaps[kk] > 0) & ((coverage < min_coverage) | (longest[kk] >= max_gap_run))
        for row_pos in np.flatnonzero(flagged):
            j = int(kk[row_pos])
            out.iat[row_pos, col_pos] = gap_reason(j + 1, months[0], months[j], longest[j], n_gaps[j], min_coverage, max_gap_run)
    return out
