# proyecto1/tests/test_fee_cell_reading_fnd0421.py
# -*- coding: utf-8 -*-
"""FND-0421 (Package H): the printed entry / exit fee cell is read as evidence (zero | applied | explicit ceiling) and the pair resolver uses it.
R-7: no database, no pipeline. Wordings are condensed from stored KIDs.

Owner definition 2026-11-14: a PRIIPs percent or amount printed in the cell is the APPLIED fee (EXTRACTED), also beside "this is the most you will be charged"; it is a CEILING only when
the cell says "up to X %" / "hasta X %" with the percent; an explicit zero is ZERO."""
import os
import sys

import pytest

_CORE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)
_ROOT = os.path.normpath(os.path.join(_CORE_DIR, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import cost_table_parser as ctp  # noqa: E402
from shared import fee_state as fs  # noqa: E402

CARMIGNAC = "Costes de entrada 4,00% del importe que pagará usted al realizar esta inversión. Se trata de la cantidad máxima que se le cobrará. Hasta 400 EUR Costes de salida 0 EUR Costes corrientes"
MG = "Costes de entrada 4,00% del valor de su inversión. Se trata del importe máximo que se le cobrará. 400 EUR Costes de salida Se trata de la comisión de salida máxima que cobrará M&G. €0,00 Costes corrientes"
HASTA_PCT = "Costes de entrada Hasta el 6,00% del importe que pagará usted al realizar esta inversión. 600 EUR Costes de salida 0 EUR Costes corrientes"
UP_TO_EN = "Exit costs A charge of up to 1.00% may apply where the Manager suspects excessive trading by an investor. Ongoing costs"
PERCENT_AMOUNT_LINE = "Entry costs 5.00% of the amount you pay in when entering this investment. Up to EUR 500 Exit costs 0 EUR"
HEADER_BOTH = "Costes únicos de entrada o salida a esta clase de acciones. Hasta EUR 500 1.33% del valor de su inversión al año. Costes corrientes"
ZERO_FIRST = "Entry costs No cobramos una tarifa de entrada. 0 EUR Sin embargo, se aplicará una comisión de hasta el 5% en el futuro. Exit costs"


@pytest.mark.parametrize("text,side,kind,pct", [
    (CARMIGNAC, "entry", "applied", 4.0),                 # the standard sentence + the "Hasta 400 EUR" amount line: the percent is applied
    (CARMIGNAC, "exit", "zero", None),
    (MG, "entry", "applied", 4.0),                        # "Se trata del importe maximo" is the definition, not a ceiling
    (MG, "exit", "zero", None),                           # "... maxima que cobrara M&G. EUR 0,00": a printed zero
    (HASTA_PCT, "entry", "explicit_ceiling", 6.0),
    (UP_TO_EN, "exit", "explicit_ceiling", 1.0),
    (PERCENT_AMOUNT_LINE, "entry", "applied", 5.0),
    (ZERO_FIRST, "entry", "zero", None),                  # an explicit zero printed first wins over a later "up to 5 %"
])
def test_the_printed_cell_is_read_as_zero_applied_or_explicit_ceiling(text, side, kind, pct):
    assert ctp.fee_cell_reading(text, side) == (kind, pct)


def test_a_header_naming_both_sides_and_an_annual_percent_are_not_the_cell():
    assert ctp.fee_cell_reading(HEADER_BOTH, "entry") == (None, None)
    assert ctp.fee_cell_reading("Costes de entrada 1,33% del valor de su inversión al año. Costes de salida", "entry") == (None, None)
    assert ctp.fee_cell_reading("nothing about fees", "exit") == (None, None)


def test_the_reading_has_a_kill_switch(monkeypatch):
    monkeypatch.setattr(ctp, "_FEE_CELL_EVIDENCE_ENABLED", False)
    assert ctp.fee_cell_reading(CARMIGNAC, "entry") == (None, None)


# ── the resolver ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _res(**kw):
    stored = kw.pop("stored", (None, None))
    return fs.resolve_pair(fs.PairEvidence(**kw), stored[0], stored[1], same_document=True)


def test_an_applied_cell_makes_the_ceiling_the_applied_fee():
    r = _res(extractor_ceiling_pct=4.95, cell_kind="applied", cell_pct=None)           # amount-only cell: the extractor's ceiling, the point follows
    assert (r.point, r.ceiling_pct) == (0.0495, 4.95)
    assert fs.derive_fee_state(r.point, r.ceiling_pct) == "EXTRACTED"


def test_an_explicit_ceiling_never_keeps_a_stored_or_parser_point():
    stored = _res(extractor_ceiling_pct=6.0, cell_kind="explicit_ceiling", cell_pct=6.0, stored=(0.06, 6.0))    # the old parser's point, corroborated by the ceiling, is dropped
    assert (stored.point, stored.ceiling_pct) == (None, 6.0)
    assert fs.derive_fee_state(stored.point, stored.ceiling_pct) == "ENTRY_CONDITIONAL"
    parser = _res(parser_point=0.06, extractor_ceiling_pct=6.0, cell_kind="explicit_ceiling", cell_pct=6.0)    # the parser read the 'up to' percent as a point
    assert parser.point is None


def test_a_zero_cell_is_a_zero_even_beside_a_ceiling():
    r = _res(extractor_ceiling_pct=5.0, cell_kind="zero")
    assert r.point == 0.0 and fs.derive_fee_state(r.point, r.ceiling_pct) == "ZERO_CONFIRMED"
    only = _res(cell_kind="zero")                                                       # no ceiling at all: still an evidence, so the pair is re-derived (M&G exit)
    assert (only.point, only.ceiling_pct, only.source) == (0.0, None, "this_run")


def test_the_cell_percent_is_the_last_resort_ceiling():
    r = _res(cell_kind="explicit_ceiling", cell_pct=2.0)                                # English exit row neither reader binds
    assert (r.point, r.ceiling_pct) == (None, 2.0)
    assert _res(extractor_ceiling_pct=1.0, cell_kind="explicit_ceiling", cell_pct=2.0).ceiling_pct == 1.0   # the extractor's value stays first


def test_without_cell_evidence_the_resolver_is_what_it_was():
    r = _res(extractor_ceiling_pct=5.0, stored=(0.05, 5.0))                             # corroborated stored point kept
    assert r.point == 0.05
    assert _res(cell_kind="nonsense", extractor_ceiling_pct=5.0).point is None
    assert _res(stored=(0.05, 5.0)).source == "stored"


def test_reconcile_passes_the_cells_per_side_and_leaves_the_other_side_alone():
    rec = {"Entry_Fee_Pct_Max": 4.0, "Exit_Fee_Pct_Max": 1.0}
    stored = {"Entry_Fee_Pct": None, "Entry_Fee_Pct_Max": 4.0, "Exit_Fee_Pct": 0.01, "Exit_Fee_Pct_Max": 1.0}
    fs.reconcile_fee_pairs(rec, {}, stored, full_cost_pass=True, cells={"entry": ("applied", 4.0), "exit": ("explicit_ceiling", 1.0)}, same_document=True)
    assert rec["Entry_Fee_Pct"] == 0.04 and rec["Exit_Fee_Pct"] is None
    assert fs.derive_fee_state(rec["Entry_Fee_Pct"], rec["Entry_Fee_Pct_Max"], "entry") == "EXTRACTED"
    assert fs.derive_fee_state(rec["Exit_Fee_Pct"], rec["Exit_Fee_Pct_Max"], "exit") == "EXIT_CONDITIONAL"
