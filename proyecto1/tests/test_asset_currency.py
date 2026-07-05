# proyecto1/tests/test_asset_currency.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de classify_utils.detect_asset_currency_from_name() y
detect_fx_share_class_mismatch() (schema v21, BL-44-FX).

Cubre la clase de bug encontrada tras el despliegue de Asset_Currency:
menciones de divisa adyacentes a marcadores de cobertura ("HDG"/"HEDGE")
u otros artefactos de clase de participación deben ignorarse -- solo la
divisa que define la ESTRATEGIA del fondo debe extraerse.
"""

import os
import sys
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from classify_utils import (
    detect_asset_currency_from_name,
    detect_asset_currency_from_kiid_text,
    detect_fx_share_class_mismatch,
)


def _kiid(objective_text):
    """Envuelve un fragmento de texto objetivo con suficiente relleno para
    que caiga dentro de la ventana objetivo por defecto (formato UNKNOWN,
    200-4500) que usa _get_obj_bounds() cuando el texto no contiene ninguna
    de las cabeceras DDF/KIID reconocidas -- evita que _extract_window()
    devuelva cadena vacía por texto demasiado corto."""
    filler = "x" * 250
    return filler + " " + objective_text


# ---------------------------------------------------------------------------
# detect_asset_currency_from_name — casos básicos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("SISF US DOLLAR LIQUIDITY A ACC", "USD"),
    ("PICTET EUR BONDS P ACC", "EUR"),
    ("BGF USD SHRT DUR BND A3 EU INC", "USD"),
    ("SOME FUND WITH NO CURRENCY NAMED", None),
    (None, None),
    ("", None),
])
def test_basic_currency_extraction(name, expected):
    assert detect_asset_currency_from_name(name) == expected


# ---------------------------------------------------------------------------
# Sufijo de clase de participación (trailing "<CCY> ACC/INC/DIS/CAP")
# ---------------------------------------------------------------------------

def test_trailing_share_class_suffix_does_not_hide_real_currency():
    """"PICTET USD GOV BDS I EUR ACC": activos en USD, clase en EUR --
    el sufijo final "EUR ACC" no debe ganar sobre la señal real "USD"."""
    assert detect_asset_currency_from_name("PICTET USD GOV BDS I EUR ACC") == "USD"


# ---------------------------------------------------------------------------
# FIX-ASSET-CCY-1: divisa adyacente a HDG/HEDGE es el destino de cobertura,
# NO la divisa de los activos.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "JPM US VALUE A EUR HDG ACC",           # renta variable EE.UU., no EUR
    "JPM EMERGING EQ A EUR HDG ACC",        # emergentes diversificado, no EUR
    "JPM GLOBAL MACRO (EUR HDG) D",         # macro diversificado, no EUR
    "FIDEL.F.JAP.A.FUND A EUR HEDGE",       # renta variable Japón, no EUR
    "SISF JAPANESE EQI.EUR HEDGED B",       # renta variable Japón, no EUR
    "BGF WORLD MINING A2 EUR HDG AC",       # sectorial global, no EUR
    "BGF SUST ENERGY A EUR HDG ACC",        # sectorial global, no EUR
    "FVS MULTI.OPP II ET USD HDG AC",       # multiactivo diversificado, no USD
])
def test_hedge_target_currency_is_not_asset_currency(name):
    assert detect_asset_currency_from_name(name) is None


def test_hedge_mask_does_not_hide_real_currency_appearing_earlier():
    """"BGF EURO BOND D2 (USD HDG) ACC": fondo de bonos en EUR (declarado
    explícitamente en el nombre) con clase cubierta a USD -- "USD HDG" debe
    enmascararse y "EURO" (la señal real) debe ganar. Bug confirmado:
    antes de este fix devolvía 'USD' (el destino de cobertura), no 'EUR'."""
    assert detect_asset_currency_from_name("BGF EURO BOND D2 (USD HDG) ACC") == "EUR"


@pytest.mark.parametrize("name,expected", [
    ("BGF USD HY BD A2 EURHDG ACC", "USD"),
    ("BGF EURO BOND A2 USDHDG ACC", "EUR"),
    ("JPM EUR EQ + A PERF USDHDG ACC", "EUR"),
    ("INVESCO PN EUR EQ A USDHDG ACC", "EUR"),
    ("UBS CHINA OPP USD Q EURHDG ACC", "USD"),
    ("BGF EURO BOND A2 (USD) ACC", "EUR"),  # sin "HDG" -- leftmost-match ya resuelve
])
def test_hedge_variants_resolve_to_the_real_strategy_currency(name, expected):
    assert detect_asset_currency_from_name(name) == expected


def test_fused_hedge_abbreviation_is_safely_ignored():
    """"EURH"/"USDH" (abreviatura de cobertura fusionada sin espacio) no
    coincide con \\beur\\b/\\busd\\b (sin límite de palabra tras "eur"/"usd"
    dentro de "eurh"/"usdh") -- se ignora de forma segura (None), no genera
    falso positivo. Confirmado corpus-wide: 263 fondos con este patrón,
    todos ya devuelven None sin necesidad de un mask adicional."""
    assert detect_asset_currency_from_name("JPM GLOBAL DIVIDEND A EURH ACC") is None
    assert detect_asset_currency_from_name("PIMCO GLOBAL BOND I USDH ACC") is None


# ---------------------------------------------------------------------------
# detect_asset_currency_from_kiid_text — fallback cuando el nombre no basta
# ---------------------------------------------------------------------------

def test_kiid_text_asset_subject_included():
    """Sujeto de ACTIVO explícito ("activos", "instrumentos del mercado
    monetario") -- señal limpia, se acepta."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "estos activos siempre estarán denominados en dólares estadounidenses."
    )) == "USD"
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el fondo invertirá en instrumentos del mercado monetario "
        "denominados en euros."
    )) == "EUR"


