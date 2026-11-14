# proyecto1/tests/test_entry_fee_from_amount_fnd0406.py
# -*- coding: utf-8 -*-
"""FND-0406: a PRIIPs KID that prints the entry charge only as an AMOUNT on the example investment ("Costes de entrada ... cantidad máxima ... 496 USD" on "Inversión: 10.000 USD") now fills
Entry_Fee_Pct_Max from the document's own numbers. Measured over the 3,770 stored KID texts (read-only replay, old vs new): 144 funds change, all of them from NULL to a value in Entry_Fee_Pct_Max,
nothing else moves and no stored value is replaced. R-7: no database, no pipeline."""
import os
import sys

import pytest

_CORE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import cost_table_parser as ctp  # noqa: E402
import priips_cost_extractor as pce  # noqa: E402

# Condensed from the stored KID of IE00B0122Y20 (PIMCO): same labels, same order, same wording.
PIMCO = ("Documento de datos fundamentales Período de mantenimiento recomendado: 1 año Inversión: 10.000 USD En caso de salida después de 1 año Costes totales 584 USD Incidencia anual de los costes (*) 5,8% "
         "Composición de los costes Costes de entrada La incidencia de los costes que usted pagará al realizar su inversión. Se trata de la cantidad máxima que se le cobrará, y podría pagar menos. 496 USD "
         "Costes de salida No cobramos una comisión de salida por este producto, pero es posible que la persona que se lo venda sí lo haga. 0 USD de 1 año Comisiones de gestión y otros costes administrativos o de funcionamiento de 1 año "
         "Comisiones de rendimiento Este producto no tiene comisiones de rendimiento. 0 USD")
# The "up to" layout (LU0076315455 and 31 more): the amount carries the ceiling in its own words.
UP_TO = "Inversión: 10.000 EUR Costes de entrada Hasta 300 EUR Costes de salida No cobramos comisión de salida. 0 EUR Costes corrientes detraídos cada año"


def test_the_pimco_layout_gives_the_amount_over_the_example_investment():
    assert ctp.entry_cost_ratio_from_amount(PIMCO) == pytest.approx(0.0496)


def test_the_up_to_layout_gives_the_ceiling_the_amount_states():
    assert ctp.entry_cost_ratio_from_amount(UP_TO) == pytest.approx(0.03)


def test_the_base_is_the_documents_own_not_a_hard_coded_ten_thousand():
    assert ctp.entry_cost_ratio_from_amount(UP_TO.replace("10.000", "100.000")) == pytest.approx(0.003)
    assert ctp.entry_cost_ratio_from_amount(UP_TO.replace("Inversión: 10.000 EUR ", "")) is None      # no base statement: nothing is guessed


def test_a_cell_with_a_percentage_is_left_to_the_composition_parsers():
    assert ctp.entry_cost_ratio_from_amount("Inversión: 10.000 EUR Costes de entrada 3,00% del importe que paga al realizar esta inversión. Hasta 300 EUR Costes de salida 0 EUR") is None


def test_a_zero_amount_is_not_an_entry_charge_here():
    assert ctp.entry_cost_ratio_from_amount("Inversión: 10.000 EUR Costes de entrada 0 EUR Costes de salida 0 EUR") is None      # zero charges belong to the zero detectors and the negations


def test_a_ratio_above_the_schema_ceiling_is_refused():
    assert ctp.entry_cost_ratio_from_amount("Inversión: 10.000 EUR Costes de entrada 3.000 EUR Costes de salida 0 EUR") is None          # 30 % is not an entry charge
    assert ctp.entry_cost_ratio_from_amount("Inversión: 10.000 EUR Costes de entrada 2.500 EUR Costes de salida 0 EUR") == pytest.approx(0.25)


def test_the_exit_amount_is_never_read_as_the_entry_amount():
    assert ctp.entry_cost_ratio_from_amount("Inversión: 10.000 EUR Costes de entrada La incidencia de los costes. Costes de salida 500 EUR") is None


def test_no_label_or_no_text_is_none():
    assert ctp.entry_cost_ratio_from_amount("") is None and ctp.entry_cost_ratio_from_amount(None) is None
    assert ctp.entry_cost_ratio_from_amount("Inversión: 10.000 EUR Costes de salida 500 EUR") is None


def test_the_extractor_fills_the_column_and_the_switch_restores_the_former_behaviour(monkeypatch):
    monkeypatch.setattr(pce, "_ENTRY_FROM_AMOUNT_ENABLED", False)
    old = pce.extract_priips_costs(text=PIMCO, isin="XX0000000001")
    monkeypatch.setattr(pce, "_ENTRY_FROM_AMOUNT_ENABLED", True)
    new = pce.extract_priips_costs(text=PIMCO, isin="XX0000000001")
    assert "Entry_Fee_Pct_Max" not in old and new["Entry_Fee_Pct_Max"] == pytest.approx(4.96)
    assert {k: v for k, v in new.items() if k != "Entry_Fee_Pct_Max"} == old                       # the only difference between old and new: the filled column


def test_a_negation_wins_over_the_amount(monkeypatch):
    monkeypatch.setattr(pce, "_ENTRY_FROM_AMOUNT_ENABLED", True)
    text = PIMCO.replace("Se trata de la cantidad máxima", "No cobramos comisión de entrada. Se trata de la cantidad máxima")
    assert "Entry_Fee_Pct_Max" not in pce.extract_priips_costs(text=text, isin="XX0000000001")
