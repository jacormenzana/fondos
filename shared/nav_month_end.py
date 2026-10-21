"""The one definition of the date a MONTHLY NAV observation stands for (FND-0300).

`fund_nav_monthly` holds one row per fund and calendar month (its semantic key is (ISIN, YYYY-MM): nav_discovery replaces the month's
row, 2026-09-13). The stored Date is the day the source priced it: 71% of the rows sit on the calendar month-end and 28% on the last
BUSINESS day (1-3 days earlier), and 99.6% of the funds mix both. Every consumer that compares raw dates therefore measured a month by
its stamp:
  * the macro OLS inner-joined raw NAV dates with a calendar month-end factor index: ~30% of the months silently dropped out;
  * persistence windows (P2 and the PIT) bound [start, start + 36 months] by raw dates, so a peer or the fund itself lost or gained an
    edge month depending on how that month was stamped, and the start drifted across month-ends (Jan 31 -> Jul 31 -> Jan 31, but
    Aug 31 -> Feb 28 -> Aug 28);
  * the PIT stale-NAV age and the trailing DateOffset cut-offs moved by up to 3 days with the stamp.
The FND-0193 alignment gate measured the effect on the model-value study: |dSharpe| 0.0126 between raw and month-end dates (tolerance 0.01).

ON (shared.config.NAV_MONTH_END_ALIGN_ENABLED): a monthly NAV dated within MONTH_CLOSE_TOLERANCE_DAYS of its calendar month-end IS that
month's close and is labelled with the month-end; a row further from it (the provisional OPEN month, e.g. 2026-10-18, or a fund's last,
mid-month NAV before liquidation) keeps its real date, so no value is ever stamped with a date it had not reached. The tolerance is the one
pit_backtest.last_complete_month_end already uses to decide that a month is complete. Applied by every reader that feeds a calculation
(P2 db_readers.load_nav, the PIT NAV panel, the audit's NAV rows), always AFTER the EUR view, which converts at the real pricing date.
Persistence windows additionally count whole calendar months (`persistence_windows`), so they are invariant to the stamp of a peer's
row too. OFF = the stored convention, bit-for-bit.

Do NOT flip the switch without the recompute of the families it touches (utils/family_versions.FLAG_FAMILIES): until then the stored
values follow the raw-date convention.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from shared.period_key import PARTIAL_TAIL_DAYS, to_period_key

# R-9: the month key and the open-month tolerance are defined once, in shared/period_key.py; this module is the NAV-specific,
# switch-gated layer on top of them (labels that keep an open month's real date, the long / wide panel alignment, persistence windows).
# A NAV this close to its calendar month-end closes the month (last business day, Easter / year-end holidays). The widest gap among the
# month-closing rows of fund_nav_monthly on 2026-10-19 was 5 days (18 rows at 4-5 days of 672,554; the next gaps, 6-14 days, are 38
# mid-month rows of funds that stopped pricing, and the 3,709 open-month rows).
MONTH_CLOSE_TOLERANCE_DAYS: int = PARTIAL_TAIL_DAYS


def month_end_align_enabled() -> bool:
    """Read at call time (not import time), like the other kill-switches, so tests and `--flag` can flip it."""
    from shared import config
    return bool(getattr(config, "NAV_MONTH_END_ALIGN_ENABLED", False))


def month_end_labels(dates, tolerance_days: int = MONTH_CLOSE_TOLERANCE_DAYS) -> pd.DatetimeIndex:
    """The month-end of each date that closes its month (within `tolerance_days` of it), the date itself otherwise."""
    d = pd.DatetimeIndex(pd.to_datetime(dates))
    me = to_period_key(d)
    closes = (me - d).days <= tolerance_days
    return pd.DatetimeIndex(np.where(closes, me.values, d.values))


def align_nav_long(df: pd.DataFrame, date_col: str = "date", key: "str | None" = None, force: bool = False) -> pd.DataFrame:
    """Long NAV rows (one fund, or several with `key` = the fund column) with each date replaced by its month-end label.

    Identity (the same object) while the switch is off, unless `force`. Two rows that end on one label (cannot happen in fund_nav_monthly,
    whose key is one row per fund and month; kept for safety) keep the later real date. Order is by (key, label)."""
    if not (force or month_end_align_enabled()) or df.empty:
        return df
    out = df.copy()
    real = pd.to_datetime(out[date_col])
    out[date_col] = month_end_labels(real)
    out["_real"] = real.values
    by = ([key] if key else []) + [date_col]
    out = out.sort_values(by + ["_real"]).drop_duplicates(by, keep="last").drop(columns="_real")
    return out.reset_index(drop=True)


def align_nav_wide(nav: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """Wide NAV panel (dates x funds) on month-end labels: the rows that close one month collapse into one row (each fund keeps its own,
    later value; groupby.last skips NaN). Identity while the switch is off, unless `force`."""
    if not (force or month_end_align_enabled()) or nav.empty:
        return nav
    out = nav.groupby(month_end_labels(nav.index)).last()
    out.index.name = nav.index.name
    return out


def persistence_windows(first, last, window_months: int, step_months: int, aligned: "bool | None" = None):
    """The rolling windows of alpha_persistence for a fund observed from `first` to `last` (P2 persistence.compute_persistence and the PIT
    pit_peer_metrics.alpha_persistence both iterate this; one definition, P#11).

    Yields (lo, hi): an observation belongs to the window when lo <= date <= hi.
      * OFF (the stored convention): start at `first`, hi = start + DateOffset(window), step DateOffset(step) cumulatively; raw dates.
      * ON: whole calendar months. The first window starts at the month of `first`, hi = start month-end + MonthEnd(window), the
        start steps MonthEnd(step); lo = the first day of the start month. A row of a month belongs to it whatever day it is stamped
        (labels or raw dates give the same membership), and the start never drifts across month-ends.
    A window is produced while hi <= last (`last` = the fund's last observation, as returned by its reader)."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    if aligned is None:
        aligned = month_end_align_enabled()
    if not aligned:
        ws = first
        while True:
            we = ws + pd.DateOffset(months=window_months)
            if we > last:
                return
            yield ws, we
            ws = ws + pd.DateOffset(months=step_months)
    ws = first + pd.offsets.MonthEnd(0)
    while True:
        we = ws + pd.offsets.MonthEnd(window_months)
        if we > last:
            return
        yield ws.replace(day=1).normalize(), we
        ws = ws + pd.offsets.MonthEnd(step_months)
