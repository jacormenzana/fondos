# test_fixes_20260725.py
# R-7: no pipeline.py / core.io imports.
# Covers:
#   FIX-MMF-ENUM-NEGATION-1  — bond-enumeration / risk-comparison negation guards
#                               for WEAK MMF include patterns
#   FIX-ALTRV-LSBOND-1       — "posiciones largas y cortas" added to has_ar +
#                               Spanish AR-mandate phrase added to _ar_mandate_explicit

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from proyecto1.core.classify_utils import detect_nature_from_kiid

# ── Window-priming helpers ────────────────────────────────────────────────────
#
# detect_nature_from_kiid uses _get_obj_bounds → _detect_kiid_format:
#   KIID format  → objective window 1200-4500  (triggered by "key investor information")
#   DDF format   → objective window  500-5000  (triggered by "documento de datos fundamentales")
#
# These helpers place objective content inside the correct window.

_KIID_HEADER = "Key Investor Information Document\n"   # triggers KIID format
_DDF_MARKER  = "Documento de datos fundamentales\n"

def _kiid(obj_text: str) -> str:
    """Builds a KIID-format text with objective content at position ~1210."""
    pad = " " * (1210 - len(_KIID_HEADER))
    return _KIID_HEADER + pad + obj_text + "\n" * 200

def _ddf(obj_text: str) -> str:
    """Builds a DDF-format text with objective content at position ~533."""
    pad = " " * 500
    return _DDF_MARKER + pad + obj_text + "\n" * 200


# ═══════════════════════════════════════════════════════════════════════════════
# WS-A: FIX-MMF-ENUM-NEGATION-1
#
# "instrumentos del mercado monetario" / "mercado monetario" are WEAK signals.
# When they appear as the trailing item of a bond/debt enumeration, or in a
# risk comparison, the Monetario branch must NOT fire (even though no STRONG
# self-identifying MMF marker is present).
#
# STRONG marker funds are never affected even if they incidentally match a guard.
# ═══════════════════════════════════════════════════════════════════════════════

# -- 4.1 bond-enumeration suppresses WEAK-only MMF signal ---------------------

def test_bond_enum_with_mm_instruments_not_monetario():
    """CARMIGNAC/EDR/DWS shape: bond-primary mandate lists MM instruments last."""
    text = _kiid(
        "Objetivos El subfondo invierte principalmente en bonos, títulos de deuda "
        "e instrumentos del mercado monetario denominados en euros, emitidos por "
        "gobiernos y empresas de calidad crediticia elevada. "
        "El índice de referencia es el ICE BofA 1-3 Year All Euro Government."
    )
    result = detect_nature_from_kiid(text)
    assert result != "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1: bond-enumeration objective must not classify "
        f"as Monetario; got {result!r}"
    )


def test_deuda_y_mm_instruments_not_monetario():
    """'valores de deuda y los instrumentos del mercado monetario' — EDR shape."""
    text = _kiid(
        "Objetivo: El Producto invertirá al menos el 90% de los valores de deuda "
        "y los instrumentos del mercado monetario o los emitidos por organismos "
        "públicos. El valor de referencia está compuesto en un 50% por el Bloomberg "
        "Euro Aggregate Corporate Total Return Index."
    )
    result = detect_nature_from_kiid(text)
    assert result != "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1 (EDR shape): debe salir de Monetario; got {result!r}"
    )


def test_deuda_publica_e_mm_not_monetario():
    """'deuda pública, bonos corporativos e instrumentos del mercado monetario' — DWS shape."""
    text = _kiid(
        "Objetivos El fondo invierte en deuda pública, bonos corporativos e "
        "instrumentos del mercado monetario de todo el mundo. "
        "Se gestiona activamente con duración corta (short duration credit)."
    )
    result = detect_nature_from_kiid(text)
    assert result != "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1 (DWS shape): deuda pública+MM must not be "
        f"Monetario; got {result!r}"
    )


# -- 4.2 risk-comparison suppresses WEAK-only MMF signal ---------------------

def test_mm_in_risk_comparison_not_monetario():
    """THREADNEEDLE shape: 'perfil de riesgo más alto que los valores del mercado monetario'."""
    text = _kiid(
        "El objetivo del Fondo es obtener un rendimiento positivo para usted a "
        "medio plazo, a pesar de los cambios en las condiciones del mercado. "
        "El Fondo presenta un perfil de riesgo más alto que los valores del "
        "mercado monetario debido a un riesgo de crédito más elevado."
    )
    result = detect_nature_from_kiid(text)
    assert result != "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1 (risk comparison): mercado monetario in risk "
        f"comparison must not be Monetario; got {result!r}"
    )


# -- 4.3 REGRESSION: genuine MMF with STRONG marker is unaffected ------------

def test_genuine_mmf_strong_marker_stays_monetario():
    """Genuine MMF with 'money market fund' (STRONG) → stays Monetario even if
    objective also contains a bond-enumeration sentence."""
    text = _kiid(
        "This fund is a standard money market fund investing in high-quality "
        "short-term debt instruments. The fund invests in bonds, treasury bills "
        "e instrumentos del mercado monetario de alta calidad. "
        "vencimiento medio ponderado del portfolio inferior a 60 días."
    )
    result = detect_nature_from_kiid(text)
    assert result == "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1 regression: STRONG-marker MMF must stay "
        f"Monetario; got {result!r}"
    )


