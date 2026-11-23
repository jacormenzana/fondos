# proyecto3/src/pit_run.py
# -*- coding: utf-8 -*-
"""
Orchestrator of the point-in-time scoring pipeline -- FND-0159 d3.

    inputs (NAV panel, nature, attributes, IPC, rate, daily NAV chunks)
      -> risk metrics (d1a) -> peer metrics (d1b) -> momentum -> short gates (d1c) -> pit_scores (d2)

Every heavy stage goes through the parquet cache (pit_cache) keyed by a content hash of its inputs, and each
stage is timed so the first (cold) pass can be measured on a sample and extrapolated before the full
universe is run. Pure with respect to the DB: callers pass DataFrames (see pit_inputs for the readers).
"""

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import ranking_levers as RL
from proyecto3.src.pit_cache import CODE_VERSION, ParquetCache, cached_frames, content_hash
from proyecto3.src.pit_baselines import flatten_score_detail
from proyecto3.src.pit_candidates import DEFAULT_MAX_STALE_DAYS, log_universe_summary, pit_scores
from proyecto3.src.pit_metrics import TRAILING_KEYS, assert_series_cover, expanding_risk_metrics, trailing_risk_metrics
from proyecto3.src.scorer_config import COMMON_WINDOW_MONTHS, SCORER_CONFIG_HASH
from proyecto3.src.pit_peer_metrics import (alpha_persistence, capture_ratios, momentum_rank, trailing_alpha_persistence,
                                            trailing_capture_ratios)
from proyecto3.src.pit_short_horizon import SHORT_GATE_METRICS, log_coverage, short_gate_metrics
from shared.config import MIN_NAV_ROWS, REGIME_PUBLICATION_LAG_MONTHS

logger = logging.getLogger(__name__)

SCORE_BLOCK_DATES = 12        # evaluation dates per cached scoring block (FND-0193: a killed run resumes at the last finished block)


@dataclass
class PitInputs:
    nav: pd.DataFrame                 # raw-date wide monthly NAV panel
    attrs: pd.DataFrame               # index isin; ATTRIBUTE_COLUMNS + Management_Company, In_Current_Universe
    ipc: "pd.DataFrame | None"        # date, ipc_index
    rate: "pd.DataFrame | None"       # date, rate (decimal) -- risk-free rate and cash yield
    daily_chunks: "callable | None" = None   # () -> iterable of long daily frames; None = no short gates
    macro: "pd.DataFrame | None" = None      # macro_sensitivity.load_macro_factors(); None = no group B (PIT v1)
    fund_geo: "pd.DataFrame | None" = None   # index isin; geography / development_status (pit_macro.load_macro_fund_attrs)
    fx_exposed: "pd.Series | None" = None    # index isin, bool: counts against the non-EUR budget (pit_inputs.load_fx_exposed); None = no limit
    fees: "pd.DataFrame | None" = None       # Package L lever L4: index isin, columns Entry_Fee_Pct / Exit_Fee_Pct (fund_master, ratios; today's values, a documented look-ahead). Deliberately NOT a
                                             # column of `attrs`, which feeds every score cache key: it enters a key only when L4 is ON (lever_key_parts)


@dataclass
class PitRun:
    scores: pd.DataFrame
    universe: pd.DataFrame
    timings: dict = field(default_factory=dict)
    cache_hits: dict = field(default_factory=dict)
    short_coverage: "pd.DataFrame | None" = None     # per date: funds with daily NAV / evaluable windows (fail-open view)
    macro: "dict | None" = None                      # FND-0224 group B frames {metric: dates x isin}, None without group B
    vol_ann: "pd.DataFrame | None" = None            # expanding annualised volatility panel (base-risk control for variants)
    detail: "pd.DataFrame | None" = None             # keep_detail: Layer-3 components flattened (as_of, isin, m_*, x_*); None otherwise


def risk_cache_flags(_config) -> dict:
    """The behaviour switches that change the expanding / trailing risk metrics, as they enter the PIT risk cache key WHEN ON (an OFF run keeps today's keys: the warm cache stays valid).
    FND-0253: the by-dates annualisation; FND-0405: the production volatility floor (its VALUE: a different floor is a different Sharpe, and a stale cache would keep serving the artifact). Pure."""
    from shared.ratio_floor import ratio_vol_floor
    flags = {n: True for n in ("ANNUALIZATION_BY_DATES_ENABLED",) if getattr(_config, n, False)}
    if ratio_vol_floor() > 0:
        flags["ratio_vol_floor"] = ratio_vol_floor()
    return flags


