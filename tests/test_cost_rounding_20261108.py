"""
tests/test_cost_rounding_20261108.py -- the ONE definition of "the printed ACI is OC + transaction costs, rounded by the publisher" (shared/cost_rounding.py; owner decision 2026-11-08, FND-0034).

LU2132880597 is the case that failed the cost audit: management 0,30 + transaction 0,04 = 0,34 (30 EUR + 4 EUR = 34 EUR on 10,000), printed "0,3". R-7: pure, no DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.cost_rounding import ACI_PUBLISHED_UNIT_PP, aci_rounding_explained, refined_aci_pct  # noqa: E402


def test_the_publisher_rounding_explains_an_aci_equal_to_the_oc_and_the_exact_value_is_oc_plus_transaction_costs():
    assert aci_rounding_explained(0.30, 0.3, 0.04) is True                                   # LU2132880597
    assert refined_aci_pct(0.30, 0.3, 0.04) == 0.34
    assert refined_aci_pct(0.8999999999999999, 0.9, 0.01) == 0.91                              # a ratio x 100 that is not bit-exact still lands on two clean decimals
    assert refined_aci_pct(0.10, 0.1, 0.02, entry_fee_pct=0.0, exit_fee_pct=None, performance_fee_pct=0) == 0.12   # zero and absent fees are the same thing


@pytest.mark.parametrize("tc, explained", [(0.0, True), (0.04, True), (0.0499, True), (0.05, False), (0.06, False), (0.5, False)])
def test_the_explanation_stops_at_half_a_published_unit_and_the_edge_itself_is_ambiguous(tc, explained):
    assert aci_rounding_explained(0.30, 0.3, tc) is explained


@pytest.mark.parametrize("kw", [dict(entry_fee_pct=0.5), dict(exit_fee_pct=1.0), dict(performance_fee_pct=15.0)])
def test_any_other_charge_means_the_rounding_does_not_explain_the_equality(kw):
    assert aci_rounding_explained(0.30, 0.3, 0.04, **kw) is False
    assert refined_aci_pct(0.30, 0.3, 0.04, **kw) is None


def test_unknown_components_explain_nothing_and_nothing_is_ever_refined_from_them():
    assert aci_rounding_explained(0.30, 0.3, None) is False                                   # no transaction cost on record: cannot tell
    assert aci_rounding_explained(None, 0.3, 0.04) is False and aci_rounding_explained(0.30, None, 0.04) is False
    assert aci_rounding_explained(0.30, 0.3, float("nan")) is False
    assert refined_aci_pct(0.30, 0.3, None) is None and refined_aci_pct(None, 0.3, 0.04) is None


def test_only_an_aci_equal_to_the_oc_is_refined_and_a_zero_transaction_cost_changes_nothing():
    assert refined_aci_pct(0.30, 0.4, 0.04) is None                                           # ACI differs from the OC: the document's figure stays
    assert refined_aci_pct(0.30, 0.34, 0.04) is None                                          # already exact
    assert refined_aci_pct(0.30, 0.3, 0.0) is None                                            # ACI = OC for real: explained, but there is nothing to refine
    assert aci_rounding_explained(0.30, 0.3, 0.0) is True


def test_the_refinement_is_idempotent():
    once = refined_aci_pct(0.30, 0.3, 0.04)
    assert once == 0.34 and refined_aci_pct(0.30, once, 0.04) is None


def test_the_unit_and_the_tolerances_are_defined_once_and_stay_equal_everywhere():
    from shared.statistical_audit import tolerances as t
    assert t.ACI_PUBLISHED_UNIT_PP is ACI_PUBLISHED_UNIT_PP == 0.10
    import shared.cost_rounding as cr
    assert cr._EQUAL_PP == t.FLOAT_IDENTITY_TOLERANCE                                          # the identity tolerance of OC_NOT_CONTAMINATED
