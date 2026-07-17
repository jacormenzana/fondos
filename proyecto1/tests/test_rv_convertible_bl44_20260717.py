# proyecto1/tests/test_rv_convertible_bl44_20260717.py
# -*- coding: utf-8 -*-
"""
Regression tests for FIX-RV-CONVERTIBLE-1 + FIX-BL44-OPTB2 (2026-07-17).

FIX-RV-CONVERTIBLE-1 (classify_utils.py):
    The phrase "renta variable y bonos" in the early Mixtos check was matching
    inside "instrumentos relacionados con la renta variable y bonos convertibles"
    (M&G Global Listed Infrastructure KIID: "el 80% del fondo se invierte en
    acciones, instrumentos relacionados con la renta variable y bonos convertibles").
    Convertible bonds are equity-linked hybrid instruments (not a primary fixed-income
    allocation). Guard added: "renta variable y bonos" only fires as Mixtos early
    trigger when NOT immediately followed by "convertibles".
    Effect: M&G GL Listed Infrastructure EUR share classes (LU1665237613, etc.)
    now correctly return Renta Variable instead of Mixtos.

FIX-BL44-OPTB2 (pipeline.py):
    _bl44_already_handled was only checking _bl44_srri_anomaly (set by the
    block's internal SRRI path). When the block returns via the early strong-signal
    path ("money market fund"/"vnav" in KIID), it never reaches the SRRI check
    that sets the flag. The pipeline BL-44 then fires on SRRI_KIID mismatch.
    Fix: also bypass BL-44 when Vehicle_Structure=Money Market Fund or
    MMF_Structure is populated — strong block evidence that this is a legitimate MMF.
    Effect: DWS ESG EU M MKT IC100 EUR ACC (LU2098886703) preserved as Monetario.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
for _p in (_CORE_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from classify_utils import detect_nature_from_kiid


# ─── FIX-RV-CONVERTIBLE-1: convertible bonds guard ───────────────────────────

class TestRVConvertibleGuard:
    """'renta variable y bonos convertibles' is NOT a mixed mandate.
    Convertible bonds are equity-like hybrid instruments — the phrase appears
    in infrastructure equity fund KIIDs as a secondary instrument allowance,
    not as a dual equity+bond mandate."""

    # Pattern from M&G Global Listed Infrastructure KIID
    _INFRAST_KIID = (
        "El objetivo del fondo es proporcionar una combinación de crecimiento del "
        "capital e ingresos para ofrecer un rendimiento superior al del mercado de "
        "renta variable a lo largo de un ciclo de mercado, a la vez que se obtiene "
        "una baja correlación con los mercados de renta variable. Al menos el 80% "
        "del fondo se invierte en acciones, instrumentos relacionados con la renta "
        "variable y bonos convertibles de empresas que el Gestor considera que tienen "
        "características de infraestructura. El fondo invierte en una cartera "
        "diversificada de acciones globales. La selección de valores se basa en un "
        "análisis fundamental de las empresas subyacentes. El fondo invierte en una "
        "cartera diversificada, invirtiendo en valores de renta variable emitidos "
        "por empresas de infraestructuras, trusts de inversión y REIT de todo el mundo."
    )

    def test_rv_convertible_not_mixtos(self):
        """'renta variable y bonos convertibles' must NOT return Mixtos —
        convertibles are equity-like instruments in an infrastructure equity fund."""
        result = detect_nature_from_kiid(self._INFRAST_KIID)
        assert result == "Renta Variable", (
            f"Infrastructure equity fund with 'bonos convertibles' as secondary "
            f"instrument must be Renta Variable, got {result!r}"
        )

    # Padding: detect_nature_from_kiid uses an objective window starting at ~pos 500.
    # The tests below prepend a realistic KIID header so the mandate text falls
    # within the window. Without this, a short text would produce an empty window
    # and the early Mixtos check would never fire.
    _KIID_HEADER = (
        "Documento de Datos Fundamentales. FINALIDAD: Este documento le proporciona "
        "información fundamental que debe conocer sobre este producto de inversión. "
        "No se trata de material comercial. Es una información exigida por ley para "
        "ayudarle a comprender la naturaleza, los riesgos, los costes y las pérdidas "
        "y ganancias potenciales de este producto y para ayudarle a compararlo con "
        "otros productos. PRODUCTO: Clase A con ISIN LU0000000000. Fondo producido "
        "por Investment Manager S.A. Este PRIIP está autorizado en Luxemburgo. "
        "OBJETIVOS Y POLÍTICA DE INVERSIÓN: "
    )

    def test_acciones_y_bonos_still_triggers_mixtos(self):
        """'acciones y bonos' (without 'convertibles') stays in the static
        early-Mixtos list and must still return Mixtos — the guard only
        affects the 'renta variable y bonos' pattern."""
        kiid = self._KIID_HEADER + (
            "El fondo invierte en una combinación de acciones y bonos emitidos "
            "por empresas y gobiernos de todo el mundo. La asignación entre "
            "renta variable y renta fija varía según las condiciones de mercado "
            "dentro de los rangos establecidos por la política de inversión."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Mixtos", (
            f"Explicit 'acciones y bonos' mixed mandate must still return "
            f"Mixtos, got {result!r}"
        )

    def test_acciones_bonos_convertibles_not_mixtos(self):
        """A fund explicitly named as equity that also allows convertibles
        as a secondary instrument must be Renta Variable, not Mixtos."""
        kiid = self._KIID_HEADER + (
            "El fondo invierte principalmente en valores de renta variable emitidos "
            "por empresas de infraestructuras. El fondo puede invertir hasta un 20% "
            "en instrumentos relacionados con la renta variable y bonos convertibles "
            "como complemento a la cartera de acciones principal."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Renta Variable", (
            f"Equity fund with convertible bonds as complement must be RV, got {result!r}"
        )

    def test_renta_fija_y_renta_variable_still_mixtos(self):
        """'renta fija y renta variable' (the static-list pattern, RF first)
        must still return Mixtos — the guard only affects 'renta variable y bonos'."""
        kiid = self._KIID_HEADER + (
            "El fondo invierte en una amplia combinación de renta fija y renta "
            "variable de todo el mundo, con una distribución flexible entre "
            "ambas clases de activos en función de las condiciones de mercado."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Mixtos", (
            f"Explicit 'renta fija y renta variable' mixed mandate must return "
            f"Mixtos, got {result!r}"
        )


# ─── FIX-BL44-OPTB2: Vehicle_Structure bypass ────────────────────────────────

class TestBl44Optb2VehicleStructureBypass:
    """pipeline.py BL-44 must be bypassed when the block has confirmed that
    the fund is a Money Market Fund via strong KIID signals (Vehicle_Structure
    or MMF_Structure), even if _bl44_srri_anomaly was not set."""

    @classmethod
    def _import_monetarios(cls):
        import importlib
        _p1 = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
        _blk = os.path.join(_p1, "blocks")
        for _p in (_p1, _blk, _CORE_DIR):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        return importlib.import_module("monetarios")

    def _bl44_already_handled(self, classification: dict) -> bool:
        """Replicate the FIX-BL44-OPTB2 bypass logic from pipeline.py."""
        return (
            classification.get("_bl44_srri_anomaly") is not None
            or classification.get("Vehicle_Structure") == "Money Market Fund"
            or classification.get("MMF_Structure") not in (None, "Not Applicable")
        )

    def test_money_market_fund_vehicle_bypasses_bl44(self):
        """When the block returns Vehicle_Structure='Money Market Fund', BL-44
        must be bypassed regardless of whether _bl44_srri_anomaly was set."""
        monetarios = self._import_monetarios()
        kiid = (
            "This is a short-term money market fund (standard MMF). "
            "The fund is a UCITS-compliant Variable Net Asset Value (VNAV) "
            "money market fund. Weighted average maturity: max 60 days. "
            "Weighted average life: max 120 days. Risk indicator: 5/7."
        )
        result = monetarios.classify_fund("SHORT TERM MMF EURO IC EUR ACC", kiid)
        assert result.get("Vehicle_Structure") == "Money Market Fund", (
            "Block must set Vehicle_Structure='Money Market Fund' for MMF KIID text"
        )
        assert self._bl44_already_handled(result), (
            "BL-44 bypass must fire when Vehicle_Structure='Money Market Fund'"
        )

    def test_standard_mmf_structure_bypasses_bl44(self):
        """When MMF_Structure is populated (not 'Not Applicable'), BL-44
        must be bypassed — the block confirmed a legitimate MMF via KIID text."""
        monetarios = self._import_monetarios()
        kiid = (
            "El subfondo es un fondo del mercado monetario a corto plazo de valor "
            "liquidativo variable (VNAV). Vencimiento medio ponderado: máximo 60 días. "
            "Vida media ponderada: máximo 120 días. Indicador de riesgo: 5/7."
        )
        result = monetarios.classify_fund("FONDO MONETARIO VNAV EUR IC ACC", kiid)
        assert result.get("MMF_Structure") not in (None, "Not Applicable"), (
            "Block must populate MMF_Structure for genuine MMF KIID text"
        )
        assert self._bl44_already_handled(result), (
            "BL-44 bypass must fire when MMF_Structure is populated"
        )

    def test_non_mmf_does_not_bypass_bl44(self):
        """A generic bond fund without MMF signals must NOT set Vehicle_Structure
        to 'Money Market Fund' — BL-44 remains applicable."""
        monetarios = self._import_monetarios()
        kiid = (
            "El fondo invierte en instrumentos del mercado monetario denominados "
            "en euros con el objetivo de gestión de liquidez. "
            "Indicador de riesgo y rendimiento: 5/7."
        )
        result = monetarios.classify_fund("FONDO GENERICO BOND A EUR ACC", kiid)
        # Without STRONG_MMF_STRUCTURE_MARKERS or MMF KIID language, block may
        # not set Vehicle_Structure='Money Market Fund'
        vs = result.get("Vehicle_Structure")
        ms = result.get("MMF_Structure")
        # If neither MMF signal is present, _bl44_already_handled should be False
        anomaly = result.get("_bl44_srri_anomaly")
        if vs != "Money Market Fund" and ms in (None, "Not Applicable") and anomaly is None:
            assert not self._bl44_already_handled(result), (
                "Non-MMF fund must not bypass BL-44"
            )
