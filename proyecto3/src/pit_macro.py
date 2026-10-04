# proyecto3/src/pit_macro.py
# -*- coding: utf-8 -*-
"""
Point-in-time scorer group B, macro part -- FND-0224.

PIT v1 (pit_candidates.py) leaves the scorer's group B metrics (macro betas, macro_r2, ...) absent, so every regime
multiplier that needs them is neutral in the backtest. This module recomputes the macro-regression part with the
SAME production function (proyecto2 macro_sensitivity.compute_macro_sensitivity, never a second implementation:
P#11 / R-1) on information available at each evaluation date:

  * the fund's month-end NAV observations <= t (expanding window);
  * the macro factors published by t: a factor whose source is published with a lag (CPI, CLI, M3: the regime
    classifier's REGIME_PUBLICATION_LAG_MONTHS) has its last `lag` months masked;
  * quarterly evaluation dates by default (step_months=3): the betas of the last quarter-end carry forward, which
    uses only past information. Cost about 12 ms per fund and date, so the full universe at quarterly dates is
    roughly one hour single-process; callers cache the result (pit_run).

Variants (FND-0224, option 1: experiments only, never a live change):
  control       production semantics exactly (single-pass VIF), the control group;
  iterative_hy  iterative VIF with spread_hy and vix_yoy protected (the two crisis factors), spread_ig excluded,
                at most n_obs/10 factors, and
                +/-inf macro cells turned into NaN (FND-0196 / FND-0226 candidates).

Not here yet: fx_contribution_pct, crisis stress scores and per-regime statistics (later phases of FND-0224).
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto2.src.calculations.macro_sensitivity import MIN_OBS, compute_macro_sensitivity
from shared.config import MACRO_ITERATIVE_VIF, REGIME_PUBLICATION_LAG_MONTHS

logger = logging.getLogger(__name__)

MACRO_METRICS = ("beta_oil", "beta_rate_eu", "beta_spread_hy", "beta_vix", "macro_r2")

VARIANTS = {
    "control": {},
    "iterative_hy": {                                    # the options P2 uses when MACRO_VIF_ITERATIVE_ENABLED (one source)
        "vif_mode": "iterative",
        "extra_priority": frozenset(MACRO_ITERATIVE_VIF["extra_priority"]),
        "exclude": frozenset(MACRO_ITERATIVE_VIF["exclude"]),
        "max_factors_per_obs": MACRO_ITERATIVE_VIF["max_factors_per_obs"],
        "clean_inf": True,
    },
}


def variant_signature(variant: str) -> dict:
    """Deterministic description of a variant's settings (sets as sorted lists) for cache keys: the key must change
    when the SETTINGS change, not only the name, and a frozenset repr is not stable across processes."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown macro variant {variant!r}; choose one of {sorted(VARIANTS)}")
    return {"name": variant, **{k: (sorted(v) if isinstance(v, (set, frozenset)) else v) for k, v in VARIANTS[variant].items()}}


def factor_lag_months(column: str, lags: dict) -> int:
    """Publication lag (months) of a macro factor column, from the regime classifier's lag table."""
    if column.startswith("ipc_yoy"):
        return int(lags.get("ipc_index", 0))
    if column.startswith("cli_yoy"):
        return int(lags.get("cli", 0))
    if column == "m3_yoy":
        return int(lags.get("m3_yoy", 0))
    return 0


def visible_macro(macro: pd.DataFrame, t: pd.Timestamp, lags: dict) -> pd.DataFrame:
    """The macro factor frame as known at month-end t: rows after t dropped, and for each lagged factor the rows
    newer than t - lag months masked (not yet published)."""
    out = macro.loc[:t].copy()
    for col in out.columns:
        lag = factor_lag_months(col, lags)
        if lag:
            cutoff = t - pd.offsets.MonthEnd(lag)
            out.loc[out.index > cutoff, col] = np.nan
    return out


def _month_end_series(s: pd.Series) -> pd.Series:
    s = s.dropna()
    s.index = pd.DatetimeIndex(s.index) + pd.offsets.MonthEnd(0)
    return s[~s.index.duplicated(keep="last")].sort_index()


