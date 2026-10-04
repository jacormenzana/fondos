# proyecto3/src/backtest_inference.py
# -*- coding: utf-8 -*-
"""
Statistical inference for backtests -- first piece of FND-0219 (shared by the out-of-sample test FND-0193, the cadence
experiment and the calibrator).

Monthly returns of a simulated portfolio are autocorrelated and the sample holds only a handful of independent crises, so
a point estimate such as "Sharpe 0.73 vs 0.63" says little by itself. This module gives the uncertainty around a
DIFFERENCE between two strategies evaluated on the same months:

  * stationary_bootstrap_indices: Politis-Romano stationary bootstrap resampling of month positions (random block
    lengths, geometric with mean `mean_block`), which keeps the serial dependence inside blocks;
  * sharpe_ratio: annualised Sharpe of returns in excess of the cash leg;
  * paired_sharpe_difference: point estimate, percentile confidence interval and the share of resamples above zero
    for Sharpe(a) - Sharpe(b), both series resampled with the SAME indices (paired);
  * expected_max_sharpe / probabilistic_sharpe_ratio / deflated_sharpe_ratio: how good a Sharpe must be to beat "the best of
    N strategies tried" (selection on the same data);
  * effective_sample_size: how many independent months an autocorrelated series is worth.

Pure numpy / pandas, no DB (R-7).
"""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd


def stationary_bootstrap_indices(n: int, n_boot: int, mean_block: float = 6.0, seed: int = 12345) -> np.ndarray:
    """(n_boot, n) integer positions of a stationary bootstrap over a series of length n."""
    if n < 2:
        raise ValueError("need at least 2 observations")
    rng = np.random.default_rng(seed)
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, size=n_boot)
    restart = rng.random((n_boot, n)) < p
    jumps = rng.integers(0, n, size=(n_boot, n))
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], jumps[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def sharpe_ratio(returns, cash=None, periods_per_year: int = 12) -> float:
    """Annualised mean(excess) / std(excess); NaN for a flat or too short series."""
    r = np.asarray(returns, dtype=float)
    if cash is not None:
        r = r - np.asarray(cash, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 3:
        return float("nan")
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(periods_per_year)) if sd > 1e-12 else float("nan")


def _sharpe_rows(excess: np.ndarray, idx: np.ndarray, periods_per_year: int) -> np.ndarray:
    s = excess[idx]                                                    # (n_boot, n)
    sd = s.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 1e-12, s.mean(axis=1) / sd * np.sqrt(periods_per_year), np.nan)


def paired_sharpe_difference(a: pd.Series, b: pd.Series, cash: "pd.Series | None" = None, n_boot: int = 2000,
                             mean_block: float = 6.0, ci: float = 0.90, seed: int = 12345,
                             periods_per_year: int = 12) -> dict:
    """Sharpe(a) - Sharpe(b) on the months both series have (and `cash`, when given), with a paired stationary-bootstrap
    percentile interval. Returns {n, sharpe_a, sharpe_b, diff, ci_low, ci_high, prob_a_better}: prob_a_better is the share
    of resamples with diff > 0 (a one-sided reading; not a p-value)."""
    d = pd.concat({"a": a, "b": b}, axis=1).dropna()
    c = pd.Series(0.0, index=d.index) if cash is None else cash.reindex(d.index).fillna(0.0)
    ea, eb = (d["a"] - c).to_numpy(dtype=float), (d["b"] - c).to_numpy(dtype=float)
    n = len(d)
    out = {"n": n, "sharpe_a": sharpe_ratio(ea, None, periods_per_year), "sharpe_b": sharpe_ratio(eb, None, periods_per_year)}
    out["diff"] = out["sharpe_a"] - out["sharpe_b"]
    if n < 12:
        return {**out, "ci_low": float("nan"), "ci_high": float("nan"), "prob_a_better": float("nan")}
    idx = stationary_bootstrap_indices(n, n_boot, mean_block, seed)
    diff = _sharpe_rows(ea, idx, periods_per_year) - _sharpe_rows(eb, idx, periods_per_year)
    diff = diff[np.isfinite(diff)]
    lo, hi = np.quantile(diff, [(1 - ci) / 2, 1 - (1 - ci) / 2])
    return {**out, "ci_low": float(lo), "ci_high": float(hi), "prob_a_better": float((diff > 0).mean())}


# ============================================================
# Multiple testing and serial dependence (FND-0219)
# ============================================================

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe(n_trials: int, var_trial_sharpes: float) -> float:
    """Expected maximum of the SHARPES (per-period, not annualised) of `n_trials` independent strategies whose true Sharpe
    is zero, with cross-trial variance `var_trial_sharpes` (Bailey & Lopez de Prado). The bar a selected strategy must clear
    to beat 'the best of N noise strategies'. 0 for a single trial."""
    if n_trials <= 1 or var_trial_sharpes <= 0:
        return 0.0
    sd = math.sqrt(var_trial_sharpes)
    return sd * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1.0 / n_trials) + EULER_GAMMA * _N.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def _moments(r: np.ndarray) -> "tuple[float, float]":
    d = r - r.mean()
    sd = d.std(ddof=0)
    if sd <= 0:
        return 0.0, 3.0
    return float(((d / sd) ** 3).mean()), float(((d / sd) ** 4).mean())


def probabilistic_sharpe_ratio(returns, benchmark_sharpe: float = 0.0) -> float:
    """P(true per-period Sharpe > benchmark_sharpe) given the observed series (non-normality aware: skewness and kurtosis
    widen the interval). Sharpe and benchmark in PER-PERIOD units (monthly here)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 4 or r.std(ddof=1) <= 1e-12:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    skew, kurt = _moments(r)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if denom <= 0:
        return float("nan")
    return float(_N.cdf((sr - benchmark_sharpe) * math.sqrt(n - 1) / math.sqrt(denom)))


def deflated_sharpe_ratio(returns, n_trials: int, var_trial_sharpes: float) -> float:
    """Probabilistic Sharpe against the expected maximum of `n_trials` zero-skill strategies: the probability that the
    SELECTED strategy's true Sharpe is above what picking the best of n_trials by chance would give. Use it on the data the
    selection was made on (e.g. the design period), with the variance of the per-period Sharpes across the trials."""
    return probabilistic_sharpe_ratio(returns, expected_max_sharpe(n_trials, var_trial_sharpes))


def effective_sample_size(returns, max_lag: "int | None" = None) -> float:
    """n / (1 + 2 * sum_k (1 - k/(L+1)) rho_k) with Bartlett weights: how many INDEPENDENT observations an autocorrelated
    series is worth (never above n, never below 1). 12-month windows that overlap monthly are worth about n / 12."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 4:
        return float(n)
    L = max_lag if max_lag is not None else int(round(4 * (n / 100.0) ** (2.0 / 9.0)))
    d = r - r.mean()
    var = (d ** 2).sum()
    if var <= 0:
        return float(n)
    rho = [(d[k:] * d[:-k]).sum() / var for k in range(1, L + 1)]
    factor = 1.0 + 2.0 * sum((1.0 - k / (L + 1.0)) * rho[k - 1] for k in range(1, L + 1))
    return float(min(max(n / factor, 1.0), n)) if factor > 0 else float(n)
