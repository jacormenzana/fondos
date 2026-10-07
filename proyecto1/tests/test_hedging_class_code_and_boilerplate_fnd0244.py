# proyecto1/tests/test_hedging_class_code_and_boilerplate_fnd0244.py
# -*- coding: utf-8 -*-
"""FND-0244 part 2 - Hedging_Policy. R-7: pure, no DB.

Two defects, both measured on the 2,950 active funds:
 1. kiid_parser.ES_UNHEDGED matched "no esta cubierta" inside the PRIIPs compensation-scheme boilerplate ("dicha perdida no esta cubierta por
    ningun regimen de compensacion ...") and, evaluated before ES_HEDGED, turned classes whose own text says "clase de participaciones con
    cobertura cambiaria" into UNHEDGED: 14 funds (0 remain; the parser then agrees with the canonical detector on every fund).
 2. Share-class codes that end in H (AH, BH, ZH, PH, BDH, BGDH, ZDH, PDH) mean "hedged" and survive the 30-character name cut when the
    explicit marker does not: 28 funds change to Hedged, 9 confirmed by their KIID, 0 contradicted.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
for _p in (_ROOT, os.path.join(_ROOT, "proyecto1")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from core.fund_characterizer import detect_currency_hedged  # noqa: E402
from core.kiid_parser import _detect_hedging_policy  # noqa: E402

BOILERPLATE = ("Si no podemos pagarle lo que se le debe, podría perder toda su inversión. Dicha pérdida no está cubierta por ningún régimen de \n"
               "compensación o protección para los inversores.")
HEDGED_CLASS = "Es una clase de participaciones con cobertura cambiaria. Trata de reducir los efectos que producen en su inversión las fluctuaciones."


# ---------------------------------------------------------------- the parser
def test_the_compensation_scheme_boilerplate_is_not_an_unhedged_statement():
    assert _detect_hedging_policy(BOILERPLATE, "ES") is None


def test_a_hedged_class_is_hedged_even_when_the_boilerplate_is_in_the_same_document():
    assert _detect_hedging_policy(BOILERPLATE + "\n" + HEDGED_CLASS, "ES") == "HEDGED"
    assert _detect_hedging_policy(HEDGED_CLASS + "\n" + BOILERPLATE, "ES") == "HEDGED"


@pytest.mark.parametrize("text", [
    "La clase de acciones no está cubierta frente al euro.",
    "La divisa de esta clase no está cubierta.",
    "Esta clase no está cubierta por el fondo frente a las fluctuaciones de la divisa de la cartera.".replace("no está cubierta por el fondo", "sin cobertura"),
])
def test_a_genuine_currency_statement_is_still_unhedged(text):
    assert _detect_hedging_policy(text, "ES") == "UNHEDGED"


@pytest.mark.parametrize("text", [
    "Dicha pérdida no está cubierta por el fondo de garantía de depósitos.",
    "La pérdida no está cubierta por el sistema de compensación de inversores.",
])
def test_other_compensation_scheme_wordings_are_not_a_currency_statement(text):
    assert _detect_hedging_policy(text, "ES") is None


# ---------------------------------------------------------------- the name signal
@pytest.mark.parametrize("name", [
    "ms sicav global brands ah", "capital new perspective bh acc", "capital new perspective zh acc", "m&g (lux) optimal income ah acc",
    "capital group glbl hi opps bdh", "capital g nw prsp bgdh euh inc", "capital new prspctv zdh eur in", "capital g. new persp pdh eur",
    "robeco hy bonds bh (eur) inc", "capital emerging markts opp bh",
])
def test_class_codes_ending_in_h_are_hedged(name):
    assert detect_currency_hedged(name) == "Hedged"


@pytest.mark.parametrize("name", [
    "capital new perspective b acc", "ms sicav global brands a", "fidelity euro bond fund", "pictet phoenix fund",
    "xyz sh eur acc", "xyz fund eh", "capital new perspective zdg",
])
def test_names_without_such_a_token_are_not_touched(name):
    assert detect_currency_hedged(name) is None


def test_an_explicit_unhedged_statement_in_the_name_still_wins_over_the_class_code():
    assert detect_currency_hedged("fund ah unhedged") == "Unhedged"


def test_the_existing_explicit_markers_are_unchanged():
    for name in ("fund a eurhdg acc", "fund a usd hedged", "fund (h) acc", "fund gbph acc"):
        assert detect_currency_hedged(name) == "Hedged", name


# ---------------------------------------------------------------- the PERSISTED path (kiid_parser) -- the characterizer's Currency_Hedged is not stored since v20
from core.kiid_parser import parse_kiid_generic  # noqa: E402

_NO_HEDGE_TEXT = "This document provides key information about this investment product. " * 6


@pytest.mark.parametrize("name", ["CAPITAL NEW PERSPECTIVE BH ACC", "MS SICAV GLOBAL BRANDS AH", "CAPITAL G NW PRSP BGDH EUH INC",
                                  "M&G (LUX) OPTIMAL INCOME AH ACC", "CAPITAL G. NEW PERSP PDH EUR"])
def test_the_class_code_sets_the_stored_hedging_policy_through_the_parser(name):
    r = parse_kiid_generic(_NO_HEDGE_TEXT, None, None, name)
    assert r["Hedging_Policy"] == "HEDGED" and "HEDGING_FROM_NAME_CLASSCODE" in r["Inference_Trace"]


def test_the_explicit_marker_keeps_its_own_trace_and_a_plain_class_is_untouched():
    explicit = parse_kiid_generic(_NO_HEDGE_TEXT, None, None, "FUND A USD HEDGED")
    assert explicit["Hedging_Policy"] == "HEDGED" and "HEDGING_FROM_NAME_CLASSCODE" not in explicit["Inference_Trace"]
    plain = parse_kiid_generic(_NO_HEDGE_TEXT, None, None, "CAPITAL NEW PERSPECTIVE B ACC")
    assert plain["Hedging_Policy"] is None


def test_one_definition_serves_both_detectors():
    from core import classify_utils, fund_characterizer, kiid_parser
    assert fund_characterizer.HEDGED_CLASS_CODE_RE is classify_utils.HEDGED_CLASS_CODE_RE is kiid_parser.HEDGED_CLASS_CODE_RE
