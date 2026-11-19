# proyecto3/src/pit_cost_check.py
# -*- coding: utf-8 -*-
"""The cost check of roadmap v4 (doc/backlog/PKG_L_p3_alignment.md, FND-0430): how the value of turnover reduction scales with the cost of trading.

Why. Every PIT figure charges a flat 25 bp per side, while the eligible funds carry stated entry fees of median 2.0 % and mean 2.3 %. At 25 bp a hysteresis band saves about as much cost as the gross return it loses (band 0.10 on the
2026-11-19 run: +0.53 pp of cost saved, -0.54 pp of gross), so its net effect is about zero BY STRUCTURE and scales with the true cost. These pure functions turn a gross monthly series and its turnover into net series for ANY
cost, price the trades fund by fund with stated fees, and find the break-even cost at which a lower-turnover variant overtakes the baseline. PURE (pandas / numpy; the weights come from pit_backtest.build_weights)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from proyecto3.src.pit_backtest import series_stats

DEFAULT_GRID_BPS = (25.0, 50.0, 100.0, 200.0)


def net_series(gross: pd.Series, turnover: pd.Series, bps: float) -> pd.Series:
    """Monthly net return at `bps` per side of traded notional: gross - bps/1e4 x turnover (the chained-series convention of pit_backtest.assemble_table)."""
    return gross - bps / 1e4 * turnover


def grid_stats(gross: pd.Series, turnover: pd.Series, cash: pd.Series, bps_grid=DEFAULT_GRID_BPS) -> dict:
    """{bps: series_stats of the net series} (annual return, volatility, Sharpe against `cash`, max drawdown) for every cost of the grid."""
    return {float(b): series_stats(net_series(gross, turnover, b), cash) for b in bps_grid}


def fee_cost_series(W: pd.DataFrame, entry: pd.Series, exit_: pd.Series, default_entry: float, default_exit: float) -> "tuple[pd.Series, dict]":
    """Monthly cost of the trades implied by the target weights `W` (dates x isin, NaN = not held), fund by fund: entry fee x purchases + exit fee x sales, with the first month entering from cash and a month after an
    empty month too (the convention of pit_backtest.monthly_turnover). `entry` / `exit_` are fractions by ISIN (0.02 = 2 %); a fund without a value takes the stated default and is COUNTED, never silently filled.
    Returns (cost series, {"funds": n, "missing_entry": n, "missing_exit": n})."""
    w = np.nan_to_num(W.to_numpy(dtype=float), nan=0.0)
    prev = np.vstack([np.zeros((1, w.shape[1])), w[:-1]])
    d = w - prev
    cols = W.columns
    e = entry.reindex(cols).to_numpy(dtype=float)
    x = exit_.reindex(cols).to_numpy(dtype=float)
    miss_e, miss_x = ~np.isfinite(e), ~np.isfinite(x)
    e = np.where(miss_e, default_entry, e)
    x = np.where(miss_x, default_exit, x)
    cost = (np.clip(d, 0, None) * e).sum(axis=1) + (np.clip(-d, 0, None) * x).sum(axis=1)
    held = W.notna().any(axis=1).to_numpy()
    return pd.Series(np.where(held, cost, np.nan), index=W.index), {"funds": int(len(cols)), "missing_entry": int(miss_e.sum()), "missing_exit": int(miss_x.sum())}


def break_even_bps(gross_a: pd.Series, turnover_a: pd.Series, gross_b: pd.Series, turnover_b: pd.Series, cash: pd.Series, metric: str = "ann_return",
                   max_bps: float = 400.0, step: float = 1.0) -> "float | None":
    """The smallest cost per side (bp) at which variant A (the lower-turnover one) is at least as good as the baseline B on `metric` ("ann_return" or "sharpe"): 0.0 when A already wins at no cost, None when it never does
    up to `max_bps`. Net returns are linear in the cost, so a scan is exact up to the step."""
    if metric not in ("ann_return", "sharpe"):
        raise ValueError(f"metric must be 'ann_return' or 'sharpe', not {metric!r}")
    for b in np.arange(0.0, max_bps + step, step):
        a = series_stats(net_series(gross_a, turnover_a, b), cash)[metric]
        base = series_stats(net_series(gross_b, turnover_b, b), cash)[metric]
        if np.isfinite(a) and np.isfinite(base) and a >= base:
            return float(b)
    return None
