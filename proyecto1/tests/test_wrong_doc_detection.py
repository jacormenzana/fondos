# proyecto1/tests/test_wrong_doc_detection.py
# -*- coding: utf-8 -*-
"""
Tests unitarios para detect_wrong_kiid_document() (kiid_parser.py, B1 2026-07-11).

Cubre:
  - Estatutos SICAV coordinados → detección con razón
  - Informe anual / estados financieros → detección con razón
  - KIID real (con cabecera) → None (no dispara)
  - KIID real (sin cabecera explícita pero con objetivos + SRRI) → None
  - Texto vacío / None / demasiado corto → None
  - Veto por cabecera KIID presente junto a marcador positivo → None
  - Regression: 'annual report' en texto de referencia/benchmark no dispara
    si el KIID tiene la estructura correcta
"""

from __future__ import annotations
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import pytest
from kiid_parser import detect_wrong_kiid_document


# ─── Textos sintéticos ────────────────────────────────────────────────────────

def _statute_text() -> str:
    """Estatutos SICAV coordinados sin estructura KIID."""
    return (
        "SICAV MULTI-FONDO S.A. — STATUTS COORDONNÉS\n"
        "Article 1. Dénomination et forme. Il existe une société d'investissement "
        "à capital variable (SICAV) sous la forme d'une société anonyme.\n"
        "Compartiment A: Acties Wereld. Compartiment B: Obligaties Euro.\n"
        "Compartiment C: Monetair. Compartiment D: Mixtos Defensivos.\n"
        "Compartiment E: Alternativos. \n"
        "Les statuts ont été publiés au Mémorial, Recueil des Sociétés et Associations.\n"
    )


def _annual_report_text() -> str:
    """Informe anual multi-fondo sin estructura KIID.
    Usa marcadores estructurales fuertes (no solo 'annual report' que aparece
    también en KIIDs reales). El texto NO tiene cabecera KIID ni sección SRRI."""
    return (
        "AUDITED FINANCIAL STATEMENTS\n"
        "For the year ended 31 December 2025\n"
        "Fund Management Ltd.\n"
        "Schedule of investments as at 31 December 2025:\n"
        "Combined statement of net assets — All compartments\n"
        "Independent auditor's report to the shareholders\n"
        "Notes to the financial statements: Note 1 — Accounting policies.\n"
        "Report of the board of directors: The board is pleased to present...\n"
    )


def _genuine_kiid_with_header() -> str:
    """KIID real con cabecera estándar española."""
    return (
        "DATOS FUNDAMENTALES PARA EL INVERSOR\n"
        "Este documento contiene información fundamental sobre este fondo de inversión.\n"
        "No es material de marketing. La información aquí contenida es exigida por ley.\n"
        "OBJETIVOS Y POLÍTICA DE INVERSIÓN\n"
        "El objetivo del fondo es proporcionar una apreciación del capital a largo plazo "
        "invirtiendo principalmente en renta variable europea.\n"
        "Indicador de riesgo: 4 / 7. La categoría 4 refleja un riesgo moderado.\n"
        "GASTOS: Gastos corrientes: 1,50%. Comisión de éxito: ninguna.\n"
    )


def _genuine_kiid_obj_plus_srri() -> str:
    """KIID real sin cabecera explícita pero con objetivos + tabla SRRI."""
    return (
        "Este documento contiene información fundamental para el inversor.\n"
        "OBJECTIVES AND INVESTMENT POLICY\n"
        "The fund aims to provide long-term capital growth by investing in global equities.\n"
        "RISK AND REWARD INDICATOR\n"
        "Typically lower reward — Typically higher reward\n"
        "Lower risk — 1 | 2 | 3 | [4] | 5 | 6 | 7 — Higher risk\n"
        "The indicator is not guaranteed and may change over time.\n"
        "CHARGES: Ongoing charges: 1.20% per year.\n"
    )


