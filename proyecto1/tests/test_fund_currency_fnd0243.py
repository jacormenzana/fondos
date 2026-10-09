# proyecto1/tests/test_fund_currency_fnd0243.py
# -*- coding: utf-8 -*-
"""
FND-0243 step 2: the share-class currency must be a FACT before a NAV can be converted to EUR.

Three generic signals were measured on the 2,950 active funds before being added (2026-10-09; 99 had no
Fund_Currency, 15 remain unresolved and are excluded from EUR scoring instead of being assumed EUR):
  - English PRIIPs cost row and the footnote marker in _detect_fund_currency ("Total costs 194 EUR",
    "Costes totales* 102 EUR");
  - the PRIIPs investment example ("Example investment: EUR 10,000", "Inversión: 10.000 EUR");
  - the hedged-class token of the name (detect_hedged_class_currency_from_name: "EURHDG", "USDHD", "EUR HD").
The text fragments below are verbatim excerpts of real KIDs (ISIN in each test). R-7: no pipeline/core.io import.
"""
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from kiid_parser import _detect_fund_currency  # noqa: E402
from classify_utils import (  # noqa: E402
    detect_fund_currency_from_name, detect_hedged_class_currency_from_name,
)


# --- KIID text: English cost row, footnote marker ----------------------------------------------------------

def test_english_total_costs_row_amount_then_currency():
    # LU1890796136 ABN A.P US ESG EQ AH EURH AC
    text = "Total Costs 194 EUR 972 EUR\nAnnual cost impact* 2.0% 2.2% each year"
    assert _detect_fund_currency(text, "EN") == "EUR"


def test_english_total_costs_row_currency_then_amount():
    # LU2867168655 APOLLO A E ELTIF Y100 EURHD AC
    text = "Total costs EUR 3,108 Annual cost impact* 3.90%"
    assert _detect_fund_currency(text, "EN") == "EUR"


def test_spanish_cost_row_with_footnote_marker():
    # LU1720050985 ALLIANZ BEST STY RT EURHDG ACC
    text = "Costes totales* 102 EUR 721 EUR Incidencia anual de los costes (*)"
    assert _detect_fund_currency(text, "ES") == "EUR"


def test_cost_row_outranks_the_asset_denomination_sentence():
    # LU0110451209 CAPITAL GLOB HIGH INCOME OPP B -- "Class B EUR (LU0110451209)"; persisted USD came from the
    # asset sentence "denominated in USD", which describes the portfolio, not the share class.
    text = ("Capital Group Global High Income Opportunities (LUX) (the \"fund\"), Class B EUR (LU0110451209)\n"
            "Bonds denominated in USD and various national currencies.\n"
            "Example Investment: 10,000 EUR\nTotal costs 174 EUR 868 EUR")
    assert _detect_fund_currency(text, "EN") == "EUR"


def test_prose_mentioning_total_costs_is_not_a_currency():
    text = "The total costs take into account one-off, ongoing and incidental costs. Fund currency USD."
    assert _detect_fund_currency(text, "EN") != "EUR"


# --- KIID text: investment example ----------------------------------------------------------------------------

def test_investment_example_currency_then_amount():
    # LU1770034509 ALL CHINA EQUITY A EURHDG ACC (no cost row in the cached text)
    assert _detect_fund_currency("Recommended Holding Period: 5 years  Example investment: EUR 10,000", "EN") == "EUR"


def test_investment_example_spanish_amount_then_currency():
    # ES0125756009 DB BOLSA GLOBAL A
    assert _detect_fund_currency("Inversión: 10.000 EUR Periodo de mantenimiento recomendado", "ES") == "EUR"
    assert _detect_fund_currency("suponiendo que invierta 10.000 USD. Los escenarios", "ES") == "USD"


def test_investment_example_with_symbol():
    # LU1670722757 M&G (LUX) GLOBAL CH EURHDG INC
    assert _detect_fund_currency("based on an investment of €10.000", "EN") == "EUR"


# --- Name: hedged-class token ------------------------------------------------------------------------------------

def test_hedged_token_variants():
    assert detect_hedged_class_currency_from_name("ABN A.P US ESG EQ AH EURH AC") == "EUR"
    assert detect_hedged_class_currency_from_name("ABRDN ST DIV INC A EURHDG ACC") == "EUR"
    assert detect_hedged_class_currency_from_name("ALGEBRIS FINAN INC R USDHD ACC") == "USD"
    assert detect_hedged_class_currency_from_name("ALGEBRIS IG FIN CRD R GBPHD AC") == "GBP"
    assert detect_hedged_class_currency_from_name("CGR GLOB H IN OP ZH EUR HD ACC") == "EUR"
    assert detect_hedged_class_currency_from_name("SEILERN AMERICA HR (EURHDG)") == "EUR"
    assert detect_hedged_class_currency_from_name("ALLIANZ BEST AT EURHEDGED ACC") == "EUR"
    assert detect_hedged_class_currency_from_name("JPM US VALUE A EUR HDG ACC") == "EUR"


def test_hedged_token_rejects_words_and_plain_suffixes():
    assert detect_hedged_class_currency_from_name("BNY MELLON EUROLAND BOND A") is None   # EUR + O, not a token
    assert detect_hedged_class_currency_from_name("JPM GLOBAL SELECT EQ A EUR ACC") is None  # no hedge marker
    assert detect_hedged_class_currency_from_name("NEUBERGER S D E M D IEURHDG AC") is None  # glued to a letter
    assert detect_hedged_class_currency_from_name("CAPITAL NEW PERSPECTIVE BH ACC") is None  # no currency
    assert detect_hedged_class_currency_from_name(None) is None
    assert detect_hedged_class_currency_from_name("") is None


def test_two_different_hedged_tokens_are_ambiguous():
    assert detect_hedged_class_currency_from_name("X FUND USDH TO EURH ACC") is None


def test_trailing_suffix_extractor_is_unchanged():
    # The suffix function keeps its documented behaviour; the pipeline consults the token only without a suffix.
    assert detect_fund_currency_from_name("JPM US VALUE A EUR HDG ACC") is None
    assert detect_fund_currency_from_name("JPM GLOBAL NATURL RES A USD ACC") == "USD"


def test_representative_kiid_of_a_sister_class_loses_to_the_name_token():
    # IE00BWY56W81 ALGEBRIS FINANCIA R USDHDG ACC: its KIID is the representative document of Class R EUR,
    # so the text says EUR; the class itself is USD. Pipeline precedence: suffix > hedged token > KIID text.
    name = "ALGEBRIS FINANCIA R USDHDG ACC"
    text = ("This document in respect of the Class R EUR is a representative key investor information document. "
            "Example inversion: EUR 10 000")
    by_name = detect_fund_currency_from_name(name) or detect_hedged_class_currency_from_name(name)
    assert by_name == "USD"
    assert _detect_fund_currency(text, "EN") == "EUR"
    assert (by_name or _detect_fund_currency(text, "EN")) == "USD"
