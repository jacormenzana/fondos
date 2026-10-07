"""The one definition of "which CPI month does a NAV date belong to" (FND-0241).

The ES CPI is stamped on the CALENDAR month-end (db_readers.load_ipc), while a monthly NAV is dated on the last BUSINESS day of its
month (2016-01-29: 01-31 was a Sunday). deflate_nav looks the CPI up with merge_asof(backward), so those NAVs (29% of
fund_nav_monthly) took the CPI of the PREVIOUS month: that month's real return equalled its nominal one and the next month absorbed two
months of inflation. Moving a monthly NAV to its own month-end before the lookup gives every month its own CPI.

Behind shared.config.DEFLATION_MONTH_ALIGN_ENABLED (default False: the stored convention, bit-for-bit). Everything that deflates a
MONTHLY series (consistency, risk_metrics, rolling_stats via deflate_nav) and everything that audits those stored values against the
data (timeseries.nav_with_ipc) calls this, so the producer and its audit cannot disagree. A DAILY series (short_horizon) must not use
it: every day of a month would take that month's CPI. Do NOT flip the switch without the recompute of the families it touches
(utils.family_versions.FLAG_FAMILIES: risk, rolling): until then the stored values keep the old lookup and the audit's identities
report the gap.
"""
from __future__ import annotations

import pandas as pd


def month_align_enabled() -> bool:
    """Read at call time (not import time), like the other kill-switches, so tests and the switch can flip it."""
    from shared import config
    return bool(getattr(config, "DEFLATION_MONTH_ALIGN_ENABLED", False))


def cpi_lookup_dates(dates, align: "bool | None" = None) -> pd.Series:
    """The dates to look the CPI up with: each NAV date moved to the calendar month-end of its own month when `align`
    (None reads the switch), unchanged otherwise. Returns a Series positionally aligned to `dates`."""
    s = pd.Series(pd.to_datetime(pd.Series(dates).reset_index(drop=True)))
    if align is None:
        align = month_align_enabled()
    return (s.dt.normalize() + pd.offsets.MonthEnd(0)) if align else s
