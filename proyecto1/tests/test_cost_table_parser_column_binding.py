# proyecto1/tests/test_cost_table_parser_column_binding.py
# -*- coding: utf-8 -*-
"""FIX-COST-COLUMN-BIND (FND-0278 family): a plain-text multi-column costs-over-time row is ONE line, and every horizon used to take its FIRST
value ("Total costs EUR914 EUR1,560" stored 914 at 1 AND 5 years). The snippets are the real KID text of the funds that exposed it
(IE0031575503, LU1956163882), cut to the costs-over-time block. No pipeline imports (R-7)."""
from __future__ import annotations

import os
import sys

_CORE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import cost_table_parser as p  # noqa: E402

EN_LABEL_FIRST = """What are the costs?
Costs over time The person advising on or selling you this product may charge you other costs.
The tables show the amounts that are taken from your investment to cover different types of costs.
We have assumed that the product performs as shown in the moderate scenario and EUR 10 000 is invested.
If you exit after 1 year
If you exit after 5 years
Total costs €914 €1,560
Annual cost impact (*)
10.6% 3.8% each year
(*) This illustrates how costs reduce your return each year over the holding period. For example it shows that if you exit at the recommended
holding period your average return per year is projected to be 15.7% before costs and 11.9% after costs.
"""

ES_LABEL_LAST = """Costes a lo largo del tiempo
Los cuadros muestran los importes que se detraen de su inversión para cubrir diferentes tipos de costes.
Se invierten 10.000 USD.
En caso de salida después de 1 año En caso de salida después de 5 años
392,46 USD 1.162,12 USD Costes totales
3,95% 1,76% cada año Incidencia anual de los costes (*)
(*) Refleja la medida en que los costes reducen su rendimiento cada año. El rendimiento medio será del 13,92% antes de deducir los costes y del
12,16% después de deducir los costes.
"""


def _by_year(rows):
    return {r["horizon_years"]: (r["total_cost_eur"], r["aci_pct"]) for r in rows if not r["is_rhp"]}


def test_label_first_layout_binds_each_column_to_its_own_horizon():
    got = _by_year(p.parse_costs_over_time(EN_LABEL_FIRST))
    assert got[1.0] == (914.0, 0.106) and got[5.0] == (1560.0, 0.038)        # was (914, .106) at BOTH horizons


def test_label_last_layout_with_decimal_commas_and_dot_thousands():
    got = _by_year(p.parse_costs_over_time(ES_LABEL_LAST))
    assert got[1.0] == (392.46, 0.0395) and got[5.0] == (1162.12, 0.0176)


def test_eur_list_reads_every_currency_marked_amount_in_order():
    f = p._extract_eur_list_from_cell
    assert f("Total costs €914 €1,560") == [914.0, 1560.0]
    assert f("392,46 USD 1.162,12 USD Costes totales") == [392.46, 1162.12]
    assert f("Costes totales 0 $") == [0.0]
    assert f("no amounts here, only 3.8% and a year 2026") == []


def _dup(horizon, total=914.0, aci=0.106, rhp=False):
    return {"horizon_label": f"{horizon}y", "horizon_years": horizon, "is_rhp": rhp, "total_cost_eur": total, "aci_pct": aci, "source": "PLAIN_TEXT"}


def test_rebind_changes_nothing_when_the_line_does_not_carry_exactly_one_value_per_column():
    rows = [_dup(1.0), _dup(5.0)]
    out = p._rebind_duplicate_columns("Total costs €914\nAnnual cost impact\n10.6% each year", [dict(r) for r in rows])
    assert [(r["total_cost_eur"], r["aci_pct"]) for r in out] == [(914.0, 0.106), (914.0, 0.106)]          # 1 value for 2 columns: unknown, untouched


def test_rebind_refuses_a_cumulative_total_that_decreases_with_the_horizon():
    out = p._rebind_duplicate_columns("Total costs €1,560 €914\nAnnual cost impact\n10.6% 3.8% each year", [_dup(1.0, 1560.0), _dup(5.0, 1560.0)])
    assert [r["total_cost_eur"] for r in out] == [1560.0, 1560.0]                                         # totals only grow: a swapped line is not trusted


def test_rebind_leaves_genuinely_different_columns_and_single_columns_alone():
    rows = [_dup(1.0, 914.0, 0.106), _dup(5.0, 1560.0, 0.038)]
    assert p._rebind_duplicate_columns("Total costs €1 €2", [dict(r) for r in rows]) == rows
    one = [_dup(5.0)]
    assert p._rebind_duplicate_columns("Total costs €1 €2", [dict(r) for r in one]) == one


def test_the_rhp_labelled_duplicate_takes_the_last_column():
    rows = [_dup(1.0, 10000.0, 0.073), _dup(-1.0, 10000.0, 0.073, rhp=True), _dup(5.0, 10000.0, 0.073)]
    out = p._rebind_duplicate_columns("Total costs €727 €1,689\nAnnual cost impact\n7.3% 3.3% each year", rows)
    assert [(r["horizon_years"], r["total_cost_eur"], r["aci_pct"]) for r in out] == [(1.0, 727.0, 0.073), (-1.0, 1689.0, 0.033), (5.0, 1689.0, 0.033)]


# --- FND-0280/0285: the component-vs-total rules carry the KID rounding tolerance --------------------------------------------------------
def test_component_vs_total_rules_tolerate_kid_rounding_but_not_a_real_excess():
    sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
    from shared.statistical_audit.catalog_invariants import COST_INVARIANTS
    from shared.statistical_audit.tolerances import KID_ROUNDING_TOLERANCE_PP
    rules = {r.rule_id: r for r in COST_INVARIANTS}
    for rule_id, column in (("MGMT_LE_TOTAL", "Management_Fee_Pct"), ("TXN_LE_TOTAL", "Transaction_Cost_Pct")):
        expr = rules[rule_id].expression
        assert str(KID_ROUNDING_TOLERANCE_PP) in expr, expr
        holds = lambda comp, total: eval(expr, {}, {column: comp, "ACI_RHP": total})
        assert holds(0.12, 0.10)                  # 0.02 over: the 2-decimal component vs the 1-decimal total
        assert not holds(0.31, 0.0)               # a zero total against a disclosed 0.31 charge IS a violation when the rule applies...
        assert rules[rule_id].when == "Cost_RHP_Years >= 1"   # ...and it applies only to holding periods of a year or more: an annual
                                                           # charge over '1 day to 3 months' (LU0987193777) is legitimately a ~0 total
        assert not holds(0.61, 0.40)
