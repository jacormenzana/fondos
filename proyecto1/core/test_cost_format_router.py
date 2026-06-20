# proyecto1/tests/test_cost_format_router.py
# -*- coding: utf-8 -*-
"""
Tests para cost_format_router.py — BL-COST-3.

Estructura:
    Suite A — tests unitarios con texto sintético (siempre ejecutables).
    Suite B — tests de integración sobre PDFs reales (solo si existen en disco).

Ejecutar:
    python -X utf8 test_cost_format_router.py
    python -X utf8 test_cost_format_router.py --real-pdfs   (activa Suite B)
"""

import sys
import os
import re

# ---------------------------------------------------------------------------
# Resolver import del módulo bajo test sin manipular __init__.py.
# Estructura asumida:
#   C:/desarrollo/fondos/
#       proyecto1/
#           core/cost_format_router.py
#           tests/test_cost_format_router.py  (este fichero)
# ---------------------------------------------------------------------------
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.join(_THIS_DIR, '..', 'core')
_PROJ1_DIR = os.path.join(_THIS_DIR, '..')
_ROOT_DIR  = os.path.join(_THIS_DIR, '..', '..')

for _p in (_CORE_DIR, _PROJ1_DIR, _ROOT_DIR):
    _p = os.path.normpath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from cost_format_router import detect_kid_format, detect_kid_currency, get_format_signals_detail
except ImportError:
    # Fallback: ejecutando desde /home/claude en el entorno de desarrollo
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cost_format_router import detect_kid_format, detect_kid_currency, get_format_signals_detail


# ======================================================================
# Textos sintéticos representativos
# ======================================================================

# Texto PRIIPs mínimo (3 señales fuertes + moneda EUR)
_TEXT_PRIIPS_EUR = """
Documento de datos fundamentales
Este documento proporciona información esencial sobre este producto de inversión.

Costes a lo largo del tiempo
Si retira el dinero después de 1 año, los costes serán de 12 EUR por cada 10.000 EUR invertidos.
Incidencia anual de los costes: 0,12%

Composición de los costes
Costes de entrada: 0%
Costes de salida: 0%

Escenarios de rentabilidad
Período de mantenimiento recomendado: 1 año
"""

# Texto PRIIPs con moneda USD (fondo Fidelity LU1084165304)
_TEXT_PRIIPS_USD = """
Key Information Document
Costs over time
If you exit after 1 year: 510 USD out of 10,000 USD invested.
Annual cost impact: 5.25%
Performance scenarios
Recommended holding period: 5 years
Composition of costs
Entry costs: 5.25%  Exit costs: 0%
"""

# Texto PRIIPs con pocas señales pero reforzado por Cat.3 (criterio relajado D-S2-1)
_TEXT_PRIIPS_WEAK = """
Key Information Document
Performance scenarios
Favourable scenario: you get back 11,500 EUR
"""

# Texto UCITS clásico (2 señales fuertes UCITS)
_TEXT_UCITS = """
Datos fundamentales para el inversor
Este documento le proporciona información esencial sobre este fondo.

Gastos corrientes: 1,50%
Comisión de rentabilidad: no procede
"""

# Texto UCITS en inglés — 2 señales: "key investor information" + patron entry/exit/ongoing
_TEXT_UCITS_EN = """
Key Investor Information
This document provides you with key investor information about this fund.
Charges: Entry charge up to 3%.  Exit charge: none.  Ongoing charges: 1.50%
"""

# Texto vacío / insuficiente
_TEXT_EMPTY = ''
_TEXT_INSUFFICIENT = 'Este es un documento genérico sin señales específicas de formato.'

# Texto sin moneda detectable
_TEXT_NO_CURRENCY = """
Key Information Document
Costs over time — annual cost impact 1.5%
Performance scenarios
"""

# Texto con moneda GBP
_TEXT_PRIIPS_GBP = """
Key Information Document
Costs over time
If you exit after 3 years: 450 GBP per 10,000 GBP invested.
Annual cost impact: 1.5%
Performance scenarios
Recommended holding period: 3 years
"""


# ======================================================================
# Suite A: tests unitarios con texto sintético
# ======================================================================

