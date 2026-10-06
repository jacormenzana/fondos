"""§5 catalog_metric_bounds — Block 7 plausibility bounds for P2 metrics that
are NOT already expressed as a Block 5 invariant in catalog_invariants.py.
Deliberately does not duplicate coverage: max_dd, srri_nav, upside/downside
capture, macro_r2, |sharpe|/|sortino| already have an equivalent
HARD_INVARIANT or PLAUSIBILITY rule in P2_INVARIANTS. bounded_unit metrics
(momentum, persistence, pct-months, regime_coverage_ratio)
get a single generic 0-1 rule generated from their MetricSpec.statistical_type
rather than one hand-written entry per metric (P#11/DRY) — the cost-domain
catalog has no equivalent shortcut because its columns don't share one
common [0,1] shape.

capture_ratio (2026-09-28, audit-skill-alignment plan Wave 1, FND-0119): unlike
upside_capture/downside_capture, capture_ratio itself was named in the P2
skill's Block 5 ("capture_ratio finite") and Block 7 (-5..5) but had no
CAPTURE_CLAMP coverage and no entry here — a confirmed, unintentional gap,
not a deliberate non-duplication.
"""
from __future__ import annotations

from dataclasses import replace

from shared.config import (
    ENERGY_SCENARIO_SHOCK, FX_CONTRIBUTION_PCT_CLAMP, HY_SPREAD_SCENARIO_SHOCK, MACRO_BETA_PLAUSIBLE_MAX,
)

from .catalog_metrics import get_metric_spec
from .invariants import BoundRule

_ENERGY_MAX = MACRO_BETA_PLAUSIBLE_MAX * ENERGY_SCENARIO_SHOCK
_HY_SPREAD_MAX = MACRO_BETA_PLAUSIBLE_MAX * HY_SPREAD_SCENARIO_SHOCK

METRIC_BOUNDS: dict[str, BoundRule] = {
    "vol_ann": BoundRule("vol_ann", 0.0, 5.0, "PLAUSIBILITY"),
    "srri_volatility": BoundRule("srri_volatility", 0.0, 5.0, "PLAUSIBILITY"),
    "return_ann": BoundRule("return_ann", -0.9, 3.0, "PLAUSIBILITY"),
    "capture_ratio": BoundRule("capture_ratio", -5.0, 5.0, "PLAUSIBILITY"),
    # FND-0234 (2026-10-06): these three are signed scales, not unit fractions; the generic [0, 1] bound they used to get
    # flagged 1,935 / 906 / 467 live rows as "outside bounds" that were correct. The limits derive from the producers'
    # own constants (shared.config), so they move with them: scenario impacts are bounded by the beta circuit breaker x the
    # shock, fx_contribution_pct by its clamp (a value AT the clamp is the unstable-ratio problem of FND-0235, not a bound
    # violation).
    "energy_sensitivity_pct": BoundRule("energy_sensitivity_pct", -_ENERGY_MAX, _ENERGY_MAX, "PLAUSIBILITY"),
    "hy_spread_sensitivity_pct": BoundRule("hy_spread_sensitivity_pct", -_HY_SPREAD_MAX, _HY_SPREAD_MAX, "PLAUSIBILITY"),
    "fx_contribution_pct": BoundRule("fx_contribution_pct", -FX_CONTRIBUTION_PCT_CLAMP, FX_CONTRIBUTION_PCT_CLAMP,
                                     "PLAUSIBILITY"),
}

_BOUNDED_UNIT_TEMPLATE = BoundRule("*", 0.0, 1.0, "PLAUSIBILITY")

# macro_r2 already has a HARD_INVARIANT (MACRO_R2_RANGE) — a duplicate
# PLAUSIBILITY bound on the same metric would only produce a redundant finding.
_BOUNDED_UNIT_EXCLUDE = {"macro_r2"}


def get_metric_bound(metric: str) -> BoundRule | None:
    if metric in METRIC_BOUNDS:
        return METRIC_BOUNDS[metric]

    if metric in _BOUNDED_UNIT_EXCLUDE:
        return None

    spec = get_metric_spec(metric)
    if spec.statistical_type == "bounded_unit":
        return replace(_BOUNDED_UNIT_TEMPLATE, metric=metric)

    return None
