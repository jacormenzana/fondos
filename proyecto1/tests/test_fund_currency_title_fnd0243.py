# proyecto1/tests/test_fund_currency_title_fnd0243.py
# -*- coding: utf-8 -*-
"""FND-0243 follow-up (2026-10-18): two KIID-text rules of kiid_parser._detect_fund_currency, measured on the stored KIIDs and checked against the documents.

1. CELL-LAYOUT cost table -- "Total Costs / Annual cost impact (*) / 665 EUR 2,045 EUR": the old cost-table rule wanted the currency right after the label, so three active
   Seilern funds kept Fund_Currency NULL (EUR classes of a GBP-base fund; the KID header reads "Seilern World Growth EUR H R Class").
2. LAST-RESORT TITLE rule -- old-format KIIDs with no cost-table amounts name the class and its currency in the first lines ("Algebris Financial Credit Fund Class R EUR",
   "Class A(Mdis)USD"); only the first 600 characters, ISO codes only, exactly one distinct code, and only when every other rule is silent.

The real KID text is in `_fnd0279_kid_slices`. No pipeline imports (R-7)."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
for _p in (_CORE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from kiid_parser import _detect_fund_currency, resolve_fund_currency  # noqa: E402
from _fnd0279_kid_slices import (CELL_LAYOUT_SEILERN, HEAD_ALGEBRIS, HEAD_AMUNDI, HEAD_BNY, HEAD_ELEVA, HEAD_MUZINICH, HEAD_NEUBERGER,  # noqa: E402
                                 HEAD_TEMPLETON, HEAD_THREADNEEDLE)


# ---- 1. cell-layout cost table --------------------------------------------------------------------------------------------------------------------------

def test_real_cell_layout_cost_table_gives_the_class_currency():
    assert _detect_fund_currency(CELL_LAYOUT_SEILERN, "EN") == "EUR"
    assert resolve_fund_currency(CELL_LAYOUT_SEILERN, "EN", "SEILERN WORLD GROWTH HR HEDGE") == "EUR"


def test_label_first_cost_table_synthetic():
    text = "Costs over time\nTotal costs\nAnnual cost impact (*)\n665 USD 2,045 USD\n6.65% 1.65% each year\n"
    assert _detect_fund_currency(text, "EN") == "USD"


def test_a_totals_row_naming_two_currencies_decides_nothing():
    assert _detect_fund_currency("Total costs\nAnnual cost impact (*)\n665 EUR 2,045 USD\n", "EN") is None


# ---- 2. the title rule ---------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("head, expected", [
    (HEAD_ALGEBRIS, "EUR"),          # "Algebris Financial Credit Fund Class R EUR"
    (HEAD_TEMPLETON, "USD"),         # "Templeton Global Total Return Fund Class A(Mdis)USD"
    (HEAD_THREADNEEDLE, "EUR"),      # "Acciones de capitalizacion (EUR) GB00B0WGY707 Clase 1"
    (HEAD_AMUNDI, "EUR"),            # "... SUSTAINABLE INCOME 11/2027 - Y EUR"
    (HEAD_ELEVA, "EUR"),             # "... Class A2 (EUR) acc."
    (HEAD_NEUBERGER, "EUR"),         # "... EUR I Accumulating Class (IE00BDZRX185)"
])
def test_the_class_currency_of_the_title_line(head, expected):
    language = "ES" if head in (HEAD_THREADNEEDLE, HEAD_AMUNDI) else "EN"
    assert _detect_fund_currency(head, language) == expected


def test_a_title_without_an_iso_code_decides_nothing():
    assert _detect_fund_currency(HEAD_BNY, "EN") is None                  # "BNY Mellon ... Euroland Bond": no currency in the title
    assert _detect_fund_currency(HEAD_MUZINICH, "EN") is None             # "Hedged Euro Accumulation R Units": the currency is a word, the base (USD) comes later


def test_two_codes_in_the_title_decide_nothing():
    assert _detect_fund_currency("Key Investor Information Some Fund Class A EUR hedged against USD", "EN") is None


def test_only_the_first_600_characters_are_the_title():
    assert _detect_fund_currency("Key Investor Information. " + "x " * 320 + " Class A EUR", "EN") is None


def test_the_cost_table_rule_outranks_the_title():
    text = "Key Investor Information Fidelity Flex Bond Class E GBP hedged\n" + "x\n" * 5 + "Costes totales 595 EUR 1 200 EUR\n"
    assert _detect_fund_currency(text, "ES") == "EUR"


def test_the_name_suffix_still_outranks_every_kiid_rule():
    text = "Key Investor Information Algebris Fund Class R EUR\nTotal costs 60 EUR 341 EUR\n"
    assert resolve_fund_currency(text, "EN", "ALGEBRIS IG FIN CRD ID USD INC") == "USD"        # FUNDCCY_NAME_WINS: the KID can show the base currency
