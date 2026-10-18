# proyecto1/tests/test_cost_totals_arbitration_fnd0279.py
# -*- coding: utf-8 -*-
"""FND-0279 / FND-0283 / FND-0284 -- arbitration of the costs-over-time TOTALS between the plain-text reading and the text + DLA2 reading (R-7: no pipeline, no DB).

The extractor is fed `Raw_KIID_Text + DLA2_Table_Text`. The DLA2 table sometimes loses a digit or a cell, and being appended last the extractor prefers it:
  LU1734046979  plain text 'Costes totales 456 EUR 1 216 EUR'  -> DLA2 '456 EUR / 216 EUR'      (216 was stored)
  LU1683489089  plain text '... EUR 637 EUR 1,141'              -> DLA2 row with one cell        (the 5-year total was lost)
Measured over the 2,857 PRIIPs KIDs (2026-10-17): switching to plain text only is WORSE (1,294 vs 1,064 funds with structural violations), so only the totals
are taken, per horizon, and only when that removes a violation type without adding one: 258 funds / 384 cells, 963 -> 705 funds with a violation.
The two real slices below are public KID text (the cost section only).
"""
import os
import sys

import pytest

_CORE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import priips_cost_extractor as pe  # noqa: E402
from _fnd0279_kid_slices import HELIUM_DLA2, HELIUM_RAW, VONTOBEL_DLA2, VONTOBEL_RAW  # noqa: E402
from cost_totals_arbitration import (  # noqa: E402
    arbitrate_schedule_totals, extract_priips_costs_arbitrated, structural_violations)


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setattr(pe, "PRIIPS_COST_EXTRACTION_ENABLED", True)


def _totals(out):
    return [(r["Horizon_Years"], r.get("Total_Costs_EUR")) for r in out["_cost_schedule_rows"]]      # a lost total is an ABSENT key


def _row(h, eur, pct, annual):
    return {"Horizon_Years": h, "Total_Costs_EUR": eur, "Total_Costs_Pct": pct, "Annual_Impact_Pct": annual, "Is_RHP": 0, "Source": "PRIIPS_COSTS_OVER_TIME"}


# ---------------- the two real defects ----------------

def test_the_dla2_table_that_truncates_a_digit_no_longer_wins():
    fed = HELIUM_RAW + "\n\n" + HELIUM_DLA2
    assert _totals(pe.extract_priips_costs(text=fed, isin="LU1734046979")) == [(1.0, 456.0), (3.0, 216.0)]       # the defect, pinned
    out, replaced = extract_priips_costs_arbitrated(HELIUM_RAW, fed, "LU1734046979")
    assert _totals(out) == [(1.0, 456.0), (3.0, 1216.0)]
    assert replaced == [(3.0, 216.0, 1216.0)]
    assert [r["Total_Costs_Pct"] for r in out["_cost_schedule_rows"]] == [4.56, 12.16]


def test_the_dla2_row_that_loses_a_cell_no_longer_loses_the_total():
    fed = VONTOBEL_RAW + "\n\n" + VONTOBEL_DLA2
    assert _totals(pe.extract_priips_costs(text=fed, isin="LU1683489089"))[-1] == (5.0, None)                    # the defect, pinned
    out, replaced = extract_priips_costs_arbitrated(VONTOBEL_RAW, fed, "LU1683489089")
    assert _totals(out) == [(1.0, 637.0), (5.0, 1141.0)]
    assert replaced == [(5.0, None, 1141.0)]


def test_only_the_totals_change_the_rest_of_the_extractor_output_is_the_current_reading():
    fed = HELIUM_RAW + "\n\n" + HELIUM_DLA2
    before = pe.extract_priips_costs(text=fed, isin="LU1734046979")
    after, _ = extract_priips_costs_arbitrated(HELIUM_RAW, fed, "LU1734046979")
    scalars = lambda o: {k: v for k, v in o.items() if k != "_cost_schedule_rows"}
    assert scalars(after) == scalars(before)
    keep = lambda o: [(r["Horizon_Years"], r["Annual_Impact_Pct"], r["Is_RHP"], r["Source"]) for r in o["_cost_schedule_rows"]]
    assert keep(after) == keep(before)


def test_without_a_dla2_table_there_is_one_reading_and_nothing_to_arbitrate():
    out, replaced = extract_priips_costs_arbitrated(HELIUM_RAW, HELIUM_RAW, "LU1734046979")
    assert replaced == [] and _totals(out) == [(1.0, 456.0), (3.0, 1216.0)]


# ---------------- the rule, pure ----------------

