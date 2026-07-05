# proyecto1/tests/test_fund_currency.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de kiid_parser._detect_fund_currency() (FIX-FUNDCCY-1).

Cubre el bug encontrado tras extender el trabajo de Asset_Currency a
Fund_Currency: la variante de tabla de costes con celdas separadas por
dos puntos ("Costes totales:\\n:\"En caso de salida...\"1 año:: 252: USD:",
observada en KIIDs de JPMorgan) hacía fallar la detección de alta
prioridad y caía al fallback "moneda base del Subfondo" -- que describe
la divisa del SUBFONDO, no de la CLASE DE PARTICIPACIÓN específica.
Confirmado corpus-wide: 118 fondos (familia JPM), cada uno verificado
contra el sufijo de divisa de su propio nombre de clase.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from kiid_parser import _detect_fund_currency
from classify_utils import detect_fund_currency_from_name


def test_colon_table_format_extracts_share_class_currency():
    """Formato JPM real (LU0157178582, JPM GLOBAL SELECT EQ A EUR ACC):
    la clase de participación es EUR, aunque la divisa base del Subfondo
    (mencionada más abajo en el documento) sea USD."""
    text = (
        'Costes totales:\n'
        ':“En caso de salida después de”1 año:: 252: EUR:\n'
        ':“En caso de salida después de”5 años“'
        '(período de mantenimiento recomendado):: 2.701: EUR:\n'
        'Incidencia anual de los costes*:\n'
        'Divisas Moneda base del Subfondo: USD. Divisas de denominación de '
        'los activos: cualquiera.'
    )
    assert _detect_fund_currency(text, "ES") == "EUR"


def test_colon_table_format_other_currencies():
    text_usd = (
        'Costes totales:\n'
        ':“En caso de salida después de”1 año:: 685: USD:\n'
        'Moneda base del Subfondo: EUR.'
    )
    assert _detect_fund_currency(text_usd, "ES") == "USD"

    text_gbp = (
        'Costes totales:\n'
        ':“En caso de salida después de”1 año:: 812: GBP:\n'
        'Moneda base del Subfondo: EUR.'
    )
    assert _detect_fund_currency(text_gbp, "ES") == "GBP"


def test_simple_space_separated_format_still_works():
    """Formato simple pre-existente ("Costes totales 778 EUR") no debe
    romperse por la nueva variante de tabla con dos puntos."""
    text = "Costes totales 778 EUR 3330 EUR\nImpacto anual de los costes(*)"
    assert _detect_fund_currency(text, "ES") == "EUR"

    text2 = "Costes totales USD 478 USD 880\nIncidencia anual de los costes (*)"
    assert _detect_fund_currency(text2, "ES") == "USD"


def test_subfund_base_currency_used_only_as_last_resort():
    """Sin ninguna tabla de costes parseable, el fallback a "moneda base
    del Subfondo" sigue disponible (mejor que nada), pero no debe ganar
    cuando la tabla de costes de la propia clase sí es parseable."""
    text = (
        'Costes totales:\n'
        ':“En caso de salida después de”1 año:: 252: EUR:\n'
        'Moneda base del Subfondo: USD.'
    )
    assert _detect_fund_currency(text, "ES") == "EUR"


# ---------------------------------------------------------------------------
# detect_fund_currency_from_name (FIX-FUNDCCY-2) — cross-validación por
# nombre, la misma clase de comprobación dual (nombre + texto KIID) usada
# para Asset_Currency, aplicada aquí a Fund_Currency.
# ---------------------------------------------------------------------------

def test_name_suffix_extracts_share_class_currency():
    assert detect_fund_currency_from_name("JPM GLOBAL SELECT EQ A EUR ACC") == "EUR"
    assert detect_fund_currency_from_name("JPM US VALUE A EUR ACC") == "EUR"
    assert detect_fund_currency_from_name("JPM GLOBAL NATURL RES A USD ACC") == "USD"
    assert detect_fund_currency_from_name("JPM EUROPE EQ PLUS A GBP INC") == "GBP"
    assert detect_fund_currency_from_name("JPM CHINA ASHARE OPP C EUR ACC") == "EUR"


def test_name_suffix_none_without_recognizable_pattern():
    """"...EUR HDG ACC" no termina en "EUR ACC" directamente (hay "HDG" de
    por medio) -- correctamente no se reconoce como sufijo de clase, ya
    que "EUR" ahí es el destino de cobertura, no necesariamente la divisa
    final de la clase (ver FIX-ASSET-CCY-1, misma familia de ambigüedad)."""
    assert detect_fund_currency_from_name("JPM US VALUE A EUR HDG ACC") is None
    assert detect_fund_currency_from_name("BGF WORLD MINING A2 EUR HDG AC") is None
    assert detect_fund_currency_from_name(None) is None
    assert detect_fund_currency_from_name("") is None


def test_name_suffix_truncated_acc_not_recognized():
    """Limitación conocida y compartida con `_TRAILING_SHARE_CLASS_SUFFIX`
    (Asset_Currency): el Excel maestro a veces trunca "ACC" a "AC" --
    "...USD AC" no coincide con el patrón (exige la palabra completa). No
    es una regresión de este fix, es el mismo comportamiento ya existente
    en el extractor de Asset_Currency."""
    assert detect_fund_currency_from_name("JPM GLOBAL NATURL RES A USD AC") is None


def test_name_and_kiid_signals_confirm_the_fixed_bug():
    """Reproduce el caso real que motivó FIX-FUNDCCY-1/2: antes del fix,
    el texto KIID (vía _detect_fund_currency) caía al fallback de nivel de
    subfondo (USD) mientras que el nombre de la propia clase (EUR ACC)
    daba la señal correcta -- ambas señales, una vez FIX-FUNDCCY-1
    aplicado, deben coincidir."""
    name = "JPM GLOBAL SELECT EQ A EUR ACC"
    kiid_text = (
        'Costes totales:\n'
        ':“En caso de salida después de”1 año:: 252: EUR:\n'
        'Moneda base del Subfondo: USD.'
    )
    assert detect_fund_currency_from_name(name) == "EUR"
    assert _detect_fund_currency(kiid_text, "ES") == "EUR"
