# proyecto1/tests/test_fee_conditional_evidence_pkgh.py
# -*- coding: utf-8 -*-
"""Package H (FND-0408): the parser keeps the 'up to / maximum' fee it reads as evidence (Entry_Fee_Conditional / Exit_Fee_Conditional, a ratio)
instead of throwing it away, so the pair resolver can use it as the ceiling when the cost extractor yields none (14 funds were ENTRY_CONDITIONAL
with no value at all). Everything the parser emitted before is unchanged: point NULL on a ceiling, the legacy flag, the exit point. R-7: no database."""
import os
import sys

import pytest

_CORE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import kiid_parser as kp  # noqa: E402

UP_TO = "Costes de entrada Hasta el 3,00% del importe que pagara usted al realizar esta inversion. Costes de salida 0,00% No cobramos comision de salida."
PLAIN = "Costes de entrada 5,00% del importe que pagara usted al realizar esta inversion. Costes de salida 1,00% de su inversion antes de que se le pague."
EXIT_UP_TO = "Costes de entrada 2,00% de su inversion. Costes de salida Hasta el 1,50% de su inversion antes de que se le pague."


def test_an_entry_ceiling_is_kept_as_conditional_evidence_and_the_point_stays_null():
    r = kp.parse_kiid_generic(UP_TO)
    assert r["Entry_Fee_Conditional"] == pytest.approx(0.03)
    assert r["Entry_Fee_Pct"] is None and r["Fee_Known_Flag"] == "ENTRY_CONDITIONAL"      # the legacy outputs are untouched
    assert r["Exit_Fee_Conditional"] is None


def test_a_plain_fee_leaves_the_conditional_keys_empty():
    r = kp.parse_kiid_generic(PLAIN)
    assert r["Entry_Fee_Conditional"] is None and r["Exit_Fee_Conditional"] is None
    assert r["Entry_Fee_Pct"] == pytest.approx(0.05) and r["Exit_Fee_Pct"] == pytest.approx(0.01) and r["Fee_Known_Flag"] == "EXTRACTED"


def test_an_exit_ceiling_is_kept_as_conditional_evidence_and_the_exit_point_stays_null():
    r = kp.parse_kiid_generic(EXIT_UP_TO)
    assert r["Exit_Fee_Conditional"] == pytest.approx(0.015) and r["Exit_Fee_Pct"] is None
    assert r["Entry_Fee_Conditional"] is None and r["Entry_Fee_Pct"] == pytest.approx(0.02)


def test_the_empty_result_template_carries_both_keys():
    r = kp.parse_kiid_generic("")
    assert "Entry_Fee_Conditional" in r and "Exit_Fee_Conditional" in r
    assert r["Entry_Fee_Conditional"] is None and r["Exit_Fee_Conditional"] is None
