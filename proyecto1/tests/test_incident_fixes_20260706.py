# proyecto1/tests/test_incident_fixes_20260706.py
# -*- coding: utf-8 -*-
"""
Tests for fixes applied 2026-07-06:

  BL-RV-EX7 (renta_variable.py):
    "jpm income" added to exclude_patterns.
    Root cause: JPM INCOME / JPM GLOBAL INCOME captured by bare "income"
    and "global" include-patterns in RV, producing a triple-claim with
    rf_flexible and mixtos (INTER-13 with 3 different natures each cycle).
    Fix reduces triple-claim to dual-claim handled by INTER_DBLCLAIM.

  FIX-GEO-7 (pipeline.py):
    When KIID geography = 'Global' and name-based geography = specific
    country/region, name signal now wins. Resolves 69 GEOGRAPHY_NAME_KIID
    _MISMATCH warnings (BGF CHINA BOND series, etc.). DQ issue emitted as
    INFO 'GEOGRAPHY_NAME_WINS', not WARN.

  FIX-GEO-7 / Country+Global auto-correction (pipeline.py):
    When _geo_name_wins AND validate_geography_universe returns WARNING
    (Country + Global), Investment_Universe is auto-corrected to 'Country'.

  FIX-FUNDCCY-NAME (pipeline.py):
    Fund_Currency now prefers name suffix over KIID base-fund currency.
    Share-class denomination suffix (e.g. "EUR INC", "USD ACC") is
    definitionally the Fund_Currency for that class. Resolves 3 funds:
    JPM GLB BND OPP C EUR INC, MAGNA MENA G USD ACC, SCHRODER ISF SEC
    CRE A EUR INC.

  FIX-HEDGCCY-1 (kiid_parser.py):
    ES_HEDGED pattern for currency+hedged now requires parentheses
    (changed from optional to required). Bare "EUR Hedged" (common in
    benchmark index names like "Bloomberg Pan European HY Index EUR Hedged")
    no longer triggers Hedging_Policy='Hedged'. Resolves ~20 false-positive
    HEDGCCY_NO_MISMATCH_INCONSISTENCY warnings.
"""

from __future__ import annotations

import os
import re
import sys

import pandas as pd
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
_P1_DIR    = os.path.normpath(os.path.join(_TESTS_DIR, '..'))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import blocks.renta_variable as rv