def _genuine_kiid_with_annual_mention() -> str:
    """KIID real que menciona marcadores de informe de pasada pero tiene cabecera KIID.
    El veto de cabecera debe evitar la detección aunque el texto contenga 'schedule of
    investments' o 'annual report' en una referencia informativa."""
    return (
        "DATOS FUNDAMENTALES PARA EL INVERSOR\n"
        "OBJETIVOS Y POLÍTICA DE INVERSIÓN\n"
        "El fondo toma como referencia el índice MSCI World. Los inversores pueden "
        "consultar el schedule of investments y combined statement of net assets "
        "en el informe anual disponible gratuitamente en la web del gestor.\n"
        "Indicador de riesgo: 5 / 7\n"
        "GASTOS CORRIENTES: 0,85%\n"
    )


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestStatuteDetection:
    def test_statute_text_detected(self):
        """Estatutos coordinados sin KIID structure → detección positiva."""
        reason = detect_wrong_kiid_document(_statute_text())
        assert reason is not None, "Estatutos SICAV deben ser detectados"
        assert "statuts coordonnés" in reason.lower() or "marcador" in reason.lower()

    def test_statute_reason_contains_marker(self):
        """La razón devuelta debe mencionar el marcador encontrado."""
        reason = detect_wrong_kiid_document(_statute_text())
        assert isinstance(reason, str) and len(reason) > 10


class TestAnnualReportDetection:
    def test_annual_report_detected(self):
        """Informe anual sin KIID structure → detección positiva."""
        reason = detect_wrong_kiid_document(_annual_report_text())
        assert reason is not None, "Informe anual debe ser detectado como wrong doc"

    @pytest.mark.parametrize("snippet", [
        "audited financial statements for the year ended 31 december 2025",
        "schedule of investments as of the reporting date",
        "combined statement of net assets — all compartments",
        "notes to the financial statements note 1 — accounting policies",
        "independent auditor report to the shareholders",
        "report of the board of directors to shareholders",
    ])
    def test_financial_statement_snippets_detected(self, snippet):
        """Cada marcador de informe financiero aislado dispara la detección.
        El texto sintético no tiene cabecera KIID ni tabla SRRI (sin veto)."""
        text = "A" * 200 + "\n" + snippet + "\n" + "B" * 200
        reason = detect_wrong_kiid_document(text)
        assert reason is not None, f"Marcador '{snippet[:50]}' debe detectarse"


class TestGenuineKiidVeto:
    def test_genuine_kiid_with_header_not_detected(self):
        """KIID real con cabecera no debe disparar la detección."""
        reason = detect_wrong_kiid_document(_genuine_kiid_with_header())
        assert reason is None, "KIID con cabecera DFPI no debe ser marcado como wrong doc"

    def test_genuine_kiid_obj_srri_not_detected(self):
        """KIID real sin cabecera explícita pero con objectives+risk no detectado."""
        reason = detect_wrong_kiid_document(_genuine_kiid_obj_plus_srri())
        assert reason is None, "KIID con objectives+risk-indicator no debe ser wrong doc"

    def test_annual_mention_in_genuine_kiid_not_detected(self):
        """'annual report' de pasada en un KIID real (con cabecera) no dispara."""
        reason = detect_wrong_kiid_document(_genuine_kiid_with_annual_mention())
        assert reason is None, (
            "Un KIID real que menciona 'annual report' de pasada no debe ser marcado"
        )


class TestEdgeCases:
    def test_none_returns_none(self):
        assert detect_wrong_kiid_document(None) is None

    def test_empty_string_returns_none(self):
        assert detect_wrong_kiid_document("") is None

    def test_very_short_text_returns_none(self):
        assert detect_wrong_kiid_document("annual report") is None  # < 100 chars

    def test_no_positive_marker_returns_none(self):
        text = (
            "Este fondo invierte en renta variable europea. "
            "El objetivo es proporcionar crecimiento a largo plazo. " * 10
        )
        assert detect_wrong_kiid_document(text) is None

    def test_statute_with_kiid_header_is_vetoed(self):
        """Si un estatuto contiene inesperadamente una cabecera KIID → None."""
        text = (
            "STATUTS COORDONNÉS de la SICAV\n"
            "KEY INVESTOR INFORMATION\n"  # <- header veta la detección
            "OBJECTIVES AND INVESTMENT POLICY\n"
            "This fund invests in European equities.\n"
            "RISK AND REWARD: 3 / 7\n"
        )
        reason = detect_wrong_kiid_document(text)
        assert reason is None, (
            "La presencia de cabecera KIID debe vetar la detección aunque haya marcadores positivos"
        )

    def test_srri_none_does_not_force_detection(self):
        """srri=None es corroborante pero no suficiente: sin marcador positivo → None."""
        text = "El fondo invierte en renta variable. " * 50  # > 100 chars, sin marcadores
        reason = detect_wrong_kiid_document(text, srri=None)
        assert reason is None


