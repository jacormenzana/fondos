# proyecto3/src/pit_metrics.py
# -*- coding: utf-8 -*-
"""
Point-in-time (PIT) fund metrics for the P3 backtester -- FND-0159, Wave B step d1a.

For every month-end t the scorer's inputs are recomputed from the NAV observed up to t ONLY (an
expanding window), instead of reading today's `since_inception` values from fund_metrics (which use
the whole history -> look-ahead). Pure: no DB, no pipeline imports; the caller supplies a wide NAV
panel (month-end dates x ISINs, NaN where a fund has no observation) and the IPC / risk-free series.

Definitions are P2's, vectorized over time (one numpy pass per fund) instead of re-running P2 per
date, and are pinned to P2's own pure functions by proyecto3/tests/test_pit_metrics.py on every
truncation point (P#11: same maths, no divergent copy -- the test, not this file, is the guarantee):
  * return_ann = (NAV_t / NAV_0) ** (12 / n) - 1 with n = observations so far (P2 uses len/12, NOT
    (n-1)/12) -- returns.annualized_return
  * vol_ann    = std(simple monthly returns, ddof=1) * sqrt(12), needs >= 2 returns
  * sharpe     = (return_ann - rf_t) / vol_ann, rf_t = deposit rate as of the month of t
    (rolling_stats.resolve_rf_rate semantics: month-end align, ffill, bfill before the series)
  * max_dd     = min over the expanding drawdown vs running max
  * *_real     = same on NAV deflated by IPC as of t (deflation.deflate_nav: backward as-of)
  * srri_nav   = ESMA bucket of vol_ann (srri.compute_srri: needs 13 NAV, 0 if vol > 5.0)

Dates: P2 works on the RAW NAV dates (most funds report mid/late month, not month-end) and aligns IPC
by backward as-of on that raw date and rf on the month-end of it; this module does the same with
whatever DatetimeIndex it is given, and is bit-identical to P2 on raw dates (verified on a live sample:
stored since_inception max_dd/return_ann/vol_ann/sharpe/srri_nav/return_ann_real all equal). Feeding a
month-end-normalized panel (as Backtester._load_nav_matrix does) shifts the IPC as-of by up to one month
-> return_ann_real differs by ~1e-4 for non-month-end funds; nominal metrics are date-agnostic.

PIT specifics that P2's live path does not need:
  * ipc_lag_months: IPC for month m is only published ~m+2 -> the real series at t deflates with the
    IPC of t - lag (0 = P2-identical, used by the regression pin; the backtester passes
    shared.config.REGIME_PUBLICATION_LAG_MONTHS['ipc_index']).
  * a fund has values only at dates where it has a NAV observation (NaN elsewhere): the PIT universe
    at t is "funds with a value at t".

Not here (later steps): persistence / capture / momentum over the PIT peer set (d1b), short-horizon
gates from daily NAV (d1c).
"""

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import RISK_FREE_RATE_ANN

_P2_SRC = _ROOT / "proyecto2" / "src"
_P2_MODULES: dict = {}

PERIODS_PER_YEAR = 12


