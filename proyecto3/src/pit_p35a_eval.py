# proyecto3/src/pit_p35a_eval.py
# -*- coding: utf-8 -*-
"""Pure evaluation pieces of PREREG_P3_5A step F (doc/decisiones/PREREG_P3_5A.md, the adoption rule of section 2, the ceiling protocol of section 3 and the operational definitions of its Amendment 1):
how a variant's monthly series is judged. pandas / numpy only, no database, no pipeline import (R-7); the runner is scripts/audit/p3/p3_p35a_trials.py.

Conventions. A monthly series is indexed by FORMATION date: the value at t is the return of the month after t (as the PIT backtest and the pool drawdown of C1b). A drawdown of a window starts from the
opening capital (level 1 is the first peak); the ceiling's full-path drawdown keeps the convention of `pit_crisis_diagnostic.ann_and_drawdown`, so it reproduces the 4.14 % of D0.
"""
from __future__ import annotations

from statistics import NormalDist

import numpy as np
import pandas as pd

from proyecto3.src.backtest_inference import stationary_bootstrap_indices
from proyecto3.src.pit_p35a_prep import paired_se_annual_pp
from proyecto3.src.pit_selection_test import realised_quantile_path

PERIODS = 12


# ---- basic statistics -----------------------------------------------------------------------------------------------------------------------------------------------------------
def ann_return(r) -> float:
    """Geometric annualised return of a monthly series (NaN if any month is missing: never filled)."""
    x = np.asarray(r, dtype=float)
    return float(np.expm1(np.log1p(x).mean() * PERIODS)) if len(x) and np.isfinite(x).all() else float("nan")


def max_dd(r) -> float:
    """Maximum drawdown (negative) of a monthly return series, the opening capital being the first peak; NaN if any month is missing."""
    x = np.asarray(r, dtype=float)
    if not len(x) or not np.isfinite(x).all():
        return float("nan")
    level = np.concatenate(([1.0], np.cumprod(1.0 + x)))
    return float((level / np.maximum.accumulate(level) - 1.0).min())


def episode_formation_range(lo, hi, recovery_months: int = 12) -> tuple:
    """(first, last) formation date of the calendar months of the window [lo, hi] extended by `recovery_months` after it: the return of a month is in the series at the previous month-end."""
    first = pd.Timestamp(lo) + pd.offsets.MonthEnd(0) + pd.offsets.MonthEnd(-1)
    last = pd.Timestamp(hi) + pd.offsets.MonthEnd(0) + pd.offsets.MonthEnd(recovery_months - 1)
    return first, last


def window_dd(series: pd.Series, first, last) -> float:
    """Maximum drawdown of the series restricted to the formation dates [first, last]."""
    return max_dd(series.loc[first:last].to_numpy())


def crisis_mask(index: pd.DatetimeIndex, windows) -> np.ndarray:
    """True for each formation date inside any window (lo, hi, recovery_months) (the months of the episodes and of the recovery after them)."""
    idx = pd.DatetimeIndex(index)
    out = np.zeros(len(idx), dtype=bool)
    for lo, hi, rec in windows:
        first, last = episode_formation_range(lo, hi, rec)
        out |= (idx >= first) & (idx <= last)
    return out