def _df(*rows):
    return pd.DataFrame([
        {"ISIN": isin, "Fund_Name": name, "Management_Company": "Test"}
        for isin, name in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# BL-RV-EX7: JPM Income triple-claim eliminated
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("isin,name", [
    ("LU0248177003", "JPM INCOME A EUR ACC"),
    ("LU0248177185", "JPM INCOME B EUR ACC"),
    ("LU0248177268", "JPM INCOME C EUR ACC"),
    ("LU1694220003", "JPM GLOBAL INCOME A EUR ACC"),
    ("LU1694220185", "JPM GLOBAL INCOME C EUR INC"),
])
def test_jpm_income_not_in_renta_variable(isin, name):
    """BL-RV-EX7: JPM Income funds must NOT be claimed by renta_variable
    (they are multi-asset income funds, not equity)."""
    df = _df((isin, name))
    assert isin not in rv.get_universe_isins(df), (
        f"{name!r} must be excluded from renta_variable (BL-RV-EX7 'jpm income')"
    )


def test_jpm_equity_unaffected_by_rv_ex7():
    """Regression: genuine JPM equity fund must still be captured by RV."""
    df = _df(("LU0129459060", "JPM EUROPE EQUITY A EUR ACC"))
    assert "LU0129459060" in rv.get_universe_isins(df)


def test_jpm_dynamic_unaffected_by_rv_ex7():
    """Regression: JPM Dynamic fund (equity) is not excluded by 'jpm income'."""
    df = _df(("LU9999000001", "JPM DYNAMIC GROWTH EUR ACC"))
    assert "LU9999000001" in rv.get_universe_isins(df)


# ─────────────────────────────────────────────────────────────────────────────
# FIX-GEO-7: name wins when KIID = 'Global'
# Tested via classify_utils.detect_geography_from_name (pipeline.py logic
# uses the same function; the actual _geo_name_wins variable is pipeline-
# internal but the underlying signal is testable here per R-7).
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_geography_china_from_name():
    """detect_geography (alias detect_geography_from_name in pipeline) must
    return a non-Global geography for funds with 'china' in the name.
    FIX-GEO-7 uses this signal to override KIID='Global'."""
    from core.classify_utils import detect_geography
    result = detect_geography("bgf china bond a2 eur acc")
    assert result is not None and result.lower() != "global", (
        f"'BGF CHINA BOND' must detect a specific geography (got {result!r})"
    )


def test_detect_geography_global_still_global():
    """detect_geography must still return 'Global' for funds with 'globl'
    in the name — the FIX-GEO-7 override must NOT affect those."""
    from core.classify_utils import detect_geography
    result = detect_geography("robeco bp globl prem d usd ac")
    assert result == "Global", (
        f"'ROBECO GLOBL PREM' must still detect 'Global' (got {result!r})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-HEDGCCY-1: benchmark "EUR Hedged" no longer triggers Hedging_Policy
# ─────────────────────────────────────────────────────────────────────────────

# Import the pattern list and the helper directly (R-7: no pipeline import)
from core.kiid_parser import ES_HEDGED, _detect_hedging_policy

_ES_HEDGED_CURRENCY_RE = re.compile(
    r"\b(?:eur|usd|gbp|chf|jpy)\s*\(\s*hedged\s*\)", re.IGNORECASE
)


@pytest.mark.parametrize("benchmark_text", [
    # Bloomberg EUR Hedged benchmark (BGF European HY Bond, INVESCO pattern)
    "El fondo toma como referencia el Bloomberg Pan European High Yield "
    "3% Issuer Constrained Index EUR Hedged (el índice).",
    # Bloomberg Corp EUR Hedged (INVESCO PAN EU HI pattern)
    "el Bloomberg Pan European Aggregate Corp EUR Hedged Index "
    "(rendimiento total)",
    # ICE BofA style — EUR not immediately before "Hedged" but "Hedged" in parens
    "The ICE BofA European Currency Non-Financial High Yield "
    "Constrained Index (100% Hedged) is the reference benchmark.",
])
def test_benchmark_eur_hedged_not_detected(benchmark_text):
    """FIX-HEDGCCY-1: benchmark index names containing 'EUR Hedged' (no parens
    or 100% prefix) must NOT trigger Hedging_Policy='Hedged' for ES KIIDs."""
    result = _detect_hedging_policy(benchmark_text, language="ES")
    assert result != "HEDGED", (
        f"Benchmark text must not trigger Hedging='HEDGED': {benchmark_text[:80]!r}"
    )


@pytest.mark.parametrize("class_text,expected", [
    # Genuine ES share class declaration with currency+paren form
    ("Esta clase EUR (hedged) tiene cobertura de divisa.", "HEDGED"),
    # Genuine ES: cobertura de divisa
    ("La clase de acciones está cubierta frente al riesgo de divisa.", "HEDGED"),
    # Genuine ES: cubierto frente a (pattern: \bcubierto\s+(?:en|frente\s+a)\b)
    ("El fondo está cubierto frente a la divisa base.", "HEDGED"),
    # Genuine ES: (hedged) bare form
    ("Clase de acciones (hedged) denominada en EUR.", "HEDGED"),
    # Unhedged
    ("La clase no está cubierta frente al riesgo de divisa.", "UNHEDGED"),
])
def test_genuine_hedge_still_detected(class_text, expected):
    """FIX-HEDGCCY-1 regression: genuine share-class hedging declarations
    must still be detected after restricting the currency+hedged pattern."""
    result = _detect_hedging_policy(class_text, language="ES")
    assert result == expected, (
        f"Expected Hedging={expected!r}, got {result!r} for: {class_text[:80]!r}"
    )


def test_eur_hedged_with_parens_still_detected():
    """FIX-HEDGCCY-1: '(EUR hedged)' WITH parens must still trigger HEDGED."""
    text = "La clase de participaciones EUR (hedged) invierte en bonos europeos."
    result = _detect_hedging_policy(text, language="ES")
    assert result == "HEDGED", (
        f"'EUR (hedged)' with parens must still detect Hedging='HEDGED'; got {result!r}"
    )


def test_bare_eur_hedged_no_parens_suppressed():
    """FIX-HEDGCCY-1: 'EUR Hedged' WITHOUT parens (benchmark style) must NOT
    trigger HEDGED when no other hedge signal is present."""
    text = (
        "El fondo toma como referencia el Bloomberg Pan European High Yield "
        "3% Issuer Constrained Index EUR Hedged para comparación de rentabilidad."
    )
    result = _detect_hedging_policy(text, language="ES")
    assert result != "HEDGED", (
        f"Bare 'EUR Hedged' without parens must not trigger HEDGED; got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-ASSET-CCY-2: extended _HEDGE_TARGET_CURRENCY strips abbreviations
# ─────────────────────────────────────────────────────────────────────────────

from core.classify_utils import detect_asset_currency_from_name, _HEDGE_TARGET_CURRENCY


@pytest.mark.parametrize("name,expected", [
    # Original HDG/HEDGE variants — must still be stripped
    ("FIDELITY GLOBAL BOND EUR HDG A ACC", None),
    ("AB DYNAMIC DIV EUR HEDGED A ACC",    None),
    # FIX-ASSET-CCY-2: new abbreviations
    ("FIDELITY F.INT.BOND A EUR HED",      None),   # HED (truncated)
    ("JPM GLOBAL MACRO (EUR HGD) A",       None),   # HGD (transposed)
    ("SISF STRATEGIC BO.EUR HE.B ACC",     None),   # HE (two-char truncation)
    ("PIMCO INCOME INV EUR H ACC",         None),   # H (standalone bare H)
    ("GS PATRIM BAL SUST EURH ACC",        None),   # EURH (fused, no space)
])
def test_hedge_target_stripped_from_name(name, expected):
    """FIX-ASSET-CCY-2: all hedge abbreviation variants must be stripped before
    asset currency detection, so detect_asset_currency_from_name returns None
    (not EUR) for these share-class-hedged fund names."""
    result = detect_asset_currency_from_name(name)
    assert result == expected, (
        f"detect_asset_currency_from_name({name!r}): expected {expected!r}, got {result!r}"
    )


def test_hedge_target_pattern_direct():
    """FIX-ASSET-CCY-2 unit test: _HEDGE_TARGET_CURRENCY matches all new variants."""
    cases = [
        "EUR HED", "EUR HGD", "EUR HE", "EUR H", "EURH",
        "USD HED", "GBP HGD", "CHF HE",
        # old forms still matched
        "EUR HDG", "EUR HEDGE", "EUR HEDGED",
    ]
    for case in cases:
        assert _HEDGE_TARGET_CURRENCY.search(case), (
            f"_HEDGE_TARGET_CURRENCY must match {case!r}"
        )


def test_hedge_target_no_false_positive():
    """FIX-ASSET-CCY-2 regression: currency-only tokens must NOT be stripped."""
    no_match = ["EUR ACC", "USD INC", "GBP", "EUR", "HIGH YIELD EUR", "EUREKA"]
    for case in no_match:
        assert not _HEDGE_TARGET_CURRENCY.search(case), (
            f"_HEDGE_TARGET_CURRENCY must NOT match {case!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-ASSET-CCY-3: _KIID_MULTI_CCY_CONTINUATION leading-space bug
# ─────────────────────────────────────────────────────────────────────────────

from core.classify_utils import _KIID_MULTI_CCY_CONTINUATION


@pytest.mark.parametrize("tail", [
    " o en otras monedas con cobertura en eur",   # M&G AH — the root bug
    " o en otras divisas",
    " u otras monedas",
    ", otras divisas",
    " o dólares estadounidenses",
    " o euros",
    " o libras esterlinas",
    "(eur) o en otras monedas",                   # with optional currency code
])
def test_multi_ccy_continuation_matches(tail):
    """FIX-ASSET-CCY-3: multi-currency continuation tails must be recognized
    (including tails starting with a single space before 'o/u/y')."""
    assert _KIID_MULTI_CCY_CONTINUATION.search(tail), (
        f"_KIID_MULTI_CCY_CONTINUATION must match tail {tail!r}"
    )


@pytest.mark.parametrize("tail", [
    " y acciones europeas",       # GS PATRIM — equity, not multi-CCY
    " que sean líquidos",
    " con vencimiento",
    "",
])
def test_multi_ccy_continuation_no_false_positive(tail):
    """FIX-ASSET-CCY-3 regression: non-currency continuations must NOT match."""
    assert not _KIID_MULTI_CCY_CONTINUATION.search(tail), (
        f"_KIID_MULTI_CCY_CONTINUATION must NOT match tail {tail!r}"
    )


def test_mg_optimal_income_kiid_extractor_returns_mcy():
    """FIX-ASSET-CCY-3 / BL-ASSET-CCY-MULTI integration: the M&G AH KIID
    pattern is an EXPLICIT multi-currency mandate, so the extractor must
    return the MCY sentinel (not 'EUR', and not None -- None is reserved for
    'no signal at all'). Uses synthetic KIID text reproducing the exact tail."""
    from core.classify_utils import (
        detect_asset_currency_from_kiid_text,
        ASSET_CURRENCY_MULTI,
    )
    # Synthetic objectives text that reproduces the M&G AH scenario:
    # currency verb fires on "euros", then tail is " o en otras monedas"
    # which IS recognized as a multi-currency continuation -> MCY.
    # NB: the objective window for UNKNOWN-format text starts at offset 200
    # (see _get_obj_bounds), so we pad the preamble to push the currency
    # clause past that boundary -- otherwise it falls outside the window.
    preamble = (
        "OBJETIVOS Y POLÍTICA DE INVERSIÓN. El objetivo del subfondo es "
        "generar una combinación de crecimiento del capital y rentas para "
        "el inversor a lo largo de cualquier periodo de cinco años, mediante "
        "la inversión en una cartera diversificada de activos de renta fija. "
    )
    kiid = (
        preamble
        + "Un mínimo del 80% del fondo se invertirá en activos "
        "expresados en euros o en otras monedas con cobertura en eur "
        "para reducir el riesgo de divisas."
    )
    result = detect_asset_currency_from_kiid_text(kiid)
    assert result == ASSET_CURRENCY_MULTI, (
        f"M&G AH pattern: expected MCY (explicit multi-currency mandate), got {result!r}"
    )
