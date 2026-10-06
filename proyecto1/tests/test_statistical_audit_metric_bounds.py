# proyecto1/tests/test_statistical_audit_metric_bounds.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/catalog_metric_bounds.py —
cierra la cobertura de Block 7 (P2) sin duplicar los invariantes ya cubiertos
en catalog_invariants.py (doc/reglas/AUDITORIA_ESTADISTICA.md §5).

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.catalog_metric_bounds import get_metric_bound


def test_vol_ann_explicit_bound():
    b = get_metric_bound("vol_ann")
    assert b.min_value == 0.0 and b.max_value == 5.0
    assert b.bound_type == "PLAUSIBILITY"


def test_bounded_unit_metric_gets_generic_zero_one_bound():
    b = get_metric_bound("momentum_rank")
    assert (b.min_value, b.max_value) == (0.0, 1.0)


def test_macro_r2_excluded_to_avoid_duplicating_hard_invariant():
    assert get_metric_bound("macro_r2") is None


def test_continuous_signed_metric_without_explicit_entry_has_no_bound():
    assert get_metric_bound("beta_rate_eu") is None


def test_bound_carries_the_correct_metric_name_not_the_template_wildcard():
    b = get_metric_bound("alpha_persistence")
    assert b.metric == "alpha_persistence"


def test_capture_ratio_has_explicit_bound():
    # FND-0119 (2026-09-28): capture_ratio was named in the skill's Block 5/7
    # but had no coverage anywhere (unlike upside_capture/downside_capture,
    # which are HARD_INVARIANT-covered by CAPTURE_CLAMP).
    b = get_metric_bound("capture_ratio")
    assert (b.min_value, b.max_value) == (-5.0, 5.0)
    assert b.bound_type == "PLAUSIBILITY"


# FND-0234 (2026-10-06): three derived metrics are signed scales, not unit fractions.
def test_sensitivity_and_fx_pct_are_signed_scales_not_unit_fractions():
    from shared.config import (
        ENERGY_SCENARIO_SHOCK, FX_CONTRIBUTION_PCT_CLAMP, HY_SPREAD_SCENARIO_SHOCK, MACRO_BETA_PLAUSIBLE_MAX,
    )
    from shared.statistical_audit.catalog_metrics import get_metric_spec

    for metric in ("energy_sensitivity_pct", "hy_spread_sensitivity_pct", "fx_contribution_pct"):
        assert get_metric_spec(metric).statistical_type == "continuous_signed"
    e = get_metric_bound("energy_sensitivity_pct")
    h = get_metric_bound("hy_spread_sensitivity_pct")
    f = get_metric_bound("fx_contribution_pct")
    # derived from the producers' own constants: the beta circuit breaker times the scenario shock, and the clamp
    assert (e.min_value, e.max_value) == (-MACRO_BETA_PLAUSIBLE_MAX * ENERGY_SCENARIO_SHOCK,
                                          MACRO_BETA_PLAUSIBLE_MAX * ENERGY_SCENARIO_SHOCK)
    assert (h.min_value, h.max_value) == (-MACRO_BETA_PLAUSIBLE_MAX * HY_SPREAD_SCENARIO_SHOCK,
                                          MACRO_BETA_PLAUSIBLE_MAX * HY_SPREAD_SCENARIO_SHOCK)
    assert (f.min_value, f.max_value) == (-FX_CONTRIBUTION_PCT_CLAMP, FX_CONTRIBUTION_PCT_CLAMP)
    assert all(b.bound_type == "PLAUSIBILITY" for b in (e, h, f))


def test_a_negative_scenario_impact_and_a_value_at_the_fx_clamp_are_inside_the_bounds():
    import pandas as pd
    from shared.statistical_audit.invariants import check_bounds

    for metric, value in (("energy_sensitivity_pct", -0.09), ("hy_spread_sensitivity_pct", -0.08),
                          ("hy_spread_sensitivity_pct", 0.09), ("fx_contribution_pct", 5.0),
                          ("fx_contribution_pct", -5.0), ("fx_contribution_pct", 1.7)):
        assert check_bounds(pd.DataFrame({metric: [value]}), metric, get_metric_bound(metric)).n_breaches == 0


def test_a_value_beyond_what_the_beta_breaker_allows_is_still_flagged():
    import pandas as pd
    from shared.statistical_audit.invariants import check_bounds

    bound = get_metric_bound("energy_sensitivity_pct")
    assert check_bounds(pd.DataFrame({"energy_sensitivity_pct": [bound.max_value + 0.5]}),
                        "energy_sensitivity_pct", bound).n_breaches == 1


def test_genuine_unit_fraction_metrics_keep_the_zero_one_bound():
    for metric in ("momentum_rank", "alpha_persistence", "regime_coverage_ratio", "pct_negative_months"):
        b = get_metric_bound(metric)
        assert (b.min_value, b.max_value) == (0.0, 1.0)
