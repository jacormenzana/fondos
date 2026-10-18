# proyecto1/tests/test_cost_label_binding_fnd0279.py
# -*- coding: utf-8 -*-
"""FND-0279 follow-up (LU0617149793): the plain-text costs parser read "the first amount within 200 characters after a horizon label", but a KID repeats the wording
"En caso de salida despues de N anos" in its SCENARIOS table, which comes first in pdf text order: the 1-year column took the stress payout (4 931 USD with its
-50,7 % read as an impact) and the recommended-period column the "10000 USD invertidos" bullet, while the row labelled "Costes totales 719 USD 1960 USD" was never read.
`_bind_columns_by_label` binds the label rows to the columns (ascending horizon order) when the counts agree. The real KID text is in `_fnd0279_kid_slices`
(UBS_RAW / UBS_DLA2); the rest are synthetic rows. No pipeline imports (R-7)."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
for _p in (_CORE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import cost_table_parser as p  # noqa: E402
import priips_cost_extractor as pe  # noqa: E402
from _fnd0279_kid_slices import BE_FED, UBS_DLA2, UBS_RAW  # noqa: E402


def _row(hy, rhp=False, eur=None, aci=None):
    return {"horizon_years": hy, "is_rhp": rhp, "total_cost_eur": eur, "aci_pct": aci}


# ---- the real KID -------------------------------------------------------------------------------------------------------------------------------------------

def test_real_kid_reads_the_costs_table_not_the_scenarios_table():
    out = pe.extract_priips_costs(text=UBS_RAW + "\n\n" + UBS_DLA2, isin="LU0617149793")
    rows = {r["Horizon_Years"]: r for r in out["_cost_schedule_rows"]}
    assert rows[1.0]["Total_Costs_EUR"] == 719.0
    assert rows[6.0]["Total_Costs_EUR"] == 1960.0
    assert rows[1.0]["Annual_Impact_Pct"] == 7.2
    assert rows[6.0]["Annual_Impact_Pct"] == 3.0


def test_the_scenario_amounts_are_gone():
    out = pe.extract_priips_costs(text=UBS_RAW + "\n\n" + UBS_DLA2, isin="LU0617149793")
    totals = {r.get("Total_Costs_EUR") for r in out["_cost_schedule_rows"]}
    assert 4931.0 not in totals and 10000.0 not in totals


# ---- the binding rule -----------------------------------------------------------------------------------------------------------------------------------------

ZONE_TWO = "Costes totales 719 USD 1960 USD\nIncidencia anual de los costes (*)\n7,2% 3,0%\n"


def test_two_columns_are_bound_by_position_and_the_rhp_row_takes_the_last():
    res = [_row(-1.0, True, 10000.0, 0.072), _row(6.0, False, 10000.0, None), _row(1.0, False, 4931.0, 0.507)]
    out = p._bind_columns_by_label(ZONE_TWO, res)
    by = {(e["horizon_years"], e["is_rhp"]): e for e in out}
    assert by[(1.0, False)]["total_cost_eur"] == 719.0
    assert by[(6.0, False)]["total_cost_eur"] == 1960.0
    assert by[(-1.0, True)]["total_cost_eur"] == 1960.0
    assert abs(by[(1.0, False)]["aci_pct"] - 0.072) < 1e-9
    assert abs(by[(6.0, False)]["aci_pct"] - 0.030) < 1e-9


def test_a_single_found_column_is_never_bound():
    # IE00BD8DY654: the label line carries only the FIRST column's amount, the 5-year amount is in the next cell; one horizon was found -> do not bind
    zone = "Total Costs 666 EUR\nAnnual Cost Impact (*) 6.66%\n2,089 EUR\n1.66% each year\n"
    res = [_row(5.0, True, None, 0.0666)]
    out = p._bind_columns_by_label(zone, res)
    assert out[0]["total_cost_eur"] is None


def test_a_different_number_of_values_leaves_the_entries_alone():
    res = [_row(1.0, False, 11.0, 0.05), _row(3.0, False, 22.0, 0.03), _row(5.0, True, 33.0, 0.02)]
    out = p._bind_columns_by_label(ZONE_TWO, res)
    assert [e["total_cost_eur"] for e in out] == [11.0, 22.0, 33.0]


def test_totals_that_fall_with_the_horizon_are_not_bound():
    zone = "Costes totales 1960 USD 719 USD\nIncidencia anual de los costes (*)\n7,2% 3,0%\n"
    res = [_row(1.0, False, 1.0, None), _row(6.0, True, 2.0, None)]
    out = p._bind_columns_by_label(zone, res)
    assert [e["total_cost_eur"] for e in out] == [1.0, 2.0]


def test_impacts_that_rise_with_the_horizon_are_not_bound():
    zone = "Costes totales 719 USD 1960 USD\nIncidencia anual de los costes (*)\n3,0% 7,2%\n"
    res = [_row(1.0, False, None, 0.5), _row(6.0, True, None, 0.6)]
    out = p._bind_columns_by_label(zone, res)
    assert [e["aci_pct"] for e in out] == [0.5, 0.6]


def test_three_columns():
    zone = "Total costs EUR 120 EUR 410 EUR 980\nAnnual cost impact 1.2% 0.9% 0.8%\n"
    res = [_row(1.0, False, None, None), _row(3.0, False, None, None), _row(5.0, True, None, None)]
    out = p._bind_columns_by_label(zone, res)
    assert [e["total_cost_eur"] for e in out] == [120.0, 410.0, 980.0]
    assert [round(e["aci_pct"], 4) for e in out] == [0.012, 0.009, 0.008]


def test_no_label_in_the_zone_changes_nothing():
    res = [_row(1.0, False, 5.0, 0.01), _row(5.0, True, 6.0, 0.02)]
    out = p._bind_columns_by_label("sin etiquetas aqui", res)
    assert [e["total_cost_eur"] for e in out] == [5.0, 6.0]


# ---- the table body BEFORE its caption (BE0947854676) ---------------------------------------------------------------------------------------------------

def test_a_row_that_precedes_the_caption_is_bound():
    # pdf text order puts "Total costs 208 EUR 1 066 EUR" ~650 characters before "Costs over time"; the KID says 208 / 1 066 EUR, 2.1 % / 1.3 %
    out = pe.extract_priips_costs(text=BE_FED, isin="BE0947854676")
    rows = {r["Horizon_Years"]: r for r in out["_cost_schedule_rows"]}
    assert rows[1.0]["Total_Costs_EUR"] == 208.0 and rows[6.0]["Total_Costs_EUR"] == 1066.0
    assert rows[1.0]["Annual_Impact_Pct"] == 2.1 and rows[6.0]["Annual_Impact_Pct"] == 1.3


def test_the_risk_scale_digits_are_not_read_as_totals():
    out = pe.extract_priips_costs(text=BE_FED, isin="BE0947854676")
    assert {r.get("Total_Costs_EUR") for r in out["_cost_schedule_rows"]}.isdisjoint({1.0, 6.0})


def test_row_values_skips_a_prose_mention_of_the_label():
    zone = "The total costs of the fund are shown below.\nTotal costs 208 EUR 1 066 EUR\nAnnual cost impact (*)\n2.1% 1.3% each year\n"
    assert p._row_values(zone, p._TOTAL_COSTS_LABEL, p._extract_eur_list_from_cell, 2) == [208.0, 1066.0]


def test_row_values_wants_exactly_n_cols():
    zone = "Total costs 208 EUR\nsomething\nelse\n\nTotal costs 208 EUR 1 066 EUR\n"
    assert p._row_values(zone, p._TOTAL_COSTS_LABEL, p._extract_eur_list_from_cell, 2) == [208.0, 1066.0]
    assert p._row_values(zone, p._TOTAL_COSTS_LABEL, p._extract_eur_list_from_cell, 3) == []


# ---- the two-column rule and the binding on the rows of either parser (follow-up 3) -------------------------------------------------------------------

def _by_h(out):
    return {r["Horizon_Years"]: r for r in out["_cost_schedule_rows"]}


def test_two_values_and_only_the_recommended_period_found_means_1_year_and_that_period():
    # IE000NGCG706: "Costes totales 567 EUR 697 EUR" / "5,7% 2,4%" and only "3 anos" in the pdf text; stored 567 at 3 years and no 1-year row
    from _fnd0279_kid_slices import IE_ONECOL_FED
    rows = _by_h(pe.extract_priips_costs(text=IE_ONECOL_FED, isin="IE000NGCG706"))
    assert rows[3.0]["Total_Costs_EUR"] == 697.0 and rows[1.0]["Total_Costs_EUR"] == 567.0
    assert rows[3.0]["Annual_Impact_Pct"] == 2.4 and rows[1.0]["Annual_Impact_Pct"] == 5.7


def test_the_dla2_parser_that_loses_a_total_is_completed_from_the_documents_own_row():
    # LU0577510026: the DLA2 rows carried the 1-year total only; the KID's row says 302 / 2 350 EUR
    from _fnd0279_kid_slices import LU_DLA2LOST_FED
    rows = _by_h(pe.extract_priips_costs(text=LU_DLA2LOST_FED, isin="LU0577510026"))
    assert rows[1.0]["Total_Costs_EUR"] == 302.0 and rows[7.0]["Total_Costs_EUR"] == 2350.0


def test_the_synthetic_one_year_row_needs_a_horizon_longer_than_a_year():
    zone = "Costes totales 567 EUR 697 EUR\nIncidencia anual de los costes (*)\n5,7% 2,4%\n"
    res = [_row(1.0, False, None, 0.1)]                                # the single found horizon is one year: not the recommended period
    assert p._bind_columns_by_label(zone, res)[0]["total_cost_eur"] is None


def test_the_synthetic_one_year_row_is_added_with_whatever_the_row_binds():
    zone = "Costes totales 567 EUR 697 EUR\nIncidencia anual de los costes (*)\n5,7% 2,4%\n"
    out = p._bind_columns_by_label(zone, [_row(3.0, False, None, None)])
    by = {e["horizon_years"]: e for e in out}
    assert by[1.0]["total_cost_eur"] == 567.0 and abs(by[1.0]["aci_pct"] - 0.057) < 1e-9 and by[1.0]["is_rhp"] is False
    assert by[3.0]["total_cost_eur"] == 697.0 and abs(by[3.0]["aci_pct"] - 0.024) < 1e-9


def test_three_values_and_a_single_found_horizon_are_left_alone():
    zone = "Costes totales 100 EUR 300 EUR 500 EUR\nIncidencia anual de los costes (*)\n5,0% 3,0% 2,0%\n"
    out = p._bind_columns_by_label(zone, [_row(5.0, True, None, None)])
    assert len(out) == 1 and out[0]["total_cost_eur"] is None
