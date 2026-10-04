# proyecto3/src/pit_oos.py
# -*- coding: utf-8 -*-
"""
Out-of-sample test of the rotation parameters + the weight no-trade band -- FND-0193 and FND-0207 (PIT side).

Two things the evaluations asked for and the backtest could not do:

  1. FND-0207: `apply_weight_no_trade_band(W, delta)`: a fund held in two consecutive months keeps last month's weight
     when the new target is within `delta` of it (re-weighting a held fund is 28-56% of the turnover that is left once the
     selection is made sticky). A fund that leaves the portfolio is sold fully, a new fund enters at its target, and the
     difference between the frozen and the target total stays in cash (never leverage: a total above 1 is scaled down).

  2. FND-0193: `band_grid_tables` runs the whole (hysteresis band x weight band) grid on the same scores and returns one
     backtest table per cell; `oos_report` chooses the cell ONLY on the design period (dates <= `split`), by net Sharpe with
     a parsimony rule (among cells within `tolerance` of the best Sharpe, the lowest turnover), and reports how that choice
     and the fixed alternatives did on the blind period (dates > `split`), with a paired bootstrap interval of the Sharpe
     difference against a reference cell (backtest_inference). Monthly returns of month t are realised in t+1, so design
     and blind never share a return.

  3. FND-0217: `cadence_tables` / `cadence_report`: rebalance every k months (1, 2, 3, 4, 6, 12) with the portfolio held in
     between. Every PHASE of each cadence is run (which months you rebalance in is luck for k > 1) and the statistics are
     reported on the phase-averaged return series with the spread across phases; the cadence is chosen on the design period
     only and compared on the blind one against monthly rebalancing (paired bootstrap).

`load_saved_run` rebuilds the inputs of a saved PIT run (scores from scores_long.parquet, NAV / attributes / rates / regime
history from the DB, read-only) so these tests run in minutes without re-scoring.

Nothing here touches the live builder; the live builder reads a chosen cell later (FND-0220).
"""

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.backtest_inference import (deflated_sharpe_ratio, effective_sample_size, paired_sharpe_difference,
                                              probabilistic_sharpe_ratio)
from proyecto3.src.pit_backtest import FORWARD_WINDOWS, assemble_table, build_weights, prepare_pit_backtest, series_stats
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS

CADENCES = (1, 2, 3, 4, 6, 12)
HYSTERESIS_BANDS = (0.0, 0.05, 0.10, 0.20)
WEIGHT_BANDS = (0.0, 0.01, 0.02, 0.03)
REFERENCE_CELL = (0.0, 0.0)               # every month a fresh selection, weights at target: today's behaviour


def apply_weight_no_trade_band(W: pd.DataFrame, delta: float) -> pd.DataFrame:
    """Master weights (dates x funds, NaN = not held) with the no-trade band applied month by month. delta in master-weight
    units (0.02 = 2% of the portfolio). delta <= 0 returns W unchanged. See the module docstring for the rule."""
    if delta is None or delta <= 0:
        return W
    values = W.to_numpy(dtype=float)
    out = values.copy()
    prev = np.zeros(values.shape[1])
    for i in range(values.shape[0]):
        target = values[i]
        held_now = np.isfinite(target) & (target > 0)
        if not held_now.any():                                  # empty month: nothing held, the next one enters from cash
            prev = np.zeros_like(prev)
            continue
        eff = np.where(held_now, target, np.nan)
        keep = held_now & (prev > 0) & (np.abs(target - prev) < delta)
        eff[keep] = prev[keep]
        total = np.nansum(eff)
        if total > 1.0:                                         # frozen weights above their targets must not lever the book
            eff = eff / total
        out[i] = eff
        prev = np.nan_to_num(eff, nan=0.0)
    return pd.DataFrame(out, index=W.index, columns=W.columns)