def lever_key_parts(levers: "RL.LeverConfig", fees: "pd.DataFrame | None") -> list:
    """What the ranking levers add to the score cache key: NOTHING when they are all off (the warm caches stay valid, byte for byte), otherwise every value that can change a score through a lever
    (and the fee frame when L4 is on). A changed lever value can never hit a stale cache (FND-0192's rule)."""
    if not levers.any_on:
        return []
    return [{"levers": levers.key(), "fees": content_hash(fees) if (levers.l4_h_years is not None and fees is not None) else None}]


def fee_aware_key_parts(fee_aware: "bool | None", fee_tolerance: "float | None") -> list:
    """What the fee-aware class choice (P3-7, arm B) adds to the score cache key: NOTHING while it is OFF (None = the switch P3_FEE_AWARE_CLASS_ENABLED; the warm caches stay valid, byte for byte), otherwise its
    tolerance. The choice happens inside the scorer's family de-duplication, so it changes which class represents a family and a stale cache hit would be wrong (FND-0192's rule). The fee frame it reads is
    part of the key through `lever_key_parts` only when L4 is on; here it is added whenever the choice is."""
    from shared import config as _config
    on = _config.P3_FEE_AWARE_CLASS_ENABLED if fee_aware is None else bool(fee_aware)
    if not on:
        return []
    tol = _config.P3_FEE_AWARE_CLASS_SCORE_TOLERANCE if fee_tolerance is None else float(fee_tolerance)
    return [{"fee_aware_class": True, "tolerance": tol}]