def test_kiid_text_feminine_subject_included():
    """Sujetos femeninos ("deuda", "renta fija") con concordancia femenina
    ("denominada"/"expresada") -- misma familia de señal, género distinto.
    Caso reportado por el usuario tras revisar los resultados."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "perfil de inversión del 50% en renta fija denominada en euros "
        "(valor de referencia: bloomberg euro aggregate)."
    )) == "EUR"
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el fondo invierte en deuda de gobiernos y organismos "
        "gubernamentales denominada en euros o cubierta frente a esta moneda."
    )) == "EUR"


def test_kiid_text_share_class_subject_excluded():
    """"acciones"/"participaciones"/"clase" preceden a "denominada(s) en"
    en el mismo tipo de frase, pero refieren a la CLASE DE PARTICIPACIÓN,
    no a los activos -- caso reportado por el usuario: "este producto es
    una clase de participaciones de acumulación del fondo denominada en
    usd" no debe extraer USD como Asset_Currency."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "este producto es una clase de participaciones de acumulación "
        "del fondo denominada en usd."
    )) is None
    assert detect_asset_currency_from_kiid_text(_kiid(
        "sus acciones estarán denominadas en dólares estadounidenses, "
        "la moneda base del fondo."
    )) is None


def test_kiid_text_closest_subject_wins_over_farther_one():
    """"el índice mide la RENTABILIDAD de los VALORES denominados en
    euros": "rentabilidad" (excluir) aparece en la ventana, pero "valores"
    (incluir) está más cerca del verbo y debe ganar."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el índice de referencia mide la rentabilidad de los valores de "
        "rf denominados en euros y con categoría de grado de inversión."
    )) == "EUR"


def test_kiid_text_objective_framing_excluded():
    """"apreciación del capital"/"rentabilidad" expresada en una divisa
    describe el MARCO DE MEDICIÓN del objetivo, no la divisa de los
    activos -- frecuentemente un fondo genuinamente diversificado."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el objetivo del subfondo es proporcionar una apreciación del "
        "capital a largo plazo, expresada en dólares estadounidenses, "
        "con una cartera de renta variable cotizada."
    )) is None