def band_grid_tables(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex, bands=HYSTERESIS_BANDS,
                     deltas=WEIGHT_BANDS, tx_cost_bps: float = 25.0, entry_sides: int = 1, windows=FORWARD_WINDOWS,
                     max_stale_days: int = 45, target_annual: "pd.Series | None" = None) -> dict:
    """{(hysteresis_band, weight_band): backtest table}. Scores, forward returns and benchmark are shared by every cell."""
    base = prepare_pit_backtest(pit_run, inputs, regime_hist, at, windows, max_stale_days)
    tables = {}
    for b in bands:
        if b == 0:
            Wb = base.W
        else:
            cons = dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=b)
            Wb, _ = build_weights(pit_run.scores, inputs.attrs, regime_hist, base.at, cons, use_incumbents=True)
        for d in deltas:
            W = apply_weight_no_trade_band(Wb, d)
            held = W.notna().any(axis=1)
            cash_w = (1.0 - W.sum(axis=1)).where(held)
            prep = dataclasses.replace(base, W=W, cash_w=cash_w)
            tables[(b, d)] = assemble_table(prep, tx_cost_bps, entry_sides, target_annual)
    return tables


def _excess(series: pd.Series, cash: "pd.Series | None") -> pd.Series:
    return series - (cash.reindex(series.index).fillna(0.0) if cash is not None else 0.0)


def selection_diagnostics(design_series: dict, chosen, blind_series: pd.Series) -> dict:
    """Caveats that go with a choice made among several candidates: the DEFLATED Sharpe of the chosen candidate on the data it
    was selected on (probability its true Sharpe beats the best of N noise strategies; per-period units), the probabilistic
    Sharpe against zero, and the effective number of independent months in the blind period.
    design_series: {candidate: excess return series on the design period}."""
    sharpes = {k: (s.mean() / s.std(ddof=1)) for k, s in design_series.items() if s.std(ddof=1) > 1e-12}
    n = len(sharpes)
    var = float(np.var(list(sharpes.values()), ddof=1)) if n > 1 else 0.0
    return {"n_trials": n, "design_sharpe_variance": var,
            "design_psr_vs_zero": probabilistic_sharpe_ratio(design_series[chosen].to_numpy()),
            "design_deflated_sharpe": deflated_sharpe_ratio(design_series[chosen].to_numpy(), n, var),
            "blind_months": int(blind_series.notna().sum()), "blind_effective_months": effective_sample_size(blind_series.to_numpy())}


def period_stats(table: pd.DataFrame, start=None, end=None) -> dict:
    """series_stats of the chained 1m net series on [start, end] plus the mean monthly turnover and the 12m excess."""
    t = table.loc[start:end] if (start is not None or end is not None) else table
    st = series_stats(t["net_turnover_1m"], t["cash_ret_1m"] if "cash_ret_1m" in t else None)
    e12 = t["excess_12m"].dropna() if "excess_12m" in t else pd.Series(dtype=float)
    return {**st, "mean_turnover": float(t["turnover"].mean()), "mean_excess_12m": float(e12.mean()) if len(e12) else np.nan}


def select_cell(design: pd.DataFrame, tolerance: float = 0.02) -> tuple:
    """Cell with the best design Sharpe; among the cells within `tolerance` of it, the one with the lowest turnover
    (parsimony: a more complex setting must earn its place). design: index (band, delta), columns sharpe, mean_turnover."""
    d = design.dropna(subset=["sharpe"])
    near = d[d["sharpe"] >= d["sharpe"].max() - tolerance]
    return near["mean_turnover"].idxmin()


