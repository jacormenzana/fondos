# shared/report_reconciliation.py
# -*- coding: utf-8 -*-
"""
FND-0428: independent reconciliation of the headline returns a report shows against the NAV they come from.

Why a second implementation: the original defect (a return column that meant something other than its name) was found by a person comparing with an
external source; the P2 audit compares P2 with itself (scalar vs timeseries), so a shared mis-binding is invisible to it. This module recomputes
three headline figures from the SAME source series (the EUR-view monthly NAV that P2 read) over each metric's declared window with plain pandas,
sharing no code with `proyecto2/src/calculations/returns.py`. It is a DELIBERATE exception to the one-implementation rule (P#11): limited to
three nominal headline returns, covered by a test that pins it to P2's own functions on synthetic series.

Like with like: a mismatch counts only on IDENTICAL definitions (the same rows, the same window, the same annualization convention). A definitional gap
(a "1Y" window of 11.3 months, daily vs monthly NAV) is shown on the label, never failed here. Pure (numpy / pandas); the conventions are arguments.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TOLERANCE_BP_DEFAULT = 5.0
HEADLINE = ("ytd", "ret_1y", "ret_si_nominal")


@dataclass(frozen=True)
class Mismatch:
    isin: str
    metric: str
    stored: float
    recomputed: "float | None"
    diff_bp: "float | None"          # None = the stored value cannot be reproduced at all

    def text(self) -> str:
        if self.recomputed is None:
            return f"{self.isin} {self.metric}: stored {self.stored:.6f} cannot be reproduced from the NAV"
        return f"{self.isin} {self.metric}: stored {self.stored:.6f} vs NAV {self.recomputed:.6f} ({self.diff_bp:.1f} bp)"


def _ordinal(ts: pd.Timestamp) -> int:
    return ts.year * 12 + ts.month - 1


def _annualized(win: pd.DataFrame, by_dates: bool, interval: bool) -> "float | None":
    """Geometric annualized return of a window of NAV rows, with the P2 conventions: horizon by the first..last calendar month when `by_dates`
    (months in the span + 1 points) else by rows; `interval` = N points span N - 1 periods."""
    if len(win) < 2:
        return None
    first, last = win["nav"].iloc[0], win["nav"].iloc[-1]
    if not (np.isfinite(first) and np.isfinite(last)) or first <= 0:
        return None
    n_points = (_ordinal(win["date"].iloc[-1]) - _ordinal(win["date"].iloc[0]) + 1) if by_dates else len(win)
    years = ((n_points - 1) if interval else n_points) / 12.0
    if years <= 0:
        return None
    return float((last / first) ** (1.0 / years) - 1.0)


def _ytd(df: pd.DataFrame, max_staleness_bdays: int) -> "float | None":
    end = df["date"].iloc[-1]
    dec31 = pd.Timestamp(year=end.year - 1, month=12, day=31)
    prior = df[df["date"] <= dec31]
    if prior.empty:
        base = df.iloc[0]                                               # launched this year: since the first NAV
    else:
        base = prior.iloc[-1]
        if int(np.busday_count(base["date"].date(), dec31.date())) > max_staleness_bdays:
            return None
    if base["date"] >= end or not np.isfinite(base["nav"]) or base["nav"] <= 0:
        return None
    return float(df["nav"].iloc[-1] / base["nav"] - 1.0)


def recompute_headline(nav_df: pd.DataFrame, *, by_dates: bool = True, interval: bool = True, ytd_staleness_bdays: int = 5) -> dict:
    """{'ytd', 'ret_1y', 'ret_si_nominal'} as fractions (None where the NAV does not allow it). `nav_df` has columns date, nav (any order)."""
    out = {k: None for k in HEADLINE}
    if nav_df is None or nav_df.empty:
        return out
    df = nav_df.assign(date=pd.to_datetime(nav_df["date"])).sort_values("date").reset_index(drop=True)
    end = df["date"].iloc[-1]
    out["ytd"] = _ytd(df, ytd_staleness_bdays)
    out["ret_1y"] = _annualized(df[df["date"] > end - pd.DateOffset(months=12)].reset_index(drop=True), by_dates, interval)
    out["ret_si_nominal"] = _annualized(df, by_dates, interval)
    return out


def reconcile(isin: str, stored: dict, nav_df: pd.DataFrame, tolerance_bp: float = TOLERANCE_BP_DEFAULT, **conventions) -> list:
    """The mismatches (list of `Mismatch`) between the stored headline values of one fund and the NAV. A stored value of None makes no claim and is
    skipped; a stored value the NAV cannot reproduce is a mismatch with `recomputed=None`."""
    fresh = recompute_headline(nav_df, **conventions)
    bad = []
    for key in HEADLINE:
        s = stored.get(key)
        if s is None or (isinstance(s, float) and not np.isfinite(s)):
            continue
        r = fresh[key]
        if r is None:
            bad.append(Mismatch(isin, key, float(s), None, None))
            continue
        diff = abs(float(s) - r) * 1e4
        if diff > tolerance_bp:
            bad.append(Mismatch(isin, key, float(s), r, diff))
    return bad


def excess_is_like_for_like(shown_excess_pct, ret_1y_nominal_pct, objective_pct, tolerance_pp: float = 0.011) -> bool:
    """The displayed excess must equal the NOMINAL 1-year return minus the NOMINAL objective (both in %, the report rounds to 2 decimals). It fails when a
    real return was subtracted from a nominal target, or the other way round."""
    if shown_excess_pct is None or ret_1y_nominal_pct is None or objective_pct is None:
        return shown_excess_pct is None
    return abs(float(shown_excess_pct) - (float(ret_1y_nominal_pct) - float(objective_pct))) <= tolerance_pp
