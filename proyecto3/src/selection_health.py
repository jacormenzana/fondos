# proyecto3/src/selection_health.py
# -*- coding: utf-8 -*-
"""P3-1.6 -- `selection_health`: standing monitors of the scoring and selection process (roadmap of 2026-11-18, doc/backlog/PKG_L_p3_alignment.md, FND-0430).

Why. Until now the reliability of the selection was checked by one-off studies. These monitors turn the questions "did the ranking move a lot", "is the top concentrated", "did the
eligible set change" into numbers per (date, sub-portfolio), so a change in the process shows up in the month it happens. E-7 (a partial scoring run) is watched here through the
eligible-set change: a run that scored only part of the universe makes it jump.

Calibration mode. A threshold taken from a guess raises false alarms and erodes the zero-touch principle (Directive D-0). Thresholds are therefore EMPIRICAL percentiles of the
same monitors over the point-in-time history (`calibrate`), and the live block stays report-only until the owner has read them. An alert never blocks anything.

Monitors (each per date and sub-portfolio; top set = the `n` best eligible funds by score_final with score > 0, ties by ISIN):
  eligible            number of eligible funds
  eligible_change     |E_t symmetric-difference E_t-1| / |E_t-1|   (the share of the eligible set that entered or left since the previous date)
  top_turnover        1 - |top_t intersect top_t-1| / n_t          (how much of the top changed)
  top_nature_max      the largest number of top funds of one Fund_Nature
  top_manager_max     the largest number of top funds of one manager (needs the manager of each fund)
Not here, because the PIT scores do not carry them: the share of score weight sitting on a missing metric (`nan_weight_share` below works on the metrics frame the live scorer loads) and the
counts of funds under a data hold or a data-suspicion flag (live only). PURE: pandas / numpy, no database.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MONITORS = ("eligible", "eligible_change", "top_turnover", "top_nature_max", "top_manager_max")
QUANTILES = (0.50, 0.90, 0.95, 0.99)


def _top_set(block: pd.DataFrame, n: int) -> list:
    ok = block[block["eligible"] & (block["score_final"] > 0)]
    return ok.sort_values(["score_final", "isin"], ascending=[False, True])["isin"].head(n).tolist()


def health_series(scores: pd.DataFrame, manager: "pd.Series | None" = None, n: int = 10) -> pd.DataFrame:
    """The monitors per (as_of, subportfolio), indexed by those two keys, in time order. `scores`: as_of, subportfolio, isin, fund_nature, eligible, score_final.
    `manager`: Series indexed by ISIN (without it `top_manager_max` is NaN). The first date of each sub-portfolio has no previous date: its change monitors are NaN."""
    rows = []
    for sub, g in scores.groupby("subportfolio", sort=True):
        prev_e, prev_top = None, None
        for as_of, block in sorted(g.groupby("as_of"), key=lambda kv: kv[0]):
            elig = set(block.loc[block["eligible"], "isin"])
            top = _top_set(block, n)
            nat = block.set_index("isin")["fund_nature"].reindex(top).value_counts()
            mgr = manager.reindex(top).value_counts() if manager is not None else None
            rows.append({"as_of": as_of, "subportfolio": sub, "eligible": len(elig),
                         "eligible_change": (len(elig ^ prev_e) / max(len(prev_e), 1)) if prev_e is not None else np.nan,
                         "top_turnover": (1.0 - len(set(top) & set(prev_top)) / max(len(top), 1)) if prev_top is not None and top else np.nan,
                         "top_nature_max": float(nat.max()) if len(nat) else np.nan,
                         "top_manager_max": float(mgr.max()) if mgr is not None and len(mgr) else np.nan})
            prev_e, prev_top = elig, top
    return pd.DataFrame(rows).set_index(["as_of", "subportfolio"]).sort_index()


def calibrate(health: pd.DataFrame, quantiles=QUANTILES) -> pd.DataFrame:
    """Empirical quantiles of every monitor per sub-portfolio over the history (NaN months dropped). Index (subportfolio, monitor), columns q50 / q90 / ..., max and n."""
    out = {}
    for sub, g in health.groupby(level="subportfolio"):
        for m in MONITORS:
            v = g[m].dropna()
            out[(sub, m)] = {**{f"q{int(q * 100)}": float(v.quantile(q)) if len(v) else np.nan for q in quantiles}, "max": float(v.max()) if len(v) else np.nan, "n": int(len(v))}
    return pd.DataFrame(out).T.rename_axis(["subportfolio", "monitor"])


def degenerate(calibration: pd.DataFrame, q: float = 0.95) -> list:
    """The (subportfolio, monitor) pairs whose `q` quantile already equals the largest value ever seen: a threshold there can never be exceeded, so the monitor cannot fire.
    Measured on the 2026-11-17 PIT run: `top_nature_max` of the RAW top 10 (the nature bonus and the within-nature percentiles make that list one nature in the median month of
    Dinamica; the real diversification happens in the constrained selection, which the PIT scores do not carry)."""
    col = f"q{int(q * 100)}"
    return sorted((s, m) for (s, m), r in calibration.iterrows() if m != "eligible" and np.isfinite(r[col]) and r[col] >= r["max"])


def thresholds(calibration: pd.DataFrame, q: float = 0.95) -> dict:
    """{(subportfolio, monitor): threshold} = the `q` quantile of the calibration, without the monitors that cannot fire (`degenerate`) and without `eligible`, a level and not an
    alarm of its own (its change has a threshold)."""
    col = f"q{int(q * 100)}"
    dead = set(degenerate(calibration, q))
    return {(s, m): float(v) for (s, m), v in calibration[col].items() if m != "eligible" and np.isfinite(v) and (s, m) not in dead}


def evaluate(row: "pd.Series | dict", sub: str, limits: dict) -> list:
    """The monitors of one (date, sub-portfolio) row that exceed their threshold: [(monitor, value, threshold)]. Report-only: nothing blocks."""
    return [(m, float(row[m]), t) for (s, m), t in limits.items() if s == sub and m in row and np.isfinite(row[m]) and row[m] > t]


def historical_alert_rate(health: pd.DataFrame, limits: dict) -> pd.Series:
    """Share of months in which each monitor exceeded its threshold, per (subportfolio, monitor): about 1 - q by construction, a sanity check that the thresholds are not degenerate."""
    out = {}
    for (s, m), t in limits.items():
        v = health.xs(s, level="subportfolio")[m].dropna()
        out[(s, m)] = float((v > t).mean()) if len(v) else np.nan
    return pd.Series(out)


def nan_weight_share(metrics: pd.DataFrame, weights: dict) -> pd.Series:
    """Per fund: the share of the base-score weight sitting on a metric the fund does not have (E-5: such a metric contributes 0, below the last-ranked fund). `metrics`: one row per
    fund with a column per weighted metric; `weights`: {metric: weight} of one sub-portfolio. A metric with no column counts as missing for every fund."""
    w = pd.Series(weights, dtype=float)
    present = pd.DataFrame({m: metrics[m].notna() if m in metrics.columns else pd.Series(False, index=metrics.index) for m in w.index})
    return 1.0 - present.mul(w, axis=1).sum(axis=1) / w.sum()