def expanding_macro_metrics(
    nav: pd.DataFrame,
    macro: pd.DataFrame,
    at: pd.DatetimeIndex,
    fund_attrs: "pd.DataFrame | None" = None,
    lags: "dict | None" = None,
    variant: str = "control",
    step_months: int = 3,
    workers: int = 1,
) -> dict:
    """{metric: DataFrame(evaluation dates x isin)} for MACRO_METRICS.

    nav: raw-date wide monthly NAV panel (pit_inputs.load_nav_panel); macro: macro_sensitivity.load_macro_factors();
    at: evaluation month-ends; fund_attrs: index isin, optional columns geography / development_status (the same
    inputs the P2 pipeline passes). Dates with month % step_months != 0 are skipped (the scorer carries the last
    value forward, see pit_candidates.pit_scores). workers > 1 splits the funds over processes (the regressions are
    independent per fund and date, so the result is identical to workers=1)."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown macro variant {variant!r}; choose one of {sorted(VARIANTS)}")
    if workers > 1 and nav.shape[1] > 1:
        return _parallel_macro_metrics(nav, macro, at, fund_attrs, lags, variant, step_months, workers)
    opts = dict(VARIANTS[variant])
    clean_inf = opts.pop("clean_inf", False)
    lags = REGIME_PUBLICATION_LAG_MONTHS if lags is None else lags
    at = pd.DatetimeIndex(at)
    eval_at = at[(at.month % step_months) == 0] if step_months > 1 else at
    base = macro.replace([np.inf, -np.inf], np.nan) if clean_inf else macro

    out = {m: pd.DataFrame(np.nan, index=eval_at, columns=nav.columns) for m in MACRO_METRICS}
    series = {isin: _month_end_series(nav[isin]) for isin in nav.columns}
    geo = fund_attrs["geography"] if fund_attrs is not None and "geography" in fund_attrs.columns else None
    dev = fund_attrs["development_status"] if fund_attrs is not None and "development_status" in fund_attrs.columns else None

    for t in eval_at:
        m_t = visible_macro(base, t, lags)
        if m_t.empty:
            continue
        for isin, s_all in series.items():
            s = s_all.loc[:t]
            if len(s) <= MIN_OBS:
                continue
            frame = pd.DataFrame({"date": s.index, "nav": s.to_numpy()})
            res = compute_macro_sensitivity(
                frame, m_t,
                geography=None if geo is None else _clean(geo.get(isin)),
                development_status=None if dev is None else _clean(dev.get(isin)),
                **opts)
            for name, value, _ in res:
                if name in out and value is not None:
                    out[name].at[t, isin] = value
    return out


def _chunk_task(args) -> dict:
    nav, macro, at, fund_attrs, lags, variant, step_months = args
    return expanding_macro_metrics(nav, macro, at, fund_attrs, lags, variant, step_months, workers=1)


def _parallel_macro_metrics(nav, macro, at, fund_attrs, lags, variant, step_months, workers) -> dict:
    from concurrent.futures import ProcessPoolExecutor
    n_chunks = min(nav.shape[1], workers * 4)
    tasks = []
    for idx in np.array_split(np.arange(nav.shape[1]), n_chunks):
        cols = list(nav.columns[idx])
        attrs = None if fund_attrs is None else fund_attrs.reindex(cols)
        tasks.append((nav[cols], macro, at, attrs, lags, variant, step_months))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        parts = list(pool.map(_chunk_task, tasks))
    return {m: pd.concat([p[m] for p in parts], axis=1).reindex(columns=nav.columns) for m in MACRO_METRICS}


def _clean(value):
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else value


def load_macro_fund_attrs(conn, isins: "list | None" = None) -> pd.DataFrame:
    """isin -> geography / development_status from fund_master (what the P2 pipeline hands to the regression)."""
    if isins is None:
        rows = conn.execute("SELECT isin, geography, development_status FROM fund_master").fetchall()
    else:
        rows = conn.execute("SELECT isin, geography, development_status FROM fund_master WHERE isin = ANY(%s)",
                            (list(isins),)).fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=["isin", "geography", "development_status"]).set_index("isin")
