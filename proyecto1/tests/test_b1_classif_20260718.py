# proyecto1/tests/test_b1_classif_20260718.py
# -*- coding: utf-8 -*-
"""
Regression tests for B1 CRITICAL benchmark-conflict fixes (2026-07-18 session).

Covers:
  FIX-B1-TRUEVAL-1     — Spanish "como mínimo X%de la exposición total en
                          renta variable" now fires eq_dominant (not just
                          "en acciones").
  FIX-B1-EN-STOCKPICK-1 — "stock picking" / "stock selection process" added
                           to eq_dominant; prevents "investment grade" in
                           has_bonds from routing English equity funds to
                           _RF_pending.
  FIX-B1-EN-TWOTHIRDS-1 — "invest at least two thirds/X% ... in equity[ies]"
                            regex fires eq_dominant for English equity funds.
  FIX-B1-EN-UPTO-BONDS-1 — "invest up to X% in debt securities/bonds/fixed
                             income" sets _minor_secondary_bond=True so that
                             a capped secondary bond allocation in an English
                             equity fund does not block eq_dominant→RV.
  FIX-B1-COMMODITY-PRIMAR-1 — "principalmente en ... materias primas" fires
                                before FIX-B1-MIXTO-ENUM so that commodity-
                                primary funds classify as Alternativo (not
                                Mixtos) even when secondary equity/FI
                                instruments are mentioned.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

_CORE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from classify_utils import detect_nature_from_kiid

# ── Shared header block (DDF format) ────────────────────────────────────────
# Must be long enough so that the DDF window [500:5000] captures the
# objective section that follows. (~500 chars of realistic DDF boilerplate.)
_DDF_HDR = (
    "Documento de Datos Fundamentales "
    "Finalidad Este documento le proporciona información fundamental "
    "que debe conocer sobre este producto de inversión. No se trata de "
    "material comercial. Es una información exigida por ley para ayudarle "
    "a comprender la naturaleza, los riesgos, los costes y los beneficios "
    "y pérdidas potenciales de este producto y para ayudarle a compararlo "
    "con otros productos. Producto Nombre del producto: TESTFUND "
    "ISIN: XX0000000000 Nombre del productor: TESTGESTORA S.A. "
    "autorizada en España y regulada por CNMV. "
    "Este documento se publicó el 01/01/2026. "
)

# English KIID header (UNKNOWN format → window 200-4500)
_EN_HDR = (
    "This document provides you with key information about this investment "
    "product. It is not marketing material. The information is required by law "
    "to help you understand the nature, risks, costs, potential gains and losses "
    "of this product and to help you compare it with other products. "
    "The Competent Authority is responsible for supervising the manufacturer. "
)


def _kiid(header: str, body: str) -> str:
    return header + body


# ── FIX-B1-TRUEVAL-1 ────────────────────────────────────────────────────────

class TestFIXB1TrueVal:
    """'como mínimo el X%de la exposición total en renta variable' → RV."""

    def test_minimo_en_renta_variable_no_space_after_percent(self):
        """'75%de la exposición total en renta variable' fires eq_dominant."""
        body = (
            "¿Qué es este producto? Tipo: Fondo de Inversión. RENTA VARIABLE INTERNACIONAL. "
            "Objetivos: La gestión toma como referencia la rentabilidad del índice MSCI World. "
            "Se invierte como mínimo el 75%de la exposición total en renta variable en compañías "
            "en crecimiento sin predeterminación por capitalización bursátil. "
            "La parte no expuesta a renta variable se invertirá en activos de renta fija pública."
        )
        result = detect_nature_from_kiid(_kiid(_DDF_HDR, body))
        assert result == "Renta Variable", (
            f"'como mínimo el 75%de la exposición total en renta variable' "
            f"should fire eq_dominant → 'Renta Variable'; got {result!r}"
        )

    def test_al_menos_el_X_en_renta_variable(self):
        """'al menos el 80% en renta variable' fires eq_dominant."""
        body = (
            "¿Qué es este producto? Tipo: Fondo de Inversión. "
            "Objetivos: El fondo invierte al menos el 80% en renta variable global "
            "de empresas de mercados desarrollados. "
            "El resto podrá invertirse en activos de renta fija con alta calificación crediticia."
        )
        result = detect_nature_from_kiid(_kiid(_DDF_HDR, body))
        assert result == "Renta Variable", (
            f"'al menos el 80% en renta variable' should fire eq_dominant; got {result!r}"
        )

    def test_minimo_75_en_acciones_still_works(self):
        """Original 'mínimo X% en acciones' pattern not broken by FIX-B1-TRUEVAL-1."""
        body = (
            "¿Qué es este producto? Tipo: Fondo de Inversión. "
            "Objetivos: El fondo invierte un mínimo del 75% en acciones de emisores "
            "con sede en la Unión Europea. El resto en efectivo e instrumentos a corto plazo."
        )
        result = detect_nature_from_kiid(_kiid(_DDF_HDR, body))
        assert result == "Renta Variable", (
            f"'mínimo del 75% en acciones' should still fire eq_dominant; got {result!r}"
        )


# ── FIX-B1-EN-STOCKPICK-1 ───────────────────────────────────────────────────

class TestFIXB1EnStockpick:
    """'stock picking' in eq_dominant prevents 'investment grade' false-bond signal."""

    def test_stock_picking_eq_dominant(self):
        """An English fund with 'stock picking process' classifies as Renta Variable."""
        body = (
            "Objectives: The fund's investment objective is to deliver long-term capital "
            "appreciation by investing in global equities. The fund implements an active "
            "and discretionary management based on a rigorous stock picking process, "
            "involving direct meetings with company management teams. "
            "At the time of acquisition, eligible securities are deemed investment grade, "
            "i.e. having a minimum rating of BBB-. "
            "The fund reserves the right to invest a maximum of 25% in fixed-income products."
        )
        result = detect_nature_from_kiid(_kiid(_EN_HDR, body))
        assert result == "Renta Variable", (
            f"'stock picking process' + 'investment grade' (quality constraint) "
            f"should classify 'Renta Variable', not let 'investment grade' force "
            f"_RF_pending; got {result!r}"
        )

    def test_stock_selection_process_eq_dominant(self):
        """'stock selection process' also fires eq_dominant."""
        body = (
            "Objectives: The fund invests primarily in global equities using a systematic "
            "stock selection process based on quality and valuation factors. "
            "Eligible securities must meet an investment grade credit quality threshold. "
        )
        result = detect_nature_from_kiid(_kiid(_EN_HDR, body))
        assert result == "Renta Variable", (
            f"'stock selection process' should fire eq_dominant; got {result!r}"
        )


# ── FIX-B1-EN-TWOTHIRDS-1 ───────────────────────────────────────────────────

class TestFIXB1EnTwoThirds:
    """'invest at least two thirds ... in equity' fires eq_dominant."""

    def test_at_least_two_thirds_in_equity(self):
        """Ashoka-pattern: minimum two-thirds equity mandate fires eq_dominant."""
        body = (
            "Objectives: The Fund's investment objective is to achieve long-term capital "
            "appreciation. The Fund will invest at least two thirds of its net assets in "
            "equity and equity related transferable securities and/or other collective "
            "investment schemes which provide exposure to companies domiciled in India. "
            "The Fund may also invest up to 20% in fixed or floating rate government and "
            "corporate investment grade debt securities. "
        )
        result = detect_nature_from_kiid(_kiid(_EN_HDR, body))
        assert result == "Renta Variable", (
            f"'invest at least two thirds ... in equity' should fire eq_dominant; got {result!r}"
        )

    def test_at_least_75_percent_in_equities(self):
        """'invest at least 75% in equities' fires eq_dominant (secondary bonds capped)."""
        body = (
            "Objectives: The Sub-fund invests at least 75% of its net assets in equities "
            "of companies across all market caps. The sub-fund may invest up to 25% in "
            "investment grade bonds for liquidity management. "
        )
        result = detect_nature_from_kiid(_kiid(_EN_HDR, body))
        assert result == "Renta Variable", (
            f"'invest at least 75% in equities' should fire eq_dominant; got {result!r}"
        )


# ── FIX-B1-EN-UPTO-BONDS-1 ──────────────────────────────────────────────────

class TestFIXB1EnUptoBonds:
    """'invest up to X% in debt securities' = secondary bond → _minor_secondary_bond=True."""

    def test_up_to_20_percent_debt_secondary(self):
        """'invest up to 20% in debt securities' is secondary; eq_dominant wins."""
        body = (
            "Objectives: The Fund will invest at least two thirds of its net assets in "
            "equity and equity related transferable securities in companies domiciled in "
            "emerging markets. "
            "The Fund may also invest up to 20% in fixed or floating rate investment "
            "grade debt securities of government and corporate issuers. "
        )
        result = detect_nature_from_kiid(_kiid(_EN_HDR, body))
        assert result == "Renta Variable", (
            f"'up to 20% in debt securities' is secondary; with equity mandate should "
            f"return 'Renta Variable', not let bond_dominant block it; got {result!r}"
        )

    def test_up_to_30_percent_bonds_secondary(self):
        """'invest up to 30% in bonds' is a capped secondary position."""
        body = (
            "Objectives: The Fund invests primarily in global equities based on stock "
            "picking process. The Fund may invest up to 30% in bonds and other fixed income "
            "instruments to preserve capital in adverse market conditions. "
        )
        result = detect_nature_from_kiid(_kiid(_EN_HDR, body))
        assert result == "Renta Variable", (
            f"'up to 30% in bonds' is capped secondary; should not block RV; got {result!r}"
        )


# ── FIX-B1-COMMODITY-PRIMAR-1 ───────────────────────────────────────────────

class TestFIXB1CommodityPrimar:
    """'principalmente en materias primas' → Alternativo before MIXTO-ENUM fires."""

    def test_principalmente_materias_primas(self):
        """Pure commodity mandate with secondary RV/RF → Alternativo (not Mixtos)."""
        body = (
            "¿Qué es este producto? Tipo: Fondo de Inversión. "
            "Objetivos: El Fondo trata de ofrecer un nivel atractivo de rentabilidad total, "
            "invirtiendo principalmente en una amplia gama de materias primas seleccionadas "
            "entre diversos grupos de materias primas de todo el mundo. "
            "El Fondo trata de conseguir esta exposición a través de derivados financieros "
            "relacionados con materias primas. "
            "El Fondo puede realizar inversiones directas en valores de renta variable y "
            "relacionados con la renta variable como acciones ordinarias y preferentes. "
            "El Fondo también invierte en instrumentos de renta fija incluyendo efectivo "
            "e instrumentos asimilables al efectivo para gestionar la garantía de derivados."
        )
        result = detect_nature_from_kiid(_kiid(_DDF_HDR, body))
        assert result == "Alternativo", (
            f"'principalmente en ... materias primas' should classify 'Alternativo' "
            f"even when RV/RF instruments are mentioned as secondary; got {result!r}"
        )

    def test_mixto_enum_not_blocked_for_genuine_multi_asset(self):
        """FIX-B1-MIXTO-ENUM still works: commodity + equity + FI = Mixtos if not 'principalmente'."""
        body = (
            "¿Qué es este producto? Tipo: Fondo de Inversión. "
            "Objetivos: El fondo invierte en una amplia gama de clases de activos incluyendo "
            "instrumentos relacionados con materias primas, renta variable y valores relacionados "
            "con la renta variable, y renta fija. La asignación entre clases de activo es flexible."
        )
        result = detect_nature_from_kiid(_kiid(_DDF_HDR, body))
        assert result == "Mixtos", (
            f"Multi-asset fund with materias primas + RV + RF (no 'principalmente') "
            f"should still return 'Mixtos' via FIX-B1-MIXTO-ENUM; got {result!r}"
        )