def oos_report(tables: dict, split, reference=REFERENCE_CELL, tolerance: float = 0.02, n_boot: int = 2000,
               mean_block: float = 6.0) -> dict:
    """{'grid': per cell x period statistics, 'chosen': cell picked on the design period, 'blind': the blind-period
    comparison of the chosen cell, the reference and every fixed cell against the reference (paired bootstrap)}."""
    split = pd.Timestamp(split)
    rows = []
    for cell, tab in tables.items():
        for period, (lo, hi) in {"design": (None, split), "blind": (split + pd.offsets.MonthEnd(1), None)}.items():
            rows.append({"hysteresis_band": cell[0], "weight_band": cell[1], "period": period, **period_stats(tab, lo, hi)})
    grid = pd.DataFrame(rows)
    des = grid[grid["period"] == "design"].set_index(["hysteresis_band", "weight_band"])
    chosen = select_cell(des, tolerance)
    ref_tab = tables[reference]
    cash = ref_tab["cash_ret_1m"] if "cash_ret_1m" in ref_tab else None
    lo = split + pd.offsets.MonthEnd(1)
    blind = []
    for cell, tab in tables.items():
        cmp = paired_sharpe_difference(tab["net_turnover_1m"].loc[lo:], ref_tab["net_turnover_1m"].loc[lo:],
                                       cash.loc[lo:] if cash is not None else None, n_boot=n_boot, mean_block=mean_block)
        blind.append({"hysteresis_band": cell[0], "weight_band": cell[1], "is_chosen": cell == chosen,
                      "is_reference": cell == reference, **cmp})
    design_series = {c: _excess(t["net_turnover_1m"].loc[:split].dropna(), t["cash_ret_1m"] if "cash_ret_1m" in t else None)
                     for c, t in tables.items()}
    diag = selection_diagnostics(design_series, chosen, _excess(tables[chosen]["net_turnover_1m"].loc[lo:].dropna(),
                                                                tables[chosen]["cash_ret_1m"] if "cash_ret_1m" in tables[chosen] else None))
    return {"grid": grid, "chosen": chosen, "blind": pd.DataFrame(blind), "diagnostics": diag}


# ============================================================
# FND-0217: rebalance cadence
# ============================================================

def cadence_tables(pit_run, inputs, regime_hist: pd.DataFrame, at: pd.DatetimeIndex, cadences=CADENCES,
                   hysteresis_band: float = 0.0, tx_cost_bps: float = 25.0, entry_sides: int = 1,
                   max_stale_days: int = 45, target_annual: "pd.Series | None" = None,
                   regime_trigger=False) -> dict:
    """{(k, phase): backtest table} for every cadence k and every phase 0..k-1 (1m window only). Scores, forward returns and
    benchmark are shared; only the held portfolios differ. hysteresis_band > 0 gives the incumbents their bonus at each
    rebalance (the incumbents are the portfolio held at the previous rebalance). regime_trigger: also rebalance at once when
    the regime label changes (True = any change; a collection of regime names = only into / out of those regimes)."""
    base = prepare_pit_backtest(pit_run, inputs, regime_hist, at, (1,), max_stale_days)
    cons = dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=hysteresis_band) if hysteresis_band else DEFAULT_CONSTRAINTS
    tables = {}
    for k in cadences:
        for phase in range(k):
            W, cash_w = build_weights(pit_run.scores, inputs.attrs, regime_hist, base.at, cons,
                                      use_incumbents=bool(hysteresis_band), rebalance_every=k, phase=phase,
                                      regime_trigger=regime_trigger)
            prep = dataclasses.replace(base, W=W, cash_w=cash_w)
            tables[(k, phase)] = assemble_table(prep, tx_cost_bps, entry_sides, target_annual)
    return tables


def _phase_average(tables: dict, k: int) -> "tuple[pd.Series, pd.Series, float]":
    """(phase-averaged net 1m series, cash series, mean turnover) of cadence k."""
    parts = [t for (kk, _), t in tables.items() if kk == k]
    series = pd.concat([t["net_turnover_1m"] for t in parts], axis=1).mean(axis=1, skipna=True)
    series.name = "net_turnover_1m"
    turnover = float(np.mean([t["turnover"].mean() for t in parts]))
    return series, parts[0]["cash_ret_1m"], turnover


