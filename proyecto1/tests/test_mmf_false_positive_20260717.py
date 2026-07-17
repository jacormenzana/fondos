# proyecto1/tests/test_mmf_false_positive_20260717.py
# -*- coding: utf-8 -*-
"""
Regression tests for FIX-P1-MMF8 + FIX-P1-MMF9 (2026-07-17).

FIX-P1-MMF8:
    "se mantendrá en depósitos en entidades de crédito e instrumentos del
    mercado monetario" is a DEFENSIVE CASH management clause common in macro /
    absolute-return KIIDs — it describes a liquidity reserve, not a money-market
    mandate.  The existing include-patterns for Monetario would match
    "instrumentos del mercado monetario" and falsely return 'Monetario' for
    JPM Global Macro Opportunities funds (LU0917670407/LU0917670829).
    Fix: add _defensive_cash_clause exclusion.

FIX-P1-MMF9:
    _permissive_secondary_mmf used [^.]{0,250} (up to 250 non-period chars)
    which breaks on abbreviations like "EE. UU." (Estados Unidos) — the period
    in "EE." stops the match before "mercado monetario" is reached.  This left
    JANUS.H. US FORTY funds (US equity) falsely detected as Monetario.
    Fix: change to [\\s\\S]{0,300} to ignore periods inside abbreviations.

FIX-BL44-OPTB:
    pipeline.py BL-44 INTER rule (BL44_NATURE_SRRI_R4) must NOT override to
    Restantes when monetarios.classify_fund() already identified the fund as a
    legitimate MMF with anomalous SRRI (via STRONG_MMF_STRUCTURE_MARKERS) and
    set the _bl44_srri_anomaly flag.  Confirmed regression: DWS ESG EU M MKT
    IC100 EUR ACC (LU2098886703) contains "eu m mkt" → STRONG_MMF_STRUCTURE_MARKERS
    match → _bl44_srri_anomaly set → BL-44 must skip the Restantes override.
    Fix: add `and classification.get("_bl44_srri_anomaly") is None` to the
    Monetario reclassification condition in pipeline.py.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
for _p in (_CORE_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from classify_utils import detect_nature_from_kiid


# ─── FIX-P1-MMF8: defensive cash management clause ────────────────────────

class TestDefensiveCashClause:
    """'se mantendrá en depósitos ... instrumentos del mercado monetario'
    is a liquidity-reserve clause, not a money-market fund declaration."""

    # Actual KIID text from JPM Global Macro Opportunities
    _JPM_MACRO_TEXT = (
        "hasta un 100% del patrimonio se mantendrá en depósitos en entidades "
        "de crédito e instrumentos del mercado monetario y hasta un 10% del "
        "patrimonio en fondos del mercado monetario con fines de inversión y "
        "defensivos, con el objeto de gestionar las suscripciones y los "
        "reembolsos en efectivo, así como para atender pagos corrientes y "
        "excepcionales."
    )

    def test_jpm_macro_defensive_clause_not_monetario(self):
        """'se mantendrá en depósitos … instrumentos del mercado monetario'
        in a macro/absolute-return KIID must NOT return Monetario."""
        result = detect_nature_from_kiid(self._JPM_MACRO_TEXT)
        assert result != "Monetario", (
            "Defensive cash management clause must not trigger Monetario detection"
        )

    def test_defensive_clause_generic_form(self):
        """Generic form 'se mantendrá en depósitos en entidades de crédito e
        instrumentos del mercado monetario' is a reserve clause, not MMF mandate."""
        kiid = (
            "Hasta un 80% del patrimonio se mantendrá en depósitos en entidades "
            "de crédito e instrumentos del mercado monetario para cubrir la "
            "gestión de liquidez intradiaria."
        )
        result = detect_nature_from_kiid(kiid)
        assert result != "Monetario", (
            "Generic defensive cash clause must not be read as Monetario mandate"
        )

    def test_genuine_mmf_declaration_still_detected(self):
        """Genuine MMF ('es un fondo del mercado monetario') must still return Monetario."""
        kiid = (
            "El subfondo es un fondo del mercado monetario a corto plazo de "
            "valor liquidativo variable (VNAV). Invierte principalmente en "
            "instrumentos del mercado monetario de alta calidad crediticia."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Monetario", (
            "A genuine 'fondo del mercado monetario' must still be detected"
        )


# ─── FIX-P1-MMF9: permissive secondary MMF with ee. uu. abbreviation ─────