def test_priips_eur_detected():
    """Texto PRIIPs estándar con EUR → PRIIPS_KID."""
    result = detect_kid_format(_TEXT_PRIIPS_EUR)
    assert result == 'PRIIPS_KID', f"Expected PRIIPS_KID, got {result!r}"

def test_priips_usd_detected():
    """Texto PRIIPs en inglés con USD → PRIIPS_KID."""
    result = detect_kid_format(_TEXT_PRIIPS_USD)
    assert result == 'PRIIPS_KID', f"Expected PRIIPS_KID, got {result!r}"

def test_priips_weak_with_cat3():
    """PRIIPs con solo 1 señal fuerte + 1 Cat.3 + sin UCITS → PRIIPS_KID (D-S2-1)."""
    result = detect_kid_format(_TEXT_PRIIPS_WEAK)
    assert result == 'PRIIPS_KID', f"Expected PRIIPS_KID (D-S2-1 relajado), got {result!r}"

def test_ucits_es_detected():
    """Texto UCITS en español → UCITS_KIID."""
    result = detect_kid_format(_TEXT_UCITS)
    assert result == 'UCITS_KIID', f"Expected UCITS_KIID, got {result!r}"

def test_ucits_en_detected():
    """Texto UCITS en inglés → UCITS_KIID."""
    result = detect_kid_format(_TEXT_UCITS_EN)
    assert result == 'UCITS_KIID', f"Expected UCITS_KIID, got {result!r}"

def test_empty_text_unknown():
    """Texto vacío → UNKNOWN."""
    assert detect_kid_format(_TEXT_EMPTY) == 'UNKNOWN'

def test_whitespace_only_unknown():
    """Texto solo espacios → UNKNOWN."""
    assert detect_kid_format('   \n\t  ') == 'UNKNOWN'

def test_insufficient_signals_unknown():
    """Texto sin señales suficientes → UNKNOWN."""
    assert detect_kid_format(_TEXT_INSUFFICIENT) == 'UNKNOWN'

def test_ucits_priority_over_priips():
    """
    Si un texto tiene tanto señales UCITS como PRIIPs (documento híbrido),
    UCITS tiene prioridad → UCITS_KIID.
    """
    hybrid = _TEXT_UCITS + '\n' + _TEXT_PRIIPS_EUR
    result = detect_kid_format(hybrid)
    assert result == 'UCITS_KIID', (
        f"UCITS debe tener prioridad sobre PRIIPs. got {result!r}"
    )

def test_currency_eur():
    """detect_kid_currency detecta EUR."""
    assert detect_kid_currency(_TEXT_PRIIPS_EUR) == 'EUR'

def test_currency_usd():
    """detect_kid_currency detecta USD."""
    assert detect_kid_currency(_TEXT_PRIIPS_USD) == 'USD'

def test_currency_gbp():
    """detect_kid_currency detecta GBP."""
    assert detect_kid_currency(_TEXT_PRIIPS_GBP) == 'GBP'

def test_currency_none_when_absent():
    """Sin patrón de moneda → None."""
    assert detect_kid_currency(_TEXT_NO_CURRENCY) is None

def test_currency_none_empty():
    """Texto vacío → None."""
    assert detect_kid_currency('') is None

def test_currency_case_insensitive():
    """El patrón de moneda es case-insensitive."""
    text = 'costes a lo largo del tiempo  12 eur por cada 10.000 eur invertidos'
    result = detect_kid_currency(text)
    assert result == 'EUR', f"Expected EUR, got {result!r}"

def test_signals_detail_priips():
    """get_format_signals_detail devuelve dict con las claves esperadas."""
    detail = get_format_signals_detail(_TEXT_PRIIPS_EUR)
    assert 'priips_count' in detail
    assert 'ucits_count'  in detail
    assert 'cat3_count'   in detail
    assert 'format'       in detail
    assert 'currency'     in detail
    assert detail['format'] == 'PRIIPS_KID'
    assert detail['priips_count'] >= 3
    assert detail['ucits_count']  == 0
    assert detail['currency'] == 'EUR'

