"""Generic corruption classes for monthly NAV series (FND-0392, WS0.5 of FND-0391; plan amendments A3 G-05 and A5).

Why: "0 misses among the defects we already found" says nothing about what the system misses. The only independent ground truth is to CORRUPT a real, healthy series in a known way and ask the
validators (and later the suspicion predicate and the escalation engine) whether they notice. Each class below is a generic transformation of a monthly NAV frame (columns date, nav), never tied to a
fund (P#5). Pure: no DB, no config; deterministic (no random numbers); `inject` returns a copy and never touches its input.

`expected` names the detectors that SHOULD catch a class today, among DETECTORS of scripts/audit/nav/fault_injection_run.py. An empty tuple is not a bug of the class: it marks a gap that a later layer
(WS2 predicate, WS3 engine) must close, and the harness reports it as such instead of hiding it.

  scale_up_x100        the series jumps x100 from month k (a share-class re-denomination read as a price)
  unit_shift_div100    the series drops to 1/100 from month k (cents vs units)
  frozen_then_catchup  12 identical months from k, then +70 % at once, after which the true path continues (stale carry-forward + catch-up; the FND-0383 pattern)
  duplicated_month     the row of month k appears twice (a load that is not idempotent)
  wrong_currency_step  the level is multiplied by 1.12 from month k (a NAV read in another currency for the rest of the series)
  level_shift_50/100/150  the level is multiplied by 1.5 / 2.0 / 2.5 in ONE month and never reverts (DE000DWS3LN1: +92.9 %); below the 8x test, not a freeze
  smooth_nav           from month k the monthly returns are squeezed to 10 % of their deviation from the mean (an implausibly smooth NAV: volatility collapses, Sharpe and Sortino explode)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

FREEZE_MONTHS = 12
CATCHUP = 0.70
SMOOTH_KEEP = 0.10
MIN_TAIL = FREEZE_MONTHS + 2         # a corruption needs this many months after its start to be observable


def _copy(nav_df: pd.DataFrame) -> pd.DataFrame:
    return nav_df[["date", "nav"]].reset_index(drop=True).copy()


def scale_up_x100(nav_df, k):
    out = _copy(nav_df); out.loc[k:, "nav"] *= 100.0; return out


def unit_shift_div100(nav_df, k):
    out = _copy(nav_df); out.loc[k:, "nav"] /= 100.0; return out


def frozen_then_catchup(nav_df, k):
    out = _copy(nav_df)
    n, end = len(out), k + FREEZE_MONTHS
    if k < 1 or end >= n:
        raise ValueError("frozen_then_catchup needs k >= 1 and k + 12 < len(nav_df)")
    base = float(out.loc[k - 1, "nav"])
    true_end = float(out.loc[end, "nav"])
    tail = out.loc[end:, "nav"].to_numpy(dtype=float) / true_end * base * (1.0 + CATCHUP)       # the true path continues from a level 70 % above the frozen one
    out.loc[k:end - 1, "nav"] = base
    out.loc[end:, "nav"] = tail
    return out


def duplicated_month(nav_df, k):
    out = _copy(nav_df)
    return pd.concat([out.iloc[:k + 1], out.iloc[[k]], out.iloc[k + 1:]], ignore_index=True)


def wrong_currency_step(nav_df, k):
    out = _copy(nav_df); out.loc[k:, "nav"] *= 1.12; return out


def _level_shift(pct: float) -> Callable:
    def apply(nav_df, k):
        out = _copy(nav_df); out.loc[k:, "nav"] *= (1.0 + pct); return out
    apply.__name__ = f"level_shift_{int(pct * 100)}"
    return apply


def smooth_nav(nav_df, k):
    out = _copy(nav_df)
    vals = out["nav"].to_numpy(dtype=float)
    rets = vals[k:] / vals[k - 1:-1] - 1.0 if k >= 1 else vals[1:] / vals[:-1] - 1.0
    if len(rets) < 3:
        raise ValueError("smooth_nav needs at least 3 returns after k")
    squeezed = rets.mean() + SMOOTH_KEEP * (rets - rets.mean())
    new = vals.copy()
    start = max(k, 1)
    for i, r in enumerate(squeezed):
        new[start + i] = new[start + i - 1] * (1.0 + r)
    out["nav"] = new
    return out


@dataclass(frozen=True)
class Corruption:
    name: str
    description: str
    apply: Callable
    expected: tuple            # detectors that should catch it today (names in fault_injection_run.DETECTORS); () = a gap for a later layer
    min_tail: int = MIN_TAIL   # months needed from k on


CORRUPTIONS: dict = {c.name: c for c in (
    Corruption("scale_up_x100", "series x100 from month k", scale_up_x100, ("validate_nav",)),
    Corruption("unit_shift_div100", "series /100 from month k", unit_shift_div100, ("validate_nav",)),
    Corruption("frozen_then_catchup", "12 identical months, then +70 % at once", frozen_then_catchup, ("validate_nav_stale_flat",), FREEZE_MONTHS + 2),
    Corruption("duplicated_month", "one month's row twice", duplicated_month, ()),
    Corruption("wrong_currency_step", "level x1.12 from month k", wrong_currency_step, ()),
    Corruption("level_shift_50", "+50 % in one month, never reverts", _level_shift(0.5), ("validate_nav_unshared_jump",)),
    Corruption("level_shift_100", "+100 % in one month, never reverts", _level_shift(1.0), ("validate_nav_unshared_jump",)),
    Corruption("level_shift_150", "+150 % in one month, never reverts", _level_shift(1.5), ("validate_nav_unshared_jump",)),
    Corruption("smooth_nav", "returns squeezed to 10 % of their deviation (vol collapse)", smooth_nav, (), 14),
)}


def inject(nav_df: pd.DataFrame, name: str, k: int) -> pd.DataFrame:
    """A corrupted COPY of the monthly NAV frame: class `name` applied from row `k` (the corrupted month). Raises ValueError for an unknown class or when the series is too short for it."""
    if name not in CORRUPTIONS:
        raise ValueError(f"unknown corruption {name!r}; one of {sorted(CORRUPTIONS)}")
    c = CORRUPTIONS[name]
    if k < 1 or k + c.min_tail > len(nav_df):
        raise ValueError(f"{name}: k={k} leaves fewer than {c.min_tail} months in a series of {len(nav_df)}")
    return c.apply(nav_df, k)


def choose_month(nav_df: pd.DataFrame, name: str, ok_month: Callable[[int], bool], lo: float = 0.5) -> "int | None":
    """The first row index k (searching from `lo` of the series) that leaves the class its tail and for which `ok_month(k)` holds (e.g. enough peers reported that month); None when there is none.
    Deterministic: the same series gives the same month on every machine."""
    tail = CORRUPTIONS[name].min_tail
    for k in range(max(1, int(len(nav_df) * lo)), len(nav_df) - tail + 1):
        if ok_month(k):
            return k
    return None
