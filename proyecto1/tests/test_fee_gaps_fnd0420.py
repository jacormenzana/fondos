# proyecto1/tests/test_fee_gaps_fnd0420.py
# -*- coding: utf-8 -*-
"""FND-0420 (Package H): the extraction gaps the independent accuracy reader found. R-7: no database, no pipeline. Wordings are condensed from stored KIDs.

 1. English entry rows that state the charge in words ("This includes distribution costs of 5.25%", "5.00% is the maximum amount you pay").
 2. The English example-investment base ("Example Investment: 10,000 EUR", "EUR 10,000 is invested"), which left the FND-0406 amount reader with no base.
 3. The negation the fee-pair resolver reads: "no cobra gastos de salida ... podria cobrarle hasta un maximo del 1.00%" (Pictet) and "do not charge an exit fee". The extractor's own narrow negation
    is unchanged, because it forces a printed ceiling to 0."""
import os
import sys

import pytest

_CORE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import cost_table_parser as ctp  # noqa: E402

DISTRIBUTION = ("Recommended holding period: 5 years Example Investment: 10,000 EUR Scenarios Composition of costs Entry costs This includes distribution costs of 5.25%. This is the most you will be charged. "
                "The person selling you the product will inform you of the actual charge. Up to 525 EUR Exit costs We do not charge an exit fee for this product, but the person selling you the product may do so. 0 EUR")
MAX_AMOUNT = ("Recommended holding period: 5 years Example Investment: EUR 10,000 Scenarios Composition of costs Entry costs 5.00% is the maximum amount you pay when entering this investment and you could pay less. "
              "If you invest through a third-party the person selling you the product will inform you of the actual charge. Exit costs A charge of up to 1.00% may apply where the Manager suspects excessive trading")
PICTET_ES = ("Costes de entrada Pictet Asset Management (Europe) S.A. no cobra gastos de entrada por este producto, si bien la persona que se lo venda podría cobrarle hasta un máximo del 5.00%. Hasta 500 USD "
             "Costes de salida Pictet Asset Management (Europe) S.A. no cobra gastos de salida por este producto, si bien la persona que se lo venda podría cobrarle hasta un máximo del 1.00%. Hasta 100 USD COSTES CORRIENTES")
PICTET_EN = ("Entry costs Pictet Asset Management (Europe) S.A. do not charge an entry fee for this product, but the person selling you the product may charge you no more than 5.00%. Up to EUR 500 "
             "Exit costs Pictet Asset Management (Europe) S.A. do not charge an exit fee for this product, but the person selling you the product may charge you no more than 1.00%. Up to EUR 101 ONGOING COSTS")


# ── 1. the English description rows ──────────────────────────────────────────────────────────────────────────────

def test_distribution_costs_of_a_percent_gives_the_entry_ceiling():
    assert ctp.entry_cost_ratio_from_description(DISTRIBUTION) == pytest.approx(0.0525)


def test_a_percent_that_is_the_maximum_amount_you_pay_gives_the_entry_ceiling():
    assert ctp.entry_cost_ratio_from_description(MAX_AMOUNT) == pytest.approx(0.05)


def test_the_exit_rows_percent_is_never_read_as_the_entry_charge():
    """LU2441282899 prints 'up to 1.00%' on the EXIT row: the cell ends at the exit label."""
    text = "Entry costs No entry charge is described here Exit costs 1.00% is the maximum amount you pay"
    assert ctp.entry_cost_ratio_from_description(text) is None


def test_no_label_no_wording_or_an_impossible_value_gives_none():
    assert ctp.entry_cost_ratio_from_description("") is None
    assert ctp.entry_cost_ratio_from_description("distribution costs of 5.25% but no entry label anywhere") is None
    assert ctp.entry_cost_ratio_from_description("Entry costs This includes distribution costs of 40.00%. Exit costs") is None      # above the schema ceiling (25 %)
    assert ctp.entry_cost_ratio_from_description("Entry costs 5.00% of the amount you pay. Exit costs") is None                     # the composition parsers read this layout