def test_priips_count_threshold():
    """Exactamente 2 señales PRIIPs sin Cat.3 → UNKNOWN (no alcanza umbral)."""
    text = (
        'Key Information Document\n'
        'Costs over time: 1.5%\n'
        # Solo 2 señales fuertes PRIIPs, sin Cat.3, sin UCITS
    )
    result = detect_kid_format(text)
    assert result == 'UNKNOWN', (
        f"2 señales PRIIPs sin Cat.3 debería ser UNKNOWN, got {result!r}"
    )


# ======================================================================
# Textos sintéticos para los 8 ISINs muestra
# (representan el contenido mínimo que generaría el extractor de texto)
# ======================================================================

_SAMPLE_TEXTS = {
    'IE00BJGT6Q17': """
        Documento de datos fundamentales — PIMCO
        Costes a lo largo del tiempo
        10.000 EUR invertidos durante 1 año: 197 EUR
        Incidencia anual de los costes: 1,97%
        Composición de los costes: costes de entrada 0%
        Escenarios de rentabilidad
        Período de mantenimiento recomendado: 1 año
    """,
    'LU1084165304': """
        Key Information Document — Fidelity
        Costs over time
        10,000 USD invested over 1 year: 510 USD
        Annual cost impact: 5.25%
        Composition of costs: entry costs 5.25%
        Performance scenarios
        Recommended holding period: 5 years
    """,
    'IE0032875985': """
        Documento de datos fundamentales — PIMCO
        Costes a lo largo del tiempo — 10.000 EUR
        Incidencia anual de los costes: 2,40%
        Composición de los costes
        Costes de entrada: 0%
        Escenarios de rentabilidad
        Período de mantenimiento recomendado: 3 años
    """,
    'IE00B45H7020': """
        Key Information Document — BlackRock
        Costs over time
        10,000 EUR — annual cost impact: 0.10%
        Composition of costs: entry 0% exit 0%
        Performance scenarios
        Recommended holding period: 1 year
    """,
    'FR0000989626': """
        Documento de datos fundamentales — Groupama
        Costes a lo largo del tiempo
        10.000 EUR invertidos: costes de entrada 50 EUR (0,50%)
        Incidencia anual de los costes: 1,20%
        Composición de los costes
        Escenarios de rentabilidad
        Período de mantenimiento recomendado: 5 años
    """,
    'LU0135992385': """
        Key Information Document — Schroders
        Costs over time
        10,000 EUR — no entry costs charged
        Annual cost impact: 1.65%
        Composition of costs
        Performance scenarios
        Recommended holding period: 5 years
    """,
    'IE00BZ4D7085': """
        Key Information Document — Polar Capital
        Costs over time
        10,000 EUR: 12 EUR annual cost impact 0.12%
        Composition of costs: entry costs 0% (up to 5% in future)
        Performance scenarios
        Recommended holding period: 1 year
    """,
    'LU1502282632': """
        Documento de datos fundamentales — Candriam
        Costes a lo largo del tiempo — 10.000 EUR
        Incidencia anual de los costes: 1,80%
        Composición de los costes
        Costes de entrada: máximo 3,50%
        Escenarios de rentabilidad
        Período de mantenimiento recomendado: 5 años
    """,
}

_EXPECTED_CURRENCIES = {
    'IE00BJGT6Q17': 'EUR',
    'LU1084165304': 'USD',
    'IE0032875985': 'EUR',
    'IE00B45H7020': 'EUR',
    'FR0000989626': 'EUR',
    'LU0135992385': 'EUR',
    'IE00BZ4D7085': 'EUR',
    'LU1502282632': 'EUR',
}


def test_all_8_samples_priips():
    """Los 8 textos sintéticos de los ISINs muestra → todos PRIIPS_KID."""
    failed = []
    for isin, text in _SAMPLE_TEXTS.items():
        result = detect_kid_format(text)
        if result != 'PRIIPS_KID':
            detail = get_format_signals_detail(text)
            failed.append(
                f"{isin}: expected PRIIPS_KID, got {result!r} "
                f"(priips={detail['priips_count']}, ucits={detail['ucits_count']}, "
                f"cat3={detail['cat3_count']})"
            )
    assert not failed, 'Fallos en ISINs muestra:\n' + '\n'.join(failed)


