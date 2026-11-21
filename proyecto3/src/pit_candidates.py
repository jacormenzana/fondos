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

from proyecto3.src import ranking_levers as RL                 # the PIT reaches the levers through the module (a test spies on it) and the scorer applies them
from proyecto3.src.fund_scorer import baseline_eligible_isins, score_funds_from_df
from shared.config import MIN_NAV_ROWS, P3_FUND_NAV_MAX_AGE_DAYS

logger = logging.getLogger(__name__)

ATTRIBUTE_COLUMNS = ("Fund_Name", "Fund_Nature", "srri_kiid", "Investment_Focus", "Credit_Quality",
                     "Ongoing_Charge", "SRRI_Quality_Flag", "fund_family_id")
SHORT_COLUMN_MAP = {                       # pit_short_horizon name -> column the scorer reads
    "short_max_drawdown_6m":   "short_max_drawdown__rolling_6m",
    "short_liquidity_flag_6m": "short_liquidity_flag__rolling_6m",
    "short_vol_adj_3m":        "short_vol_adj__rolling_3m",
}
# FND-0254: ONE definition of "stale NAV" for the live build-time eligibility rule and the PIT universe (was a PIT-only 45). A fund whose
# newest NAV at t is older than this drops out of the PIT universe, which is what `check_hard_filters` does live (nav_stale).
DEFAULT_MAX_STALE_DAYS = P3_FUND_NAV_MAX_AGE_DAYS


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
    nav_gap: "pd.DataFrame | None" = None,
    log_universe: bool = True,
    data_suspect: "dict | None" = None,
    frames_out: "dict | None" = None,
    levers: "RL.LeverConfig | None" = None,
    lever_inputs: "RL.LeverInputs | None" = None,
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """Score the universe as of every date in `at`.

    levers / lever_inputs (Package L step F, PREREG_P3_5A): the ranking levers that are on (None = the configuration's switches, all OFF by default) and what they read: the trailing 60-month
    drawdown and 36-month nominal return frames (L1, L2), the fee frame (L4) and the month-end NAV panel (L3's pool). A lever that is ON without its input is an ERROR, never a silent no-op.
    The levers act inside `score_funds_from_df`, the function production runs; this only builds the same frame columns the live loader reads from P2 and the state of L3.

    risk:    pit_metrics.expanding_risk_metrics output (needs n_obs, max_dd, return_ann_real, sharpe, srri_nav).
    peers:   {'alpha_persistence': DataFrame, 'capture_ratio': DataFrame} from pit_peer_metrics (raw-date index).
    attrs:   index isin; columns ATTRIBUTE_COLUMNS (+ optional In_Current_Universe, 1/0).
    regime_by_date: Series date -> regime label (e.g. the publication-lagged classify_historical()['regime']);
             the regime at t is its latest value <= t; dates before the first label are skipped with a warning.
    momentum: pit_peer_metrics.momentum_rank output evaluated at `at`; short: pit_short_horizon.short_gate_metrics
             metrics dict evaluated at `at` (both optional: missing -> NaN -> neutral / fail-open).
    nav_gap: shared.nav_continuity.reasons_by_date output (index == `at`, columns isin): the NAV-continuity exclusion reason of each
             fund AS OF each date, fed to the scorer's own `check_hard_filters` through the `nav_gap_reason` column (FND-0254: the
             live rule, not a second one). None = the rule is not applied. Accepted audit residuals are deliberately NOT modelled:
             they are today's governance decisions, applying them in the past would be look-ahead.
    data_suspect: FND-0394 phase 2, {"family_of": {isin: family id}, "exemptions": {isin: {'metric|horizon'}}} (None = the rule is not applied):
             at EACH date the shared suspicion predicate (shared.data_suspect.pit_suspect_reasons) judges the metrics observable then, with the months of
             NAV of each fund as the sample size, and feeds its reason to the scorer's own `check_hard_filters` through the `data_suspect_reason` column
             (the live rule, not a second one: plan G-02, no look-ahead). Only the exemption registry is today's decision, as for the accepted residuals.
    frames_out: FND-0435, a dict that receives {date: a COPY of the metric frame that date is scored on} (read-only diagnostics, e.g. which metric carried a selected fund). None = nothing is
             kept and the behaviour is exactly what it was.
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

    vol_snap = asof_snapshot(risk["vol_ann"], at, max_stale_days)[0] if (data_suspect is not None and "vol_ann" in risk) else None   # the denominator of the Sharpe (degenerate-ratio rule)

    cfg = levers if levers is not None else RL.LeverConfig.from_config()
    lever_snaps = {}
    if cfg.any_on:
        li = lever_inputs
        need = [(cfg.l1_q is not None, "dd60", "L1 (the trailing 60-month drawdown frame)"), (cfg.l2_z0 is not None, "ret36", "L2 (the trailing 36-month return frame)"),
                (cfg.l4_h_years is not None, "fees", "L4 (lever_inputs.fees: the entry and exit fee frame)"), (cfg.l3_k is not None, "nav_m", "L3 (the month-end NAV panel)")]
        if li is None:
            raise ValueError("pit_scores: a lever is ON but lever_inputs is None: " + ", ".join(n for on, _a, n in need if on))
        for on, attr, name in need:
            if on and getattr(li, attr) is None:
                raise ValueError(f"pit_scores: lever {name} is ON but its input is missing")
        if cfg.l1_q is not None:
            lever_snaps["dd60"], _ = asof_snapshot(li.dd60, at, max_stale_days)
        if cfg.l2_z0 is not None:
            lever_snaps["ret36"], _ = asof_snapshot(li.ret36, at, max_stale_days)

    attrs = attrs.copy()
    in_universe = attrs["In_Current_Universe"] if "In_Current_Universe" in attrs.columns else None
    all_scores, rows = [], []

    for i, t in enumerate(at):
        pos = regimes.index.searchsorted(t, side="right") - 1
        if pos < 0:
            logger.warning("pit_scores: no regime label at or before %s -- date skipped", t.date())
            rows.append(dict(as_of=t, regime=None, entered=0, stale=0, young=0, scored=0, eligible=0,
                             entered_retired=0, eligible_retired=0))
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
        if "doc_status_reason" in attrs.columns:                # FND-0323 D1: present only when the switch was ON at load time
            frame["doc_status_reason"] = attrs.loc[isins, "doc_status_reason"]
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
        if nav_gap is not None:
            frame["nav_gap_reason"] = nav_gap.iloc[i].reindex(isins)
        if data_suspect is not None and len(frame):
            from shared.data_suspect import pit_suspect_reasons
            reasons = pit_suspect_reasons(frame, n_obs_fresh.iloc[i], data_suspect.get("family_of"), data_suspect.get("exemptions"),
                                          vol=vol_snap.iloc[i] if vol_snap is not None else None)
            frame["data_suspect_reason"] = pd.Series(reasons, dtype=object).reindex(frame.index)

        recovery_k, l3_state = None, None
        if cfg.any_on and len(frame):
            # the lever columns, named as the live loader names them (P2: max_dd at rolling_5y, return_ann at rolling_3y; fund_master: the fees)
            if cfg.l1_q is not None:
                frame[RL.DD60_COLUMN] = lever_snaps["dd60"].iloc[i].reindex(isins)
            if cfg.l2_z0 is not None:
                frame[RL.RET36_COLUMN] = lever_snaps["ret36"].iloc[i].reindex(isins)
            if cfg.l4_h_years is not None:
                for c in RL.FEE_COLUMNS:
                    frame[c] = lever_inputs.fees[c].reindex(isins) if c in lever_inputs.fees.columns else np.nan
            if cfg.l3_k is not None:
                pool = baseline_eligible_isins(frame)          # the BASELINE eligible set: no circularity with L1, the same pool as the live build
                l3_state = RL.pool_state(RL.pool_returns_from_nav(lever_inputs.nav_m, pool, t))
                recovery_k = float(cfg.l3_k) if l3_state == "recovery" else None

        if frames_out is not None:
            frames_out[t] = frame.copy()
        res = score_funds_from_df(frame, regime, verbose=False, levers=cfg, recovery_k=recovery_k) if len(frame) else pd.DataFrame()
        n_scored = len(res)
        n_elig = int(res["eligible"].sum()) if n_scored else 0
        retired = _retired_isins(frame.index, in_universe)
        n_elig_retired = 0
        if n_scored:
            res_isin = res["isin"] if "isin" in res.columns else res.index.to_series()
            n_elig_retired = int(res["eligible"][res_isin.isin(retired).to_numpy()].sum())
        rows.append(dict(as_of=t, regime=regime, entered=len(frame), stale=n_stale, young=n_young,
                         scored=n_scored, eligible=n_elig, entered_retired=len(retired),
                         eligible_retired=n_elig_retired,
                         **({"l3_state": l3_state} if cfg.l3_k is not None else {})))      # L3 only: the pool state of the date (cached with the block; an all-off run keeps today's columns)
        if n_scored:
            if not keep_detail:
                res = res.drop(columns=["detail"])
            res.insert(0, "regime", regime)
            res.insert(0, "as_of", t)
            all_scores.append(res)

    universe = pd.DataFrame(rows).set_index("as_of")
    scores = pd.concat(all_scores, ignore_index=True) if all_scores else pd.DataFrame()
    if log_universe:
        _log_universe(universe)
    return scores, universe


def _retired_isins(isins: pd.Index, in_universe: "pd.Series | None") -> pd.Index:
    """ISINs of `isins` that are not in today's catalogue (In_Current_Universe = 0): the survivorship counterweight
    the PIT universe carries (FND-0198). A missing flag column or a NULL flag counts as not retired (unknown)."""
    if in_universe is None:
        return isins[:0]
    flag = in_universe.reindex(isins)
    return isins[(flag == 0).to_numpy()]


def _log_universe(universe: pd.DataFrame) -> None:
    if universe.empty:
        return
    logger.info("PIT scoring: %d dates, funds entering per date min/median/max = %d/%d/%d; stale dropped %d, "
                "too young %d (fund-dates)", len(universe), universe["entered"].min(), universe["entered"].median(),
                universe["entered"].max(), int(universe["stale"].sum()), int(universe["young"].sum()))
    if "entered_retired" in universe.columns and universe["entered"].sum():
        logger.info("PIT survivorship composition: retired funds (In_Current_Universe=0) are %.1f%% of fund-dates entering "
                    "and %.1f%% of eligible fund-dates", 100 * universe["entered_retired"].sum() / universe["entered"].sum(),
                    100 * universe["eligible_retired"].sum() / max(universe["eligible"].sum(), 1))
    empty = universe.index[universe["scored"] == 0]
    if len(empty):
        logger.warning("PIT scoring: no scored funds on %d of %d dates (%s .. %s) -- those months have no portfolio",
                       len(empty), len(universe), empty.min().date(), empty.max().date())


log_universe_summary = _log_universe          # public name for pit_run, which scores in cached blocks and logs the whole run once