class TestPermissiveSecondaryAbbreviation:
    """'podrá invertir ... instrumentos del mercado monetario' must be detected
    as a secondary/permissive allocation even when the text contains the
    abbreviation 'EE. UU.' (Estados Unidos) between 'podrá' and 'mercado
    monetario'."""

    # Actual KIID text from JANUS.H. US FORTY (simplified)
    _JANUS_FORTY_TEXT = (
        "el subfondo podrá invertir en otros activos, incluidas las sociedades "
        "fuera de ee. uu., el efectivo y los instrumentos del mercado monetario. "
        "el asesor delegado de inversiones selecciona alrededor de cuarenta "
        "valores de renta variable de alta convicción emitidos principalmente "
        "por empresas con sede o cotización en ee. uu."
    )

    def test_us_equity_fund_with_ee_uu_not_monetario(self):
        """JANUS.H. US FORTY type: 'podrá invertir ... ee. uu. ... mercado
        monetario' contains periods in the abbreviation.  Must not be Monetario."""
        result = detect_nature_from_kiid(self._JANUS_FORTY_TEXT)
        assert result != "Monetario", (
            "Period in 'EE. UU.' must not block the permissive-secondary-MMF guard"
        )

    def test_permissive_secondary_generic_no_abbreviation(self):
        """Standard form without abbreviation: 'podrá invertir … mercado
        monetario' (already covered by FIX-P1-MMF5) must still be blocked."""
        kiid = (
            "el fondo invierte principalmente en bonos de alta calidad. "
            "podrá invertir hasta un 10% en instrumentos del mercado monetario "
            "para gestionar la liquidez de la cartera."
        )
        result = detect_nature_from_kiid(kiid)
        assert result != "Monetario", (
            "Permissive secondary MMF allocation must not trigger Monetario"
        )

    def test_permissive_secondary_with_period_mid_sentence(self):
        """Period inside the span (from abbreviation) must not break the guard."""
        kiid = (
            "el subfondo podrá invertir en valores emitidos en ee. uu. o en "
            "instrumentos del mercado monetario con carácter auxiliar."
        )
        result = detect_nature_from_kiid(kiid)
        assert result != "Monetario", (
            "Period in 'EE. UU.' mid-span must not block permissive-secondary guard"
        )

    def test_genuine_mmf_not_blocked(self):
        """A genuine MMF with no 'podrá/puede' qualifier must still return Monetario."""
        kiid = (
            "el subfondo invierte en instrumentos del mercado monetario denominados "
            "en euros con vencimiento inferior a 6 meses. vencimiento medio ponderado "
            "de la cartera: no superior a 60 días."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Monetario", (
            "Genuine MMF without permissive qualifier must still be detected as Monetario"
        )


# ─── FIX-BL44-OPTB: _bl44_srri_anomaly flag bypass ───────────────────────

class TestBl44SrriAnomalyFlag:
    """monetarios.classify_fund() sets _bl44_srri_anomaly when it detects a
    legitimate MMF (via STRONG_MMF_STRUCTURE_MARKERS) with anomalous SRRI.
    pipeline.py BL-44 must NOT override to Restantes in this case."""

    @classmethod
    def _import_monetarios(cls):
        import sys, os
        # monetarios.py uses 'from core.classify_utils import ...', so proyecto1/
        # must be in sys.path for 'core' to resolve.
        _p1 = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
        _blk = os.path.join(_p1, "blocks")
        for _p in (_p1, _blk, _CORE_DIR):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        import importlib
        try:
            return importlib.import_module("monetarios")
        except ModuleNotFoundError:
            # already imported under blocks namespace
            import monetarios as _m
            return _m

    def test_bl44_srri_anomaly_flag_is_set_for_eu_m_mkt(self):
        """For DWS ESG EU M MKT type funds, monetarios.classify_fund() must
        set _bl44_srri_anomaly when name contains a STRONG_MMF marker and
        KIID SRRI >= 3.  The function reads SRRI from the text (regex '5/7'),
        not from a parameter."""
        monetarios = self._import_monetarios()

        # KIID text with anomalous SRRI signal "5/7" — the block reads SRRI from text.
        kiid = (
            "el subfondo es un fondo del mercado monetario a corto plazo de valor "
            "liquidativo variable (VNAV). vencimiento medio ponderado: máximo 60 días. "
            "vida media ponderada: máximo 120 días. indicador de riesgo y rendimiento 5/7."
        )
        result = monetarios.classify_fund("DWS ESG EU M MKT IC100 EUR ACC", kiid)
        assert "_bl44_srri_anomaly" in result, (
            "Fund with STRONG_MMF_STRUCTURE_MARKER 'eu m mkt' and SRRI=5 must "
            "set _bl44_srri_anomaly flag so BL-44 pipeline rule is skipped"
        )
        assert result.get("Fund_Nature") == "Monetario", (
            "Fund_Nature must remain 'Monetario' when _bl44_srri_anomaly is set"
        )

    def test_no_strong_marker_still_triggers_bl44(self):
        """A generic bond fund without STRONG_MMF_STRUCTURE_MARKERS must NOT
        set _bl44_srri_anomaly — BL-44 remains in effect for them."""
        monetarios = self._import_monetarios()

        # KIID text with SRRI=5 and no STRONG_MMF_STRUCTURE_MARKER in the name.
        kiid = (
            "el fondo invierte en instrumentos del mercado monetario denominados en euros. "
            "indicador de riesgo y rendimiento 5/7."
        )
        result = monetarios.classify_fund("FONDO GENERICO BOND A EUR ACC", kiid)
        # Without a STRONG_MMF_STRUCTURE_MARKER, the block reclassifies to Restantes
        # (BL-44 within the block fires) — _bl44_srri_anomaly is NOT set.
        assert "_bl44_srri_anomaly" not in result or result.get("_bl44_srri_anomaly") is None