def test_all_8_samples_currency():
    """Los 8 textos sintéticos detectan la moneda esperada."""
    failed = []
    for isin, text in _SAMPLE_TEXTS.items():
        expected = _EXPECTED_CURRENCIES[isin]
        result = detect_kid_currency(text)
        if result != expected:
            failed.append(f"{isin}: expected {expected!r}, got {result!r}")
    assert not failed, 'Fallos de moneda:\n' + '\n'.join(failed)


# ======================================================================
# Suite B: tests sobre PDFs reales (solo si --real-pdfs y archivos existen)
# ======================================================================

def _suite_b_real_pdfs(pdf_dir: str) -> None:
    """
    Lee los 8 PDFs reales con pdfplumber y verifica detect_kid_format → PRIIPS_KID.
    Requiere pdfplumber instalado y los PDFs accesibles en pdf_dir.
    """
    try:
        import pdfplumber
    except ImportError:
        print('[Suite B] pdfplumber no disponible — omitiendo tests sobre PDFs reales.')
        return

    ISINS = [
        'IE00BJGT6Q17', 'LU1084165304', 'IE0032875985', 'IE00B45H7020',
        'FR0000989626', 'LU0135992385', 'IE00BZ4D7085', 'LU1502282632',
    ]

    failed = []
    skipped = []
    for isin in ISINS:
        pdf_path = os.path.join(pdf_dir, f'{isin}.pdf')
        if not os.path.isfile(pdf_path):
            skipped.append(isin)
            continue
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
        except Exception as exc:
            failed.append(f'{isin}: error al leer PDF — {exc}')
            continue

        fmt = detect_kid_format(text)
        cur = detect_kid_currency(text)
        detail = get_format_signals_detail(text)

        if fmt != 'PRIIPS_KID':
            failed.append(
                f'{isin}: expected PRIIPS_KID, got {fmt!r} '
                f'(priips={detail["priips_count"]}, ucits={detail["ucits_count"]}, '
                f'cat3={detail["cat3_count"]})'
            )
        else:
            print(
                f'[Suite B] {isin}: {fmt} currency={cur} '
                f'(priips={detail["priips_count"]}, cat3={detail["cat3_count"]})'
            )

    if skipped:
        print(f'[Suite B] PDFs no encontrados (omitidos): {skipped}')
    if failed:
        raise AssertionError('[Suite B] Fallos:\n' + '\n'.join(failed))
    else:
        n = len(ISINS) - len(skipped)
        print(f'[Suite B] {n}/{len(ISINS)} PDFs reales: OK')


# ======================================================================
# Runner
# ======================================================================

def _run_all_tests() -> None:
    suite_a = [
        test_priips_eur_detected,
        test_priips_usd_detected,
        test_priips_weak_with_cat3,
        test_ucits_es_detected,
        test_ucits_en_detected,
        test_empty_text_unknown,
        test_whitespace_only_unknown,
        test_insufficient_signals_unknown,
        test_ucits_priority_over_priips,
        test_currency_eur,
        test_currency_usd,
        test_currency_gbp,
        test_currency_none_when_absent,
        test_currency_none_empty,
        test_currency_case_insensitive,
        test_signals_detail_priips,
        test_priips_count_threshold,
        test_all_8_samples_priips,
        test_all_8_samples_currency,
    ]

    passed = 0
    failed_names = []
    for fn in suite_a:
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed_names.append(f'  FAIL {fn.__name__}: {exc}')
        except Exception as exc:
            failed_names.append(f'  ERROR {fn.__name__}: {exc}')

    total = len(suite_a)
    print(f'Suite A: {passed}/{total} OK')
    if failed_names:
        print('\n'.join(failed_names))

    # Suite B (PDFs reales) si se pide explícitamente
    if '--real-pdfs' in sys.argv:
        # Buscar PDFs en la ruta de producción o en el directorio del script
        pdf_dir = os.environ.get(
            'FONDOS_PDF_DIR',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'data', 'kiids')
        )
        print(f'\nSuite B (PDFs reales) — buscando en: {os.path.normpath(pdf_dir)}')
        _suite_b_real_pdfs(pdf_dir)

    if failed_names:
        sys.exit(1)


if __name__ == '__main__':
    _run_all_tests()