# ── 2. the English example-investment base ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,base", [
    ("Recommended holding period: 5 years Example Investment: 10,000 EUR Scenarios", 10000),
    ("Recommended holding period: 5 years Example Investment: EUR 10,000 Scenarios", 10000),
    ("the product performs as shown in the moderate scenario. - EUR 10,000 is invested. If you exit", 10000),
    ("assuming you were to invest USD 10,000 in the sub-fund", 10000),                                   # the layout that already worked
])
def test_english_example_investment_bases_are_read(text, base):
    assert ctp.parse_investment_base(text) == base


def test_a_minimum_investment_is_still_not_the_example_base():
    assert ctp.parse_investment_base("The shares have a minimum investment: EUR 100,000 per investor") is None


def test_the_amount_reader_now_runs_on_an_english_amount_only_cell():
    text = "Example Investment: 10,000 EUR Composition of costs Entry costs The most you will be charged. Up to 525 EUR Exit costs 0 EUR"
    assert ctp.entry_cost_ratio_from_amount(text) == pytest.approx(0.0525)


# ── 3. the negation the pair resolver reads ──────────────────────────────────────────────────────────────────────

def test_the_wider_negation_reads_the_pictet_wording_the_narrow_one_does_not():
    assert ctp.EXIT_FEE_NEGATION_ANY.search(PICTET_ES) and not ctp.EXIT_FEE_NEGATION.search(PICTET_ES)
    assert ctp.ENTRY_FEE_NEGATION_ANY.search(PICTET_ES) and not ctp.ENTRY_FEE_NEGATION.search(PICTET_ES)
    assert ctp.EXIT_FEE_NEGATION_ANY.search(PICTET_EN) and not ctp.EXIT_FEE_NEGATION.search(PICTET_EN)
    assert ctp.ENTRY_FEE_NEGATION_ANY.search(PICTET_EN) and not ctp.ENTRY_FEE_NEGATION.search(PICTET_EN)


def test_the_wider_negation_still_reads_everything_the_narrow_one_reads():
    for text in ("No cobramos comisión de salida", "No se cobra ninguna comisión de salida", "We do not charge an exit fee", "no exit fee"):
        assert ctp.EXIT_FEE_NEGATION.search(text) and ctp.EXIT_FEE_NEGATION_ANY.search(text)
    for text in ("No cobramos comisión de entrada", "we do not charge an entry fee", "no entry charge"):
        assert ctp.ENTRY_FEE_NEGATION.search(text) and ctp.ENTRY_FEE_NEGATION_ANY.search(text)


def test_the_wider_negation_does_not_fire_on_a_positive_fee_or_the_other_side():
    assert not ctp.EXIT_FEE_NEGATION_ANY.search("Costes de salida 1,00% de su inversión antes de que se le pague")
    assert not ctp.ENTRY_FEE_NEGATION_ANY.search("Costes de entrada 5,00% del importe que pagará usted. Costes de salida No cobramos comisión de salida")
    assert not ctp.EXIT_FEE_NEGATION_ANY.search("No cobramos comisión de entrada")


def test_the_pair_resolver_turns_the_pictet_case_into_zero_applied_with_its_maximum():
    """LU0128496485: stored exit pair (0.01, 1.0); the KID says the manager charges nothing and a distributor may charge up to 1 %."""
    from shared.fee_state import PairEvidence, derive_fee_state, resolve_pair
    r = resolve_pair(PairEvidence(negation=True, extractor_ceiling_pct=1.0), stored_point=0.01, stored_ceiling_pct=1.0)
    assert (r.point, r.ceiling_pct, r.warnings) == (0.0, 1.0, ())
    assert derive_fee_state(r.point, r.ceiling_pct, "exit") == "ZERO_CONFIRMED"
