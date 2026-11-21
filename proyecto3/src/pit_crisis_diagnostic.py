# proyecto3/src/pit_crisis_diagnostic.py
# -*- coding: utf-8 -*-
"""Pure pieces of the turning-point diagnostic (FND-0435, roadmap v4 of doc/backlog/PKG_L_p3_alignment.md): what the model did at the 2008 top and bottom, read from the SEALED saved PIT run.
READ-ONLY analysis. No database, no formula change: pandas / numpy only (R-7), driven by scripts/audit/p3/p3_crisis_diagnostic.py.

Rules built in (not left to the caller):
  R9 date lock -- the design uses 2008 only (+-1 month sensitivity), 2020 is DESCRIPTIVE; every other stress episode of `LOCKED_EPISODES` is refused until `PREREG_P3_5A.md` is committed
  (`assert_dates_allowed`), so a later out-of-sample judgement has not been read in advance.
  D0 -- every number taken from an earlier scratch session is reproduced here with its recovered definition before anything new is built on it (`ceiling_variants`, `eligibility_by_nature`).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

SUBPORTFOLIOS = ("Defensiva", "Equilibrada", "Dinamica")
PREREG_NAME = "PREREG_P3_5A.md"

# Design windows (R9). The design dates are month-ends; the sensitivity is one month either side.
DESIGN_TOP = ("2007-12-31", "2008-01-31", "2008-06-30")
DESIGN_BOTTOM = tuple(d.strftime("%Y-%m-%d") for d in pd.date_range("2008-10-31", "2009-06-30", freq="ME"))
DESCRIPTIVE_2020 = ("2020-02-29", "2020-03-31", "2020-04-30")
# Stress episodes that stay LOCKED until PREREG_P3_5A.md is in git HEAD: name -> (first day, last day).
LOCKED_EPISODES = {
    "2015-07/08": ("2015-07-01", "2015-08-31"),
    "2015-11..2016-01": ("2015-11-01", "2016-01-31"),
    "2018-11": ("2018-11-01", "2018-11-30"),
    "2022-01..2023-01": ("2022-01-01", "2023-01-31"),
    "2025-03": ("2025-03-01", "2025-03-31"),
    "2026-02": ("2026-02-01", "2026-02-28"),
}
CRASH_WINDOW = ("2007-11-01", "2009-03-31")      # the window removed / halved in the hindsight bounds (D3)


class LockedEpisode(Exception):
    """A requested date falls in a stress episode that is locked until the preregistration is committed."""


def locked_episode(date) -> "str | None":
    d = pd.Timestamp(date)
    for name, (lo, hi) in LOCKED_EPISODES.items():
        if pd.Timestamp(lo) <= d <= pd.Timestamp(hi):
            return name
    return None


def assert_dates_allowed(dates, prereg_committed: bool) -> None:
    """Refuse every date inside a locked episode while the preregistration is not committed; with it committed nothing is refused."""
    if prereg_committed:
        return
    bad = sorted({f"{pd.Timestamp(d).date()} ({locked_episode(d)})" for d in dates if locked_episode(d)})
    if bad:
        raise LockedEpisode(f"{PREREG_NAME} is not in git HEAD: locked stress episodes cannot be read: " + ", ".join(bad))


# Package L step C0 (plan rev. 3.1, issue I6): the lock above covers the DRAWDOWN windows, which is enough for a baseline read. A LEVER (the relative drawdown filter, the recovery
# modulation) acts in the months AFTER the trough, so any lever output over the recovery would read the confirmation evidence. A lever output is therefore locked for
# LEVER_RECOVERY_MONTHS after the end of each episode (a superset of trough + 12 months: the trough lies inside the episode, so no trough has to be located, which would itself be a read).
# Baseline reads keep `assert_dates_allowed`; the lever runner (step F) must call `assert_lever_dates_allowed`.
LEVER_RECOVERY_MONTHS = 12


def lever_locked_episodes(date) -> list:
    """Names of every locked episode whose window, extended by LEVER_RECOVERY_MONTHS after its last day (inclusive), contains `date` (the windows overlap: more than one can match)."""
    d = pd.Timestamp(date)
    return [name for name, (lo, hi) in LOCKED_EPISODES.items() if pd.Timestamp(lo) <= d <= pd.Timestamp(hi) + pd.DateOffset(months=LEVER_RECOVERY_MONTHS)]


def assert_lever_dates_allowed(dates, prereg_committed: bool) -> None:
    """Like `assert_dates_allowed` for the output of a LEVER: refuses every date inside a locked episode or its recovery (LEVER_RECOVERY_MONTHS after it) while the preregistration is not committed."""
    if prereg_committed:
        return
    bad = sorted({f"{pd.Timestamp(d).date()} ({', '.join(eps)})" for d in dates for eps in [lever_locked_episodes(d)] if eps})
    if bad:
        raise LockedEpisode(f"{PREREG_NAME} is not in git HEAD: a lever output cannot be read over locked stress episodes or their {LEVER_RECOVERY_MONTHS}-month recovery: " + ", ".join(bad))


def prereg_in_git_head(root) -> bool:
    """True when a file named PREREG_P3_5A.md exists in the tree of git HEAD (a working-tree copy that is not committed does not count)."""
    try:
        out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=60, check=True).stdout
    except Exception:                                                     # noqa: BLE001 -- unreadable git means locked, never open
        return False
    return any(Path(line).name == PREREG_NAME for line in out.splitlines())


# ---------------------------------------------------------------- D0: the hindsight bounds on the static mix ----------

def pool_returns(scores: pd.DataFrame, fwd_returns: pd.DataFrame, at, subs=SUBPORTFOLIOS) -> pd.DataFrame:
    """Per date and sub-portfolio, the equal-weight 1-month FORWARD return of the funds that were ELIGIBLE then (frictionless): the pool each sub-portfolio could draw from.
    `scores`: as_of, subportfolio, isin, eligible; `fwd_returns`: dates x isin."""
    el = scores[scores["eligible"]]
    out = {}
    for s in subs:
        vals = {}
        for t, g in el[el["subportfolio"] == s].groupby("as_of"):
            if t in fwd_returns.index:
                vals[t] = fwd_returns.loc[t].reindex(g["isin"].unique()).mean()
        out[s] = pd.Series(vals, dtype=float).reindex(pd.DatetimeIndex(at))
    return pd.DataFrame(out)


def ann_and_drawdown(r: pd.Series) -> tuple:
    """(annualised return, maximum drawdown) of a monthly return series, NaN months dropped."""
    r = r.dropna()
    cum = (1 + r).cumprod()
    return float(cum.iloc[-1] ** (12 / len(r)) - 1), float((cum / cum.cummax() - 1).min())


def best_static_mix(S: pd.DataFrame, dd_limit: float = -0.15, step: float = 0.1) -> dict:
    """The constant-weight mix of the three pools (grid of `step`) with the highest annual return whose maximum drawdown is not worse than `dd_limit`. HINDSIGHT bound, frictionless."""
    best = None
    grid = np.arange(0, 1 + 1e-9, step)
    for wd in grid:
        for we in grid[grid <= 1 - wd + 1e-9]:
            wn = 1 - wd - we
            r = S["Defensiva"] * wd + S["Equilibrada"] * we + S["Dinamica"] * wn
            a, dd = ann_and_drawdown(r)
            if dd >= dd_limit and (best is None or a > best["ann"]):
                best = {"ann": a, "max_dd": dd, "Defensiva": round(float(wd), 2) + 0.0, "Equilibrada": round(float(we), 2) + 0.0, "Dinamica": round(float(wn), 2) + 0.0}   # + 0.0: no negative zero
    return best or {"ann": float("nan"), "max_dd": float("nan"), "Defensiva": float("nan"), "Equilibrada": float("nan"), "Dinamica": float("nan")}


def _in_window(index, window) -> np.ndarray:
    idx = pd.DatetimeIndex(index)
    return np.asarray((idx >= pd.Timestamp(window[0])) & (idx <= pd.Timestamp(window[1])))


def remove_window(S: pd.DataFrame, window=CRASH_WINDOW) -> pd.DataFrame:
    """Hindsight: the months of the window are deleted (perfect avoidance)."""
    return S[~_in_window(S.index, window)]


def halve_negatives(S: pd.DataFrame, window=CRASH_WINDOW) -> pd.DataFrame:
    """Hindsight: inside the window every negative monthly return is halved."""
    out = S.copy()
    inside = _in_window(S.index, window)
    sub = S.loc[inside]
    out.loc[inside] = sub.where(sub > 0, sub * 0.5)
    return out


def ceiling_variants(S: pd.DataFrame, dd_limit: float = -0.15, window=CRASH_WINDOW) -> list:
    """D0 of the scratch session: the best static mix under the drawdown limit on (a) all months, (b) the crash window removed, (c) its losses halved."""
    rows = []
    for label, frame in (("all months", S), ("crash window removed (hindsight)", remove_window(S, window)), ("crash losses halved (hindsight)", halve_negatives(S, window))):
        rows.append({"variant": label, **best_static_mix(frame, dd_limit)})
    return rows


# ---------------------------------------------------------------- D0 / D2: eligibility by nature ----------------------

def first_reason(scores: pd.DataFrame) -> pd.Series:
    """The category of the FIRST hard filter that excluded a row (the leading word of `exclusion_reason`, e.g. max_drawdown, return_ann_real, srri, Credit_Quality, short_max_drawdown_6m, nav_stale, data_suspect), 'eligible' when none. The scorer reports only the first failing filter."""
    lead = scores["exclusion_reason"].astype("string").str.extract(r"^([A-Za-z_]+)", expand=False)
    return pd.Series(np.where(lead.notna(), lead.astype(object), "eligible"), index=scores.index)


def eligibility_by_nature(scores: pd.DataFrame, dates, sub: str, natures) -> pd.DataFrame:
    """Per date and nature of one sub-portfolio: funds scored, share eligible (the sealed `eligible` column itself, never inferred from the reason text: a first version inferred it and counted the
    capitalised `Credit_Quality=...` reasons as eligible), share whose FIRST failing filter is the since-inception `max_drawdown`, share excluded by any other first filter."""
    why = first_reason(scores)
    rows = []
    for t in dates:
        g = scores[(scores["as_of"] == pd.Timestamp(t)) & (scores["subportfolio"] == sub)]
        for nat in natures:
            x = g[g["fund_nature"] == nat]
            ok = x["eligible"].astype(bool)
            dd = (why.loc[x.index] == "max_drawdown") & ~ok
            n = len(x)
            rows.append({"date": pd.Timestamp(t), "nature": nat, "n": n,
                         "eligible": float(ok.mean()) if n else float("nan"),
                         "max_drawdown": float(dd.mean()) if n else float("nan"),
                         "other": float((~ok & ~dd).mean()) if n else float("nan")})
    return pd.DataFrame(rows)
