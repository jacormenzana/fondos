#!/usr/bin/env python
"""CI catalog-consistency check (P1.5, 2026-09-15).

Pure structural checks over shared/statistical_audit/catalog_*.py and the
module-level constants in scripts/audit/run_statistical_audit.py that
duplicate catalog knowledge as hardcoded tuples (_PEER_METRICS,
_P2_SAME_GROUP_PAIRS). Catches drift between the runner's hardcoded lists
and the catalogs they are supposed to agree with -- WITHOUT touching the
database. This is a catalog self-consistency check, not a data audit
(scripts/audit/run_statistical_audit.py --mode check is the data gate).

Requires pandas: four of the six catalog modules import it transitively
(via invariants.py / comparisons.py / group_checks.py, for type hints on
dataclass fields like Callable[[pd.DataFrame], pd.Series]), even though
this script never calls a pandas-dependent function here -- only inspects
the declarative dataclass instances at import time. Run this in its own CI
job with dependencies installed, separate from sync_agents_md.py's
dependency-free job (see .github/workflows/agents-sync.yml) -- do not try
to make this script itself dependency-free; the fix for the CI-feasibility
question is a second job with its own pip install, not stripping pandas
out of the catalog modules.

Usage:
    python scripts/audit/check_catalog_consistency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS
from shared.statistical_audit.catalog_metric_bounds import METRIC_BOUNDS
from shared.statistical_audit.catalog_metrics import (
    METRIC_OVERRIDES,
    _DEFAULT_CONTINUOUS_SIGNED,
    _PREFIX_RULES,
    get_metric_spec,
)
from shared.statistical_audit.catalog_pairs import COST_PAIRS, P2_PAIRS

_KNOWN_SEGMENTATIONS = {"GLOBAL", "PEER"}


def check_rule_id_uniqueness() -> list[str]:
    """rule_id must be unique ACROSS every catalog collection, not just
    within one dict -- dicts enforce their own key uniqueness for free;
    tuples (P2_INVARIANTS, COST_INVARIANTS, COST_GROUP_CHECKS) do not, and
    neither dicts nor tuples are checked for collisions with EACH OTHER."""
    errors = []
    seen: dict[str, str] = {}
    collections = {
        "P2_PAIRS": P2_PAIRS.values(),
        "COST_PAIRS": COST_PAIRS.values(),
        "P2_INVARIANTS": P2_INVARIANTS,
        "COST_INVARIANTS": COST_INVARIANTS,
        "COST_GROUP_CHECKS": COST_GROUP_CHECKS,
    }
    for coll_name, rules in collections.items():
        for rule in rules:
            rid = rule.rule_id
            if rid in seen:
                errors.append(f"rule_id {rid!r} duplicated: {seen[rid]} and {coll_name}")
            else:
                seen[rid] = coll_name
    return errors


def check_segmentation_vocabulary() -> list[str]:
    """Every MetricSpec.segmentations value must be in the known vocabulary
    -- catches a typo'd segmentation string that would silently never match
    anything the runner checks for (today: only 'GLOBAL' and 'PEER' are
    ever read; there is no declarative 'timeseries' segmentation -- the
    runner's _SCALAR_TIMESERIES_METRICS is validated separately, by
    checking it resolves to explicit catalog entries, not by a
    segmentation flag that does not exist)."""
    errors = []
    specs = (
        list(METRIC_OVERRIDES.values())
        + [spec for _, spec in _PREFIX_RULES]
        + [_DEFAULT_CONTINUOUS_SIGNED]
    )
    for spec in specs:
        unknown = set(spec.segmentations) - _KNOWN_SEGMENTATIONS
        if unknown:
            errors.append(
                f"MetricSpec {spec.metric!r} declares unknown segmentation(s): {sorted(unknown)}"
            )
    return errors


def check_bounds_ordering() -> list[str]:
    """Every declared bound must have min < max where both are set."""
    errors = []
    for metric, bound in METRIC_BOUNDS.items():
        if (
            bound.min_value is not None and bound.max_value is not None
            and bound.min_value >= bound.max_value
        ):
            errors.append(
                f"METRIC_BOUNDS[{metric!r}]: min_value {bound.min_value} >= max_value {bound.max_value}"
            )
    for column, spec in COST_COLUMNS.items():
        for bound_name, bound in (
            ("hard_bound", spec.hard_bound), ("plausibility_bound", spec.plausibility_bound),
        ):
            if bound is None:
                continue
            if (
                bound.min_value is not None and bound.max_value is not None
                and bound.min_value >= bound.max_value
            ):
                errors.append(
                    f"COST_COLUMNS[{column!r}].{bound_name}: "
                    f"min_value {bound.min_value} >= max_value {bound.max_value}"
                )
    return errors


def check_same_group_pairs_use_explicit_metrics() -> list[str]:
    """Every metric referenced by run_statistical_audit.py's
    _P2_SAME_GROUP_PAIRS must be an EXPLICIT entry in METRIC_OVERRIDES, not
    silently falling through to a prefix rule or the generic default --
    catches a typo'd metric name that get_metric_spec() would otherwise
    resolve 'successfully' to the wrong (generic) statistical type instead
    of raising attention."""
    from scripts.audit.run_statistical_audit import _P2_SAME_GROUP_PAIRS

    errors = []
    for rule_id, (metric_a, metric_b) in _P2_SAME_GROUP_PAIRS.items():
        for metric in (metric_a, metric_b):
            if metric not in METRIC_OVERRIDES:
                errors.append(
                    f"_P2_SAME_GROUP_PAIRS[{rule_id!r}] references {metric!r}, "
                    f"which is not an explicit METRIC_OVERRIDES entry"
                )
    return errors


def check_peer_metrics_are_peer_eligible() -> list[str]:
    """_PEER_METRICS (run_statistical_audit.py) is a deliberately curated
    SUBSET of PEER-eligible metrics, not the full set -- that module's own
    comment explains why it doesn't explode across every metric x
    Fund_Nature. The invariant that DOES hold: every metric it names must
    actually declare 'PEER' support in the catalog, or the runner would be
    peer-segmenting a metric the catalog never designed for that."""
    from scripts.audit.run_statistical_audit import _PEER_METRICS

    errors = []
    for metric in _PEER_METRICS:
        spec = get_metric_spec(metric)
        if "PEER" not in spec.segmentations:
            errors.append(
                f"_PEER_METRICS includes {metric!r}, but its MetricSpec does "
                f"not declare PEER segmentation support"
            )
    return errors


def check_scalar_timeseries_metrics_are_explicit() -> list[str]:
    """_SCALAR_TIMESERIES_METRICS (run_statistical_audit.py) must reference
    only explicitly-catalogued metrics -- same typo-guard rationale as
    check_same_group_pairs_use_explicit_metrics(). There is no
    'timeseries' segmentation to check membership against (unlike PEER);
    this is the closest catalog-only invariant available for that list."""
    from scripts.audit.run_statistical_audit import _SCALAR_TIMESERIES_METRICS

    errors = []
    for metric in _SCALAR_TIMESERIES_METRICS:
        if metric not in METRIC_OVERRIDES:
            errors.append(
                f"_SCALAR_TIMESERIES_METRICS includes {metric!r}, "
                f"which is not an explicit METRIC_OVERRIDES entry"
            )
    return errors


def main() -> int:
    checks = [
        check_rule_id_uniqueness,
        check_segmentation_vocabulary,
        check_bounds_ordering,
        check_same_group_pairs_use_explicit_metrics,
        check_peer_metrics_are_peer_eligible,
        check_scalar_timeseries_metrics_are_explicit,
    ]
    all_errors: list[str] = []
    for check in checks:
        errors = check()
        if errors:
            print(f"FAIL: {check.__name__}")
            for e in errors:
                print(f"  - {e}")
        else:
            print(f"OK:   {check.__name__}")
        all_errors.extend(errors)

    print()
    if all_errors:
        print(f"{len(all_errors)} catalog-consistency error(s) found.")
        return 1
    print("All catalog-consistency checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
