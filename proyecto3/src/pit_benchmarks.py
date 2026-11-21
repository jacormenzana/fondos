# proyecto3/src/pit_benchmarks.py
# -*- coding: utf-8 -*-
"""P3-1.4 -- the decision benchmarks of checkpoint C1 (roadmap of 2026-11-18, doc/backlog/PKG_L_p3_alignment.md; cluster ticket FND-0430).

Why. FND-0429 read "the champion's net Sharpe is 0.189 below the pool" as a construction loss. The pool it compared with has no FX budget, no 10-fund cap and no costs
(pit_backtest.pool_benchmark), while the champion has both constraints and pays 25 bp per side. This module builds the references that separate the three effects:

  mandate-matched pool   every eligible fund, equal weight inside each sub-portfolio, the SAME non-EUR budget, costed through its own turnover (equal-weight monthly
                         rebalance plus the entries and exits of the eligible set); no hysteresis band, because a pool has no discrete holdings.
  R1 persistent random   the production construction (10 funds per sub-portfolio, family / manager / nature caps, FX budget) with a time-invariant random score per fund;
                         a holding is kept until it stops being eligible or a constraint removes it, and its weights are rebalanced monthly to equal weights inside the
                         regime sub-weights (the same mechanics as the pool and the champion).
  R2 monthly redraw      the same with a fresh random score every month: an UPPER BOUND on turnover, reported only as a bound.

  construction                  = R1 - mandate-matched pool        (what the construction rules cost with NO selection skill)
  selection + active rotation   = champion - R1                    (the skill of the score, and the cost of the rotation it causes)

Everything is gross AND net. Turnover is split into the part caused by the regime sub-weights jumping and the rest (holdings), for the pool, R1 and the champion, so the
effect of a transition cap is visible in each. PURE (pandas / numpy; the selection engine is the production one, called through pit_backtest.build_weights: no new engine).
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from proyecto3.src.backtest_inference import stationary_bootstrap_indices
from proyecto3.src.pit_backtest import SUB_NAMES, build_weights, monthly_turnover, portfolio_returns
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS

HOLD_BAND = 1.0e6     # incumbent bonus multiplier of R1: a held fund outranks every non-held candidate of a random score in (0, 1)
PERIODS = 12


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def _sub_weights(hist: pd.DataFrame, t) -> "dict | None":
    pos = hist.index.searchsorted(t, side="right") - 1
    if pos < 0:
        return None
    h = hist.iloc[pos]
    return dict(zip(SUB_NAMES, (float(h["weight_defensive"]), float(h["weight_balanced"]), float(h["weight_dynamic"]))))


def pool_weights(scores: pd.DataFrame, regime_hist: pd.DataFrame, at: pd.DatetimeIndex, fx_exposed: "pd.Series | None" = None,
                 exposed_budget: "float | None" = None) -> pd.DataFrame:
    """Master weights (dates x isin, NaN = not held) of the equal-weighted ELIGIBLE pool of every sub-portfolio, blended by the regime sub-weights.

    Without `exposed_budget` this is the pool that pit_backtest.pool_benchmark averages (no constraint), here as weights so that it can be costed. With it
    (the mandate-matched pool) the exposed funds of a sub-portfolio carry at most `exposed_budget` of its weight: the exposed weight is scaled down to the budget and
    the remainder is shared equally by the non-exposed funds; with no non-exposed fund the remainder stays unallocated (cash), as the construction does. A fund eligible in
    two sub-portfolios is held in both (a pool has no exclusion rule). Dates with no scores or no regime row give an empty portfolio."""
    hist = regime_hist.sort_index()
    by_date = dict(tuple(scores[scores["eligible"]].groupby("as_of"))) if len(scores) else {}
    rows = {}
    for t in at:
        sub_w = _sub_weights(hist, t)
        if t not in by_date or sub_w is None:
            rows[t] = {}
            continue
        held: dict = {}
        for sub, sw in sub_w.items():
            isins = by_date[t].loc[by_date[t]["subportfolio"] == sub, "isin"].unique()
            n = len(isins)
            if n == 0 or sw == 0:
                continue
            w = pd.Series(1.0 / n, index=isins)
            if exposed_budget is not None and fx_exposed is not None:
                exposed = fx_exposed.reindex(isins, fill_value=False).to_numpy(dtype=bool)
                if exposed.any() and exposed.mean() > exposed_budget + 1e-12:
                    w[exposed] = exposed_budget / exposed.sum()
                    w[~exposed] = (1.0 - exposed_budget) / (~exposed).sum() if (~exposed).any() else 0.0
            for isin, x in w.items():
                held[isin] = held.get(isin, 0.0) + sw * float(x)
        rows[t] = held
    return pd.DataFrame.from_dict(rows, orient="index").reindex(at)


def random_selection_weights(scores: pd.DataFrame, attrs: pd.DataFrame, regime_hist: pd.DataFrame, at: pd.DatetimeIndex, seed: int, persistent: bool,
                             constraints=DEFAULT_CONSTRAINTS, fx_exposed: "pd.Series | None" = None, band: "float | None" = None) -> "tuple[pd.DataFrame, pd.Series]":
    """Master weights and cash of the random-selection benchmark, built by the production engine (pit_backtest.build_weights) with `score_final` replaced.
    persistent=True (R1): ONE random score per fund for the whole history and the incumbent bonus at HOLD_BAND, so a holding changes only when it stops being
    eligible or a construction rule removes it. persistent=False (R2): a fresh random score per (date, fund, sub-portfolio) row, no incumbents.
    `band` (P3-7, PREREG_P3_7 section 4: the matched pool at the SAME band as its champion arm): the incumbent bonus of the persistent pool when it is not the shipped HOLD_BAND
    (None = HOLD_BAND, unchanged; 0 = a fixed random score with no bonus). Ignored by the redraw, which has no incumbents."""
    rng = np.random.default_rng(seed)
    tiny = 1e-9                                                    # a score of exactly 0 would fail the engine's score > 0 candidate rule
    if persistent:
        isins = scores["isin"].unique()
        fixed = pd.Series(rng.random(len(isins)) + tiny, index=isins)
        s = scores.assign(score_final=scores["isin"].map(fixed).to_numpy())
        return build_weights(s, attrs, regime_hist, at, dataclasses.replace(constraints, hysteresis_band=HOLD_BAND if band is None else float(band)), use_incumbents=True, fx_exposed=fx_exposed)
    s = scores.assign(score_final=rng.random(len(scores)) + tiny)
    return build_weights(s, attrs, regime_hist, at, constraints, use_incumbents=False, fx_exposed=fx_exposed)


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# turnover and series
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def turnover_parts(W: pd.DataFrame, regime_hist: pd.DataFrame, at: pd.DatetimeIndex) -> pd.DataFrame:
    """Monthly traded notional (sum |dw|, as pit_backtest.monthly_turnover) split in two: `regime` = sum over sub-portfolios of |change of the nominal regime
    sub-weight| (the part the regime label's jumps cause even with unchanged holdings) and `holdings` = the rest (selection, eligibility, rebalancing to equal weights).
    The split is exact in the total (`total` = regime + holdings) and the regime part never exceeds it; a month after an empty month enters from cash, as in the total.
    NaN where the portfolio is empty."""
    total = monthly_turnover(W)
    hist = regime_hist.sort_index()
    S = pd.DataFrame([(_sub_weights(hist, t) or {}) for t in at], index=at, columns=SUB_NAMES).astype(float)
    held = W.notna().any(axis=1)
    S = pd.DataFrame(np.where(held.to_numpy()[:, None], S.to_numpy(), 0.0), index=S.index, columns=S.columns)      # an empty month holds nothing
    prev = S.shift(1, fill_value=0.0)                                                                             # the first month, and any month after an empty one, enters from cash
    regime = (S - prev).abs().sum(axis=1).where(held)
    regime = np.minimum(regime, total)                              # nominal weights can exceed the weight really invested when a sub-portfolio holds cash
    return pd.DataFrame({"total": total, "regime": regime, "holdings": total - regime})


def benchmark_series(W: pd.DataFrame, R1: pd.DataFrame, cash_ret1: pd.Series, bps: float) -> pd.DataFrame:
    """Monthly gross return, turnover and net return (gross - bps/1e4 x turnover, the chained-series convention of pit_backtest.assemble_table) of a weight matrix."""
    gross, _ = portfolio_returns(W, R1, cash_ret1)
    turnover = monthly_turnover(W)
    return pd.DataFrame({"gross": gross, "turnover": turnover, "net": gross - bps / 1e4 * turnover})


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# mandate-level statistics (rule R6)
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def _annualised(monthly: pd.Series, window: int) -> pd.Series:
    lr = np.log1p(monthly.astype(float))
    return np.expm1(lr.rolling(window, min_periods=window).sum() * PERIODS / window)


def rolling_excess(monthly_return: pd.Series, monthly_target: pd.Series, window: int) -> pd.Series:
    """Annualised return of every `window`-month stretch minus the annualised target of the same stretch (full windows only). The windows overlap, so they are a
    descriptive profile of the mandate, not independent observations: no interval is attached to them."""
    d = pd.concat({"r": monthly_return, "t": monthly_target}, axis=1).dropna()
    return (_annualised(d["r"], window) - _annualised(d["t"], window)).dropna()


def window_summary(excess: pd.Series) -> dict:
    """n windows, median, 25th percentile, mean and share above zero of an excess series (R6: median and p25 first; the share above is binary, so it is secondary)."""
    if len(excess) == 0:
        return {"n": 0, "median": float("nan"), "p25": float("nan"), "mean": float("nan"), "share_above": float("nan")}
    return {"n": int(len(excess)), "median": float(excess.median()), "p25": float(excess.quantile(0.25)), "mean": float(excess.mean()),
            "share_above": float((excess > 0).mean())}


def _stat_rows(kind: str, r: np.ndarray, cash: np.ndarray, idx: "np.ndarray | None") -> np.ndarray:
    """The statistic of one monthly series on the original order (idx None -> one value) or on every bootstrap resample."""
    x = r[None, :] if idx is None else r[idx]
    if kind == "ann_return":
        return np.expm1(np.log1p(x).sum(axis=1) * PERIODS / x.shape[1])
    e = x - (cash[None, :] if idx is None else cash[idx])
    sd = e.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 1e-12, e.mean(axis=1) / sd * np.sqrt(PERIODS), np.nan)


def bootstrap_gap(a_series: "list[pd.Series]", b_series: pd.Series, kind: str, cash: "pd.Series | None" = None, n_boot: int = 2000, mean_block: float = 6.0,
                  ci: float = 0.90, seed: int = 12345) -> dict:
    """Gap = mean over the draws in `a_series` of stat(a_d) minus stat(b), with ONE paired stationary-bootstrap resampling of the months shared by every series, so the
    interval keeps the time dependence and the common shocks. kind: "ann_return" (geometric annualised return) or "sharpe" (excess over `cash`). Returns
    {n, n_draws, point, ci_low, ci_high, draw_sd (spread of the statistic across the draws, a Monte Carlo noise gauge), prob_positive}."""
    if kind not in ("ann_return", "sharpe"):
        raise ValueError(f"kind must be 'ann_return' or 'sharpe', not {kind!r}")
    cols = {**{f"a{i}": s for i, s in enumerate(a_series)}, "b": b_series}
    if cash is not None:
        cols["cash"] = cash                                         # months without a cash return are dropped with the others, never filled
    frame = pd.concat(cols, axis=1).dropna()
    n = len(frame)
    c = frame["cash"].to_numpy(dtype=float) if cash is not None else np.zeros(n)
    A = [frame[f"a{i}"].to_numpy(dtype=float) for i in range(len(a_series))]
    b = frame["b"].to_numpy(dtype=float)
    point_each = np.array([_stat_rows(kind, a, c, None)[0] for a in A])
    out = {"n": int(n), "n_draws": len(A), "point": float(point_each.mean() - _stat_rows(kind, b, c, None)[0]),
           "draw_sd": float(point_each.std(ddof=1)) if len(A) > 1 else 0.0}
    if n < 24:
        return {**out, "ci_low": float("nan"), "ci_high": float("nan"), "prob_positive": float("nan")}
    idx = stationary_bootstrap_indices(n, n_boot, mean_block, seed)
    boot = np.mean([_stat_rows(kind, a, c, idx) for a in A], axis=0) - _stat_rows(kind, b, c, idx)
    boot = boot[np.isfinite(boot)]
    lo, hi = np.quantile(boot, [(1 - ci) / 2, 1 - (1 - ci) / 2])
    return {**out, "ci_low": float(lo), "ci_high": float(hi), "prob_positive": float((boot > 0).mean())}
