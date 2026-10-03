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

from proyecto3.src.pit_cache import CODE_VERSION, ParquetCache, cached_frames, content_hash
from proyecto3.src.pit_candidates import DEFAULT_MAX_STALE_DAYS, pit_scores
from proyecto3.src.pit_metrics import expanding_risk_metrics
from proyecto3.src.pit_peer_metrics import alpha_persistence, capture_ratios, momentum_rank
from proyecto3.src.pit_short_horizon import SHORT_GATE_METRICS, log_coverage, short_gate_metrics
from shared.config import MIN_NAV_ROWS, REGIME_PUBLICATION_LAG_MONTHS

logger = logging.getLogger(__name__)


@dataclass
class PitInputs:
    nav: pd.DataFrame                 # raw-date wide monthly NAV panel
    attrs: pd.DataFrame               # index isin; ATTRIBUTE_COLUMNS + Management_Company, In_Current_Universe
    ipc: "pd.DataFrame | None"        # date, ipc_index
    rate: "pd.DataFrame | None"       # date, rate (decimal) -- risk-free rate and cash yield
    daily_chunks: "callable | None" = None   # () -> iterable of long daily frames; None = no short gates


@dataclass
class PitRun:
    scores: pd.DataFrame
    universe: pd.DataFrame
    timings: dict = field(default_factory=dict)
    cache_hits: dict = field(default_factory=dict)
    short_coverage: "pd.DataFrame | None" = None     # per date: funds with daily NAV / evaluable windows (fail-open view)


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
) -> PitRun:
    """Run the whole PIT scoring chain for the dates `at`. ipc_lag_months defaults to the regime IPC lag."""
    at = pd.DatetimeIndex(at)
    lag = REGIME_PUBLICATION_LAG_MONTHS.get("ipc_index", 0) if ipc_lag_months is None else ipc_lag_months
    nature = inputs.attrs["Fund_Nature"].reindex(inputs.nav.columns)
    timings, hits = {}, {}

    # ---- d1a: expanding risk metrics ----
    risk_key = content_hash(inputs.nav, inputs.ipc if inputs.ipc is not None else "no-ipc",
                            inputs.rate if inputs.rate is not None else "no-rate", {"lag": lag}, CODE_VERSION)
    risk, hit, secs = cached_frames(cache, "risk", risk_key, lambda: expanding_risk_metrics(
        inputs.nav, inputs.ipc, inputs.rate, ipc_lag_months=lag))
    timings["risk"], hits["risk"] = secs, hit

    # ---- d1b: peer metrics (persistence + capture) ----
    peers_key = content_hash(inputs.nav, nature, CODE_VERSION)
    peers, hit, secs = cached_frames(cache, "peers", peers_key, lambda: {
        **alpha_persistence(inputs.nav, nature), **capture_ratios(inputs.nav, nature)})
    timings["peers"], hits["peers"] = secs, hit

    # ---- momentum rank at the evaluation dates (cheap; depends on `at` and staleness) ----
    t0 = time.perf_counter()
    momentum = momentum_rank(risk["return_ann"], nature, at, max_stale_days=max_stale_days)
    timings["momentum"] = time.perf_counter() - t0

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

    # ---- d2: score the universe at every date (the dominant cost: cached too, except with keep_detail) ----
    def _score():
        s, u = pit_scores(
            at, risk, peers, inputs.attrs, regime_by_date, momentum=momentum, short=short,
            max_stale_days=max_stale_days, min_obs=min_obs, current_universe_only=current_universe_only,
            keep_detail=keep_detail,
        )
        return {"scores": s, "universe": u}

    if keep_detail:                                                  # 'detail' holds dicts: not parquet-able
        t0 = time.perf_counter()
        frames, hit = _score(), False
        secs = time.perf_counter() - t0
    else:
        score_key = content_hash(risk_key, peers_key, short_keys, at, regime_by_date, inputs.attrs,
                                 {"stale": max_stale_days, "min_obs": min_obs, "cur": current_universe_only}, CODE_VERSION)
        frames, hit, secs = cached_frames(cache, "scores", score_key, _score)
    scores, universe = frames["scores"], frames["universe"]
    universe.index.name = "as_of"
    timings["scoring"], hits["scoring"] = secs, hit
    timings["total"] = sum(timings.values())
    logger.info("PIT scoring chain: %s", {k: round(v, 2) for k, v in timings.items()})
    return PitRun(scores=scores, universe=universe, timings=timings, cache_hits=hits, short_coverage=short_coverage)