class TestAnnualReportWithEmbeddedSRRI:
    """FIX-WRONGDOC-AR (2026-07-13): annual reports that embed per-subfund KIID
    sections (containing SRRI numbers) were NOT flagged because the existing
    KIID-structure veto fired on the embedded SRRI data. The first-page guard
    fixes this: if the document STARTS with the annual report header, it is
    flagged regardless of later KIID-like content."""

    def _annual_report_with_srri(self) -> str:
        """Simula THREADNEEDLE UK SLCT RI: informe anual multi-fondo que embebe
        secciones de KIID individuales (con SRRI 'X / 7') para cada subfondo.
        El documento COMIENZA con la cabecera del informe anual."""
        return (
            "ANNUAL REPORT AND AUDITED FINANCIAL STATEMENTS\n"
            "THREADNEEDLE INVESTMENT FUNDS ICVC\n"
            "MARCH 2019\n"
            "Contents\n"
            "UK Fund.........................................7–15\n"
            "UK Select Fund.................................16–22\n"
            "UK Corporate Bond Fund.........................61–69\n"
            "Sterling Bond Fund.............................70–76\n"
            # Contenido simulado de la sección por subfondo (con datos SRRI)
            "UK Select Fund\n"
            "Investment objective: The fund aims to achieve long-term capital growth "
            "by investing in UK equities.\n"
            "Risk and Reward Indicator: 5 / 7\n"
            "The risk category shown is not guaranteed and may shift over time.\n"
            "Ongoing charges: 0.84% per year.\n"
            "UK Corporate Bond Fund\n"
            "Investment objective: The fund invests primarily in investment-grade bonds.\n"
            "Risk and Reward Indicator: 3 / 7\n"
        )

    def test_annual_report_with_embedded_srri_detected(self):
        """Informe anual multi-fondo con SRRI embebido → detectado vía first-page guard."""
        reason = detect_wrong_kiid_document(self._annual_report_with_srri())
        assert reason is not None, (
            "Informe anual que comienza con 'ANNUAL REPORT AND AUDITED FINANCIAL "
            "STATEMENTS' debe ser detectado aunque contenga SRRI en secciones posteriores"
        )
        assert "annual report and audited financial statements" in reason.lower() or \
               "informe anual" in reason.lower() or "marcador" in reason.lower()

    def test_genuine_kiid_with_annual_mention_in_body_not_affected(self):
        """Un KIID real que menciona 'annual report' en el CUERPO (no en los
        primeros 150 chars) no debe dispararse — la cabecera KIID está al inicio
        y veta la detección; además la primera-página guard no alcanza el cuerpo."""
        # The KIID header is in the very first chars; "ANNUAL REPORT AND AUDITED
        # FINANCIAL STATEMENTS" appears later in the body (well past char 150).
        # This reflects a real KIID that references the annual report for further info.
        kiid_header = "DATOS FUNDAMENTALES PARA EL INVERSOR\n"  # 37 chars
        body = (
            "OBJETIVOS Y POLÍTICA DE INVERSIÓN\n"
            "El fondo trata de superar la rentabilidad del MSCI World. "
            "El fondo puede invertir en acciones de todo el mundo. "
            "Puede obtenerse gratuitamente el informe anual (annual report) "
            "y la información sobre audited financial statements en la web "
            "del gestor.\n"
            "ANNUAL REPORT AND AUDITED FINANCIAL STATEMENTS are available online.\n"
            "Indicador de riesgo: 5 / 7\n"
        )
        text = kiid_header + body
        # Verify "ANNUAL REPORT AND..." appears past char 150
        assert text.lower().index("annual report and audited") > 150, \
            "Test setup: marker must be beyond the 150-char first-page window"
        reason = detect_wrong_kiid_document(text)
        assert reason is None, (
            "Un KIID real con 'annual report' en el cuerpo no debe ser marcado "
            "como wrong doc — la cabecera KIID veta la detección"
        )


class TestLanguageVariants:
    @pytest.mark.parametrize("marker,lang", [
        ("articles of incorporation", "EN"),
        ("coordinated articles", "EN"),
        ("estatutos coordinados", "ES"),
        ("board of directors report", "EN"),
        ("schedule of investments", "EN"),
        ("combined statement of net assets", "EN"),
    ])
    def test_multilingual_markers_detected(self, marker, lang):
        """Marcadores en ES, EN deben ser detectados.
        Nota: 'rapport annuel', 'annual report', 'informe anual' EXCLUIDOS a propósito
        (aparecen en KIIDs reales en la sección 'puede obtenerse gratuitamente')."""
        text = "A" * 200 + "\n" + marker + "\n" + "B" * 200
        reason = detect_wrong_kiid_document(text)
        assert reason is not None, f"Marcador '{marker}' ({lang}) debe detectarse"
