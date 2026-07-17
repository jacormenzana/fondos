# proyecto1/tests/test_b1_classif_20260717.py
# -*- coding: utf-8 -*-
"""
Regression tests for B1 CRITICAL classification fixes (2026-07-17).

FIX-B1-RV-MINIMO-1 (classify_utils.py): "un mínimo del X% en acciones"
  Pattern in eq_dominant was "al menos el X% en acciones" only.
  DWS Invest Focus Europe uses "un mínimo del 75% en acciones" — not matched,
  so eq_dominant=False; "valores de renta fija" (secondary) set bond_dominant=True
  → returned "_RF_pending" instead of "Renta Variable".

FIX-B1-BOND-PRINCIPALMENTE-2 (classify_utils.py): "invierte principalmente[...]en bonos"
  The clause "directa o indirectamente a través de derivados" between
  "principalmente" and "en bonos" broke the exact match "invierte principalmente
  en bonos". Added regex with 120-char gap allowance.

FIX-B1-PORDRINVERTIRSE-1 (classify_utils.py): capped secondary bond allocation
  "podrá invertirse hasta un 25% en valores de renta fija" is a secondary/capped
  bond allowance in an equity fund. Now sets _minor_secondary_bond=True, allowing
  eq_dominant to win and return "Renta Variable".

FIX-B1-REST-RF-1 (classify_utils.py): "el resto en activos de renta fija"
  Explicit mandate that the non-equity fraction goes into fixed income. Added to
  bond_dominant list so mixed funds like RFMI MULTIGESTION FI (max 20% equity,
  rest in bonds) no longer return "Renta Variable" via eq_dominant false-positive.

FIX-B1-MINOREQ-1 (classify_utils.py): symmetric minor-secondary-equity guard
  "en menor medida, el Fondo podrá invertir en valores de renta variable" —
  equity explicitly declared as secondary in a bond-dominant fund. New branch
  returns "_RF_pending" before falling into has_equity+has_bonds → Mixtos.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from classify_utils import detect_nature_from_kiid

# ── Common KIID header (~500 chars) so mandates fall inside the objective window ─
_HDR = (
    "Documento de Datos Fundamentales. FINALIDAD: Este documento le proporciona "
    "información fundamental que debe conocer sobre este producto de inversión. "
    "No se trata de material comercial. Es una información exigida por ley para "
    "ayudarle a comprender la naturaleza, los riesgos, los costes y las pérdidas "
    "y ganancias potenciales de este producto y para ayudarle a compararlo con "
    "otros productos. PRODUCTO: Clase LC con ISIN LU0000000000. El fondo está "
    "autorizado en Luxemburgo. GESTOR: Test Investment S.A. "
    "OBJETIVOS Y POLÍTICA DE INVERSIÓN: "
)


class TestFIXB1RVMinimo:
    """FIX-B1-RV-MINIMO-1: 'un mínimo del X% en acciones' → eq_dominant."""

    def test_un_minimo_del_x_en_acciones_returns_rv(self):
        """'un mínimo del 75% en acciones' is an equity-dominant mandate → RV."""
        kiid = _HDR + (
            "El fondo se gestiona activamente sin índice de referencia. El objetivo "
            "de la política de inversión consiste en obtener una revalorización "
            "superior a la referencia. Para lograr este objetivo, el fondo invierte "
            "un mínimo del 75% en acciones de emisores con sede principal en un "
            "Estado miembro de la UE, en el Reino Unido, en Noruega o en Islandia. "
            "El objetivo son empresas europeas con sólidos fundamentales ESG. "
            "Podrá invertirse hasta un 25% en valores de renta fija, instrumentos "
            "del mercado monetario y saldos bancarios."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Renta Variable", (
            f"'un mínimo del 75% en acciones' with secondary bond cap must return "
            f"Renta Variable, got {result!r}"
        )

    def test_como_minimo_el_x_en_acciones_returns_rv(self):
        """'como mínimo el 80% en acciones' (variant) is also equity-dominant."""
        kiid = _HDR + (
            "El subfondo invierte como mínimo el 80% en acciones de empresas "
            "cotizadas en mercados europeos. La gestión del fondo es activa. "
            "El resto puede invertirse en instrumentos de deuda a corto plazo "
            "para gestión de liquidez."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Renta Variable", (
            f"'como mínimo el 80% en acciones' must return RV, got {result!r}"
        )

    def test_al_menos_el_x_en_acciones_still_works(self):
        """Original 'al menos el X% en acciones' pattern still returns RV."""
        kiid = _HDR + (
            "El producto invertirá en todo momento al menos el 75% en acciones "
            "internacionales de empresas de todos los sectores. El fondo también "
            "podrá invertir hasta el 25% de su patrimonio neto en títulos de "
            "deuda investment grade con fines de gestión de tesorería."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Renta Variable", (
            f"'al menos el 75% en acciones' must still return RV, got {result!r}"
        )

    def test_podra_invertirse_hasta_rf_sets_minor_secondary_bond(self):
        """'podrá invertirse hasta X% en valores de renta fija' after equity mandate
        is a capped secondary bond allocation and must NOT prevent RV return."""
        kiid = _HDR + (
            "Para lograr este objetivo el fondo invierte un mínimo del 70% en "
            "acciones europeas de alta capitalización. Podrá invertirse hasta un "
            "30% en valores de renta fija e instrumentos del mercado monetario."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Renta Variable", (
            f"Equity mandate with capped RF must return RV, got {result!r}"
        )


class TestFIXB1BondPrincipalmente:
    """FIX-B1-BOND-PRINCIPALMENTE-2: 'invierte principalmente[...]en bonos'
    allows up to 120-char intermediate clause (e.g. 'directa o indirectamente
    a través de derivados')."""

    def test_franklin_hy_pattern_returns_rf_pending(self):
        """High yield bond fund with secondary equity allowance must return
        '_RF_pending' (→ Renta Fija Flexible), not Renta Variable."""
        kiid = _HDR + (
            "Objetivo de inversión: generar altos niveles de ingresos. "
            "Política de inversión: El Fondo invierte principalmente, directa o "
            "indirectamente a través de derivados, en bonos del Estado y "
            "corporativos con calificación inferior a investment grade (high yield), "
            "denominados en euros o cubiertos en dicha divisa. "
            "En menor medida, el Fondo podrá invertir en valores de renta variable, "
            "valores en situación de impago (default) y valores convertibles, "
            "incluidos bonos convertibles contingentes."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "_RF_pending", (
            f"HY bond fund with secondary equity allowance must return '_RF_pending', "
            f"got {result!r}"
        )

    def test_bond_mandate_without_intermediate_clause_still_rf(self):
        """Direct 'invierte principalmente en bonos' (no intermediate clause)
        must also return '_RF_pending'."""
        kiid = _HDR + (
            "El Fondo invierte principalmente en bonos corporativos de alta calidad "
            "crediticia (grado de inversión), denominados en euros. El Fondo busca "
            "maximizar la rentabilidad total combinando ingresos y revalorización. "
            "El Fondo puede utilizar instrumentos derivados para gestionar el riesgo."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "_RF_pending", (
            f"Bond-primary fund must return '_RF_pending', got {result!r}"
        )


class TestFIXB1RestRF:
    """FIX-B1-REST-RF-1: 'el resto en activos de renta fija' sets bond_dominant.
    A fund with explicit max equity cap and remaining in bonds must not return RV."""

    def test_max_equity_cap_with_rest_in_rf_not_rv(self):
        """'máximo del 20% en renta variable, el resto en activos de renta fija'
        must not return Renta Variable."""
        kiid = _HDR + (
            "La gestión toma como referencia el índice 74% Renta fija (45% iBoxx "
            "Euro Corp, 29% JPM GBI EMU), Renta Variable 16% (S&P 500, Eurostoxx). "
            "Se invierte un máximo del 20% de la exposición total en Renta Variable "
            "y el resto en activos de Renta Fija Pública/Privada. La inversión en "
            "acciones de baja capitalización y en activos de baja calidad crediticia "
            "puede influir negativamente en la liquidez del fondo."
        )
        result = detect_nature_from_kiid(kiid)
        assert result != "Renta Variable", (
            f"Fund with max 20% equity and rest in RF bonds must not return "
            f"Renta Variable, got {result!r}"
        )

    def test_hy_bond_minor_equity_returns_rf_pending(self):
        """The combined effect of bond-dominant + minor-secondary-equity guard
        must return '_RF_pending' for HY bond fund with minor equity allowance."""
        kiid = _HDR + (
            "El Fondo invierte principalmente, a través de derivados y en forma "
            "directa, en bonos corporativos con calificación inferior a investment "
            "grade denominados en euros. En menor medida, el Fondo podrá invertir "
            "en valores de renta variable cuando las condiciones del mercado lo "
            "hagan aconsejable."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "_RF_pending", (
            f"HY bond fund with minor equity via 'en menor medida podrá' must "
            f"return '_RF_pending', got {result!r}"
        )