def test_plain_weak_mmf_without_bond_enum_stays_monetario():
    """WEAK-only MMF with no bond-enumeration or risk-comparison → stays Monetario."""
    text = _kiid(
        "El fondo invierte exclusivamente en instrumentos del mercado monetario "
        "de alta calidad a corto plazo, con vencimiento inferior a 397 días. "
        "El objetivo es preservar el capital y proporcionar liquidez diaria."
    )
    result = detect_nature_from_kiid(text)
    assert result == "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1 regression: plain weak-only MMF (no bond enum) "
        f"must stay Monetario; got {result!r}"
    )


def test_strong_mmf_with_risk_comparison_stays_monetario():
    """STRONG-marker MMF whose text also contains a risk-comparison sentence → Monetario."""
    text = _kiid(
        "Este subfondo es un fondo monetario estándar (standard money market fund) "
        "que invierte en instrumentos de deuda a muy corto plazo. "
        "Presenta un perfil de riesgo más bajo que los valores del mercado monetario "
        "tradicionales. El vencimiento medio ponderado es inferior a 60 días."
    )
    result = detect_nature_from_kiid(text)
    assert result == "Monetario", (
        f"FIX-MMF-ENUM-NEGATION-1 regression: STRONG-marker MMF + risk comparison "
        f"must stay Monetario; got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# WS-B: FIX-ALTRV-LSBOND-1
#
# THREADNEEDLE shape: long/short bond fund with positive-return mandate.
#   has_ar    ← "posiciones largas y cortas" (newly added)
#   gate cond ← "rendimiento positivo … a pesar de los cambios en las
#                condiciones del mercado" (newly added to _ar_mandate_explicit)
# Result: detect_nature_from_kiid returns "Alternativo".
# ═══════════════════════════════════════════════════════════════════════════════

_LS_BOND_OBJ = (
    "El objetivo del Fondo es obtener un rendimiento positivo para usted a medio "
    "plazo, a pesar de los cambios en las condiciones del mercado. No se garantiza "
    "un rendimiento positivo y no existe ninguna forma de protección del capital. "
    "El Fondo se gestiona activamente e invierte al menos dos tercios de sus activos "
    "en posiciones largas y cortas en bonos con una calificación de grado de inversión "
    "o inferior emitidos por empresas y Gobiernos de todo el mundo."
)


def test_ls_bond_ar_mandate_returns_alternativo():
    """THREADNEEDLE shape: L/S bond fund with Spanish AR-mandate phrase → Alternativo."""
    text = _kiid(_LS_BOND_OBJ)
    result = detect_nature_from_kiid(text)
    assert result == "Alternativo", (
        f"FIX-ALTRV-LSBOND-1: posiciones largas y cortas + AR mandate must be "
        f"Alternativo; got {result!r}"
    )


def test_ls_bond_without_ls_phrase_not_alternativo():
    """Without 'posiciones largas y cortas', the fund should NOT reach Alternativo
    via this gate alone (drops the L/S signal from the mandate text)."""
    obj_no_ls = (
        "El objetivo del Fondo es obtener un rendimiento positivo a medio plazo, "
        "a pesar de los cambios en las condiciones del mercado. El Fondo invierte "
        "al menos dos tercios de sus activos en bonos con calificación de grado de "
        "inversión o inferior emitidos por empresas y Gobiernos de todo el mundo."
    )
    text = _kiid(obj_no_ls)
    result = detect_nature_from_kiid(text)
    assert result != "Alternativo", (
        f"FIX-ALTRV-LSBOND-1 regression: without L/S phrase, bond-only fund "
        f"must NOT be Alternativo; got {result!r}"
    )


def test_reversed_ls_order_cortas_y_largas_not_alternativo():
    """'posiciones cortas y largas' (L/S equity funds like SCHRODER Egerton) must
    NOT trigger the new has_ar token."""
    obj_reversed = (
        "El objetivo del fondo es proporcionar una rentabilidad positiva durante "
        "un periodo de tres años, mediante la inversión en valores de renta variable. "
        "El fondo abrirá posiciones cortas y largas en valores emitidos por empresas."
    )
    text = _kiid(obj_reversed)
    result = detect_nature_from_kiid(text)
    assert result != "Alternativo", (
        f"FIX-ALTRV-LSBOND-1 guard: reversed 'posiciones cortas y largas' (L/S "
        f"equity) must NOT be Alternativo; got {result!r}"
    )


def test_ls_bond_ddf_format_also_alternativo():
    """Same THREADNEEDLE objective in DDF format → also Alternativo."""
    text = _ddf(_LS_BOND_OBJ)
    result = detect_nature_from_kiid(text)
    assert result == "Alternativo", (
        f"FIX-ALTRV-LSBOND-1: DDF-format L/S bond fund must also be Alternativo; "
        f"got {result!r}"
    )