def test_a_plain_text_total_replaces_a_decreasing_one():
    current = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 216.0, 2.16, 3.3)]
    plain = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 1216.0, 12.16, 3.3)]
    rows, replaced = arbitrate_schedule_totals(current, plain)
    assert [r["Total_Costs_EUR"] for r in rows] == [456.0, 1216.0] and replaced == [(3.0, 216.0, 1216.0)]
    assert structural_violations(current) == {"EUR_DECREASING", "ANNUAL_GT_CUMULATIVE"} and structural_violations(rows) == frozenset()


def test_the_plain_text_reading_is_ignored_when_it_is_not_strictly_better():
    current = [_row(1.0, 500.0, 5.0, 5.0), _row(3.0, 1300.0, 13.0, 4.5)]
    plain = [_row(1.0, 500.0, 5.0, 5.0), _row(3.0, 1300.0, 13.0, 4.5)]
    assert arbitrate_schedule_totals(current, plain) == (current, [])                       # identical
    worse = [_row(1.0, 500.0, 5.0, 5.0), _row(3.0, 400.0, 4.0, 4.5)]                         # plain text decreasing: would ADD a violation
    assert arbitrate_schedule_totals(current, worse) == (current, [])


def test_a_plain_text_reading_that_trades_one_violation_type_for_another_is_refused():
    current = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, None, None, 3.3)]                      # LOST_TOTAL
    plain = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 456.0, 4.56, 3.3)]                       # fixes it but creates EUR_CONSTANT
    assert structural_violations(current) == {"LOST_TOTAL"} and structural_violations(plain) == {"EUR_CONSTANT"}
    assert arbitrate_schedule_totals(current, plain) == (current, [])


def test_a_plain_text_horizon_the_current_reading_does_not_have_is_not_added():
    current = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, None, None, 3.3)]
    plain = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 1216.0, 12.16, 3.3), _row(5.0, 2000.0, 20.0, 3.0)]
    rows, replaced = arbitrate_schedule_totals(current, plain)
    assert [r["Horizon_Years"] for r in rows] == [1.0, 3.0] and replaced == [(3.0, None, 1216.0)]


def test_the_one_year_annual_vs_total_relation_is_not_used_to_choose():
    # a 1-year annual impact above the total (the KID assumes a first-year return) is NOT a structural violation: it must not drive the selection
    assert structural_violations([_row(1.0, 914.0, 9.14, 10.6), _row(5.0, 1560.0, 15.6, 3.8)]) == frozenset()


def test_the_current_rows_are_not_mutated():
    current = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 216.0, 2.16, 3.3)]
    snapshot = [dict(r) for r in current]
    arbitrate_schedule_totals(current, [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 1216.0, 12.16, 3.3)])
    assert current == snapshot


# ---------------- plausibility guard (found by looking at the replaced VALUES, not only at the violation counts) ----------------

def test_the_invested_amount_is_never_taken_as_a_total():
    """49 of the first 384 replacements would have written 10,000.0 (the invested amount) as a 5-year total: it is not a violation of any structural invariant."""
    from cost_totals_arbitration import plausible_total
    current = [_row(1.0, 456.0, 4.56, 4.6), _row(5.0, None, None, 3.1)]
    plain = [_row(5.0, 10000.0, 100.0, 3.1)]
    assert plausible_total(5.0, 100.0, 3.1) is False
    assert arbitrate_schedule_totals(current, plain) == (current, [])


def test_a_plain_text_cell_whose_euro_amount_and_percentage_disagree_is_not_used():
    """LU1078767826: the plain-text reading gave 10000 EUR with 1.0 % (10,000 EUR on a 10,000 EUR investment is 100 %): the two figures must correspond."""
    current = [_row(1.0, 68.0, 0.68, 0.7), _row(3.0, None, None, 0.7)]
    plain = [_row(3.0, 10000.0, 1.0, 0.7)]
    assert arbitrate_schedule_totals(current, plain) == (current, [])


def test_the_plausibility_envelope_is_the_one_measured_on_clean_rows():
    from cost_totals_arbitration import plausible_total
    assert plausible_total(3.0, 12.16, 3.3) and plausible_total(5.0, 11.41, 2.3)             # the two real KIDs
    assert not plausible_total(3.0, 2.0, 3.3)                                                # 216 EUR for a 3.3 %/year fund: ratio 0.2
    assert plausible_total(1.0, 6.37, 6.7) and plausible_total(1.0, 9.14, 10.6)              # 1-year KID conventions (ratios 1.05, 1.16)
    assert not plausible_total(1.0, 0.04, 4.6)                                               # a 4 EUR total read for 4.6 %/year (ratio about 115)
    assert plausible_total(3.0, 20.0, None) and not plausible_total(3.0, 60.0, None)         # no annual impact: at most half of the investment
    assert not plausible_total(3.0, None, 3.3) and not plausible_total(3.0, 0.0, 3.3)


