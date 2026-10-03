# proyecto3/src/pit_short_horizon.py
# -*- coding: utf-8 -*-
"""
Point-in-time short-horizon gate metrics for the P3 backtester -- FND-0159, Wave B step d1c.

The scorer's always-on short gates (fund_scorer.check_hard_filters) read three P2 metrics computed on the
LAST N DAILY NAV observations of each fund (run_pipeline: nav_daily.tail(N)):
    short_max_drawdown   rolling_6m  (tail 126, min 90 obs)   gate: < -8% / -15% / -25% by sub-portfolio
    short_liquidity_flag rolling_6m  (same window)             gate bypass when > 0.20 (daily data untrusted)
    short_vol_adj        rolling_3m  (tail 63,  min 45 obs)    gate: > 15% / 22% (Dinamica: none)
This module evaluates them at each requested date t from the daily NAV observed up to t only. Definitions
are P2's short_horizon.* (pinned by proyecto3/tests/test_pit_short_horizon.py against
compute_short_horizon_metrics + validators.validate_nav on the same windows).

FAIL-OPEN, made explicit (the gate itself skips a NaN metric, so a fund without a usable value passes):
a fund-date has NaN when (a) the fund has fewer than the minimum daily observations at t, (b) the window
fails P2's validate_nav (scale glitch >8x, non-positive NAV) -- P2 clears those rows too, (c) its newest
daily observation is older than `max_stale_days` (when set). `short_gate_metrics` returns a per-date
coverage table and logs a summary, so a deep-history backtest states how much of it ran with the short
gates effectively off.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import SHORT_WINDOW_MIN_OBS, SHORT_WINDOWS

logger = logging.getLogger(__name__)

WINDOW_6M, WINDOW_3M = SHORT_WINDOWS["rolling_6m"], SHORT_WINDOWS["rolling_3m"]
MIN_OBS_6M, MIN_OBS_3M = SHORT_WINDOW_MIN_OBS["rolling_6m"], SHORT_WINDOW_MIN_OBS["rolling_3m"]
SHORT_GATE_METRICS = ("short_max_drawdown_6m", "short_liquidity_flag_6m", "short_vol_adj_3m")
_MAX_STEP = 8.0                                 # validators.validate_nav: no adjacent jump > 8x (or < 1/8)


def _window_validity(x: np.ndarray):
    """Prefix sums that answer 'does x[a:k] pass validate_nav?' in O(1): no non-positive NAV and no adjacent
    ratio > 8 or < 0.125 among the steps INSIDE the window (nulls/ordering hold by construction)."""
    n = len(x)
    nonpos = np.concatenate([[0], np.cumsum(x <= 0)])
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = x[1:] / x[:-1]
    bad_step = np.zeros(n)
    bad_step[1:] = (ratio > _MAX_STEP) | (ratio < 1.0 / _MAX_STEP) | ~np.isfinite(ratio)
    bad = np.concatenate([[0], np.cumsum(bad_step)])
    return nonpos, bad


def _valid(nonpos, bad, a: int, k: int) -> bool:
    return (nonpos[k] - nonpos[a]) == 0 and (bad[k] - bad[a + 1]) == 0


def _vol_adj(rets: np.ndarray) -> float:
    """short_horizon._ac1_adjusted_vol on an array of simple returns."""
    if len(rets) < 10:
        return np.nan
    sigma = float(np.std(rets, ddof=1))
    if np.isnan(sigma) or sigma == 0:
        return np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        rho1 = float(np.corrcoef(rets[1:], rets[:-1])[0, 1])        # Series.autocorr(lag=1)
    factor = 1.0 if (np.isnan(rho1) or rho1 <= 0) else (1.0 + 2.0 * rho1) ** 0.5
    return round(sigma * factor * (252 ** 0.5), 6)


def short_gate_metrics(
    daily: pd.DataFrame,
    at: pd.DatetimeIndex,
    max_stale_days: "int | None" = None,
) -> "tuple[dict, pd.DataFrame]":
    """Short-gate metrics as of every date in `at`.

    daily: long frame [isin, date, nav] of daily NAV (any order). `at`: evaluation dates (e.g. month-ends).
    max_stale_days: ignore a fund whose newest daily NAV <= t is older than this (None = P2: tail(N) of
    whatever exists, however old).
    Returns ({metric: DataFrame(at x isin)}, coverage) where coverage has, per date: funds_with_daily
    (>= 1 observation <= t), evaluable_6m, evaluable_3m, invalid (failed validate_nav), stale and
    evaluable_share_6m. Funds/dates not evaluable are NaN = the scorer's gates skip them (fail-open)."""
    at = pd.DatetimeIndex(at)
    isins = sorted(daily["isin"].unique())
    out = {m: np.full((len(at), len(isins)), np.nan) for m in SHORT_GATE_METRICS}
    cov = {k: np.zeros(len(at), dtype=int) for k in ("funds_with_daily", "evaluable_6m", "evaluable_3m", "invalid", "stale")}
    at_ns = at.to_numpy().astype("datetime64[ns]")

    for j, (isin, grp) in enumerate(daily.groupby("isin", sort=True)):
        g = grp.dropna(subset=["nav"]).sort_values("date")
        x = g["nav"].to_numpy(dtype=float)
        d = g["date"].to_numpy().astype("datetime64[ns]")
        if len(x) < 2:
            continue
        nonpos, bad = _window_validity(x)
        ks = np.searchsorted(d, at_ns, side="right")                 # observations <= t
        for i, k in enumerate(ks):
            if k < 1:
                continue
            cov["funds_with_daily"][i] += 1
            if max_stale_days is not None and (at_ns[i] - d[k - 1]) / np.timedelta64(1, "D") > max_stale_days:
                cov["stale"][i] += 1
                continue
            invalid = False
            # ---- rolling_6m: tail(126), min 90 -> max drawdown + liquidity flag
            a6 = max(0, k - WINDOW_6M)
            if k - a6 >= MIN_OBS_6M:
                if _valid(nonpos, bad, a6, k):
                    win = x[a6:k]
                    out["short_max_drawdown_6m"][i, j] = float((win / np.maximum.accumulate(win) - 1.0).min())
                    r6 = win[1:] / win[:-1] - 1.0
                    out["short_liquidity_flag_6m"][i, j] = round(float((r6 == 0.0).sum()) / len(r6), 4)
                    cov["evaluable_6m"][i] += 1
                else:
                    invalid = True
            # ---- rolling_3m: tail(63), min 45 -> AC(1)-adjusted volatility
            a3 = max(0, k - WINDOW_3M)
            if k - a3 >= MIN_OBS_3M:
                if _valid(nonpos, bad, a3, k):
                    win = x[a3:k]
                    v = _vol_adj(win[1:] / win[:-1] - 1.0)
                    out["short_vol_adj_3m"][i, j] = v
                    cov["evaluable_3m"][i] += 1
                else:
                    invalid = True
            cov["invalid"][i] += invalid

    metrics = {m: pd.DataFrame(a, index=at, columns=isins) for m, a in out.items()}
    coverage = pd.DataFrame(cov, index=at)
    coverage["evaluable_share_6m"] = (coverage["evaluable_6m"] / coverage["funds_with_daily"].replace(0, np.nan)).round(4)
    _log_coverage(coverage)
    return metrics, coverage


def _log_coverage(coverage: pd.DataFrame) -> None:
    """One summary line + a WARNING listing the dates where the short gates are effectively off."""
    if coverage.empty:
        return
    total = int(coverage["funds_with_daily"].sum())
    ev = int(coverage["evaluable_6m"].sum())
    logger.info("short gates PIT coverage: %d fund-dates with daily NAV, %d evaluable (6m) = %.1f%%; "
                "%d invalid windows, %d stale", total, ev, 100.0 * ev / total if total else 0.0,
                int(coverage["invalid"].sum()), int(coverage["stale"].sum()))
    empty = coverage.index[coverage["evaluable_6m"] == 0]
    if len(empty):
        logger.warning("short gates fail-open on %d of %d dates (no fund has a usable daily window): %s .. %s",
                       len(empty), len(coverage), empty.min().date(), empty.max().date())
