# proyecto1/tests/test_dws_english_bonds_mandate_and_geldmkt.py
# -*- coding: utf-8 -*-
"""The DWS residual of FND-0237 / FND-0244 (R-7: classify_utils only, no DB).

DWS FLOAT RATE NOTE: the EUR class resolved to Renta Fija Corto Plazo, the USD class (same template: "the fund invests in government and
corporate bonds ... average duration ... a maximum of 12 months") to Renta Fija Flexible, because no presence phrase matched the English
"invests in ... bonds" and the text returned None. The same template belongs to DWS VORSORG GELDMKT, a money-market fund that only its NAME
identifies, so the German abbreviation has to vote Monetario in the same change.
Measured on the 2,950 active funds with the full resolver: exactly 2 resolved natures change (the USD class -> Renta Fija Corto Plazo, the
Geldmkt fund None -> Monetario, which is its stored value); 2 KIID detections change without moving the resolved nature (Janus Henderson Biotech).
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from proyecto1.core.classify_utils import (  # noqa: E402
    detect_nature_from_kiid, detect_nature_from_prefilter, resolve_rf_subtype, resolve_nature_evidence,
)

PAD = "This document provides key information about this investment product. " * 4


def _kiid(objective: str) -> str:
    return PAD + "Objectives and investment policy. " + objective


DWS = ("The objective of the investment policy is to generate a return in U.S. dollars. In order to achieve this, the fund invests in "
       "government and corporate bonds denominated in or hedged against the USD. The average duration of the fund is a maximum of 12 months "
       "and is achieved by employing suitable derivatives, among other things. The fund is subject to various risks.")


def test_the_english_invests_in_bonds_mandate_is_recognised_as_a_bond_fund():
    t = _kiid(DWS)
    assert detect_nature_from_kiid(t) == "_RF_pending"
    assert resolve_rf_subtype("dws float rate note ld usd inc", t) == "RF_Corto"           # the duration decides the subtype, as for the EUR class


def test_the_usd_class_now_resolves_like_its_eur_sibling():
    n, conf, trace = resolve_nature_evidence("dws float rate note ld usd inc", _kiid(DWS), None, 2, "Fixed Income")
    assert n == "Renta Fija Corto Plazo" and trace["kiid"] == "Renta Fija Corto Plazo"


def test_a_modal_may_invest_in_bonds_is_not_a_mandate():
    t = _kiid("The fund invests primarily in common stocks of companies worldwide. The fund may invest in bonds issued by governments.")
    assert detect_nature_from_kiid(t) == "Renta Variable"


def test_an_equity_fund_that_invests_in_equities_and_bonds_keeps_going_through_the_existing_arbitration():
    """Presence only: with an equity signal the text reaches has_equity + has_bonds, not _RF_pending."""
    t = _kiid("The fund invests in equities of companies worldwide and in bonds of governments, to a balanced proportion.")
    assert detect_nature_from_kiid(t) != "_RF_pending"


def test_the_german_money_market_abbreviation_votes_monetario_by_name():
    for name in ("dws vorsorg geldmkt lc eur acc", "dws geldmarkt fonds", "xyz geldmkt eur"):
        assert detect_nature_from_prefilter(name) == "Monetario", name


def test_the_geldmkt_fund_resolves_to_monetario_even_though_its_text_reads_like_a_bond_fund():
    n, conf, trace = resolve_nature_evidence("dws vorsorg geldmkt lc eur acc", _kiid(DWS.replace("U.S. dollars", "euro").replace("USD", "euro")),
                                             None, 1, "Rate")
    assert n == "Monetario"
