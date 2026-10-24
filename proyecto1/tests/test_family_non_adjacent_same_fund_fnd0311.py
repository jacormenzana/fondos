# proyecto1/tests/test_family_non_adjacent_same_fund_fnd0311.py
# -*- coding: utf-8 -*-
"""FND-0311: with the official-name family key every class of a family is the same fund by the strongest signal, so rule 4 may settle NON-ADJACENT natures of an
ACTIVE family by the reference class's evidence when its confidence is high enough (Franklin Alternative Strategies: APF class Mixtos vs siblings Alternativo).
Pure: no database (R-7)."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proyecto1.core.fund_family_builder import _NON_ADJACENT_SAME_FUND_MIN_CONF, resolve_family_nature_by_reference  # noqa: E402
from test_fund_family_arbitration_fnd0244 import EQUITY, _m  # noqa: E402

# the shared bond template resolves to Renta Fija Corto Plazo with confidence 0.93; Alternativo is NOT adjacent to it
EUR = _m("A1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo")
ODD = _m("B1", "DWS FLOAT RATE NOTE I EURHDG", "Alternativo")


def test_without_the_parameter_an_active_non_adjacent_family_is_still_left_for_review():
    assert resolve_family_nature_by_reference([EUR, ODD], allow_non_adjacent=False) == (None, [], "non-adjacent natures: left for review")


def test_a_confident_reference_class_settles_a_non_adjacent_family_when_the_caller_asks():
    nature, fix, why = resolve_family_nature_by_reference([EUR, ODD], allow_non_adjacent=False, non_adjacent_min_conf=_NON_ADJACENT_SAME_FUND_MIN_CONF)
    assert (nature, fix) == ("Renta Fija Corto Plazo", ["B1"]) and "conf 0.93" in why


def test_a_reference_class_below_the_confidence_floor_leaves_the_family_for_review():
    nature, fix, why = resolve_family_nature_by_reference([EUR, ODD], allow_non_adjacent=False, non_adjacent_min_conf=0.99)
    assert nature is None and fix == [] and "confidence 0.93 < 0.99" in why


def test_restantes_is_never_settled_whatever_the_parameter():
    members = [_m("A1", "X EUR", "Restantes"), _m("B1", "X USD", "Monetario", ccy="USD")]
    assert resolve_family_nature_by_reference(members, allow_non_adjacent=False, non_adjacent_min_conf=0.0)[0] is None


def test_the_decision_is_still_the_reference_evidence_never_a_default_nature():
    a = _m("A1", "X EUR", "Mixtos", text=EQUITY, band=5)                       # the reference resolves to Renta Variable, not one of the two natures
    b = _m("B1", "X USD", "Monetario", ccy="USD", text=EQUITY, band=5)
    nature, fix, why = resolve_family_nature_by_reference([a, b], allow_non_adjacent=False, non_adjacent_min_conf=0.0)
    assert nature is None and "not one of the family's natures" in why


def test_adjacent_natures_are_unaffected_by_the_parameter():
    a = _m("A1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo")
    b = _m("B1", "DWS FLOAT RATE NOTE I EURHDG", "Renta Fija Flexible")
    base = resolve_family_nature_by_reference([a, b], allow_non_adjacent=False)
    assert resolve_family_nature_by_reference([a, b], allow_non_adjacent=False, non_adjacent_min_conf=0.99) == base


def test_the_floor_is_the_documented_constant():
    assert _NON_ADJACENT_SAME_FUND_MIN_CONF == 0.80
