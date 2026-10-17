# proyecto1/tests/test_oc_resolution_fnd0034.py
# -*- coding: utf-8 -*-
"""FND-0034 -- the per-fund glue of the single Ongoing_Charge_Recurrent writer (R-7: no pipeline.py, no core.io, no DB).

`oc_resolution.resolve_fund_oc` runs on EVERY pass, CACHED included (where the cost-extractor block is skipped), and prepares the
candidates `cost_scale.resolve_ongoing_charge` arbitrates: the document format, the EFFECTIVE management fee / ACI (this pass's
extraction if any, else the stored value, R-4), the UCITS OCF figure and the parser value. Values are RATIOS; *_pct are whole percents.
"""
import os
import sys

import pytest

_CORE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from oc_resolution import resolve_fund_oc  # noqa: E402

UCITS = """
Datos fundamentales para el inversor
Este documento le proporciona información esencial sobre este fondo de inversión.
Directiva UCITS.

Gastos corrientes: 0,85%
Comisión de gestión: 0,65%
Costes de transacción: 0,20%
"""

PRIIPS = """
Documento de datos fundamentales
Costes a lo largo del tiempo. Período de mantenimiento recomendado: 3 años.
Si sale después de 1 año: 357 EUR. Si sale después de 3 años: 1.050 EUR.
Costes totales 357 EUR 1.050 EUR. Incidencia anual de los costes (*) 3,6 % 3,5 % cada año.
Comisiones de gestión y otros costes administrativos o de funcionamiento 1,50 % del valor de su inversión.
"""


def test_a_cached_priips_pass_uses_the_stored_management_fee_when_this_pass_extracted_nothing():
    # CACHED: the cost-extractor block did not run, so the record carries no cost fields
    value, source, fmt = resolve_fund_oc(PRIIPS, 0.020, {}, (1.50, 3.5, 3.6))
    assert fmt == "PRIIPS_KID" and source == "management_fee" and value == pytest.approx(0.0150)


def test_this_passes_extraction_wins_over_the_stored_value():
    value, _s, _f = resolve_fund_oc(PRIIPS, None, {"Management_Fee_Pct": 0.90, "ACI_RHP": 3.5, "ACI_1Y": 3.6}, (1.50, 3.5, 3.6))
    assert value == pytest.approx(0.0090)


def test_a_range_guarded_field_of_this_pass_falls_back_to_the_stored_one_like_coalesce_does():
    # the extractor loop sets a field to None when it is out of range; COALESCE keeps the stored value, so the resolver must see it too
    value, _s, _f = resolve_fund_oc(PRIIPS, None, {"Management_Fee_Pct": None}, (1.50, 3.5, 3.6))
    assert value == pytest.approx(0.0150)


def test_the_effective_aci_of_this_pass_rejects_a_candidate_the_stored_aci_would_have_let_through():
    # stored ACI_RHP 3.5 does not match 1.50; this pass re-extracted ACI_RHP = 1.50 (the management fee itself) -> contaminated
    value, source, _f = resolve_fund_oc(PRIIPS, None, {"ACI_RHP": 1.50}, (1.50, 3.5, 3.6))
    assert (value, source) == (None, "none")


def test_a_ucits_document_uses_its_own_ocf_figure_as_a_ratio():
    value, source, fmt = resolve_fund_oc(UCITS, None, {}, (0.65, None, None))
    assert fmt == "UCITS_KIID" and source == "ucits_ocf" and value == pytest.approx(0.0085)      # 0,85 % -> 0.0085, never 0.85


def test_the_parser_is_a_candidate_not_a_writer():
    # unknown format, parser value equal to the ACI -> rejected; the management fee takes over
    value, source, fmt = resolve_fund_oc("sin formato reconocible", 0.0200, {}, (0.69, 2.0, 4.1))
    assert fmt in (None, "UNKNOWN") and (value, source) == (pytest.approx(0.0069), "management_fee")


def test_no_text_and_no_candidate_means_none_so_coalesce_keeps_the_stored_value():
    assert resolve_fund_oc(None, None, {}, (None, None, None)) == (None, "none", None)
    assert resolve_fund_oc("", None, {}, ()) == (None, "none", None)


def test_the_stored_tuple_may_be_short_or_empty():
    assert resolve_fund_oc("sin formato", 0.0090, {}, ())[0] == 0.0090