def cadence_report(tables: dict, split, reference_k: int = 1, tolerance: float = 0.02, n_boot: int = 2000,
                   mean_block: float = 6.0) -> dict:
    """{'grid': per cadence x period statistics of the phase-averaged series (+ the Sharpe range across phases),
    'chosen': cadence picked on the design period only (best Sharpe, then lowest turnover within `tolerance`),
    'blind': blind-period Sharpe difference of every cadence vs `reference_k` (paired bootstrap, phase-averaged)}."""
    split = pd.Timestamp(split)
    lo = split + pd.offsets.MonthEnd(1)
    cadences = sorted({k for k, _ in tables})
    rows, avg = [], {}
    for k in cadences:
        series, cash, turnover = _phase_average(tables, k)
        avg[k] = (series, cash)
        for period, (a, b) in {"design": (None, split), "blind": (lo, None)}.items():
            seg = series.loc[a:b] if (a is not None or b is not None) else series
            st = series_stats(seg, cash.reindex(seg.index))
            phases = [series_stats(t["net_turnover_1m"].loc[a:b], t["cash_ret_1m"].loc[a:b])["sharpe"]
                      for (kk, _), t in tables.items() if kk == k]
            rows.append({"cadence_months": k, "period": period, **st, "mean_turnover": turnover,
                         "sharpe_phase_min": float(np.nanmin(phases)), "sharpe_phase_max": float(np.nanmax(phases)),
                         "n_phases": len(phases)})
    grid = pd.DataFrame(rows)
    des = grid[grid["period"] == "design"].dropna(subset=["sharpe"]).set_index("cadence_months")
    near = des[des["sharpe"] >= des["sharpe"].max() - tolerance]
    chosen = int(near["mean_turnover"].idxmin())
    ref_series, ref_cash = avg[reference_k]
    blind = []
    for k in cadences:
        series, _ = avg[k]
        cmp = paired_sharpe_difference(series.loc[lo:], ref_series.loc[lo:], ref_cash.loc[lo:], n_boot=n_boot, mean_block=mean_block)
        blind.append({"cadence_months": k, "is_chosen": k == chosen, "is_reference": k == reference_k, **cmp})
    design_series = {k: _excess(avg[k][0].loc[:split].dropna(), avg[k][1]) for k in cadences}
    diag = selection_diagnostics(design_series, chosen, _excess(avg[chosen][0].loc[lo:].dropna(), avg[chosen][1]))
    return {"grid": grid, "chosen": chosen, "blind": pd.DataFrame(blind), "diagnostics": diag}


# ============================================================
# saved PIT runs
# ============================================================

def load_saved_run(run_dir, conn):
    """(pit_run-like with .scores, PitInputs, regime history, evaluation dates) of a saved p3_pit_backtest run folder.
    Reads scores_long.parquet and pit_backtest_table.csv from the folder and NAV / attributes / deposit rate / regime history
    from the DB (read-only). ipc is not needed by the portfolio / return layers, so it is left empty."""
    from types import SimpleNamespace
    from proyecto3.src.pit_inputs import load_attributes, load_nav_panel, load_rate_deposit
    from proyecto3.src.pit_run import PitInputs
    from proyecto3.src.regime_classifier import RegimeClassifier
    from shared.config import REGIME_PUBLICATION_LAG_MONTHS
    run = Path(run_dir)
    scores = pd.read_parquet(run / "scores_long.parquet")
    at = pd.DatetimeIndex(pd.read_csv(run / "pit_backtest_table.csv", parse_dates=["date"])["date"])
    hist = RegimeClassifier(conn, publication_lags=REGIME_PUBLICATION_LAG_MONTHS).classify_historical().sort_index()
    inputs = PitInputs(nav=load_nav_panel(conn), attrs=load_attributes(conn), ipc=None, rate=load_rate_deposit(conn))
    return SimpleNamespace(scores=scores), inputs, hist, at
