# proyecto1/tests/test_cost_currency_fnd0279.py
# -*- coding: utf-8 -*-
"""FND-0279: one currency vocabulary for the cost parsers (cost_currency.py) and what depends on it. Before it, "Costes totales 808 JPY 1874 JPY" was not read as
amounts (JPY was in none of six duplicated lists), "Total costs (EUR) 278 1,105" (currency in the label, bare numbers) left four funds with a garbage 5-year total, and
`fund_master.KID_Currency` was set for half of the funds because the example base was only accepted as "10.000 EUR" / "10,000 USD". The real KID text is in
`_fnd0279_kid_slices`. No pipeline imports (R-7)."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
for _p in (_CORE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cost_currency as cc  # noqa: E402
import cost_format_router as router  # noqa: E402
import cost_table_parser as p  # noqa: E402
import priips_cost_extractor as pe  # noqa: E402
from _fnd0279_kid_slices import BARE_FED, EUR_AFTER_USD_FED, JPY_FED, JPY_MILLION_FED  # noqa: E402


def _rows(text, isin):
    out = pe.extract_priips_costs(text=text, isin=isin)
    return {r["Horizon_Years"]: r for r in out["_cost_schedule_rows"]}, out


# ---- the vocabulary -------------------------------------------------------------------------------------------------------------------------------------

def test_the_six_currencies_are_in_the_shared_vocabulary():
    for code in ("EUR", "USD", "GBP", "CHF", "JPY", "SEK", "NOK", "DKK", "PLN", "CZK"):
        assert code in cc.CURRENCY_CODES
    assert "¥" not in cc.SYMBOL_TO_CODE            # the yen sign is also the yuan sign: only the ISO code is read


def test_every_cost_parser_uses_that_vocabulary():
    assert p._CURRENCY == cc.CURRENCY and p._BASE_CUR == cc.CURRENCY
    for code in ("JPY", "SEK", "NOK", "DKK", "PLN", "CZK"):
        assert p._extract_eur_list_from_cell(f"Costes totales {code} 120 {code} 980") == [120.0, 980.0]
        assert p._extract_eur_list_from_cell(f"Total costs 120 {code} 980 {code}") == [120.0, 980.0]


# ---- bare numbers after a currency written in the label ----------------------------------------------------------------------------------------------------

def test_bare_numbers_after_the_label_currency():
    assert p._extract_eur_list_from_cell("Total costs (EUR) 278 1,105") == [278.0, 1105.0]
    assert p._extract_eur_list_from_cell("Total costs (USD) 170 807") == [170.0, 807.0]


def test_bare_numbers_are_read_only_without_a_currency_marked_amount_and_only_at_the_end_of_the_line():
    assert p._extract_eur_list_from_cell("Total costs (EUR) 278 EUR 1,105 EUR") == [278.0, 1105.0]       # the marked amounts win
    assert p._extract_eur_list_from_cell("Annual cost impact (%) 2.8% 2.7%") == []
    assert p._extract_eur_list_from_cell("Total costs (EUR) see the table below 278") == []


def test_real_kid_with_bare_numbers_reads_both_columns():
    rows, _ = _rows(BARE_FED, "LU1770034509")
    assert rows[1.0]["Total_Costs_EUR"] == 278.0 and rows[5.0]["Total_Costs_EUR"] == 1105.0


# ---- JPY ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_real_jpy_kid_reads_the_five_year_total():
    rows, out = _rows(JPY_FED, "LU0512093542")
    assert rows[1.0]["Total_Costs_EUR"] == 808.0 and rows[5.0]["Total_Costs_EUR"] == 1874.0
    assert out["KID_Currency"] == "JPY"


def test_real_jpy_kid_with_a_million_base_keeps_a_coherent_percentage():
    from cost_totals_arbitration import extract_priips_costs_arbitrated
    out, _ = extract_priips_costs_arbitrated(JPY_MILLION_FED, JPY_MILLION_FED, "LU1756323520")       # the production path: the percentages are derived here
    rows = {r["Horizon_Years"]: r for r in out["_cost_schedule_rows"]}
    assert rows[1.0]["Total_Costs_EUR"] == 70139.0 and rows[5.0]["Total_Costs_EUR"] == 203968.0
    assert abs(rows[1.0]["Total_Costs_Pct"] - 7.0139) < 1e-6          # base JPY 1 000 000, not the 10 000 default
    assert out["KID_Currency"] == "JPY"


# ---- KID_Currency ---------------------------------------------------------------------------------------------------------------------------------------

def test_the_base_is_read_in_any_grouping_and_order():
    for text, code in (("Se invierten 10.000 EUR", "EUR"), ("EUR 10,000 is invested", "EUR"), ("Investment 10 000 USD", "USD"), ("invested JPY 10,000", "JPY"),
                       ("CHF 10'000", "CHF"), ("10000 GBP", "GBP"), ("Investment EUR 10'000 If you cash", "EUR"), ("10.000,00 SEK", "SEK"), ("€ 10.000", "EUR")):
        assert router.detect_kid_currency(text) == code, text
    assert router.detect_kid_currency("no base here") is None


def test_the_currency_of_the_totals_row_beats_an_earlier_example_in_another_currency():
    # LU0368229703: an earlier example in USD, the cost table (and the fund) in EUR
    assert cc.detect_base_currency(EUR_AFTER_USD_FED) == "USD"          # the earliest base alone would say USD ...
    assert router.detect_kid_currency(EUR_AFTER_USD_FED) == "EUR"       # ... the totals row says EUR


def test_without_a_totals_row_the_base_nearest_before_it_then_the_earliest_decide():
    text = "Investment USD 10,000\nsomething\nInvestment EUR 10,000\nTotal costs 100 200\n"
    assert router.detect_kid_currency(text) == "EUR"                    # nearest before the label
    assert router.detect_kid_currency("Investment USD 10,000 and later EUR 10,000") == "USD"      # no label: the earliest


def test_a_totals_row_naming_two_currencies_decides_nothing():
    assert router.detect_kid_currency("Investment EUR 10,000\nTotal costs EUR 100 USD 200\n") == "EUR"