def load_p2_calc(name: str, subdir: str = "calculations"):
    """Load a PURE P2 module (calculations: returns, drawdown, deflation, srri, short_horizon, ...;
    utils: validators) by file path.

    P2 modules import each other as `src.calculations.*`, which collides with P3's own `src` package, so
    they are loaded by path as members of a private package (`_p2_<subdir>`; this also resolves their
    relative imports such as `from .deflation import ...`). Modules that import `src.*` absolutely
    (risk_metrics, run_pipeline, ...) cannot be loaded this way."""
    key = (subdir, name)
    if key not in _P2_MODULES:
        pkg = f"_p2_{subdir}"
        if pkg not in sys.modules:
            package = types.ModuleType(pkg)
            package.__path__ = [str(_P2_SRC / subdir)]
            sys.modules[pkg] = package
        full = f"{pkg}.{name}"
        spec = importlib.util.spec_from_file_location(full, _P2_SRC / subdir / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        _P2_MODULES[key] = mod
    return _P2_MODULES[key]


def _srri_tables():
    srri = load_p2_calc("srri")
    edges = np.array([t for t, _ in srri._SRRI_THRESHOLDS], dtype=float)
    buckets = np.array([b for _, b in srri._SRRI_THRESHOLDS], dtype=float)
    return edges, buckets, 5.0                      # 5.0 = srri.compute_srri's _VOL_SANITY_CAP (local there)


METRICS = ("max_dd", "return_ann", "vol_ann", "sharpe", "return_ann_real", "max_dd_real", "srri_nav", "n_obs")


def _month_end(index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(index)) + pd.offsets.MonthEnd(0)


def aligned_rf(dates: pd.DatetimeIndex, rf: "pd.DataFrame | None", fallback: float = RISK_FREE_RATE_ANN) -> np.ndarray:
    """Risk-free rate (decimal) as of each date: month-end align, ffill, bfill before the series start
    (resolve_rf_rate semantics). `rf` has columns date, rate. Constant `fallback` when rf is None/empty."""
    if rf is None or len(rf) == 0:
        return np.full(len(dates), fallback, dtype=float)
    s = rf.copy()
    s["date"] = _month_end(s["date"])
    s = s.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["rate"].astype(float)
    targets = _month_end(dates)                         # resolve_rf_rate aligns the target date to month-end
    aligned = s.reindex(s.index.union(targets)).sort_index().ffill().bfill()
    return aligned.reindex(targets).to_numpy()


def first_observable_date(rf: "pd.DataFrame | None", ipc: "pd.DataFrame | None", lag_months: int = 0) -> "pd.Timestamp | None":
    """First month-end at which BOTH the risk-free series and the (publication-lagged) IPC are observable; None when
    neither exists. Before it, aligned_rf / aligned_ipc fall back to bfill (the earliest value, P2's documented
    contract), which for an EVALUATION date earlier than this would use data that did not exist yet."""
    firsts = []
    if rf is not None and len(rf):
        firsts.append(_month_end(rf["date"]).min())
    if ipc is not None and len(ipc):
        firsts.append(_month_end(ipc["date"]).min() + pd.offsets.MonthEnd(int(lag_months)))
    return max(firsts) if firsts else None


def assert_series_cover(at: pd.DatetimeIndex, rf: "pd.DataFrame | None", ipc: "pd.DataFrame | None", lag_months: int = 0) -> None:
    """FND-0228: refuse to evaluate a date before the rf / IPC series are observable (the bfill would leak the first value
    backwards). Dates after it are safe: the bfill then only reaches NAV history older than the series."""
    first = first_observable_date(rf, ipc, lag_months)
    if first is not None and len(at) and pd.DatetimeIndex(at).min() < first:
        raise ValueError(f"PIT evaluation date {pd.DatetimeIndex(at).min().date()} precedes the first month at which the "
                         f"risk-free rate and the lagged IPC are both observable ({first.date()}): the backward fill of "
                         f"aligned_rf / aligned_ipc would use a later value. Start the backtest on or after {first.date()}.")


def aligned_ipc(dates: pd.DatetimeIndex, ipc: "pd.DataFrame | None", lag_months: int = 0) -> "np.ndarray | None":
    """IPC index observable at each date: backward as-of (deflate_nav semantics, bfill before the series),
    after delaying the IPC by `lag_months` (publication lag). None when there is no IPC."""
    if ipc is None or len(ipc) == 0:
        return None
    s = ipc[["date", "ipc_index"]].copy()
    s["date"] = _month_end(s["date"])
    if lag_months:
        s["date"] = s["date"] + pd.offsets.MonthEnd(int(lag_months))
    s = s.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["ipc_index"].astype(float)
    out = s.reindex(s.index.union(dates)).sort_index().ffill().bfill().reindex(dates).to_numpy()
    return out


def _fund_expanding(x: np.ndarray, rf_t: np.ndarray, ipc_t: "np.ndarray | None") -> dict:
    """Expanding metrics of ONE fund over its consecutive observations x (all finite, > 0)."""
    m = len(x)
    n = np.arange(1, m + 1, dtype=float)
    nan = np.full(m, np.nan)
    out = {k: nan.copy() for k in METRICS}
    out["n_obs"] = n

    cm = np.maximum.accumulate(x)
    out["max_dd"] = np.minimum.accumulate(x / cm - 1.0)

    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if m >= 2:
            out["return_ann"][1:] = (x[1:] / x[0]) ** (PERIODS_PER_YEAR / n[1:]) - 1.0

            r = x[1:] / x[:-1] - 1.0                           # simple monthly returns (pct_change)
            k = np.arange(1, m, dtype=float)                   # returns available at obs index 1..m-1
            rc = r - r.mean()                                  # variance is shift-invariant; centring avoids cancellation
            cs1, cs2 = np.cumsum(rc), np.cumsum(rc * rc)
            var = np.full(m - 1, np.nan)
            ok = k >= 2
            var[ok] = np.maximum((cs2[ok] - cs1[ok] ** 2 / k[ok]) / (k[ok] - 1.0), 0.0)
            vol = np.sqrt(var) * np.sqrt(PERIODS_PER_YEAR)
            out["vol_ann"][1:] = vol

            sharpe = (out["return_ann"] - rf_t) / out["vol_ann"]
            sharpe[~np.isfinite(sharpe) | (out["vol_ann"] == 0)] = np.nan
            out["sharpe"] = sharpe

            edges, buckets, cap = _srri_tables()
            vol_full = out["vol_ann"]
            srri = np.zeros(m)
            valid = (n >= 13) & np.isfinite(vol_full) & (vol_full <= cap)
            idx = np.searchsorted(edges, vol_full[valid], side="right") - 1
            srri[valid] = buckets[np.clip(idx, 0, len(buckets) - 1)]
            out["srri_nav"] = srri
        else:
            out["srri_nav"] = np.zeros(m)

        if ipc_t is not None:
            y = x / ipc_t                                      # deflated NAV up to a constant (base cancels)
            out["max_dd_real"] = np.minimum.accumulate(y / np.maximum.accumulate(y) - 1.0)
            if m >= 2:
                out["return_ann_real"][1:] = (y[1:] / y[0]) ** (PERIODS_PER_YEAR / n[1:]) - 1.0
    return out


def expanding_risk_metrics(
    nav_wide: pd.DataFrame,
    ipc: "pd.DataFrame | None" = None,
    rf: "pd.DataFrame | None" = None,
    ipc_lag_months: int = 0,
    rf_fallback: float = RISK_FREE_RATE_ANN,
) -> dict:
    """Expanding-window risk metrics for every fund and month-end.

    nav_wide: DatetimeIndex (month-end, ascending) x ISIN, NaN where the fund has no observation.
    ipc: DataFrame[date, ipc_index] (load_ipc); rf: DataFrame[date, rate] decimal (load_rf_rate).
    Returns {metric: DataFrame shaped like nav_wide}, values only at dates where the fund has a NAV.
    A fund's series starts at its first valid NAV; internal gaps are skipped exactly as P2 does (it
    works on the fund's own dropna'd observations)."""
    dates = pd.DatetimeIndex(nav_wide.index)
    rf_t = aligned_rf(dates, rf, rf_fallback)
    ipc_t = aligned_ipc(dates, ipc, ipc_lag_months)
    arrays = {k: np.full(nav_wide.shape, np.nan) for k in METRICS}

    values = nav_wide.to_numpy(dtype=float)
    for j in range(values.shape[1]):
        col_vals = values[:, j]
        mask = np.isfinite(col_vals) & (col_vals > 0)
        if not mask.any():
            continue
        pos = np.flatnonzero(mask)
        fund = _fund_expanding(col_vals[pos], rf_t[pos], None if ipc_t is None else ipc_t[pos])
        for k in METRICS:
            arrays[k][pos, j] = fund[k]
    return {k: pd.DataFrame(a, index=nav_wide.index, columns=nav_wide.columns) for k, a in arrays.items()}
