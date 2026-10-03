# proyecto3/src/pit_backtest.py
# -*- coding: utf-8 -*-
"""
Point-in-time backtest of the P3 portfolio -- FND-0159 / FND-0191 / FND-0189 / FND-0197, Wave B step d3.

Replaces the look-ahead loop in Backtester.run(): for every month-end t
  1. the universe is scored with information available at t only (pit_run / pit_candidates);
  2. the portfolio is built by the SAME shared engine the live builder uses (portfolio_engine.select_and_weight,
     blended with the regime's sub-portfolio weights; the regime comes from the publication-lagged classifier);
  3. forward returns over 1/3/12 months are computed for ALL funds at once as matrices (as-of NAV at t and
     t+w; funds with no usable NAV at either end keep their weight in cash) and combined with the weights by a
     single matrix product -- no per-fund / per-date Python loops in the return layer (FND-0191);
  4. unallocated weight (cap residue, empty sub-portfolios, funds without NAV) earns the ECB deposit rate
     as of each month (FND-0197), negative rates included;
  5. the benchmark is the regime-weighted blend of the EQUAL-WEIGHTED ELIGIBLE POOL of each sub-portfolio
     (funds that pass the same hard filters, no score selection) -> excess return isolates selection skill
     (FND-0189 / N3), instead of the old first-100-columns average.

Not here yet: transaction costs / slippage (FND-0190, N4: needs the turnover convention for overlapping
windows decided), the IPC+M3 absolute target of N3, incumbents/hysteresis, Sharpe of the simulated series.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.backtesting import FORWARD_WINDOWS, _blend_to_master
from proyecto3.src.pit_candidates import asof_snapshot
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS, cash_weight, select_and_weight

logger = logging.getLogger(__name__)

SUB_NAMES = ["Defensiva", "Equilibrada", "Dinamica"]
CANDIDATE_COLUMNS = ["isin", "score_total", "fund_name", "fund_nature", "management_company", "fund_family_id"]


# ============================================================
# Selection over time
# ============================================================

def candidates_by_sub(scores_t: pd.DataFrame, attrs: pd.DataFrame) -> dict:
    """Candidate frames per sub-portfolio from one date's scores: eligible and score > 0 (same rule as
    score_candidates.load_current_candidates; the PIT universe already holds one row per fund and block)."""
    out = {}
    ok = scores_t[scores_t["eligible"] & (scores_t["score_final"] > 0)]
    for sub in SUB_NAMES:
        block = ok[ok["subportfolio"] == sub]
        if block.empty:
            out[sub] = pd.DataFrame(columns=CANDIDATE_COLUMNS)
            continue
        frame = pd.DataFrame({
            "isin": block["isin"].to_numpy(),
            "score_total": block["score_final"].to_numpy(),
            "fund_name": block["fund_name"].to_numpy(),
            "fund_nature": block["fund_nature"].to_numpy(),
            "management_company": attrs["Management_Company"].reindex(block["isin"]).to_numpy(),
            "fund_family_id": block["fund_family_id"].to_numpy(),
        }).sort_values("score_total", ascending=False).reset_index(drop=True)
        out[sub] = frame
    return out


def build_weights(scores: pd.DataFrame, attrs: pd.DataFrame, regime_hist: pd.DataFrame, at: pd.DatetimeIndex,
                  constraints=DEFAULT_CONSTRAINTS) -> "tuple[pd.DataFrame, pd.Series]":
    """Master weights per date: (W [dates x isin], cash [dates]). regime_hist has weight_defensive/balanced/dynamic
    (the lagged classifier's classify_historical()); dates without scores or regime row give an empty portfolio."""
    rows, cash = {}, {}
    hist = regime_hist.sort_index()
    by_date = dict(tuple(scores.groupby("as_of"))) if len(scores) else {}
    for t in at:
        pos = hist.index.searchsorted(t, side="right") - 1
        if t not in by_date or pos < 0:
            rows[t], cash[t] = {}, np.nan
            continue
        h = hist.iloc[pos]
        sub_w = dict(zip(SUB_NAMES, (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
        selection = select_and_weight(candidates_by_sub(by_date[t], attrs), sub_w, constraints)
        master = _blend_to_master(selection, sub_w)
        rows[t], cash[t] = master, (cash_weight(master) if master else np.nan)
    W = pd.DataFrame.from_dict(rows, orient="index").reindex(at)
    return W, pd.Series(cash).reindex(at)


# ============================================================
# Vectorized return layer
# ============================================================

def cash_index(rate: "pd.DataFrame | None", months: pd.DatetimeIndex) -> pd.Series:
    """Cumulative value of 1 unit of cash on the month-end grid `months` (first value 1.0). Each month accrues
    the deposit rate in force at the START of that month, (1 + r/12); r decimal, may be negative. Months before
    the rate series (or no series) accrue 0 -- conservative, warned once."""
    months = pd.DatetimeIndex(months)
    if rate is None or len(rate) == 0:
        logger.warning("cash leg: no rate_deposit series -- liquidity accrues 0%%")
        return pd.Series(1.0, index=months)
    s = rate.copy()
    s["date"] = pd.DatetimeIndex(s["date"]) + pd.offsets.MonthEnd(0)
    s = s.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["rate"].astype(float)
    prior = months[:-1]
    r = s.reindex(prior, method="ffill")
    if r.isna().any():
        logger.warning("cash leg: no rate_deposit before %s -- those months accrue 0%%", s.index.min().date())
    factors = 1.0 + r.fillna(0.0).to_numpy() / 12.0
    return pd.Series(np.concatenate([[1.0], np.cumprod(factors)]), index=months)


def forward_returns(nav: pd.DataFrame, at: pd.DatetimeIndex, months: int, max_stale_days: int) -> pd.DataFrame:
    """Simple return of every fund from t to t+months (month-end grid): NAV as-of t and as-of t+months, each
    not older than max_stale_days (so a window running past the data is NaN once the last NAV is that old);
    NaN when either end is unusable. Shape (dates x funds)."""
    ends = pd.DatetimeIndex([t + pd.offsets.MonthEnd(months) for t in at])
    start, _ = asof_snapshot(nav, at, max_stale_days)
    end, _ = asof_snapshot(nav, ends, max_stale_days)
    s = start.to_numpy()
    e = end.to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where((s > 0) & np.isfinite(s) & np.isfinite(e), e / s - 1.0, np.nan)
    out = pd.DataFrame(r, index=at, columns=nav.columns)
    return out


def portfolio_returns(W: pd.DataFrame, R: pd.DataFrame, cash_ret: pd.Series) -> "tuple[pd.Series, pd.Series]":
    """Weighted forward return for every date at once (matrix product), no renormalisation.

    W: master weights (dates x funds, NaN = not held); R: forward returns (same index; columns reindexed).
    A held fund without a usable return keeps its weight in cash, as does any unallocated weight (1 - sum W).
    Returns (return, fund_data_share): NaN return for an empty portfolio or one with no fund covered; the
    share is covered weight / held weight."""
    R = R.reindex(columns=W.columns)
    w = np.nan_to_num(W.to_numpy(dtype=float), nan=0.0)
    r = R.reindex(W.index).to_numpy(dtype=float)
    ok = np.isfinite(r)
    covered = (w * ok).sum(axis=1)
    held = w.sum(axis=1)
    weighted = (w * np.where(ok, r, 0.0)).sum(axis=1)
    cash_share = np.clip(1.0 - covered, 0.0, None)
    c = cash_ret.reindex(W.index).to_numpy(dtype=float)
    total = weighted + np.where(cash_share > 0, cash_share * np.nan_to_num(c, nan=0.0), 0.0)
    total = np.where((held > 0) & (covered > 0), total, np.nan)
    share = np.where(held > 0, covered / np.where(held > 0, held, 1.0), np.nan)
    return pd.Series(np.round(total, 6), index=W.index), pd.Series(share, index=W.index)


def pool_benchmark(scores: pd.DataFrame, R: pd.DataFrame, cash_ret: pd.Series, regime_hist: pd.DataFrame,
                   at: pd.DatetimeIndex) -> pd.Series:
    """Regime-weighted blend of the equal-weighted eligible pool of each sub-portfolio (N3). A sub-portfolio with
    no eligible fund (or no usable return) puts its weight in cash. NaN where nothing is observable."""
    hist = regime_hist.sort_index()
    out = pd.Series(np.nan, index=at)
    by_date = dict(tuple(scores[scores["eligible"]].groupby("as_of"))) if len(scores) else {}
    for t in at:
        pos = hist.index.searchsorted(t, side="right") - 1
        if t not in by_date or pos < 0 or t not in R.index:
            continue
        h = hist.iloc[pos]
        weights = dict(zip(SUB_NAMES, (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
        total, used = 0.0, 0.0
        c = cash_ret.get(t, 0.0)
        c = 0.0 if pd.isna(c) else float(c)
        for sub, sw in weights.items():
            pool = by_date[t][by_date[t]["subportfolio"] == sub]["isin"].unique()
            rets = R.loc[t].reindex(pool).dropna()
            if len(rets):
                total += sw * float(rets.mean())
                used += sw
            else:
                total += sw * c
        out[t] = round(total, 6) if used > 0 else np.nan
    return out


# ============================================================
# Runner
# ============================================================

def run_pit_backtest(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex,
                     windows=FORWARD_WINDOWS, max_stale_days: int = 45, constraints=DEFAULT_CONSTRAINTS) -> pd.DataFrame:
    """Backtest table (same columns as Backtester.run plus n_funds, cash_weight, cov_*): one row per month-end.

    pit_run: PitRun from pit_run.compute_pit_scores for the same `at`; inputs: its PitInputs (NAV, attrs, rate)."""
    at = pd.DatetimeIndex(at)
    W, cash_w = build_weights(pit_run.scores, inputs.attrs, regime_hist, at, constraints)
    grid = pd.date_range(at.min(), at.max() + pd.offsets.MonthEnd(max(windows)), freq=pd.offsets.MonthEnd())
    cidx = cash_index(inputs.rate, grid)

    hist = regime_hist.sort_index()
    regime = [hist["regime"].iloc[p] if (p := hist.index.searchsorted(t, side="right") - 1) >= 0 else None for t in at]
    out = pd.DataFrame({"regime": regime, "n_funds": W.notna().sum(axis=1).to_numpy(),
                        "cash_weight": cash_w.to_numpy()}, index=at)
    out.index.name = "date"
    for w in windows:
        R = forward_returns(inputs.nav, at, w, max_stale_days)
        ends = pd.DatetimeIndex([t + pd.offsets.MonthEnd(w) for t in at])
        cash_ret = pd.Series(cidx.reindex(ends).to_numpy() / cidx.reindex(at).to_numpy() - 1.0, index=at)
        ret, share = portfolio_returns(W, R, cash_ret)
        bench = pool_benchmark(pit_run.scores, R, cash_ret, hist, at)
        out[f"ret_{w}m"] = ret.to_numpy()
        out[f"cov_{w}m"] = share.to_numpy()
        out[f"bench_{w}m"] = bench.to_numpy()
        out[f"excess_{w}m"] = (ret - bench).to_numpy()
    return out
