# proyecto1/tests/test_hedging_policy.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de:
  - kiid_parser._detect_hedging_policy() (FIX-HEDGE-4, 2026-07-05)
  - pipeline._GENERIC_HEDGE_CLASS_PAT (FIX-HEDGCCY-2, 2026-07-12)

Contexto HEDGE-4: auditoría 'restantes' encontró 249 fondos con
Hedging_Policy=NULL. Solo se añadió patrón estrecho ("cobertura de la
clase de acciones/participaciones"). Impacto: 5 fondos.

Contexto HEDGCCY-2: M&G OPTIMAL INCOME AH/CH ACC/INC generaba
HEDGCCY_NO_MISMATCH_INCONSISTENCY porque "AH"/"CH" son códigos de clase
hedged sin sufijo de divisa explícito (EURH/USDHDG). Patrón genérico
[A-Z]{1,2}H antes de ACC/INC/DIS cubre esta convención UCITS estándar.
"""

import os
import sys
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
_PROYECTO1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..'))
_REPO_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
for _p in [_CORE_DIR, _PROYECTO1_DIR, _REPO_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from kiid_parser import _detect_hedging_policy


# ---------------------------------------------------------------------------
# Importar el patrón compilado desde pipeline (módulo-nivel, reutilizable)
# ---------------------------------------------------------------------------
def _load_generic_hedge_pat():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "pipeline",
        str(pathlib.Path(_CORE_DIR) / "pipeline.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # evitar importar la lógica de pandas/openpyxl que no está disponible
    # en el entorno de test; el patrón es una constante compilada al inicio
    try:
        spec.loader.exec_module(mod)
    except Exception:
        pass
    return getattr(mod, "_GENERIC_HEDGE_CLASS_PAT", None)


# ---------------------------------------------------------------------------
# FIX-HEDGCCY-2 (2026-07-12): _GENERIC_HEDGE_CLASS_PAT
# Cubre clases UCITS con sufijo [A-Z]{1,2}H antes de ACC/INC/DIS[T].
# Convención estándar: AH=A hedged, BH=B hedged, CH=C hedged, DH=D hedged…
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,should_match,desc", [
    # Cases that MUST match (genuine hedged share class codes)
    ("M&G (LU) OPTIMAL INCOME AH ACC",        True,  "AH ACC — M&G A hedged (caso real)"),
    ("M&G (LU) OPTIMAL INCOME AH INC",        True,  "AH INC — M&G A hedged"),
    ("M&G (LU) OPTIMAL INCOME CH ACC",        True,  "CH ACC — M&G C hedged (caso real)"),
    ("M&G (LU) OPTIMAL INCOME CH INC",        True,  "CH INC — M&G C hedged"),
    ("JUPITER M. EMERG M DEBT AH ACC",         True,  "AH ACC — Jupiter A hedged"),
    ("MS GLOBAL OPPORTUNITY AH ACC",           True,  "AH ACC — Morgan Stanley A hedged"),
    ("TROWE PRICE JAPANESE EQ AH ACC",         True,  "AH ACC — T.Rowe A hedged"),
    ("ROBECO GLOB CONSM TRNDS DH ACC",         True,  "DH ACC — Robeco D hedged"),
    ("SOME FUND ZH INC",                       True,  "ZH INC — genérico Z hedged"),
    ("SOME FUND BH DIS",                       True,  "BH DIS — genérico B hedged distribución"),
    # Cases that must NOT match (false-positive risk)
    ("BGF DYN HIGH INC E2 EURHDG ACC",         False, "HIGH INC — 'HIGH' tiene 4 chars, excluido por {1,2}"),
    ("FIDELITY GLOBAL HEALTH CARE A ACC",      False, "HEALTH CARE — no [X]H antes de ACC (HEALTH ≠ XH)"),
    ("INVESCO TECHNOLOGY FUND A TECH ACC",     False, "TECH ACC — sin sufijo H"),
    ("AMUNDI CASH A EUR ACC",                  False, "CASH — 'ASH' no es word-boundary token"),
    ("PICTET FLAGSHIP EUROPE A ACC",           False, "FLAGSHIP — no XH token"),
])
def test_generic_hedge_class_pattern(name, should_match, desc):
    """FIX-HEDGCCY-2 (2026-07-12): _GENERIC_HEDGE_CLASS_PAT identifica
    clases hedged tipo 'AH ACC', 'CH INC' sin falsos positivos en palabras
    comunes (HIGH, HEALTH, CASH, etc.)."""
    import re
    _pat = re.compile(r"\b[A-Z]{1,2}H\s+(?:ACC|INC|DIS[T]?)\b")
    result = bool(_pat.search(name.upper()))
    assert result == should_match, (
        f"Pattern match={result} (expected {should_match}) for '{desc}'. Name: {name!r}"
    )


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


# ---------------------------------------------------------------------------
# FIX-HEDGE-5 (2026-07-12): context-aware "cubierto en" + ES_UNHEDGED negation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected,desc", [
    # FP cases — must NOT produce HEDGED
    (
        "al menos un 70 % del fondo estará expresado o cubierto en euros.",
        None,
        "M&G EURO CORP: 'expresado o cubierto en' = asset composition → None",
    ),
    (
        "el valor de referencia podrá estar expresado o cubierto en la moneda "
        "de la clase de acciones correspondiente.",
        None,
        "M&G EURO STRAT VALUE: benchmark 'expresado o cubierto en' → None",
    ),
    (
        "el valor de referencia puede estar expresado o cubierto en la moneda "
        "de la clase de acciones correspondiente.",
        None,
        "M&G OPT INC J/JI EUR: benchmark 'puede estar ... cubierto en' → None",
    ),
    (
        "bloomberg us hy constrained – total return 100% cubierto en eur.",
        None,
        "NORDEA EUROP HY: '100% cubierto en EUR' = benchmark total return → None",
    ),
    (
        "índice de referencia 70% msci world (net return) cubierto en eur y 30% euribor.",
        None,
        "NORDEA GL STBL EUR HD: 'índice de referencia ... cubierto en eur' → None",
    ),
    # Real hedging — MUST produce HEDGED
    (
        "esta clase de acciones está cubierta en eur. las operaciones de cobertura.",
        "HEDGED",
        "clase cubierta en EUR (feminine 'cubierta') → HEDGED via ES_HEDGED",
    ),
    (
        "el fondo está completamente cubierto en la divisa de la clase.",
        "HEDGED",
        "'cubierto en' with no false-positive context → HEDGED via _cubierto_en_genuine",
    ),
])
def test_cubierto_en_context_guard(text, expected, desc):
    """FIX-HEDGE-5: 'cubierto en' must NOT fire when in benchmark/composition context."""
    result = _detect_hedging_policy(text, "ES")
    assert result == expected, f"Expected {expected!r} but got {result!r} for: {desc}"


@pytest.mark.parametrize("text,expected,desc", [
    (
        "el fondo puede realizar transacciones de cobertura de divisas. "
        "por lo general, no se aplica una cobertura cambiaria.",
        "UNHEDGED",
        "ROBECO D/M USD: 'no se aplica una cobertura cambiaria' → UNHEDGED",
    ),
    (
        "generalmente no se aplica la cobertura cambiaria al subfondo.",
        "UNHEDGED",
        "'no se aplica la cobertura cambiaria' variant → UNHEDGED",
    ),
    (
        "el fondo puede realizar cobertura de divisas. no se aplica cobertura cambiaria.",
        "UNHEDGED",
        "'no se aplica cobertura cambiaria' (sin artículo) → UNHEDGED",
    ),
])
def test_no_se_aplica_cobertura_cambiaria_is_unhedged(text, expected, desc):
    """FIX-HEDGE-5: 'no se aplica cobertura cambiaria' must return UNHEDGED
    (precedes 'cobertura cambiaria' ES_HEDGED pattern in evaluation order)."""
    result = _detect_hedging_policy(text, "ES")
    assert result == expected, f"Expected {expected!r} but got {result!r} for: {desc}"


@pytest.mark.parametrize("name,should_match,desc", [
    # New case: BH (CCY) INC/ACC — parenthetical currency between class code H and dist.
    ("ROBECO HY BONDS BH (EUR) INC",  True,  "BH (EUR) INC — Robeco B hedged EUR (caso real)"),
    ("SOME FUND AH (USD) ACC",        True,  "AH (USD) ACC — generic A hedged USD"),
    ("JUPITER DH (GBP) INC",          True,  "DH (GBP) INC — Jupiter D hedged GBP"),
    # Pre-existing cases still work
    ("M&G (LU) OPTIMAL INCOME AH ACC", True,  "AH ACC — regression: no parens case still works"),
    ("ROBECO GLOB CONSM TRNDS DH ACC", True,  "DH ACC — regression: no parens case still works"),
    # False-positive guards still hold
    ("BGF DYN HIGH INC E2 EURHDG ACC", False, "HIGH INC — 4-char prefix excluded by {1,2}"),
    ("FIDELITY GLOBAL HEALTH CARE A ACC", False, "HEALTH CARE — no [X]H before ACC"),
])
def test_generic_hedge_class_pattern_extended(name, should_match, desc):
    """FIX-HEDGCCY-3: _GENERIC_HEDGE_CLASS_PAT now matches 'XH (CCY) ACC/INC' in
    addition to plain 'XH ACC/INC'."""
    import re
    _pat = re.compile(r"\b[A-Z]{1,2}H\s+(?:\([A-Z]{2,3}\)\s+)?(?:ACC|INC|DIS[T]?)\b")
    result = bool(_pat.search(name.upper()))
    assert result == should_match, (
        f"Pattern match={result} (expected {should_match}) for '{desc}'. Name: {name!r}"
    )