def compute_pit_scores(
    inputs: PitInputs,
    at: pd.DatetimeIndex,
    regime_by_date: pd.Series,
    cache: ParquetCache,
    max_stale_days: int = DEFAULT_MAX_STALE_DAYS,
    min_obs: int = MIN_NAV_ROWS,
    current_universe_only: bool = False,
    ipc_lag_months: "int | None" = None,
    keep_detail: bool = False,
    macro_variant: "str | None" = None,
    macro_workers: int = 1,
    frames_out: "dict | None" = None,
    levers: "RL.LeverConfig | None" = None,
    fee_aware: "bool | None" = None,
    fee_tolerance: "float | None" = None,
) -> PitRun:
    """Run the whole PIT scoring chain for the dates `at`. ipc_lag_months defaults to the regime IPC lag.
    levers (Package L step F, PREREG_P3_5A): the ranking levers that are on (None = the configuration's switches, all OFF by default; an explicit `LeverConfig()` = none). The trailing 60 / 36-month
    frames they read are cached like the other risk frames; L4 needs `inputs.fees`; the lever values enter the score cache key only when a lever is on.
    fee_aware / fee_tolerance (P3-7, arm B): the fee-aware class choice of the scorer's family de-duplication for THIS run (None = the switch P3_FEE_AWARE_CLASS_ENABLED); ON needs `inputs.fees`; the tolerance
    enters the score cache key only when the choice is ON (`fee_aware_key_parts`).
    frames_out (FND-0435): a dict that receives the metric frame each date is scored on; it bypasses the scoring-block cache (a cache hit would never build the frames), nothing else changes."""
    at = pd.DatetimeIndex(at)
    lag = REGIME_PUBLICATION_LAG_MONTHS.get("ipc_index", 0) if ipc_lag_months is None else ipc_lag_months
    assert_series_cover(at, inputs.rate, inputs.ipc, lag)                  # FND-0228: bfill cannot reach an evaluation date
    nature = inputs.attrs["Fund_Nature"].reindex(inputs.nav.columns)
    timings, hits = {}, {}

    # ---- d1a: expanding risk metrics ----
    from shared import config as _config
    # FND-0253: the by-dates annualisation changes the expanding return / vol of funds with NAV gaps, so it is part of the key WHEN ON
    # (an OFF run keeps today's keys: the warm cache stays valid).
    risk_flags = risk_cache_flags(_config)
    risk_key = content_hash(inputs.nav, inputs.ipc if inputs.ipc is not None else "no-ipc",
                            inputs.rate if inputs.rate is not None else "no-rate", {"lag": lag}, CODE_VERSION,
                            *([risk_flags] if risk_flags else []))
    risk, hit, secs = cached_frames(cache, "risk", risk_key, lambda: expanding_risk_metrics(
        inputs.nav, inputs.ipc, inputs.rate, ipc_lag_months=lag))
    timings["risk"], hits["risk"] = secs, hit

    # ---- d1b: peer metrics (persistence + capture) ----
    # FND-0300: NAV_MONTH_END_ALIGN_ENABLED changes the persistence window grid even for a panel already on month-end dates
    peer_flags = {n: True for n in ("PERSISTENCE_FIRST_LAST_NAV_ENABLED", "CAPTURE_MONTH_END_ENABLED", "ANNUALIZATION_BY_DATES_ENABLED",
                                    "NAV_MONTH_END_ALIGN_ENABLED")
                  if getattr(_config, n, False)}
    peers_key = content_hash(inputs.nav, nature, CODE_VERSION, *([peer_flags] if peer_flags else []))
    if COMMON_WINDOW_MONTHS:      # FND-0303 V2: the same two metrics over the common trailing window (the expanding ones are not needed)
        peers_key = content_hash(peers_key, {"common_window_months": COMMON_WINDOW_MONTHS})
        peers, hit, secs = cached_frames(cache, "peers_window", peers_key, lambda: {
            **trailing_alpha_persistence(inputs.nav, nature, COMMON_WINDOW_MONTHS),
            **trailing_capture_ratios(inputs.nav, nature, COMMON_WINDOW_MONTHS)})
    else:
        peers, hit, secs = cached_frames(cache, "peers", peers_key, lambda: {
            **alpha_persistence(inputs.nav, nature), **capture_ratios(inputs.nav, nature)})
    timings["peers"], hits["peers"] = secs, hit

    # ---- momentum rank at the evaluation dates (cheap; depends on `at` and staleness) ----
    t0 = time.perf_counter()
    momentum = momentum_rank(risk["return_ann"], nature, at, max_stale_days=max_stale_days)
    timings["momentum"] = time.perf_counter() - t0

    # ---- FND-0303 V2: a common trailing window for the selection metrics (0 = off: the expanding frames above, byte for byte) ----
    risk_scored, risk_key_scored = risk, risk_key
    if COMMON_WINDOW_MONTHS:
        risk_key_scored = content_hash(risk_key, {"common_window_months": COMMON_WINDOW_MONTHS})
        win, hit, secs = cached_frames(cache, "risk_window", risk_key_scored, lambda: trailing_risk_metrics(
            inputs.nav, inputs.ipc, inputs.rate, ipc_lag_months=lag, window_months=COMMON_WINDOW_MONTHS))
        timings["risk_window"], hits["risk_window"] = secs, hit
        # momentum (above) keeps the expanding return_ann and srri_nav stays expanding: the PREREG replaces only the listed metrics
        risk_scored = {**risk, **{k: win[k] for k in TRAILING_KEYS}, "n_obs": win["n_obs"]}
        min_obs = max(min_obs, COMMON_WINDOW_MONTHS)           # a fund without a full window is ineligible (the young filter)

    # ---- Package L step F: the inputs of the ranking levers that are ON (nothing is computed while they are all OFF) ----
    levers = levers if levers is not None else RL.LeverConfig.from_config()
    lever_inputs = None
    fee_on = (fee_aware if fee_aware is not None else _config.P3_FEE_AWARE_CLASS_ENABLED)
    if fee_on and inputs.fees is None:
        raise ValueError("compute_pit_scores: the fee-aware class choice is ON but inputs.fees (the entry and exit fee frame) is missing")
    if fee_on and not levers.uses_inputs:
        lever_inputs = RL.LeverInputs(dd60=None, ret36=None, fees=inputs.fees, nav_m=None)          # only the one-off fees: no lever reads anything else
    if levers.uses_inputs:
        dd60 = ret36 = None
        if levers.l1_q is not None:
            win, hit, secs = cached_frames(cache, "lever_win60", content_hash(risk_key, {"window": 60, "kind": "p35a_v1"}), lambda: trailing_risk_metrics(
                inputs.nav, inputs.ipc, inputs.rate, ipc_lag_months=lag, window_months=60))
            dd60, timings["lever_win60"], hits["lever_win60"] = win["max_dd"], secs, hit
        if levers.l2_z0 is not None:
            win, hit, secs = cached_frames(cache, "lever_win36", content_hash(risk_key, {"window": 36, "kind": "p35a_v1"}), lambda: trailing_risk_metrics(
                inputs.nav, inputs.ipc, inputs.rate, ipc_lag_months=lag, window_months=36))
            ret36, timings["lever_win36"], hits["lever_win36"] = win["return_ann"], secs, hit
        lever_inputs = RL.LeverInputs(dd60=dd60, ret36=ret36, fees=inputs.fees,
                                      nav_m=RL.month_end_rows(inputs.nav) if levers.l3_k is not None else None)

    # ---- d1c: short gates from daily NAV, in ISIN chunks ----
    short = None
    short_coverage = None
    short_keys = []
    if inputs.daily_chunks is not None:
        t0 = time.perf_counter()
        parts = {m: [] for m in SHORT_GATE_METRICS}
        cov_parts = []
        n_hit = n_chunk = 0

        def _short(c):
            metrics, cov = short_gate_metrics(c, at, max_stale_days)
            return {**metrics, "coverage": cov}

        for chunk in inputs.daily_chunks():
            n_chunk += 1
            ckey = content_hash(chunk, at, {"stale": max_stale_days}, CODE_VERSION)
            short_keys.append(ckey)
            frames, hit, _ = cached_frames(cache, "short", ckey, lambda c=chunk: _short(c))
            n_hit += bool(hit)
            for m in SHORT_GATE_METRICS:
                parts[m].append(frames[m])
            cov_parts.append(frames["coverage"])
        if n_chunk:
            short = {m: pd.concat(parts[m], axis=1) for m in SHORT_GATE_METRICS}
            for m in short:
                short[m].index = pd.DatetimeIndex(short[m].index)
            counts = ["funds_with_daily", "evaluable_6m", "evaluable_3m", "invalid", "stale"]
            coverage = sum(c[counts] for c in cov_parts)
            coverage.index = pd.DatetimeIndex(coverage.index)
            coverage["evaluable_share_6m"] = (coverage["evaluable_6m"]
                                              / coverage["funds_with_daily"].replace(0, float("nan"))).round(4)
            short_coverage = coverage
            log_coverage(coverage)
        timings["short"] = time.perf_counter() - t0
        hits["short"] = f"{n_hit}/{n_chunk} chunks"

    # ---- d1d (FND-0224): group B macro betas / macro_r2 with the production regression, publication-lagged ----
    macro, macro_key = None, ()
    if macro_variant is not None and inputs.macro is not None:
        from proyecto3.src.pit_macro import expanding_macro_metrics, variant_signature
        macro_key = content_hash(inputs.nav, inputs.macro, inputs.fund_geo if inputs.fund_geo is not None else "no-geo",
                                 at, {"variant": variant_signature(macro_variant), "lags": REGIME_PUBLICATION_LAG_MONTHS}, CODE_VERSION)
        macro, hit, secs = cached_frames(cache, "macro", macro_key, lambda: expanding_macro_metrics(
            inputs.nav, inputs.macro, at, inputs.fund_geo, variant=macro_variant, workers=macro_workers))
        timings["macro"], hits["macro"] = secs, hit
        macro_key = (macro_key,)

    # ---- d2: score the universe at every date (the dominant cost: cached too, except with keep_detail) ----
    # FND-0254: the live build-time eligibility rule's NAV-continuity part, evaluated from the NAV rows observable at each date and fed
    # to the scorer's own check_hard_filters. Stale NAV is the universe rule above (max_stale_days, now the live P3_FUND_NAV_MAX_AGE_DAYS);
    # accepted audit residuals are NOT modelled (today's governance decisions: look-ahead). It follows the live switch: OFF = rule absent.
    elig_on = bool(getattr(_config, "FUND_DATA_ELIGIBILITY_ENABLED", False))
    elig_params = {"min_coverage": _config.P3_FUND_NAV_MIN_COVERAGE, "max_gap_run": _config.P3_FUND_NAV_MAX_GAP_RUN_MONTHS} if elig_on else None

    # FND-0394 phase 2: the shared suspicion predicate, evaluated at each date from the data observable then (plan G-02); only the exemption registry is today's decision. It follows the live switch
    # (ships OFF = rule absent) and is part of the cache key only when ON, with every parameter that can change its answer.
    suspect_on = bool(getattr(_config, "DATA_SUSPECT_ENABLED", False))
    suspect_run = None
    if suspect_on:
        from datetime import date as _date
        from shared.data_exemption import effective_exemptions
        exempt = {i: sorted(q) for i, q in effective_exemptions(_config.DATA_SUSPECT_EXEMPTIONS, _date.today(), (),
                                                                _config.DATA_SUSPECT_EXEMPTION_WARN_DAYS, include_grace=False).items()}
        family_of = inputs.attrs["fund_family_id"].dropna().to_dict() if "fund_family_id" in inputs.attrs.columns else {}
        suspect_run = {"family_of": family_of, "exemptions": {i: set(q) for i, q in exempt.items()}}
        suspect_params = {"min_dev": _config.DATA_SUSPECT_MIN_DEV, "min_metrics": _config.DATA_SUSPECT_MIN_FLAGGED_METRICS, "low_vol_floor": _config.DATA_SUSPECT_LOW_VOL_FLOOR,
                          "pct": _config.DATA_SUSPECT_SMALL_SAMPLE_PERCENTILE, "frac": _config.DATA_SUSPECT_SMALL_SAMPLE_FRACTION, "exempt": exempt}

    score_key = content_hash(risk_key_scored, peers_key, short_keys, at, regime_by_date, inputs.attrs,
                             {"stale": max_stale_days, "min_obs": min_obs, "cur": current_universe_only,
                              "scorer_cfg": SCORER_CONFIG_HASH, "universe_cols": 2},          # FND-0192: a changed scorer value can never hit a stale cache
                             CODE_VERSION, *macro_key,          # macro_key empty without group B
                             *([{"nav_continuity": elig_params}] if elig_on else []),     # FND-0254: part of the key only when the rule is ON
                             *([{"data_suspect": suspect_params}] if suspect_on else []),  # FND-0394: idem
                             *lever_key_parts(levers, inputs.fees),                        # Package L: idem; nothing while every lever is off
                             *fee_aware_key_parts(fee_aware, fee_tolerance),                # P3-7 arm B: idem; nothing while the class choice is off
                             *([{"fees": content_hash(inputs.fees)}] if (fee_on and not (levers.any_on and levers.l4_h_years is not None)) else []))      # the fee frame it reads

    # FND-0193 (resumable runs): the dominant cost is scored in blocks of SCORE_BLOCK_DATES evaluation dates and each block is cached as it
    # finishes, so a run killed by the memory guard (or the owner) loses at most one block. The score of a date uses only information at that
    # date, so blocks concatenate to exactly the single-pass result (pinned by tests/test_pit_run_blocks.py). With keep_detail the Layer-3
    # components are flattened into a third cached frame (the nested dicts are not parquet-able).
    nav_gap_full = None

    def _nav_gap_for(block):
        nonlocal nav_gap_full
        if not elig_on:
            return None
        if nav_gap_full is None:
            from shared.nav_continuity import reasons_by_date
            nav_gap_full = reasons_by_date(inputs.nav, at, elig_params["min_coverage"], elig_params["max_gap_run"])
        return nav_gap_full.loc[block]

    parts_s, parts_u, parts_d, n_hit = [], [], [], 0
    t0 = time.perf_counter()
    blocks = [at[i:i + SCORE_BLOCK_DATES] for i in range(0, len(at), SCORE_BLOCK_DATES)]
    for block in blocks:
        def _score_block(block=block):
            s, u = pit_scores(
                block, risk_scored, peers, inputs.attrs, regime_by_date, momentum=momentum, short=short,
                max_stale_days=max_stale_days, min_obs=min_obs, current_universe_only=current_universe_only,
                keep_detail=keep_detail, macro=macro, nav_gap=_nav_gap_for(block), log_universe=False, data_suspect=suspect_run,
                frames_out=frames_out, levers=levers, lever_inputs=lever_inputs, fee_aware=fee_aware, fee_tolerance=fee_tolerance,
            )
            out = {"scores": s.drop(columns=["detail"]) if (keep_detail and "detail" in s.columns) else s, "universe": u}
            if keep_detail:
                out["detail"] = flatten_score_detail(s) if len(s) else pd.DataFrame({"as_of": pd.to_datetime([]), "isin": []})
            return out

        block_key = content_hash(score_key, {"block": [str(block[0]), str(block[-1])], "detail": bool(keep_detail)})
        frames, hit, _ = (_score_block(), False, 0.0) if frames_out is not None else cached_frames(cache, "scores", block_key, _score_block)
        n_hit += bool(hit)
        parts_s.append(frames["scores"])
        parts_u.append(frames["universe"])
        if keep_detail:
            parts_d.append(frames["detail"])
    secs = time.perf_counter() - t0
    nonempty = [p for p in parts_s if len(p)]
    scores = pd.concat(nonempty, ignore_index=True) if nonempty else pd.DataFrame()
    universe = pd.concat(parts_u) if parts_u else pd.DataFrame()
    universe.index.name = "as_of"
    detail_flat = (pd.concat([p for p in parts_d if len(p)], ignore_index=True) if any(len(p) for p in parts_d) else pd.DataFrame()) if keep_detail else None
    log_universe_summary(universe)
    timings["scoring"], hits["scoring"] = secs, bool(blocks) and n_hit == len(blocks)      # True only when EVERY block came from the cache
    hits["scoring_blocks"] = f"{n_hit}/{len(blocks)}"
    timings["total"] = sum(timings.values())
    logger.info("PIT scoring chain: %s", {k: round(v, 2) for k, v in timings.items()})
    return PitRun(scores=scores, universe=universe, timings=timings, cache_hits=hits, short_coverage=short_coverage,
                  macro=macro, vol_ann=risk.get("vol_ann"), detail=detail_flat)