def test_kiid_text_negation_excluded():
    """"deuda NO denominada en euros" es una negación -- lo opuesto de una
    declaración de divisa, no debe extraerse."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "bonos preferentes y deuda no denominada en euros. la selección "
        "de estos bonos se basa en el análisis."
    )) is None


def test_kiid_text_multi_currency_continuation_excluded():
    """Continuación explícita con una segunda divisa/enumeración --
    mandato multi-divisa, no una única divisa dominante."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el subfondo invertirá hasta un 100% de su patrimonio neto en "
        "títulos de deuda denominados en euros u otras divisas."
    )) is None
    assert detect_asset_currency_from_kiid_text(_kiid(
        "estos valores de renta fija y bonos convertibles pueden estar "
        "denominados en dólares estadounidenses, otras monedas del g7 "
        "y diversas monedas de la región asia-pacífico."
    )) is None
    assert detect_asset_currency_from_kiid_text(_kiid(
        "invierte principalmente en deuda pública y corporativa "
        "denominada en dólares estadounidenses o divisas locales de "
        "emisores de asia emergente."
    )) is None


def test_kiid_text_permissive_secondary_excluded():
    """"los activos denominados en renminbis PODRÁN ser invertidos" --
    asignación secundaria/opcional tras un mandato primario en otra
    divisa, no debe eclipsar la señal principal."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "así como en depósitos de efectivo expresados en dólares. "
        "los activos denominados en renminbis podrán ser invertidos "
        "a través de los mercados chinos."
    )) is None


def test_kiid_text_hedge_qualifier_does_not_block_valid_match():
    """"denominados en euros o cubiertos frente a esa moneda" -- la
    cobertura hacia la MISMA divisa no es una continuación multi-divisa,
    la señal EUR sigue siendo válida."""
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el fondo invierte en deuda pública y bonos corporativos "
        "denominados en euros o cubiertos frente a esa moneda con un "
        "tipo de interés fijo."
    )) == "EUR"


def test_kiid_text_returns_none_without_match():
    assert detect_asset_currency_from_kiid_text(_kiid(
        "el fondo invierte en una amplia gama de activos diversificados "
        "a nivel mundial sin ningún mandato de divisa concreto."
    )) is None
    assert detect_asset_currency_from_kiid_text(None) is None
    assert detect_asset_currency_from_kiid_text("") is None


# ---------------------------------------------------------------------------
# detect_fx_share_class_mismatch
# ---------------------------------------------------------------------------

def test_fx_mismatch_true_for_genuine_currency_layer():
    assert detect_fx_share_class_mismatch("USD", "EUR") is True


def test_fx_mismatch_false_when_currencies_match():
    assert detect_fx_share_class_mismatch("EUR", "EUR") is False


def test_fx_mismatch_false_when_hedged():
    """La cobertura neutraliza el riesgo cambiario -- el descalce no exime."""
    assert detect_fx_share_class_mismatch("USD", "EUR", hedging_policy="Hedged") is False
    assert detect_fx_share_class_mismatch("USD", "EUR", hedging_policy="HEDGED") is False


def test_fx_mismatch_true_when_unhedged_or_unknown():
    assert detect_fx_share_class_mismatch("USD", "EUR", hedging_policy="Unhedged") is True
    assert detect_fx_share_class_mismatch("USD", "EUR", hedging_policy=None) is True


def test_fx_mismatch_false_above_srri_plausibility_ceiling():
    """Un SRRI muy alto no puede explicarse solo por riesgo cambiario --
    el conflicto probablemente tiene otra causa y BL-44 debe seguir
    marcándolo."""
    assert detect_fx_share_class_mismatch("USD", "EUR", srri=4) is True
    assert detect_fx_share_class_mismatch("USD", "EUR", srri=6) is False


def test_fx_mismatch_false_without_asset_currency():
    """Fondo diversificado (sin mandato de divisa única) -- nada que eximir."""
    assert detect_fx_share_class_mismatch(None, "EUR") is False
