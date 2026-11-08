# proyecto2/src/utils/peer_returns.py
# -*- coding: utf-8 -*-
"""One-month return panel of the funds of a Fund_Nature, the reference a NAV jump is judged against (FND-0392, WS0.5 of FND-0391).

A fund whose NAV moves +93 % in one calendar month is a level shift when its peers did not move, and a market event when they did. This module builds, from the monthly NAV rows of the
universe, the matrix peers_at() answers from: one-month simple returns per (calendar month, fund), grouped by Fund_Nature.

Rules (each one is a measured source of false alarms elsewhere in this repository):
  * the month is the canonical period key (shared.period_key.to_period_key, R-9), never the raw stamp: 28.7 % of the rows sit on the last BUSINESS day;
  * a return exists only between two CONSECUTIVE calendar months. A step over a gap spans k months and is not comparable with a one-month peer move (FND-0253);
  * peers_at() leaves the asked fund out of its own panel, so a corrupt value never moves the median it is judged against;
  * pure: no DB, no config. build_peer_returns() takes a long frame, so the live reader (readers/db_readers.load_peer_returns) and the offline fixtures of the fault-injection harness feed the same code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from shared.period_key import period_ordinals, to_period_key


def one_month_returns(nav_df: pd.DataFrame) -> pd.Series:
    """One-month simple returns of a monthly NAV frame (columns date, nav), indexed by the canonical month label of the NEWER observation. NaN where the previous
    observation is not the previous calendar month or its NAV is not positive. Two rows in the same month keep the last one (the semantic key is (ISIN, YYYY-MM))."""
    if nav_df is None or len(nav_df) < 2:
        return pd.Series(dtype=float)
    keys = pd.DatetimeIndex(to_period_key(nav_df["date"]))
    s = pd.Series(nav_df["nav"].to_numpy(dtype=float), index=keys)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    ordinals = np.asarray(period_ordinals(s.index))                   # P#11: the one month-ordinal definition
    prev = s.shift(1)
    consecutive = np.concatenate(([False], np.diff(ordinals) == 1))
    ok = consecutive & (prev.to_numpy() > 0) & (s.to_numpy() > 0)
    ret = (s / prev - 1.0).where(ok)
    return ret


@dataclass
class PeerReturns:
    """by_nature: {Fund_Nature: DataFrame(index = month label, columns = isin, values = one-month return or NaN)}; nature_of: {isin: Fund_Nature}."""
    by_nature: dict = field(default_factory=dict)
    nature_of: dict = field(default_factory=dict)

    def peers_at(self, isin: str, month) -> np.ndarray:
        """The one-month returns of the OTHER funds of `isin`'s Fund_Nature in `month` (non-NaN only). Empty when the fund has no nature or the month is not in the panel."""
        nature = self.nature_of.get(isin)
        panel = self.by_nature.get(nature) if nature is not None else None
        if panel is None:
            return np.empty(0)
        key = pd.Timestamp(to_period_key([month])[0])
        if key not in panel.index:
            return np.empty(0)
        row = panel.loc[key]
        if isin in row.index:
            row = row.drop(labels=isin)
        return row.dropna().to_numpy(dtype=float)

    def n_funds(self, nature=None) -> int:
        if nature is not None:
            panel = self.by_nature.get(nature)
            return 0 if panel is None else int(panel.shape[1])
        return len(self.nature_of)


def build_peer_returns(long_nav: pd.DataFrame) -> PeerReturns:
    """long_nav: columns isin, nature, date, nav (monthly rows; any order). Returns the PeerReturns panel. A fund with no nature is left out (it cannot be a peer of anything)."""
    if long_nav is None or long_nav.empty:
        return PeerReturns()
    frames: dict = {}
    nature_of: dict = {}
    for (isin, nature), g in long_nav.dropna(subset=["nature"]).groupby(["isin", "nature"], sort=False):
        ret = one_month_returns(g[["date", "nav"]].sort_values("date"))
        if ret.empty:
            continue
        nature_of[isin] = nature
        frames.setdefault(nature, {})[isin] = ret
    by_nature = {}
    for nature, cols in frames.items():
        panel = pd.DataFrame(cols).sort_index()
        panel.index = pd.DatetimeIndex(panel.index)
        by_nature[nature] = panel
    return PeerReturns(by_nature=by_nature, nature_of=nature_of)
