# test_nat_b1_fixes_20260719.py
# R-7: no pipeline.py / core.io imports.
# Covers FIX-GS-EM-DEBT-1 and FIX-JANUS-BALANCED-1.

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from proyecto1.core.classify_utils import detect_nature_from_kiid

# ── helpers ──────────────────────────────────────────────────────────────────

_DDF_HDR = (
    "Documento de Datos Fundamentales\n\n"
    "Finalidad Este documento le proporciona información fundamental.\n\n"
    "Producto Goldman Sachs EM Portfolio, ISIN: LU0133266147\n"
    "Goldman Sachs Asset Management B.V. es el productor.\n"
    "Este Fondo está autorizado en Luxemburgo.\n"
    "Este Documento de datos fundamentales tiene fecha de 01/01/2026.\n\n"
    "¿Qué es este producto?\n"
    "Tipo Goldman Sachs Funds es un OICVM.\n"
    "Plazo La duración de la Cartera es ilimitada.\n\n"
)
# DDF window = chars 500..5000; pad header so objective starts inside window
_DDF_PAD = " " * max(0, 500 - len(_DDF_HDR))


def _ddf(objective_text: str) -> str:
    return _DDF_HDR + _DDF_PAD + "Objetivos " + objective_text


_UCITS_HDR = (
    "Key Investor Information Document\n"
    "Janus Henderson Balanced Fund A2 USD ISIN: IE0004445015\n"
    "Janus Henderson Capital Funds plc.\n\n"
    "Objectives and investment policy\n"
)


def _ucits(objective_text: str) -> str:
    return _UCITS_HDR + objective_text


# ── FIX-GS-EM-DEBT-1: "también podrá invertir en renta fija" in bond-primary ─
# Should NOT set _minor_secondary_bond=True when primary mandate is bonds.
# Before the fix: detect_nature_from_kiid returned "Renta Variable".
# After the fix: should return "Mixtos" (bond-primary + 10% RV cap → not pure RF).

_GS_EM_OBJECTIVE = (
    "La Cartera invertirá principalmente en valores de renta fija "
    "de cualquier tipo de emisor de países emergentes. "
    "La Cartera también podrá invertir en valores de renta fija "
    "cuyo emisor tenga su sede en cualquier parte del mundo. "
    "La Cartera podrá invertir hasta una décima parte de sus activos "
    "en valores de renta variable o vinculados a la renta variable."
)


def test_gs_em_debt_not_renta_variable():
    """Bond-primary fund with minority equity cap must NOT classify as Renta Variable."""
    result = detect_nature_from_kiid(_ddf(_GS_EM_OBJECTIVE))
    assert result != "Renta Variable", (
        f"GS EM DEBT-style fund returned 'Renta Variable' — "
        f"FIX-GS-EM-DEBT-1 broke or regressed (got {result!r})"
    )


def test_gs_em_debt_is_mixtos_or_rf_pending():
    """Bond-primary + 10% equity cap → Mixtos or _RF_pending, never RV."""
    result = detect_nature_from_kiid(_ddf(_GS_EM_OBJECTIVE))
    assert result in ("Mixtos", "_RF_pending"), (
        f"Expected 'Mixtos' or '_RF_pending' for GS EM DEBT style, got {result!r}"
    )


# ── FIX-NAT-ES-SECBOND-1 preserved: "también podrá invertir en bonos" in equity fund ─
# In an equity-primary fund the pattern still fires → _minor_secondary_bond=True → RV.

_RV_WITH_ALSO_BONDS = (
    "El Fondo invierte principalmente en acciones de empresas de todo el mundo. "
    "El Fondo también podrá invertir en bonos corporativos y gubernamentales "
    "como asignación complementaria. "
    "El objetivo es superar el MSCI World Index."
)


def test_equity_fund_tamb_secbond_still_rv():
    """'también podrá invertir en bonos' in equity-primary context still returns RV."""
    result = detect_nature_from_kiid(_ddf(_RV_WITH_ALSO_BONDS))
    assert result == "Renta Variable", (
        f"Equity fund with 'también podrá invertir en bonos' should be 'Renta Variable', "
        f"got {result!r} — FIX-NAT-ES-SECBOND-1 regression"
    )


# ── FIX-JANUS-BALANCED-1: "balanced fund" in KIID header → Mixtos ────────────

_BALANCED_FUND_KIID = (
    "Janus Henderson Balanced Fund A2 USD ISIN: IE0004445015\n"
    "Un subfondo de Janus Henderson Capital Funds plc.\n\n"
    "Documento de Datos Fundamentales\n"
    "Finalidad: Este documento le proporciona información fundamental.\n\n"
    "Objetivos El Fondo busca obtener un rendimiento a partir de una "
    "combinación de crecimiento de capital e ingresos. "
    "El Fondo invierte entre el 35% y el 70% de sus activos en acciones "
    "(renta variable) y entre el 30% y el 65% de sus activos en "
    "valores de renta fija (deuda)."
)


def test_balanced_fund_header_is_mixtos():
    """'balanced fund' in KIID header → Mixtos (FIX-JANUS-BALANCED-1)."""
    result = detect_nature_from_kiid(_BALANCED_FUND_KIID)
    assert result == "Mixtos", (
        f"'balanced fund' in KIID header should return 'Mixtos', got {result!r}"
    )


def test_balanced_fund_in_window_is_mixtos():
    """'balanced fund' inside the objective window also → Mixtos."""
    text = (
        "Datos Fundamentales\n" * 10
        + "Este producto es un balanced fund de renta variable y renta fija. "
        + "Invierte en acciones y bonos globales."
    )
    result = detect_nature_from_kiid(text)
    assert result == "Mixtos", (
        f"'balanced fund' in objective window should return 'Mixtos', got {result!r}"
    )


def test_non_balanced_fund_unaffected():
    """A pure equity fund without 'balanced fund' is still classified as Renta Variable."""
    text = _ddf(
        "El Fondo invierte principalmente en acciones de empresas europeas. "
        "El objetivo es superar el MSCI Europe Index."
    )
    result = detect_nature_from_kiid(text)
    assert result == "Renta Variable", (
        f"Pure equity fund should remain 'Renta Variable', got {result!r}"
    )
