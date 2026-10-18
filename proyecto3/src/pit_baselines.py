# proyecto3/src/pit_baselines.py
# -*- coding: utf-8 -*-
"""
Simple baselines and ablation inputs for the P3 model-value study -- FND-0193 (pre-registered in doc/decisiones/PREREG_FND-0193.md).

A baseline is only a WEIGHT FRAME (dates x isin, NaN = not held) plus the unallocated cash share. It goes through the SAME return
and cost layer as the model (`pit_backtest.assemble_table`: forward returns, deposit-rate cash leg, 25 bp on the real turnover of its
own weights), so a difference between a baseline and the model comes from selection and weighting, never from accounting.

  * pool_weights      B-POOL / B-STATIC : equal weight over the eligible pool of every sub-portfolio, blended with the regime
                      weights (B-POOL) or with a constant mix (B-STATIC, no regime);
  * rank_weights      B-LOWVOL / B-MOM / B-CHEAP : the k best-ranked eligible funds of every sub-portfolio by a PIT metric, equal
                      weight, optional incumbent bonus (hysteresis) as the model's;
  * balanced_weights  B-6040 : 60% equal-weight eligible equity funds, 40% equal-weight eligible fixed-income funds, no regime;
  * static_regime_hist / flatten_score_detail / multiplier_variant : inputs of the ablations A1-A5.

Pure pandas / numpy, no DB (R-7): callers pass a saved PIT run (`pit_oos.load_saved_run`).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_candidates import asof_snapshot
from shared.regime_taxonomy import REGIME_WEIGHTS

SUB_NAMES = ["Defensiva", "Equilibrada", "Dinamica"]
WEIGHT_COLUMNS = ["weight_defensive", "weight_balanced", "weight_dynamic"]
EQUITY_NATURES = ("Renta Variable",)
FIXED_INCOME_NATURES = ("Renta Fija Flexible", "Renta Fija Corto Plazo")
LOWVOL_WINDOW_M = 36
LOWVOL_MIN_OBS = 24
MOMENTUM_WINDOW_M = 12
DEFAULT_TOP_K = 10


# ============================================================
# Regime mix
# ============================================================

def static_mix() -> tuple:
    """Unweighted mean of the seven REGIME_WEIGHTS rows (Defensiva, Equilibrada, Dinamica): the mix with no regime and no look-ahead
    (it does not use how often each regime occurred)."""
    arr = np.array(list(REGIME_WEIGHTS.values()), dtype=float)
    m = arr.mean(axis=0)
    return tuple(float(x) for x in m)


def static_regime_hist(regime_hist: pd.DataFrame) -> pd.DataFrame:
    """Copy of the regime history whose sub-portfolio weights are the constant static mix (labels untouched: they are reporting only)."""
    out = regime_hist.copy()
    for col, w in zip(WEIGHT_COLUMNS, static_mix()):
        out[col] = w
    return out


def _sub_weights_at(hist: pd.DataFrame, t) -> "dict | None":
    pos = hist.index.searchsorted(t, side="right") - 1
    if pos < 0:
        return None
    h = hist.iloc[pos]
    return dict(zip(SUB_NAMES, (float(h[WEIGHT_COLUMNS[0]]), float(h[WEIGHT_COLUMNS[1]]), float(h[WEIGHT_COLUMNS[2]]))))


def _finish(rows: dict, at: pd.DatetimeIndex) -> "tuple[pd.DataFrame, pd.Series]":
    W = pd.DataFrame.from_dict(rows, orient="index").reindex(at)
    held = W.notna().any(axis=1)
    cash = (1.0 - W.sum(axis=1, min_count=1)).where(held)
    return W, cash


# ============================================================
# B-POOL / B-STATIC
# ============================================================

def pool_weights(scores: pd.DataFrame, regime_hist: pd.DataFrame, at: pd.DatetimeIndex) -> "tuple[pd.DataFrame, pd.Series]":
    """Equal weight over the ELIGIBLE funds of each sub-portfolio, sub-portfolios blended with the date's regime weights (the rule of
    `pit_backtest.pool_benchmark`, now as investable weights). A fund eligible in two sub-portfolios holds both weights. A sub-portfolio
    with no eligible fund leaves its weight unallocated (cash). Pass `static_regime_hist(hist)` for B-STATIC."""
    hist = regime_hist.sort_index()
    by_date = dict(tuple(scores[scores["eligible"]].groupby("as_of"))) if len(scores) else {}
    rows = {}
    for t in at:
        sw = _sub_weights_at(hist, t)
        if t not in by_date or sw is None:
            rows[t] = {}
            continue
        day = by_date[t]
        row: dict = {}
        for sub, w in sw.items():
            pool = day.loc[day["subportfolio"] == sub, "isin"].unique()
            if len(pool) == 0 or w <= 0:
                continue
            for isin in pool:
                row[isin] = row.get(isin, 0.0) + w / len(pool)
        rows[t] = row
    return _finish(rows, at)


# ============================================================
# Rank baselines (B-LOWVOL, B-MOM, B-CHEAP)
# ============================================================

def trailing_metrics(nav: pd.DataFrame, at: pd.DatetimeIndex, max_stale_days: int) -> dict:
    """PIT trailing metrics from the monthly NAV panel, each dates x funds and using NAV observed at or before the date only:
    vol_36m (annualised std of the monthly returns, >= LOWVOL_MIN_OBS of them, else NaN) and ret_12m."""
    at = pd.DatetimeIndex(at)
    grid = pd.date_range(at.min() - pd.offsets.MonthEnd(LOWVOL_WINDOW_M + 1), at.max(), freq=pd.offsets.MonthEnd())
    snap, _ = asof_snapshot(nav, grid, max_stale_days)
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = snap / snap.shift(1) - 1.0
        ret = ret.where(np.isfinite(ret))
        vol = ret.rolling(LOWVOL_WINDOW_M, min_periods=LOWVOL_MIN_OBS).std(ddof=1) * np.sqrt(12.0)
        mom = snap / snap.shift(MOMENTUM_WINDOW_M) - 1.0
    return {"vol_36m": vol.reindex(at), "ret_12m": mom.where(np.isfinite(mom)).reindex(at)}


def _rank_score(values: pd.Series, higher_is_better: bool) -> pd.Series:
    """Percentile rank in (0, 1], 1 = best; NaN stays NaN (not rankable). Positive by construction, so an incumbent bonus
    `score * (1 + band)` always moves a fund up."""
    v = values.dropna()
    if v.empty:
        return v
    r = v.rank(method="first", ascending=not higher_is_better)       # 1 = best
    return (len(v) - r + 1) / len(v)


def rank_weights(scores: pd.DataFrame, regime_hist: pd.DataFrame, at: pd.DatetimeIndex, metric: pd.DataFrame,
                 higher_is_better: bool, k: int = DEFAULT_TOP_K, hysteresis_band: float = 0.0) -> "tuple[pd.DataFrame, pd.Series]":
    """The k best funds of every sub-portfolio by `metric` (dates x isin, PIT) among the eligible ones, equal weight within the
    sub-portfolio, blended with the regime weights. A fund already chosen in another sub-portfolio that month is skipped (as the
    model's cross-sub exclusion). hysteresis_band > 0: last month's holdings of that sub-portfolio get `rank score * (1 + band)`.
    Funds without a metric value are not rankable. Fewer than k rankable funds -> all of them."""
    hist = regime_hist.sort_index()
    by_date = dict(tuple(scores[scores["eligible"]].groupby("as_of"))) if len(scores) else {}
    rows, incumbents = {}, {}
    for t in at:
        sw = _sub_weights_at(hist, t)
        if t not in by_date or sw is None or t not in metric.index:
            rows[t], incumbents = {}, {}
            continue
        day, m = by_date[t], metric.loc[t]
        used, row, held = set(), {}, {}
        for sub, w in sw.items():
            pool = [i for i in day.loc[day["subportfolio"] == sub, "isin"].unique() if i not in used]
            if not pool or w <= 0:
                continue
            ranked = _rank_score(m.reindex(pool), higher_is_better)
            if ranked.empty:
                continue
            if hysteresis_band and incumbents.get(sub):
                ranked = ranked.where(~ranked.index.isin(incumbents[sub]), ranked * (1.0 + hysteresis_band))
            chosen = ranked.sort_values(ascending=False, kind="mergesort").head(k).index
            for isin in chosen:
                row[isin] = row.get(isin, 0.0) + w / len(chosen)
            used.update(chosen)
            held[sub] = frozenset(chosen)
        rows[t], incumbents = row, held
    return _finish(rows, at)


def cheapest_metric(attrs: pd.DataFrame, at: pd.DatetimeIndex, isins) -> pd.DataFrame:
    """B-CHEAP metric: today's Ongoing_Charge, constant over time (documented look-ahead: stored costs are today's)."""
    oc = pd.to_numeric(attrs["Ongoing_Charge"], errors="coerce").reindex(list(isins))
    return pd.DataFrame(np.tile(oc.to_numpy(dtype=float), (len(at), 1)), index=pd.DatetimeIndex(at), columns=list(isins))


# ============================================================
# B-6040
# ============================================================

def balanced_weights(scores: pd.DataFrame, at: pd.DatetimeIndex, equity: float = 0.60,
                     fixed_income: float = 0.40) -> "tuple[pd.DataFrame, pd.Series]":
    """60% equal weight over the eligible `Renta Variable` funds + 40% over the eligible `Renta Fija Flexible` / `Renta Fija Corto Plazo`
    funds, sub-portfolio membership ignored, static (no regime). An empty sleeve stays in cash."""
    elig = scores[scores["eligible"]]
    by_date = dict(tuple(elig.groupby("as_of"))) if len(elig) else {}
    rows = {}
    for t in at:
        if t not in by_date:
            rows[t] = {}
            continue
        day = by_date[t].drop_duplicates("isin")
        row = {}
        for natures, w in ((EQUITY_NATURES, equity), (FIXED_INCOME_NATURES, fixed_income)):
            pool = day.loc[day["fund_nature"].isin(natures), "isin"].unique()
            for isin in pool:
                row[isin] = row.get(isin, 0.0) + w / len(pool)
        rows[t] = row
    return _finish(rows, at)


# ============================================================
# Ablation inputs (A1-A5)
# ============================================================

def flatten_score_detail(scores: pd.DataFrame) -> pd.DataFrame:
    """The nested `detail` dicts of a --keep-detail run as flat float columns: m_<multiplier> for every Layer-3 factor that fired
    (NaN where it did not) and x_<metric> for the base metrics. Keys: as_of, isin."""
    recs = []
    for as_of, isin, d in zip(scores["as_of"], scores["isin"], scores["detail"]):
        rec = {"as_of": as_of, "isin": isin}
        for k, v in (d.get("mult_detail") or {}).items():
            rec[f"m_{k}"] = float(v)
        for k, v in (d.get("metrics") or {}).items():
            rec[f"x_{k}"] = np.nan if v is None else float(v)
        recs.append(rec)
    return pd.DataFrame.from_records(recs)


def multiplier_variant(scores: pd.DataFrame, detail: "pd.DataFrame | None" = None, keep: "tuple | None" = None,
                       drop: "tuple | None" = None) -> pd.DataFrame:
    """Scores frame with `score_final` recomputed for an ablation. No `detail`: all multipliers removed (A1, score_base only for the
    funds that pass the hard filters). With `detail` (scores_detail.parquet): score_base times the product of the m_* factors in
    `keep` (A5: one multiplier at a time) or of all but `drop`. Excluded funds keep score_final = 0."""
    out = scores.copy()
    base = out["score_base"].astype(float)
    if detail is None:
        factor = pd.Series(1.0, index=out.index)
    else:
        d = detail.set_index(["as_of", "isin"])
        cols = [c for c in d.columns if c.startswith("m_")]
        if keep is not None:
            cols = [c for c in cols if c in {f"m_{k}" for k in keep}]
        if drop is not None:
            cols = [c for c in cols if c not in {f"m_{k}" for k in drop}]
        idx = pd.MultiIndex.from_arrays([out["as_of"], out["isin"]])
        sub = d[cols].reindex(idx)
        factor = pd.Series(sub.fillna(1.0).prod(axis=1).to_numpy(), index=out.index)
    out["score_final"] = np.where(out["eligible"], base * factor, 0.0)
    return out
