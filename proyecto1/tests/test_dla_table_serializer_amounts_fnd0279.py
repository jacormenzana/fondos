# proyecto1/tests/test_dla_table_serializer_amounts_fnd0279.py
# -*- coding: utf-8 -*-
"""FND-0279 -- the DLA2 serializer reads the totals-row amounts with the plain-text parser's pattern (R-7: no pipeline, no DB, no PDF).

Root cause of the truncated / lost 'Costes totales' cells (screenshots of the two KIDs, 2026-10-17):
  LU1734046979  'Costes totales  456 EUR   1 216 EUR'   (thousands grouped with a SPACE)  -> the table row said '456 EUR / 216 EUR'
  LU1683489089  'Costes totales  EUR 637   EUR 1,141'    (currency BEFORE the number)      -> the table row had ONE cell, '637 EUR'
The serializer's private regex `(\\d[\\d.,]*)\\s*EUR` stops at a space ('10 000 EUR' read '000') and only knew the prefix '€ 637': in 'EUR 637 EUR 1,141' the first
number took the NEXT cell's 'EUR' and the last number vanished. It now uses cost_table_parser._EUR_TOKEN (the single source of the amount pattern).
Measured on the 2,950 cached texts: 246 of the 384 totals the arbitration repairs are exactly these two cases.
"""
import os
import sys

import pytest

_CORE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import dla_table_serializer as ser  # noqa: E402
from _fnd0279_kid_slices import HELIUM_RAW, VONTOBEL_RAW  # noqa: E402
import cost_table_parser as ctp  # noqa: E402

HELIUM = ("Costes a lo largo del tiempo. En caso de salida después de 1 año En caso de salida después de 3 años Costes totales 456 EUR 1 216 EUR "
          "Incidencia anual de los costes (*) 4.6% 3.3% cada año")
VONTOBEL = ("Costes a lo largo del tiempo. En caso de salida después de 1 año En caso de salida después de 5 años Costes totales EUR 637 EUR 1,141 "
            "Incidencia anual de los costes (*) 6.7% 2.3% cada año")


@pytest.mark.parametrize("text, expected", [
    ("456 EUR 1 216 EUR", ["456", "1 216"]),           # space-grouped thousands (Helium)
    ("1 216 EUR", ["1 216"]),
    ("10 000 EUR", ["10 000"]),                         # used to read '000'
    ("1 216,50 EUR", ["1 216,50"]),
    ("EUR 637 EUR 1,141", ["637", "1,141"]),            # currency before the number (Vontobel)
    ("EUR 637", ["637"]),
    ("EUR 1,141", ["1,141"]),
    ("637 EUR 1,141 EUR", ["637", "1,141"]),            # currency after: unchanged behaviour
    ("456 EUR 216 EUR", ["456", "216"]),
    ("€ 1.141", ["1.141"]),
    ("1.141 €", ["1.141"]),
    ("sin importes aquí", []),
    ("", []),
])
def test_total_row_amounts_are_read_as_written_in_the_document(text, expected):
    assert ser._total_amounts(text) == expected


def test_a_non_breaking_space_between_thousands_becomes_a_plain_space():
    assert ser._total_amounts("1 216 EUR") == ["1 216"] and ser._total_amounts("1 216 EUR") == ["1 216"]


def test_the_total_row_and_the_parser_share_one_amount_pattern():
    assert ser._AMT_TOTALS is ctp._EUR_TOKEN


def test_the_helium_total_row_keeps_both_cells_with_the_thousands_digit():
    _cmp, ot = ser._text_sections(HELIUM)
    assert "|||Costes totales|||456 EUR|||1 216 EUR|||" in ot


def test_the_vontobel_total_row_keeps_both_cells_even_with_the_currency_first():
    _cmp, ot = ser._text_sections(VONTOBEL)
    assert "|||Costes totales|||637 EUR|||1,141 EUR|||" in ot


def test_the_serialized_row_is_parsed_back_to_the_documents_numbers():
    """End to end through the extractor: raw text + the serialized table (what io.py feeds it) now gives the KID's totals, with no arbitration needed."""
    import priips_cost_extractor as pe
    pe.PRIIPS_COST_EXTRACTION_ENABLED = True
    for raw, isin, want in ((HELIUM_RAW, "T1", [(1.0, 456.0), (3.0, 1216.0)]), (VONTOBEL_RAW, "T2", [(1.0, 637.0), (5.0, 1141.0)])):
        _cmp, ot = ser._text_sections(raw)
        fed = raw + "\n\n" + "\n".join(ot)
        rows = pe.extract_priips_costs(text=fed, isin=isin).get("_cost_schedule_rows") or []
        assert [(r["Horizon_Years"], r.get("Total_Costs_EUR")) for r in rows] == want


def test_the_scoring_helper_is_left_unchanged_so_the_grid_vs_text_selection_does_not_move():
    """`_amounts` also scores which candidate rows win (grid vs text). Widening it moved that selection in 9 of 408 real PDFs (a garbled second grid row won), so the
    fix lives in `_total_amounts`, used only to build the 'Costes totales' row. Pinned: the legacy reading is still the legacy one."""
    assert ser._amounts("456 EUR 1 216 EUR") == ["456", "216"] and ser._amounts("EUR 637 EUR 1,141") == ["637"]
