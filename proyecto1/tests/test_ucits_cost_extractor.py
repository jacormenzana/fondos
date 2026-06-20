# proyecto1/tests/test_ucits_cost_extractor.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de ucits_cost_extractor.extract_ucits_costs.

BL-COST-4b (Sprint 2 S2-C). Suite de 7 casos según §5 del TRASPASO S2-C.

Ground truth sintético (§4.1):
  _UCITS_ES  → KID_Format=UCITS_KIID, OC=0.85%, Mgmt=0.65%, Transac=0.20%, 1 row schedule
  _UCITS_EN  → KID_Format=UCITS_KIID, OC=1.20%, Mgmt=0.90%, Transac=0.30%, 1 row schedule
  _PRIIPS    → KID_Format != UCITS_KIID, Quality=NONE, schedule=[]
  _EMPTY     → Quality in (NONE, LOW), schedule=[]
"""

import pytest

# ── Textos sintéticos de muestra ──────────────────────────────────────────

_UCITS_ES = """
Datos fundamentales para el inversor
Este documento le proporciona información esencial sobre este fondo de inversión.
Directiva UCITS.

Gastos corrientes: 0,85%
Comisión de gestión: 0,65%
Costes de transacción: 0,20%
"""

_UCITS_EN = """
Key Investor Information Document
This document provides key investor information about this fund.
UCITS directive.

Ongoing charges: 1.20%
Entry charge: 0.00%   Exit charge: 0.00%   Ongoing charges: 1.20%
Management fee: 0.90%
Transaction costs: 0.30%
"""

_PRIIPS_TEXT = """
Costes a lo largo del tiempo.
Período de mantenimiento recomendado: 3 años.
Si sale después de 1 año: 357 EUR
Si sale después de 3 años: 1.050 EUR
"""

_EMPTY = ""


# ── Fixture: activar kill-switch para todos los tests ─────────────────────

@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    import ucits_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)


# ── Tests ──────────────────────────────────────────────────────────────────

def test_killswitch_off_returns_empty(monkeypatch):
    """Kill-switch False → retorna {} sin ejecutar nada."""
    import ucits_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', False)
    assert ext.extract_ucits_costs(_UCITS_ES, 'TEST') == {}


def test_ucits_es_format_and_oc():
    """Texto UCITS en español: formato, OC, calidad y schedule correctos."""
    from ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_UCITS_ES, 'TEST_ES')
    assert o['KID_Format'] == 'UCITS_KIID'
    assert 'Ongoing_Charge_Recurrent' in o
    assert abs(o['Ongoing_Charge_Recurrent'] - 0.85) < 0.01
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = o['_cost_schedule_rows']
    assert len(rows) == 1
    assert rows[0]['Horizon_Years'] == 1.0
    assert rows[0]['Is_RHP'] == 1
    assert rows[0]['Source'] == 'UCITS_DERIVED'
    assert abs(rows[0]['Annual_Impact_Pct'] - 0.85) < 0.01


def test_ucits_en_format_and_oc():
    """Texto UCITS en inglés: OC 1.20% extraído correctamente."""
    from ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_UCITS_EN, 'TEST_EN')
    assert o['KID_Format'] == 'UCITS_KIID'
    assert abs(o['Ongoing_Charge_Recurrent'] - 1.20) < 0.01
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    assert len(o['_cost_schedule_rows']) == 1


def test_non_ucits_returns_none_quality():
    """Texto PRIIPs → KID_Format != UCITS_KIID, Quality=NONE, schedule vacío."""
    from ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_PRIIPS_TEXT, 'TEST_PRIIPS')
    assert o['KID_Format'] != 'UCITS_KIID'
    assert o['Cost_Extraction_Quality'] == 'NONE'
    assert o['_cost_schedule_rows'] == []


def test_empty_text_returns_none_or_low_quality():
    """Texto vacío → Quality en (NONE, LOW), schedule siempre presente."""
    from ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_EMPTY, 'TEST_EMPTY')
    assert o['Cost_Extraction_Quality'] in ('NONE', 'LOW')
    assert '_cost_schedule_rows' in o


def test_oc_not_returned_when_existing_oc_present():
    """COALESCE-safe: si existing_oc no es None, no devolver Ongoing_Charge_Recurrent."""
    from ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs(_UCITS_ES, 'TEST_OC_EXISTING', existing_oc=0.85)
    assert 'Ongoing_Charge_Recurrent' not in o


def test_no_exception_on_garbage():
    """Texto basura → retorna dict con Cost_Extraction_Quality y _cost_schedule_rows."""
    from ucits_cost_extractor import extract_ucits_costs
    o = extract_ucits_costs("\x00\x01 basura ||| sin estructura", 'TEST_GARBAGE')
    assert isinstance(o, dict)
    assert 'Cost_Extraction_Quality' in o
    assert '_cost_schedule_rows' in o
