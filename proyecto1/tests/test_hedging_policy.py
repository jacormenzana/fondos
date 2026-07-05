# proyecto1/tests/test_hedging_policy.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de kiid_parser._detect_hedging_policy() (FIX-HEDGE-4,
2026-07-05).

Contexto: auditoría de la población 'restantes' (fondos con nombre/KIID
más opacos, ver project_fix_master_load_1 / sesión 2026-07-05) encontró
249 fondos con Hedging_Policy=NULL. A diferencia de Geography (FIX-GEO-5,
mismo día), la mayoría de las "menciones" de cobertura en esos 249 fondos
son boilerplate GENÉRICO de uso de derivados ("el fondo puede usar
derivados con fines de cobertura y de inversión") -- gestión de riesgo
del fondo en general, NO la política de cobertura de la clase de
participación. Añadir un patrón bare para ese boilerplate generaría un
volumen grande de falsos positivos HEDGED (69/249 solo en la muestra
'restantes' mencionan cobertura exclusivamente en ese contexto genérico).

Solo se añadió un patrón estrecho y seguro: "cobertura de la clase de
acciones/participaciones" (ata "cobertura" explícitamente a la CLASE como
objeto, orden de palabras distinto de "clase [de acciones] cubierta" ya
cubierto). Impacto corpus-wide: 5 fondos (no 249) -- el resto del hueco
no es una regresión de patrón corregible sin riesgo de precisión, sino en
su mayoría fondos donde el concepto de cobertura de divisa genuinamente
no se discute (single-share-class, sin necesidad de cobertura).
"""

import os
import sys
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from kiid_parser import _detect_hedging_policy


def test_share_class_hedging_word_order_variant():
    """FIX-HEDGE-4: 'cobertura de la clase de acciones/participaciones'
    (orden de palabras distinto de 'clase cubierta', ya cubierto por un
    patrón preexistente)."""
    assert _detect_hedging_policy(
        "las posiciones en efectivo pueden resultar de beneficios por la "
        "cobertura de la clase de acciones frente a la divisa base.",
        "ES",
    ) == "HEDGED"
    assert _detect_hedging_policy(
        "los beneficios derivados de la cobertura de la clase de "
        "participaciones se reinvertirán periódicamente.",
        "ES",
    ) == "HEDGED"


def test_generic_derivative_boilerplate_is_not_hedged():
    """Guard de precisión: 'con fines de cobertura' es boilerplate
    GENÉRICO sobre uso de derivados del fondo (gestión de riesgo),
    no la política de cobertura de ESTA clase -- no debe producir
    HEDGED. Confirmado que es el patrón dominante (69/249) en la
    población 'restantes' con Hedging_Policy=NULL; tratarlo como señal
    positiva generaría falsos positivos masivos."""
    assert _detect_hedging_policy(
        "el subfondo podrá utilizar instrumentos financieros a plazo, "
        "tanto con fines de cobertura como de exposición a los riesgos "
        "de renta variable, renta fija y de divisas.",
        "ES",
    ) is None
    assert _detect_hedging_policy(
        "el fondo podrá utilizar derivados para reducir los riesgos "
        "(cobertura) y los costes, así como para generar crecimiento.",
        "ES",
    ) is None


def test_deposit_guarantee_scheme_is_not_hedged():
    """Guard de precisión: 'depósitos no están cubiertos por el fondo de
    garantía de depósitos' es sobre el ESQUEMA DE GARANTÍA DE DEPÓSITOS,
    no la política de cobertura de divisa del fondo."""
    assert _detect_hedging_policy(
        "en caso de que sus depósitos no estén cubiertos por el fondo de "
        "garantía de depósitos de la asociación bancaria alemana.",
        "ES",
    ) is None


def test_existing_hedged_patterns_still_work():
    """Regresión: patrones preexistentes de ES_HEDGED/ES_UNHEDGED no
    afectados por la nueva adición."""
    assert _detect_hedging_policy(
        "la clase de acciones está cubierta frente al riesgo de divisa.",
        "ES",
    ) == "HEDGED"
    assert _detect_hedging_policy(
        "esta clase de acciones no está cubierta.", "ES",
    ) == "UNHEDGED"


def test_returns_none_without_text_or_language():
    assert _detect_hedging_policy(None, "ES") is None
    assert _detect_hedging_policy("", "ES") is None
    assert _detect_hedging_policy(
        "la clase de acciones está cubierta frente al riesgo de divisa.",
        None,
    ) is None
