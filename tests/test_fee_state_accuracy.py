# -*- coding: utf-8 -*-
"""Package H: the independent accuracy reader of the derived fee state (pure core, no database, R-7). Texts are condensed from stored KIDs."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fee_state_accuracy", ROOT / "scripts" / "audit" / "p1" / "fee_state_accuracy.py")
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)

UP_TO = ("Costes de entrada Hasta 3,00% del importe que pagará usted al realizar esta inversión. Hasta 300 EUR Costes de salida No cobramos comisión de salida. 0 EUR "
         "Costes corrientes detraídos cada año")
VXP3 = ("Costes de entrada 0,00% No se aplica ninguna comisión de entrada a este producto. Sin embargo, el producto puede aplicar una comisión de entrada de hasta el 5% en el futuro. "
        "0 EUR Costes de salida 0,00% No se aplica ninguna comisión de salida. 0 EUR En caso de salida después de 1 año Comisiones de gestión")
PLAIN = "Costes de entrada 5,00% del importe que pagará usted al realizar esta inversión. 500 EUR Costes de salida 1,00% de su inversión. 100 EUR Costes corrientes"
NEGATED = "Costes de entrada No cobramos comisión de entrada. 525 EUR Costes de salida No cobramos una comisión de salida por este producto. 0 EUR Comisiones de rendimiento"
EMPTY = "Costes de entrada Costes de salida Costes corrientes detraídos cada año Comisiones de gestión"
TABLE = "Costes de entrada|||3,00%|||300 EUR||| |||Costes de salida|||0 EUR||| |||Comisiones de gestion|||0,52%|||12,61 EUR|||"


@pytest.mark.parametrize("text,entry,exit_", [
    (UP_TO, "conditional", "zero"),
    (VXP3, "zero", "zero"),                      # an explicit zero wins over 'puede aplicar ... hasta el 5 %'
    (PLAIN, "positive", "positive"),
    (NEGATED, "zero", "zero"),                   # negation beside an amount: the document says none is charged
    (EMPTY, "none", "none"),
    (TABLE, "positive", "zero"),                 # the table text carries no 'hasta': positive; the raw text of the same fund says conditional (raw is read first)
])
def test_the_reader_classifies_what_the_document_prints(text, entry, exit_):
    assert fa.read_row(fa.fee_row(text, "entry")) == entry
    assert fa.read_row(fa.fee_row(text, "exit")) == exit_


def test_the_row_is_bounded_by_the_next_cost_label():
    row = fa.fee_row(PLAIN, "entry")
    assert "Costes de salida" not in row and "1,00%" not in row and "5,00%" in row
    assert fa.fee_row("nothing about fees", "entry") == ""


def test_number_parsing_of_european_and_english_formats():
    assert fa._num("3,00") == 3.0 and fa._num("1.250,50") == 1250.5 and fa._num("1,250.50") == 1250.5 and fa._num("10.000") == 10000.0


def test_judge_agrees_disagrees_and_reports_unreadable_rows():
    assert fa.judge("ENTRY_CONDITIONAL", UP_TO, "entry")["verdict"] == "agree"
    assert fa.judge("ZERO_CONFIRMED", UP_TO, "entry")["verdict"] == "disagree"          # a stored zero against a printed maximum
    assert fa.judge("EXTRACTED", PLAIN, "exit")["verdict"] == "agree"
    assert fa.judge("NOT_FOUND", "no fee rows at all", "entry")["verdict"] == "unreadable"
    assert fa.judge("EXIT_CONDITIONAL", UP_TO, "exit")["verdict"] == "disagree"


def test_strata_split_the_exit_zero_and_summary_uses_the_wilson_rule():
    assert fa.stratum_of(0.0, None, "exit") == "ZERO_CONFIRMED:point0_no_ceiling" and fa.stratum_of(0.0, 0.0, "exit") == "ZERO_CONFIRMED:other"
    assert fa.stratum_of(0.0, None, "entry") == "ZERO_CONFIRMED" and fa.stratum_of(None, 3.0, "entry") == "ENTRY_CONDITIONAL"
    results = ([{"isin": f"A{i}", "stratum": "entry:EXTRACTED", "state": "EXTRACTED", "verdict": "agree", "row": "", "read": "positive"} for i in range(26)]
               + [{"isin": f"B{i}", "stratum": "entry:EXTRACTED", "state": "EXTRACTED", "verdict": "disagree", "row": "x", "read": "zero"} for i in range(4)]
               + [{"isin": "U", "stratum": "entry:EXTRACTED", "state": "EXTRACTED", "verdict": "unreadable", "row": "", "read": None}])
    s = fa.summarise(results)["entry:EXTRACTED"]
    assert (s["n_read"], s["disagree"], s["unreadable"], s["decision"]) == (30, 4, 1, "STOP") and len(s["disagreements"]) == 4


def test_script_parses_its_arguments_without_a_database(capsys):
    with pytest.raises(SystemExit) as exc:
        fa.main(["--help"])
    assert exc.value.code == 0 and "accuracy sample" in capsys.readouterr().out


def test_currency_symbols_are_amounts_too():
    """IE00BD2ZKR05 prints 'Hasta $500': the first live comparison counted it a regression because the reader only knew currency codes."""
    assert fa.read_row(fa.fee_row("Costes de entrada Se trata de la cantidad máxima que se le cobrará. Hasta $500 Costes de salida No cobramos una comisión de salida. $0", "entry")) == "conditional"
    assert fa.read_row("£300") == "positive"
    assert fa.read_row("€0") == "zero"
