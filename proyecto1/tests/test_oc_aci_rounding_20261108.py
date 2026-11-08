# proyecto1/tests/test_oc_aci_rounding_20261108.py
# -*- coding: utf-8 -*-
"""FND-0034 (owner decision 2026-11-08) -- the ACI is printed with ONE decimal: an OC equal to it is the publisher's rounding when ACI = OC + transaction costs.

(1) `cost_scale.resolve_ongoing_charge` no longer discards a clean OC candidate in that case, and still does in every other (R-7: no pipeline.py, no core.io, no DB).
(2) `oc_resolution.refine_fund_aci` stores the exact two-decimal ACI (0,3 printed -> 0,34), on every pass (CACHED included), from the EFFECTIVE values (R-4).
LU2132880597: management 0,30 %, transaction costs 0,04 %, printed annual impact 0,3 %.
"""
import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_CORE = os.path.join(_ROOT, "proyecto1", "core")
for _p in (_ROOT, _CORE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cost_scale import contaminated_by_aci, resolve_ongoing_charge  # noqa: E402
from oc_resolution import refine_fund_aci, resolve_fund_oc          # noqa: E402

PRIIPS = "Documento de datos fundamentales PRIIP. Costes totales 34 EUR 191 EUR. Incidencia anual de los costes 0,3 % 0,3 % cada año"


def test_an_oc_equal_to_the_aci_is_accepted_when_the_publisher_rounding_explains_it_and_rejected_otherwise():
    # the new fund: management 0.30 -> OC 0.003; ACI printed 0.3; transaction costs 0.04
    assert resolve_ongoing_charge('PRIIPS_KID', 0.30, None, None, 0.3, 0.3, transaction_cost_pct=0.04) == (0.003, 'management_fee')
    # no transaction cost on record, an entry fee, or a transaction cost of half a unit or more: the equality is still the FND-0034 contamination
    assert resolve_ongoing_charge('PRIIPS_KID', 0.30, None, None, 0.3, 0.3) == (None, 'none')
    assert resolve_ongoing_charge('PRIIPS_KID', 0.30, None, None, 0.3, 0.3, transaction_cost_pct=0.04, entry_fee_pct=3.0) == (None, 'none')
    assert resolve_ongoing_charge('PRIIPS_KID', 0.30, None, None, 0.3, 0.3, transaction_cost_pct=0.05) == (None, 'none')
    assert resolve_ongoing_charge('PRIIPS_KID', 0.30, None, None, 0.3, 0.3, transaction_cost_pct=0.04, performance_fee_pct=20.0) == (None, 'none')


def test_contaminated_by_aci_judges_each_equal_aci_on_its_own():
    assert contaminated_by_aci(0.003, 0.3, 0.3, 0.04) is False
    assert contaminated_by_aci(0.003, 0.3, 0.3) is True                                        # the old behaviour whenever the components are unknown
    assert contaminated_by_aci(0.003, 0.34, 0.3, 0.04) is False                               # only the 1-year ACI equals the OC, and the rounding explains that one
    assert contaminated_by_aci(0.003, 0.5, 0.6, 0.04) is False and contaminated_by_aci(None, 0.3, 0.3, 0.04) is False


def test_the_stored_components_are_the_effective_ones_on_a_cached_pass():
    # CACHED: the record carries no cost fields; every component comes from the stored tuple (mgmt, rhp, 1y, tc, entry, exit, perf)
    value, source, _ = resolve_fund_oc(PRIIPS, None, {}, (0.30, 0.3, 0.3, 0.04, None, 0.0, None))
    assert (value, source) == (0.003, 'management_fee')
    assert resolve_fund_oc(PRIIPS, None, {}, (0.30, 0.3, 0.3))[:2] == (None, 'none')          # the old 3-tuple still works and stays strict


def test_the_exact_aci_is_stored_for_both_horizons_from_the_effective_values():
    stored = (0.3, 0.3, 0.04, None, 0.0, None)                                                # ACI_RHP, ACI_1Y, TC, entry, exit, perf
    assert refine_fund_aci(0.003, {}, stored) == {'ACI_RHP': 0.34, 'ACI_1Y': 0.34}
    # this pass's extraction wins over the stored value (R-4): a fresh ACI of 0.34 is already exact, a fresh 1-year 0.3 is refined
    assert refine_fund_aci(0.003, {'ACI_RHP': 0.34, 'ACI_1Y': 0.3, 'Transaction_Cost_Pct': 0.04}, stored) == {'ACI_1Y': 0.34}


def test_nothing_is_refined_without_the_evidence_and_the_refinement_is_idempotent():
    assert refine_fund_aci(None, {}, (0.3, 0.3, 0.04)) == {}                                  # no OC, no judgement
    assert refine_fund_aci(0.003, {}, (0.3, 0.3, None)) == {}                                 # no transaction cost on record
    assert refine_fund_aci(0.003, {}, (0.3, 0.3, 0.04, 2.0)) == {}                            # an entry fee is charged: ACI is not OC + TC
    assert refine_fund_aci(0.003, {}, (0.3, 0.3, 0.0)) == {}                                  # ACI = OC for real
    assert refine_fund_aci(0.004, {}, (0.3, 0.3, 0.04)) == {}                                 # the ACI differs from the OC: the document's figure stays
    assert refine_fund_aci(0.003, {}, (0.34, 0.34, 0.04)) == {}                               # already exact


def test_the_pipeline_runs_the_refinement_after_the_single_oc_writer_on_every_pass():
    src = open(os.path.join(_CORE, "pipeline.py"), encoding="utf-8").read()
    assert src.count("_refine_fund_aci(") == 1 and "NORM-ACI-REFINE" in src
    assert src.index("fund_master_record[\"Ongoing_Charge_Recurrent\"] = _oc_value") < src.index("_refine_fund_aci(")
    assert src.index("_refine_fund_aci(") < src.index("publish_fund(conn, fund_master_record, None, kiid_record,")
