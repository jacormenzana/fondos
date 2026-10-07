# proyecto1/tests/test_fund_family_arbitration_fnd0244.py
# -*- coding: utf-8 -*-
"""FND-0244 part 2: family-level nature arbitration (rule 4). R-7: the pure core only, no DB.

The share classes of one fund have ONE nature. Rules 1-3 look at quality flags; rule 4 decides by the evidence of the REFERENCE class (EUR
first, then a class whose KIID yields a vote, then the longest NAV history, then the lowest ISIN), completed with the siblings' benchmark /
Morningstar signals, through the single classifier. Simulated on the live families it resolves Allianz Best Styles AT (Mixtos), CG GLB
ALLOC (Mixtos) and Muzinich Short Duration HY (Renta Fija Corto Plazo) and leaves Franklin Alt St (non-adjacent natures) for review.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from proyecto1.core.fund_family_builder import (  # noqa: E402
    _reference_sort_key, resolve_family_nature_by_reference,
)

PAD = "This document provides key information about this investment product. " * 4
BOND_ST = PAD + ("Objectives and investment policy. The objective of the investment policy is to generate a return in euro. In order to "
                 "achieve this, the fund invests in government and corporate bonds denominated in or hedged against the euro. The average "
                 "duration of the fund is a maximum of 12 months and is achieved by employing suitable derivatives, among other things.")
EQUITY = PAD + ("Investment objective. The fund's investment objective is to provide long-term growth of capital. In pursuing its investment "
                "objective, the fund invests primarily in common stocks that the investment adviser believes have the potential for growth.")
EMPTY_VOTE = PAD + "Objectives. The fund pursues a return. Nothing else is said about the asset classes."


def _m(isin, name, nature, ccy="EUR", nav=100, text=BOND_ST, band=2, bench=None, ms=None):
    return {"ISIN": isin, "Fund_Name": name, "Fund_Nature": nature, "fund_currency": ccy, "nav_n": nav, "kiid_text": text,
            "benchmark_declared": bench, "ms_asset_class": ms, "srri_band": band}


def test_the_eur_class_is_the_reference_even_with_a_shorter_history():
    usd = _m("A2", "X USD", "Renta Fija Flexible", ccy="USD", nav=200)
    eur = _m("B1", "X EUR", "Renta Fija Corto Plazo", ccy="EUR", nav=50)
    assert sorted([usd, eur], key=_reference_sort_key)[0]["ISIN"] == "B1"


def test_without_a_eur_class_a_class_with_a_kiid_vote_comes_first_then_history_then_isin():
    no_vote = _m("A1", "X", "Mixtos", ccy="USD", nav=300, text=EMPTY_VOTE)
    voted_short = _m("B1", "X", "Mixtos", ccy="USD", nav=40)
    voted_long = _m("C1", "X", "Mixtos", ccy="USD", nav=90)
    voted_long_b = _m("A9", "X", "Mixtos", ccy="USD", nav=90)
    order = [m["ISIN"] for m in sorted([no_vote, voted_short, voted_long, voted_long_b], key=_reference_sort_key)]
    assert order == ["A9", "C1", "B1", "A1"]


def test_a_class_flipped_by_its_own_evidence_is_aligned_to_the_reference():
    """Same template text, the USD class landed on Renta Fija Flexible: the family adopts the EUR reference class's resolution."""
    eur = _m("IE1", "DWS FLOAT RATE NOTE LD EUR INC", "Renta Fija Corto Plazo")
    usd = _m("LU2", "DWS FLOAT RATE NOTE LD USD INC", "Renta Fija Flexible", ccy="USD", ms="Fixed Income")
    nature, fix, why = resolve_family_nature_by_reference([eur, usd])
    assert nature == "Renta Fija Corto Plazo" and fix == ["LU2"] and "IE1" in why


def test_the_siblings_benchmark_and_morningstar_signals_complete_a_reference_class_that_has_none():
    """Hedged classes often have no Morningstar row: the sibling's signal is used for the reference class."""
    ref = _m("EUR1", "FUND H EURH", "Mixtos", text=EQUITY, band=5, ms=None)
    sib = _m("USD1", "FUND USD", "Renta Variable", ccy="USD", text=EQUITY, band=6, ms="Equity")
    nature, fix, _ = resolve_family_nature_by_reference([ref, sib])
    assert nature == "Renta Variable" and fix == ["EUR1"]


def test_non_adjacent_natures_restantes_and_consistent_families_are_left_alone():
    assert resolve_family_nature_by_reference([_m("A", "X", "Renta Variable"), _m("B", "X", "Monetario")])[0] is None
    assert resolve_family_nature_by_reference([_m("A", "X", "Restantes"), _m("B", "X", "Renta Variable")])[0] is None
    assert resolve_family_nature_by_reference([_m("A", "X", "Mixtos"), _m("B", "X", "Mixtos")])[0] is None


def test_the_winner_must_be_a_nature_the_family_already_has():
    """The reference class resolves to Renta Variable (equity text) but the family only holds Mixtos / Renta Fija Flexible: no third nature is invented."""
    nature, fix, why = resolve_family_nature_by_reference([_m("A", "X", "Mixtos", text=EQUITY), _m("B", "X", "Renta Fija Flexible", text=EQUITY)])
    assert nature is None and fix == [] and "not one of the family's natures" in why


def test_a_reference_class_without_kiid_text_is_not_decided():
    nature, fix, why = resolve_family_nature_by_reference([_m("A", "X", "Mixtos", text=None), _m("B", "X", "Renta Variable", ccy="USD", text=None)])
    assert nature is None and "no KIID text" in why


# ---------------------------------------------------------------- non-adjacent natures: only when nothing active depends on the family
def test_non_adjacent_natures_are_arbitrated_by_the_reference_evidence_when_the_caller_allows_it():
    """Franklin Alt St: Alternativo vs Mixtos, all retired. The EUR reference class's own evidence decides, never a default nature."""
    eur = _m("A1", "DWS FLOAT RATE NOTE W EUR", "Renta Fija Corto Plazo")                                               # the shared bond template resolves to Renta Fija Corto Plazo
    other = _m("B1", "DWS FLOAT RATE NOTE I EURHDG", "Alternativo")                                                     # not an adjacent pair
    assert resolve_family_nature_by_reference([eur, other])[0] is None                                # default: left for review
    nature, fix, _ = resolve_family_nature_by_reference([eur, other], allow_non_adjacent=True)
    assert nature == "Renta Fija Corto Plazo" and fix == ["B1"]


def test_allowing_non_adjacent_never_invents_a_nature_nor_touches_restantes():
    a = _m("A1", "X EUR", "Mixtos", text=EQUITY, band=5)                                              # the reference resolves to Renta Variable
    b = _m("B1", "X USD", "Monetario", ccy="USD", text=EQUITY, band=5)
    nature, fix, why = resolve_family_nature_by_reference([a, b], allow_non_adjacent=True)
    assert nature is None and "not one of the family's natures" in why
    r = resolve_family_nature_by_reference([_m("A1", "X", "Restantes"), _m("B1", "X", "Monetario")], allow_non_adjacent=True)
    assert r[0] is None
