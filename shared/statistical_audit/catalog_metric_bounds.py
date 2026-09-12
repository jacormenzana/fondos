"""§5 catalog_metric_bounds — Block 7 plausibility bounds for P2 metrics that
are NOT already expressed as a Block 5 invariant in catalog_invariants.py.
Deliberately does not duplicate coverage: max_dd, srri_nav, upside/downside
capture, macro_r2, |sharpe|/|sortino| already have an equivalent
HARD_INVARIANT or PLAUSIBILITY rule in P2_INVARIANTS. bounded_unit metrics
(momentum, persistence, pct-months, sensitivities, regime_coverage_ratio)
get a single generic 0-1 rule generated from their MetricSpec.statistical_type
rather than one hand-written entry per metric (P#11/DRY) — the cost-domain
catalog has no equivalent shortcut because its columns don't share one
common [0,1] shape.
"""
from __future__ import annotations

from dataclasses import replace

from .catalog_metrics import get_metric_spec
from .invariants import BoundRule

METRIC_BOUNDS: dict[str, BoundRule] = {
    "vol_ann": BoundRule("vol_ann", 0.0, 5.0, "PLAUSIBILITY"),
    "srri_volatility": BoundRule("srri_volatility", 0.0, 5.0, "PLAUSIBILITY"),
    "return_ann": BoundRule("return_ann", -0.9, 3.0, "PLAUSIBILITY"),
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
