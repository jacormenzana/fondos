# proyecto3/src/pit_candidates.py
# -*- coding: utf-8 -*-
"""
Point-in-time scoring of the fund universe for the P3 backtester -- FND-0159, Wave B step d2.

For every evaluation date t this module builds the frame that fund_scorer.score_funds_from_df expects
(one row per fund, metrics + fund_master attributes) using ONLY information observable at t, and scores
it with the SAME pure scoring core the live pipeline uses (P#11: no second scorer). Inputs are the
outputs of the d1 modules (pit_metrics, pit_peer_metrics, pit_short_horizon) and a static attribute
frame; nothing here touches the DB or writes fund_scores.

Universe at t (owner decisions 2026-10-03):
  * a fund is a candidate if it has a NAV observation <= t that is not older than `max_stale_days`
    (retired funds drop out instead of keeping their last value forever) and at least `min_obs` NAV
    observations so far (live analogue: MIN_NAV_ROWS, FND-0168);
  * In_Current_Universe=0 funds are INCLUDED (survivorship counterweight, FND-0198) unless
    `current_universe_only=True`.

Group B of the metrics (macro betas, macro_r2, fx contribution, per-regime stats, crisis stress, regime
coverage) is not recomputed in PIT v1: those columns are absent, so the scorer's regime multipliers are
neutral (every check is `not isnan(...)`). Only the alpha_persistence bonus (group A) acts. The static
attributes (Fund_Nature, Credit_Quality, family, ...) are TODAY's values: residual look-ahead,
documented in FND-0198.

Fail-open / coverage: returned `universe` table has, per date, how many funds entered, how many were
dropped as stale or too young, and how many were scored/eligible; it is logged, never silent.
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.fund_scorer import score_funds_from_df
from shared.config import MIN_NAV_ROWS

logger = logging.getLogger(__name__)

ATTRIBUTE_COLUMNS = ("Fund_Name", "Fund_Nature", "srri_kiid", "Investment_Focus", "Credit_Quality",
                     "Ongoing_Charge", "SRRI_Quality_Flag", "fund_family_id")
SHORT_COLUMN_MAP = {                       # pit_short_horizon name -> column the scorer reads
    "short_max_drawdown_6m":   "short_max_drawdown__rolling_6m",
    "short_liquidity_flag_6m": "short_liquidity_flag__rolling_6m",
    "short_vol_adj_3m":        "short_vol_adj__rolling_3m",
}
DEFAULT_MAX_STALE_DAYS = 45


def asof_snapshot(frame: pd.DataFrame, at: pd.DatetimeIndex, max_stale_days: "int | None" = None):
    """Latest value of every column at or before each date in `at` (the fund's last observation),
    plus the date of that observation. Values older than `max_stale_days` are dropped (NaN/NaT).
    Returns (values, last_date): DataFrames shaped (len(at), n_columns)."""
    at = pd.DatetimeIndex(at)
    grid = frame.index.union(at).sort_values()
    vals = frame.reindex(grid).to_numpy(dtype=float)
    seen = np.isfinite(vals)
    last_idx = np.maximum.accumulate(np.where(seen, np.arange(len(grid))[:, None], -1), axis=0)
    li = last_idx[grid.get_indexer(at)]
    cols = np.arange(vals.shape[1])[None, :]
    snap = np.where(li >= 0, vals[np.clip(li, 0, None), cols], np.nan)
    last_date = grid.to_numpy()[np.clip(li, 0, None)].astype("datetime64[ns]")
    last_date = np.where(li >= 0, last_date, np.datetime64("NaT"))
    if max_stale_days is not None:
        age_days = (at.to_numpy().astype("datetime64[D]")[:, None] - last_date.astype("datetime64[D]")) / np.timedelta64(1, "D")
        keep = np.isfinite(age_days) & (age_days <= max_stale_days)
        snap = np.where(keep, snap, np.nan)
        last_date = np.where(keep, last_date, np.datetime64("NaT"))
    return (pd.DataFrame(snap, index=at, columns=frame.columns),
            pd.DataFrame(last_date, index=at, columns=frame.columns))


def pit_scores(
    at: pd.DatetimeIndex,
    risk: dict,
    peers: dict,
    attrs: pd.DataFrame,
    regime_by_date: pd.Series,
    momentum: "pd.DataFrame | None" = None,
    short: "dict | None" = None,
    max_stale_days: int = DEFAULT_MAX_STALE_DAYS,
    min_obs: int = MIN_NAV_ROWS,
    current_universe_only: bool = False,
    keep_detail: bool = False,
    macro: "dict | None" = None,
    macro_max_stale_days: int = 100,
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """Score the universe as of every date in `at`.

    risk:    pit_metrics.expanding_risk_metrics output (needs n_obs, max_dd, return_ann_real, sharpe, srri_nav).
    peers:   {'alpha_persistence': DataFrame, 'capture_ratio': DataFrame} from pit_peer_metrics (raw-date index).
    attrs:   index isin; columns ATTRIBUTE_COLUMNS (+ optional In_Current_Universe, 1/0).
    regime_by_date: Series date -> regime label (e.g. the publication-lagged classify_historical()['regime']);
             the regime at t is its latest value <= t; dates before the first label are skipped with a warning.
    momentum: pit_peer_metrics.momentum_rank output evaluated at `at`; short: pit_short_horizon.short_gate_metrics
             metrics dict evaluated at `at` (both optional: missing -> NaN -> neutral / fail-open).
    Returns (scores, universe): scores is long (as_of, regime, isin, subportfolio, ..., score_final, eligible,
    exclusion_reason[, detail]); universe has per-date counts (entered, stale, young, scored, eligible)."""
    at = pd.DatetimeIndex(at)
    regimes = regime_by_date.sort_index()
    snaps = {}
    for name in ("max_dd", "return_ann_real", "sharpe", "srri_nav"):
        snaps[name], _ = asof_snapshot(risk[name], at, max_stale_days)
    n_obs_all, _ = asof_snapshot(risk["n_obs"], at, None)                     # staleness handled below, counted
    n_obs_fresh, last_obs = asof_snapshot(risk["n_obs"], at, max_stale_days)
    for name in ("alpha_persistence", "capture_ratio"):
        snaps[name], _ = asof_snapshot(peers[name], at, max_stale_days)
    # FND-0224: group B macro metrics (pit_macro.expanding_macro_metrics), evaluated on a coarser grid than `at`;
    # the last evaluation date <= t carries forward for at most macro_max_stale_days (a quarter plus slack)
    macro_snaps = {}
    if macro is not None:
        for name, frame_m in macro.items():
            macro_snaps[name], _ = asof_snapshot(frame_m, at, macro_max_stale_days)

    attrs = attrs.copy()
    in_universe = attrs["In_Current_Universe"] if "In_Current_Universe" in attrs.columns else None
    all_scores, rows = [], []

    for i, t in enumerate(at):
        pos = regimes.index.searchsorted(t, side="right") - 1
        if pos < 0:
            logger.warning("pit_scores: no regime label at or before %s -- date skipped", t.date())
            rows.append(dict(as_of=t, regime=None, entered=0, stale=0, young=0, scored=0, eligible=0))
            continue
        regime = regimes.iloc[pos]

        observed = n_obs_all.iloc[i].notna()
        fresh = n_obs_fresh.iloc[i].notna()
        old_enough = n_obs_fresh.iloc[i] >= min_obs
        keep = fresh & old_enough & n_obs_fresh.columns.isin(attrs.index)
        n_stale = int((observed & ~fresh).sum())
        n_young = int((fresh & ~old_enough).sum())

        isins = n_obs_fresh.columns[keep.to_numpy()]
        if current_universe_only and in_universe is not None:
            isins = isins[in_universe.reindex(isins).fillna(0).astype(int).to_numpy() == 1]
        frame = attrs.loc[isins, list(ATTRIBUTE_COLUMNS)].copy()
        frame.index.name = "isin"
        for name, s in snaps.items():
            frame[name] = s.iloc[i].reindex(isins)
        if momentum is not None and t in momentum.index:
            frame["momentum_rank"] = momentum.loc[t].reindex(isins)
        if short is not None:
            for src, dst in SHORT_COLUMN_MAP.items():
                if src in short and t in short[src].index:
                    frame[dst] = short[src].loc[t].reindex(isins)
        for name, snap in macro_snaps.items():
            frame[name] = snap.iloc[i].reindex(isins)

        res = score_funds_from_df(frame, regime, verbose=False) if len(frame) else pd.DataFrame()
        n_scored = len(res)
        n_elig = int(res["eligible"].sum()) if n_scored else 0
        rows.append(dict(as_of=t, regime=regime, entered=len(frame), stale=n_stale, young=n_young,
                         scored=n_scored, eligible=n_elig))
        if n_scored:
            if not keep_detail:
                res = res.drop(columns=["detail"])
            res.insert(0, "regime", regime)
            res.insert(0, "as_of", t)
            all_scores.append(res)

    universe = pd.DataFrame(rows).set_index("as_of")
    scores = pd.concat(all_scores, ignore_index=True) if all_scores else pd.DataFrame()
    _log_universe(universe)
    return scores, universe


def _log_universe(universe: pd.DataFrame) -> None:
    if universe.empty:
        return
    logger.info("PIT scoring: %d dates, funds entering per date min/median/max = %d/%d/%d; stale dropped %d, "
                "too young %d (fund-dates)", len(universe), universe["entered"].min(), universe["entered"].median(),
                universe["entered"].max(), int(universe["stale"].sum()), int(universe["young"].sum()))
    empty = universe.index[universe["scored"] == 0]
    if len(empty):
        logger.warning("PIT scoring: no scored funds on %d of %d dates (%s .. %s) -- those months have no portfolio",
                       len(empty), len(universe), empty.min().date(), empty.max().date())
