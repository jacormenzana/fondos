# shared/statistical_audit/window_coverage.py
# -*- coding: utf-8 -*-
"""
ROLLING_COVERAGE (FND-0427): a stored `rolling_Ny` scalar must rest on a window the fund's NAV history really covers.

Why an audit rule when P2 already has the coverage rule: P2 and its own tests agree with themselves; the defect this guards (a `rolling_10y` row written for a 3-year-old fund,
equal to its since-inception value) was invisible to every other detector because the row is internally consistent. The audit judges the STORED rows against the NAV they came from,
with the same two conditions the writer uses, but through `shared.nav_continuity` and plain pandas (no import of P2 code), so a regression in the writer's gate shows here.

Two conditions per stored (isin, rolling window), the last NAV of the fund being the window's end:
  span        the first NAV is at most `end - months + tolerance_days`;
  continuity  the rows inside the window pass `shared.nav_continuity.reason_for_dates` (coverage and longest gap).

Pure (no database, no config read): the thresholds are arguments. The runner applies it only while `ROLLING_WINDOW_COVERAGE_ENABLED` is ON: with the switch OFF the stored rows legitimately
carry the old behaviour and a hard finding would block promotions for a state the owner has not yet recomputed.
"""
from __future__ import annotations

import pandas as pd

from shared.nav_continuity import reason_for_dates

COLUMNS = ["isin", "horizon", "first_nav", "last_nav", "reason"]


def rolling_coverage_violations(nav_dates: pd.DataFrame, stored: pd.DataFrame, windows: dict, tolerance_days: int, min_coverage: float, max_gap_run: int) -> pd.DataFrame:
    """The (isin, horizon) pairs of `stored` whose window the NAV does not cover. `nav_dates` has columns isin, date; `stored` has isin, horizon; `windows` maps a horizon label to its
    months (`shared.config.ROLLING_WINDOWS`); a horizon not in `windows` is ignored; an ISIN without NAV rows is a violation (a stored window that no NAV supports)."""
    out = []
    if stored is None or stored.empty:
        return pd.DataFrame(columns=COLUMNS)
    by_isin = {}
    if nav_dates is not None and not nav_dates.empty:
        nd = nav_dates.assign(date=pd.to_datetime(nav_dates["date"]))
        by_isin = {k: g["date"].sort_values().reset_index(drop=True) for k, g in nd.groupby("isin")}
    for isin, horizon in stored[["isin", "horizon"]].drop_duplicates().itertuples(index=False):
        months = windows.get(horizon)
        if months is None:
            continue
        dates = by_isin.get(isin)
        if dates is None or dates.empty:
            out.append((isin, horizon, pd.NaT, pd.NaT, "no NAV rows behind a stored window"))
            continue
        end, first = dates.iloc[-1], dates.iloc[0]
        if first > end - pd.DateOffset(months=int(months)) + pd.Timedelta(days=int(tolerance_days)):
            out.append((isin, horizon, first, end, f"history {first.date()}..{end.date()} does not reach back {months} months"))
            continue
        inside = dates[dates > end - pd.DateOffset(months=int(months))]
        reason = reason_for_dates(inside, min_coverage, max_gap_run)
        if reason:
            out.append((isin, horizon, first, end, reason))
    return pd.DataFrame(out, columns=COLUMNS)