# ---- stressed windows -------------------------------------------------------------------------------------------------------------------------------------------------------------
def stress_paths(R1: pd.DataFrame, at, nature_of: pd.Series, pool_by_date: dict, W: pd.DataFrame, q: float = 0.10, months: int = 12) -> np.ndarray:
    """For each formation date t with `months` returns after it, the month-by-month return of the STRESS leg of the portfolio held at t: for every nature the portfolio holds, the REALISED path of the
    fund at the q-quantile of the 12-month return among that nature's pool funds with a complete path (`realised_quantile_path`, never a pointwise percentile), weighted by the nature's share of the
    portfolio. NaN where no full window or no nature has a path. `R1`: formation dates x ISIN 1-month forward returns (index = `at`); `pool_by_date`: {t: set of ISINs}; `W`: weights (NaN = not held)."""
    at = pd.DatetimeIndex(at)
    out = np.full((len(at), months), np.nan)
    nat = pd.Series(nature_of)
    in_nature = {n: set(nat.index[nat == n]) & set(R1.columns) for n in nat.unique()}          # computed once, not per date
    for i in range(len(at) - months + 1):
        t = at[i]
        w = W.loc[t].dropna() if t in W.index else pd.Series(dtype=float)
        w = w[w > 0]
        if w.empty:
            continue
        share = w.groupby(nat.reindex(w.index).to_numpy()).sum()
        win = R1.iloc[i:i + months]
        acc, used = np.zeros(months), 0.0
        for n, s in share.items():
            members = sorted(in_nature.get(n, set()) & set(pool_by_date.get(t, ())))
            if not members:
                continue
            try:
                path = realised_quantile_path(win[members], q)
            except ValueError:                                       # no member has a complete path
                continue
            acc += float(s) * path.to_numpy(dtype=float)
            used += float(s)
        if used > 0:
            out[i] = acc / used
    return out


def stressed_window_dds(port, stress: np.ndarray, share: float, months: int = 12) -> np.ndarray:
    """Per formation date, the maximum drawdown of the window path `(1 - share) * port + share * stress` (the whole portfolio's trajectory with a fraction `share` of the positions replaced by the
    stress leg, the opening capital the first peak); NaN where the window is incomplete. Equal, window by window, to `pit_selection_test.tail_stress_max_dd`."""
    p = np.asarray(port, dtype=float)
    n = len(p)
    out = np.full(n, np.nan)
    if n < months:
        return out
    wins = np.lib.stride_tricks.sliding_window_view(p, months)                  # (n - months + 1, months)
    mixed = (1.0 - share) * wins + share * np.asarray(stress, dtype=float)[:len(wins)]
    ok = np.isfinite(mixed).all(axis=1)
    level = np.concatenate([np.ones((len(wins), 1)), np.cumprod(1.0 + np.where(ok[:, None], mixed, 0.0), axis=1)], axis=1)
    dd = (level / np.maximum.accumulate(level, axis=1) - 1.0).min(axis=1)
    out[:len(wins)] = np.where(ok, dd, np.nan)
    return out


# ---- the ceiling ----------------------------------------------------------------------------------------------------------------------------------------------------------------------
def mixes(step: float = 0.1) -> np.ndarray:
    """The constant mixes of three sub-portfolios on a grid, in the enumeration order of `pit_crisis_diagnostic.best_static_mix`."""
    grid = np.arange(0, 1 + 1e-9, step)
    return np.array([(wd, we, 1.0 - wd - we) for wd in grid for we in grid[grid <= 1 - wd + 1e-9]])


def _paths(S: np.ndarray, M: np.ndarray) -> tuple:
    """(annual return, full-path max drawdown) of every mix, the drawdown with the diagnostic's convention (no opening peak)."""
    r = S @ M.T                                                              # (T, n_mix)
    cum = np.cumprod(1.0 + r, axis=0)
    return cum[-1] ** (PERIODS / len(r)) - 1.0, (cum / np.maximum.accumulate(cum, axis=0) - 1.0).min(axis=0)


