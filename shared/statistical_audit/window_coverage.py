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
TS_COLUMNS = ["isin", "window", "first_nav", "first_point", "reason"]


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


def timeseries_coverage_violations(first_nav: dict, stored_first: pd.DataFrame, windows: dict, tolerance_days: int) -> pd.DataFrame:
    """TIMESERIES_WINDOW_COVERAGE (FND-0443): the (isin, window) pairs of fund_metric_timeseries whose EARLIEST stored point is older than the fund's NAV history supports.

    The span condition of FND-0427 (first NAV <= point - months + tolerance) is strictest on the earliest point, so one (isin, window, first_point) row per pair decides it
    without reading the whole table; continuity inside the window is not judged here. `first_nav` = {isin: date of its first monthly NAV}; `stored_first` has columns
    isin, window, first_point; a window not in `windows` is ignored; a fund with no NAV is a violation."""
    out = []
    if stored_first is None or stored_first.empty:
        return pd.DataFrame(columns=TS_COLUMNS)
    for isin, window, first_point in stored_first[["isin", "window", "first_point"]].itertuples(index=False):
        months = windows.get(window)
        if months is None:
            continue
        point, first = pd.Timestamp(first_point), first_nav.get(isin)
        if first is None:
            out.append((isin, window, pd.NaT, point, "no NAV rows behind a stored point"))
        elif pd.Timestamp(first) > point - pd.DateOffset(months=int(months)) + pd.Timedelta(days=int(tolerance_days)):
            out.append((isin, window, pd.Timestamp(first), point, f"history from {pd.Timestamp(first).date()} does not reach back {months} months from {point.date()}"))
    return pd.DataFrame(out, columns=TS_COLUMNS)
