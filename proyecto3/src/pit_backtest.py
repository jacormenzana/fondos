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
import dataclasses
from dataclasses import dataclass
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
# Data end
# ============================================================

def last_complete_month_end(last_nav: pd.Timestamp, tolerance_days: int = 5) -> pd.Timestamp:
    """Last month-end the NAV data fully covers: the month-end of the last NAV date when that date is within
    `tolerance_days` of it (a fund reporting on the 28th of a 30-day month still closes the month), otherwise the
    PREVIOUS month-end (a last NAV of 2026-10-03 covers up to 2026-09-30 only). Windows ending after it are not
    evaluable, and neither are evaluation dates after it (FND-0204)."""
    last_nav = pd.Timestamp(last_nav)
    me = last_nav + pd.offsets.MonthEnd(0)
    return me if (me - last_nav).days <= tolerance_days else me - pd.offsets.MonthEnd(1)


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
                  constraints=DEFAULT_CONSTRAINTS, use_incumbents: bool = False, rebalance_every: int = 1,
                  phase: int = 0, regime_trigger=False) -> "tuple[pd.DataFrame, pd.Series]":
    """Master weights per date: (W [dates x isin], cash [dates]). regime_hist has weight_defensive/balanced/dynamic
    (the lagged classifier's classify_historical()); dates without scores or regime row give an empty portfolio.

    use_incumbents (FND-0205): the funds each sub-portfolio held the previous month are passed to the engine as
    incumbents, so they get the hysteresis bonus constraints.hysteresis_band in the ranking (the same mechanism
    as the live builder, PORTFOLIO_HYSTERESIS_ENABLED). An empty month resets the holdings. Only the SELECTION is
    sticky: weights stay score-proportional, so turnover from re-weighting held funds remains.

    rebalance_every / phase (FND-0217): the portfolio is re-selected only on every `rebalance_every`-th evaluation date
    (positions i with (i - phase) % rebalance_every == 0, counted over `at`); in between, the last portfolio is held as it
    is (target weights, no drift) -- the regime weights are also frozen until the next rebalance. 1 = every month (default,
    the previous behaviour). A month without scores empties the portfolio and forces a rebalance at the next dated month.
    regime_trigger: also rebalance on a date whose regime label differs from the one at the last rebalance (react at once
    to a regime change, slowly otherwise); only matters when rebalance_every > 1. True = any change; a collection of regime
    names (e.g. {"Crisis_Financiera"}) = only a change INTO or OUT OF one of those regimes."""
    rows, cash = {}, {}
    incumbents: dict = {}
    held_master, held_cash, held_regime = None, np.nan, None
    hist = regime_hist.sort_index()
    by_date = dict(tuple(scores.groupby("as_of"))) if len(scores) else {}
    for i, t in enumerate(at):
        pos = hist.index.searchsorted(t, side="right") - 1
        if t not in by_date or pos < 0:
            rows[t], cash[t] = {}, np.nan
            incumbents = {}
            held_master, held_cash, held_regime = None, np.nan, None
            continue
        h = hist.iloc[pos]
        changed = bool(regime_trigger) and held_regime is not None and h["regime"] != held_regime
        if changed and regime_trigger is not True:
            changed = h["regime"] in regime_trigger or held_regime in regime_trigger
        if rebalance_every > 1 and held_master is not None and (i - phase) % rebalance_every != 0 and not changed:
            rows[t], cash[t] = held_master, held_cash                  # between rebalances: hold
            continue
        sub_w = dict(zip(SUB_NAMES, (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
        selection = select_and_weight(candidates_by_sub(by_date[t], attrs), sub_w, constraints,
                                      incumbents if use_incumbents else None)
        if use_incumbents:
            incumbents = {sub: frozenset(df["isin"]) for sub, df in selection.items() if not df.empty}
        master = _blend_to_master(selection, sub_w)
        rows[t], cash[t] = master, (cash_weight(master) if master else np.nan)
        held_master, held_cash, held_regime = (master, cash[t], h["regime"]) if master else (None, np.nan, None)
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
    NaN when either end is unusable. A window whose END lies after the last month the data fully covers
    (last_complete_month_end) is NaN too: without this, the as-of rule would let a window that ends in the
    future use the last NAV as its end and silently evaluate a SHORTER period as if it were complete (FND-0204).
    Shape (dates x funds)."""
    ends = pd.DatetimeIndex([t + pd.offsets.MonthEnd(months) for t in at])
    data_end = last_complete_month_end(nav.index.max())
    start, _ = asof_snapshot(nav, at, max_stale_days)
    end, _ = asof_snapshot(nav, ends, max_stale_days)
    s = start.to_numpy()
    e = end.to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where((s > 0) & np.isfinite(s) & np.isfinite(e), e / s - 1.0, np.nan)
    out = pd.DataFrame(r, index=at, columns=nav.columns)
    out.loc[np.asarray(ends > data_end), :] = np.nan
    return out


def portfolio_returns(W: pd.DataFrame, R: pd.DataFrame, cash_ret: pd.Series,
                      tx_cost: float = 0.0) -> "tuple[pd.Series, pd.Series]":
    """Weighted forward return for every date at once (matrix product), no renormalisation.

    W: master weights (dates x funds, NaN = not held); R: forward returns (same index; columns reindexed).
    A held fund without a usable return keeps its weight in cash, as does any unallocated weight (1 - sum W).
    tx_cost (FND-0190, N4): fraction of the INVESTED capital lost to dealing costs, charged once per holding
    window at entry (the approved convention: charging every monthly rebalance would count the same fee
    several times across the overlapping 3- and 12-month windows). Applied as the (1 - tx_cost) multiplier on
    the invested sleeve at its end value; cash costs nothing. 0.0 reproduces the frictionless result exactly.
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
    if tx_cost:
        total = total - tx_cost * (covered + weighted)                       # (1 - c) on the invested sleeve's value
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

@dataclass
class PreparedBacktest:
    """Everything that does not depend on the cost assumption, computed once (selection is the slow part)."""
    at: pd.DatetimeIndex
    windows: tuple
    regime: list
    W: pd.DataFrame
    cash_w: pd.Series
    R: dict                  # window -> forward-return matrix
    cash_ret: dict           # window -> cash return Series
    bench: dict              # window -> eligible-pool benchmark Series


def prepare_pit_backtest(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex,
                         windows=FORWARD_WINDOWS, max_stale_days: int = 45,
                         constraints=DEFAULT_CONSTRAINTS, use_incumbents: bool = False, rebalance_every: int = 1,
                         phase: int = 0, regime_trigger=False) -> PreparedBacktest:
    at = pd.DatetimeIndex(at)
    windows = tuple(windows)
    W, cash_w = build_weights(pit_run.scores, inputs.attrs, regime_hist, at, constraints, use_incumbents,
                              rebalance_every, phase, regime_trigger)
    grid = pd.date_range(at.min(), at.max() + pd.offsets.MonthEnd(max(windows)), freq=pd.offsets.MonthEnd())
    cidx = cash_index(inputs.rate, grid)
    hist = regime_hist.sort_index()
    regime = [hist["regime"].iloc[p] if (p := hist.index.searchsorted(t, side="right") - 1) >= 0 else None for t in at]
    R, cash_ret, bench = {}, {}, {}
    for w in windows:
        R[w] = forward_returns(inputs.nav, at, w, max_stale_days)
        ends = pd.DatetimeIndex([t + pd.offsets.MonthEnd(w) for t in at])
        cash_ret[w] = pd.Series(cidx.reindex(ends).to_numpy() / cidx.reindex(at).to_numpy() - 1.0, index=at)
        bench[w] = pool_benchmark(pit_run.scores, R[w], cash_ret[w], hist, at)
    return PreparedBacktest(at, windows, regime, W, cash_w, R, cash_ret, bench)


def target_window_return(target_annual: "pd.Series | None", at: pd.DatetimeIndex, months: int) -> pd.Series:
    """Absolute IPC+M3 objective over a window of `months`, known at entry: (1 + target_ann(t)) ** (months/12) - 1,
    with target_ann(t) the latest value <= t (already publication-lagged). NaN where unknown."""
    if target_annual is None or len(target_annual) == 0:
        return pd.Series(np.nan, index=at)
    ann = target_annual.sort_index()
    ann = ann.reindex(ann.index.union(at)).ffill().reindex(at)
    return ((1.0 + ann) ** (months / 12.0) - 1.0).rename(None)


def assemble_table(prep: PreparedBacktest, tx_cost_bps: float = 0.0, entry_sides: int = 1,
                   target_annual: "pd.Series | None" = None) -> pd.DataFrame:
    """Backtest table for one cost assumption: tx_cost_bps per side x entry_sides (1 = entry only, the approved
    convention; 2 = round trip, a stricter sensitivity). Columns: regime, n_funds, cash_weight, cash_ret_1m and,
    per window w: ret_wm (net of costs), gross_wm, cost_wm, cov_wm, bench_wm (frictionless pool), excess_wm,
    target_wm (IPC+M3, when given) and vs_target_wm."""
    c = tx_cost_bps / 1e4 * entry_sides
    out = pd.DataFrame({"regime": prep.regime, "n_funds": prep.W.notna().sum(axis=1).to_numpy(),
                        "cash_weight": prep.cash_w.to_numpy()}, index=prep.at)
    out.index.name = "date"
    if 1 in prep.cash_ret:
        out["cash_ret_1m"] = prep.cash_ret[1].to_numpy()
    for w in prep.windows:
        gross, share = portfolio_returns(prep.W, prep.R[w], prep.cash_ret[w])
        net = portfolio_returns(prep.W, prep.R[w], prep.cash_ret[w], tx_cost=c)[0] if c else gross
        bench = prep.bench[w]
        out[f"ret_{w}m"] = net.to_numpy()
        out[f"gross_{w}m"] = gross.to_numpy()
        out[f"cost_{w}m"] = (gross - net).to_numpy()
        out[f"cov_{w}m"] = share.to_numpy()
        out[f"bench_{w}m"] = bench.to_numpy()
        out[f"excess_{w}m"] = (net - bench).to_numpy()
        if target_annual is not None:
            tgt = target_window_return(target_annual, prep.at, w)
            out[f"target_{w}m"] = tgt.to_numpy()
            out[f"vs_target_{w}m"] = (net - tgt).to_numpy()
    # The chained 1m series (used for Sharpe / volatility / drawdown) must be net of the trading the portfolio
    # really does month to month: charging the per-window entry cost on EVERY start date would imply 100% monthly
    # turnover and understate it. Cost per side x sum |w_t - w_t-1| (entering from cash in the first month and after
    # an empty month; exits pay too). The window columns above keep the approved once-per-window entry convention.
    if 1 in prep.windows:
        turnover = monthly_turnover(prep.W)
        out["turnover"] = turnover.to_numpy()
        out["net_turnover_1m"] = (out["gross_1m"] - tx_cost_bps / 1e4 * turnover).to_numpy()
    out.attrs["tx_cost_bps"], out.attrs["entry_sides"] = tx_cost_bps, entry_sides
    return out


def monthly_turnover(W: pd.DataFrame) -> pd.Series:
    """Traded notional per month as a share of capital: sum |w_t - w_{t-1}| over funds (target weights, drift
    ignored). The first month, and any month after an empty portfolio, enters from cash (turnover = invested weight).
    NaN where the portfolio is empty (nothing is held, no return either)."""
    w = W.fillna(0.0)
    held = W.notna().any(axis=1)
    prev = w.shift(1).fillna(0.0)                       # an empty month holds nothing, so the next one enters from cash
    turnover = (w - prev).abs().sum(axis=1)
    return turnover.where(held)


def run_pit_backtest(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex,
                     windows=FORWARD_WINDOWS, max_stale_days: int = 45, constraints=DEFAULT_CONSTRAINTS,
                     tx_cost_bps: float = 0.0, entry_sides: int = 1,
                     target_annual: "pd.Series | None" = None, use_incumbents: bool = False) -> pd.DataFrame:
    """Backtest table (same columns as Backtester.run plus n_funds, cash_weight, cov_*, cost_*, target_*): one row
    per month-end. pit_run: PitRun from pit_run.compute_pit_scores for the same `at`; inputs: its PitInputs."""
    prep = prepare_pit_backtest(pit_run, inputs, regime_hist, at, windows, max_stale_days, constraints, use_incumbents)
    return assemble_table(prep, tx_cost_bps, entry_sides, target_annual)


COST_GRID_BPS = (0.0, 25.0, 50.0)


def cost_sensitivity(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex,
                     bps=COST_GRID_BPS, entry_sides: int = 1, windows=FORWARD_WINDOWS,
                     max_stale_days: int = 45, constraints=DEFAULT_CONSTRAINTS,
                     target_annual: "pd.Series | None" = None) -> pd.DataFrame:
    """Symmetric cost sensitivity (0/25/50 bps per side by default): one row per (bps, window) with the mean net
    return, mean cost, mean excess vs the frictionless pool benchmark, hit ratio and, when a target is given, the
    share of months beating IPC+M3. Selection is computed once and shared by every cost level."""
    prep = prepare_pit_backtest(pit_run, inputs, regime_hist, at, windows, max_stale_days, constraints)
    rows = []
    for b in bps:
        tab = assemble_table(prep, b, entry_sides, target_annual)
        for w in prep.windows:
            v = tab[[f"ret_{w}m", f"excess_{w}m"]].dropna()
            row = {"bps_per_side": b, "window_m": w, "n": len(v), "mean_ret": v[f"ret_{w}m"].mean(),
                   "mean_cost": tab[f"cost_{w}m"].mean(), "mean_excess": v[f"excess_{w}m"].mean(),
                   "hit_vs_bench": float((v[f"excess_{w}m"] > 0).mean()) if len(v) else np.nan}
            if f"vs_target_{w}m" in tab:
                vt = tab[f"vs_target_{w}m"].dropna()
                row["hit_vs_target"] = float((vt > 0).mean()) if len(vt) else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def series_stats(returns: pd.Series, cash_returns: "pd.Series | None" = None, periods_per_year: int = 12) -> dict:
    """Statistics of a MONTHLY return series (non-overlapping 1m returns of the simulated portfolio or of the
    benchmark): geometric annual return, annual volatility (std ddof=1 * sqrt(12), as P2), Sharpe and max drawdown.
    Sharpe = mean(monthly return - monthly cash return) / std(...) * sqrt(12): excess over what the cash leg actually
    earned each month (ECB deposit rate, negative periods included) instead of one flat rate for the whole history.
    Without cash_returns it is the zero-rate Sharpe. NaN-safe: months without a return are dropped."""
    r = returns.dropna()
    n = len(r)
    if n < 2:
        return {"n_months": n, "ann_return": np.nan, "ann_vol": np.nan, "sharpe": np.nan, "max_drawdown": np.nan}
    cum = (1.0 + r).cumprod()
    ann_return = float(cum.iloc[-1] ** (periods_per_year / n) - 1.0)
    vol = float(r.std(ddof=1) * np.sqrt(periods_per_year))
    ex = r - (cash_returns.reindex(r.index).fillna(0.0) if cash_returns is not None else 0.0)
    sd = float(ex.std(ddof=1))
    sharpe = float(ex.mean() / sd * np.sqrt(periods_per_year)) if sd > 1e-10 else np.nan    # flat series: float noise is not volatility
    max_dd = float((cum / cum.cummax() - 1.0).min())
    return {"n_months": n, "ann_return": ann_return, "ann_vol": vol, "sharpe": sharpe, "max_drawdown": max_dd}


def table_stats(table: pd.DataFrame) -> dict:
    """series_stats for the portfolio (ret_1m) and the pool benchmark (bench_1m), both against the cash leg."""
    cash = table["cash_ret_1m"] if "cash_ret_1m" in table.columns else None
    port = table["net_turnover_1m"] if "net_turnover_1m" in table.columns else table["ret_1m"]
    return {"portfolio": series_stats(port, cash), "benchmark": series_stats(table["bench_1m"], cash)}


HYSTERESIS_GRID = (0.0, 0.05, 0.10, 0.20, 0.40)


def hysteresis_experiment(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex,
                          bands=HYSTERESIS_GRID, tx_cost_bps: float = 25.0, entry_sides: int = 1,
                          windows=FORWARD_WINDOWS, max_stale_days: int = 45,
                          target_annual: "pd.Series | None" = None) -> pd.DataFrame:
    """FND-0205: how much of the turnover is selection churn, and what does stickiness cost or buy?

    One row per hysteresis band. band 0 = no incumbents (the baseline: every month is a fresh selection); band b > 0
    gives the previous month's holdings of each sub-portfolio a score bonus of b (0.05 = +5%) in the ranking. Same
    scores, same forward returns, same benchmark for every row -- only the portfolios differ. Columns: mean/median
    monthly turnover (sum |dw|, buys + sells), the annual drag at the given cost, the chained-series statistics net
    of REAL turnover (annual return, vol, Sharpe, max drawdown), the mean number of funds, and the 12-month
    window mean return / excess / hit ratio (entry cost once per window, as approved)."""
    base = prepare_pit_backtest(pit_run, inputs, regime_hist, at, windows, max_stale_days)
    rows = []
    for b in bands:
        if b == 0:
            prep = base
        else:
            cons = dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=b)
            W, cash_w = build_weights(pit_run.scores, inputs.attrs, regime_hist, base.at, cons, use_incumbents=True)
            prep = dataclasses.replace(base, W=W, cash_w=cash_w)
        tab = assemble_table(prep, tx_cost_bps, entry_sides, target_annual)
        st = table_stats(tab)["portfolio"]
        e12 = tab[["ret_12m", "excess_12m"]].dropna() if 12 in prep.windows else pd.DataFrame()
        rows.append({
            "hysteresis_band": b,
            "mean_turnover": float(tab["turnover"].mean()), "median_turnover": float(tab["turnover"].median()),
            "annual_cost_drag": float(tab["turnover"].mean() * 12 * tx_cost_bps / 1e4),
            "ann_return": st["ann_return"], "ann_vol": st["ann_vol"], "sharpe": st["sharpe"], "max_drawdown": st["max_drawdown"],
            "mean_funds": float(tab["n_funds"].mean()),
            "mean_ret_12m": float(e12["ret_12m"].mean()) if len(e12) else np.nan,
            "mean_excess_12m": float(e12["excess_12m"].mean()) if len(e12) else np.nan,
            "hit_12m": float((e12["excess_12m"] > 0).mean()) if len(e12) else np.nan,
        })
    out = pd.DataFrame(rows)
    out.attrs["tx_cost_bps"] = tx_cost_bps
    return out