def ceiling(S: np.ndarray, stress: "np.ndarray | None", share: float, dd_cap: float = -0.15, step: float = 0.1, months: int = 12) -> dict:
    """The CEILING KPI of the risk track (PREREG_P3_5A section 3): the highest annual return of a constant mix of the three sub-portfolios (`S`: T x 3 monthly net returns, columns Defensiva,
    Equilibrada, Dinamica) whose full-path drawdown is at least `dd_cap` AND, when `stress` (T x months x 3: the stress leg of each sub-portfolio) is given, whose worst stressed 12-month window under
    `share` is also at least `dd_cap`. NaN annual return when no mix qualifies. Never re-optimised per episode."""
    M = mixes(step)
    ann, dd = _paths(S, M)
    order = np.argsort(-ann, kind="stable")                                  # the earliest of equal returns first, as the loop of best_static_mix
    for j in order:
        if not np.isfinite(ann[j]) or dd[j] < dd_cap:
            continue
        worst = float("nan")
        if stress is not None:
            r = S @ M[j]
            d = stressed_window_dds(r, np.einsum("tmk,k->tm", stress, M[j]), share, months)
            worst = float(np.nanmin(d)) if np.isfinite(d).any() else float("nan")
            if not np.isfinite(worst) or worst < dd_cap:
                continue
        return {"ann": float(ann[j]), "mix": tuple(float(x) for x in M[j]), "dd_unstressed": float(dd[j]), "dd_stressed": worst}
    return {"ann": float("nan"), "mix": (float("nan"),) * 3, "dd_unstressed": float("nan"), "dd_stressed": float("nan")}


def ceiling_at_mix(S: np.ndarray, stress: "np.ndarray | None", share: float, mix, months: int = 12) -> dict:
    """The robustness line of PREREG_P3_5A section 3: a variant evaluated at the BASELINE's mix (no extra degree of freedom)."""
    m = np.asarray(mix, dtype=float)
    r = S @ m
    out = {"ann": ann_return(r), "dd_unstressed": max_dd(r), "dd_stressed": float("nan")}
    if stress is not None:
        d = stressed_window_dds(r, np.einsum("tmk,k->tm", stress, m), share, months)
        out["dd_stressed"] = float(np.nanmin(d)) if np.isfinite(d).any() else float("nan")
    return out


def ceiling_diff_se_pp(S_a: np.ndarray, S_b: np.ndarray, n_boot: int = 300, mean_block: float = 6.0, seed: int = 12345, dd_cap: float = -0.15, step: float = 0.1) -> float:
    """Standard error (pp a year) of ceiling(a) - ceiling(b) by ONE paired stationary bootstrap of the months shared by both. Only the UNSTRESSED constraint enters the resampled ceilings (the stressed
    windows are contiguous 12-month stretches that a month bootstrap breaks): an approximation, stated in the Amendment. Resamples where either ceiling is undefined are dropped."""
    M = mixes(step)
    n = len(S_a)
    idx = stationary_bootstrap_indices(n, n_boot, mean_block, seed)
    diffs = []
    for b in range(n_boot):
        ceil = []
        for S in (S_a, S_b):
            ann, dd = _paths(S[idx[b]], M)
            ok = dd >= dd_cap
            ceil.append(float(ann[ok].max()) if ok.any() else float("nan"))
        diffs.append(ceil[0] - ceil[1])
    d = np.asarray(diffs, dtype=float)
    d = d[np.isfinite(d)]
    return float(d.std(ddof=1) * 100) if len(d) > 1 else float("nan")


# ---- non-inferiority ------------------------------------------------------------------------------------------------------------------------------------------------------------
def noninferiority(diff, level: float, margin_pp: float = -0.10, n_boot: int = 4000, mean_block: float = 6.0, seed: int = 12345) -> dict:
    """The historical test of condition 2 (PREREG_P3_5A section 2): the mean monthly paired difference variant - baseline, annualised as 12 x the mean (pp), its stationary-bootstrap standard error and
    the one-sided lower bound at `level` (Holm over the 11 trials: 0.0045). `pass` = the lower bound is above `margin_pp` (non-inferiority; adoption is left to the forward shadow)."""
    a = np.asarray(diff, dtype=float)
    a = a[np.isfinite(a)]
    se = paired_se_annual_pp(a, n_boot=n_boot, mean_block=mean_block, seed=seed)
    mean_pp = float(a.mean() * PERIODS * 100)
    lb = mean_pp - NormalDist().inv_cdf(1 - level) * se
    return {"mean_pp": mean_pp, "se_pp": float(se), "lb_pp": float(lb), "pass": bool(lb > margin_pp), "n": int(len(a))}