# ---------------- verification against the document (LU1951227419: a CORRECT 605 was about to be replaced by a false 400) ----------------

def test_a_replacement_that_is_not_in_the_document_is_refused_even_if_it_tidies_the_schedule():
    """The KID says 'Costes totales 605 EUR 1580 EUR'; the table kept 5y = 580 (a decreasing total) and the plain-text reading gave 400 everywhere. Replacing the
    correct 605 by 400 would remove the 'total decreases' violation without repairing anything."""
    text = "Se invierten 10 000 EUR. Costes totales 605 EUR 1580 EUR Incidencia anual de los costes (*) 6,1 % 3,0 % cada año"
    current = [_row(1.0, 605.0, 6.05, 6.1), _row(5.0, 580.0, 5.8, 3.0)]
    plain = [_row(1.0, 400.0, 4.0, 2.05), _row(5.0, 400.0, 4.0, 2.05)]
    assert arbitrate_schedule_totals(current, plain) != (current, [])                       # structurally it WOULD look like an improvement ...
    assert arbitrate_schedule_totals(current, plain, text) == (current, [])                 # ... but 400 is not in the document


def test_a_replacement_that_is_in_the_document_is_accepted():
    text = "Costes totales 456 EUR 1 216 EUR Incidencia anual de los costes (*) 4.6% 3.3% cada año"
    current = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 216.0, 2.16, 3.3)]
    plain = [_row(1.0, 456.0, 4.56, 4.6), _row(3.0, 1216.0, 12.16, 3.3)]
    rows, replaced = arbitrate_schedule_totals(current, plain, text)
    assert replaced == [(3.0, 216.0, 1216.0)]


@pytest.mark.parametrize("value, text, expected", [
    (1216.0, "Costes totales 456 EUR 1 216 EUR", True),            # space-grouped thousands
    (1141.0, "Costes totales EUR 637 EUR 1,141", True),            # comma-grouped, currency first
    (1141.0, "Costes totales 637 € 1.141 €", True),                # dot-grouped
    (511.97, "Costes totales 511,97 EUR 2 049,48 EUR", True),      # decimals
    (2049.48, "Costes totales 511,97 EUR 2 049,48 EUR", True),
    (400.0, "Costes totales 605 EUR 1580 EUR", False),
    (10000.0, "Se invierten 10 000 EUR. Costes totales 605 EUR 1580 EUR", False),      # the invested amount is before the label and is not a total
    (605.0, "sin etiqueta 605 EUR", False),                        # a number not next to the label does not count
])
def test_total_appears_in_text(value, text, expected):
    from cost_totals_arbitration import total_appears_in_text
    assert total_appears_in_text(value, text) is expected


@pytest.mark.parametrize("value, text, expected", [
    # LU0617149793: the stress payout is ~170 characters after the label but FOUR lines below it -> not a total
    (4931.0, "Costes totales 719 USD 1960 USD\nIncidencia anual de los costes (*)\n7,2% 3,0%\n412e2395-1673\nEn caso de salida después\nde 6 años\n4931 USD -50,7%", False),
    (1960.0, "Costes totales 719 USD 1960 USD\nIncidencia anual de los costes (*)\n7,2% 3,0%", True),
    # label-first layout: the values on the next two lines still count, the third does not
    (719.0, "Costes totales\n719 USD\n1960 USD\n5555 USD", True),
    (1960.0, "Costes totales\n719 USD\n1960 USD\n5555 USD", True),
    (5555.0, "Costes totales\n719 USD\n1960 USD\n5555 USD", False),
    # values BEFORE the label on the same line count (392,46 USD 1.162,12 USD Costes totales)
    (1162.12, "392,46 USD 1.162,12 USD Costes totales", True),
])
def test_total_appears_on_the_label_line_or_the_next_two(value, text, expected):
    from cost_totals_arbitration import total_appears_in_text
    assert total_appears_in_text(value, text) is expected


# ---------------- the verdict does not depend on the row order ----------------

def test_a_correct_schedule_is_not_decreasing_because_its_rows_come_out_longest_horizon_first():
    # ES0175414012 comes out of the extractor as 3 years then 1 year (149 / 499 EUR in the KID)
    rows = [_row(3.0, 499.0, 4.99, 1.5), _row(1.0, 149.0, 1.49, 1.5)]
    assert structural_violations(rows) == frozenset()
    assert structural_violations(list(reversed(rows))) == frozenset()


def test_a_really_decreasing_schedule_is_still_flagged_in_any_row_order():
    rows = [_row(1.0, 500.0, 5.0, 5.0), _row(3.0, 200.0, 2.0, 1.5)]
    assert structural_violations(rows) == frozenset({"EUR_DECREASING"})
    assert structural_violations(list(reversed(rows))) == frozenset({"EUR_DECREASING"})
