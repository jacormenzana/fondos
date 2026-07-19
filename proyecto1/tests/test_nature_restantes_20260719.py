# -*- coding: utf-8 -*-
"""
Regression tests for FIX-NAT-EN-SHARES-1, FIX-NAT-ES-SECBOND-1, and
FIX-NAT-BLENDED-6040-1 (2026-07-19).

R-7 compliant: no pipeline.py / core.io import.
Tests call detect_nature_from_kiid() directly.
"""

import pytest
from proyecto1.core.classify_utils import detect_nature_from_kiid

# ---------------------------------------------------------------------------
# Window-priming helpers (mirror test_b1_classif_20260718.py pattern)
# Padding ensures the objective text lands within the function's window.
# ---------------------------------------------------------------------------

_EN_HDR = (
    "key information document\n"
    "product: test fund\n"
    "isin: ie00xxxxxxxxxx\n"
    "manufacturer: test asset management\n"
    "objectives\ninvestment objective\nobjective: "
)

_ES_HDR = (
    "documento de datos fundamentales\n"
    "producto: fondo de prueba\n"
    "isin: es0000000000\n"
    "fabricante: gestora de prueba\n"
    "objetivos de inversión\nobjetivo de inversión\n"
    "política de inversión: "
)

_DDF_HDR = (
    "gestor: gestora de prueba\n"
    "isin: es0000000000\n"
    "¿qué es este producto?\n"
    "tipo de producto: fondo de inversión. "
)

_PAD = " " * 600  # keeps header in the first-600-char zone


def _en(objective: str) -> str:
    return _PAD + _EN_HDR + objective


def _es(objective: str) -> str:
    return _PAD + _ES_HDR + objective


def _ddf(tipo: str, objective: str) -> str:
    return _PAD + _DDF_HDR + tipo + "\n" + "objetivos de inversión\n" + objective


# ---------------------------------------------------------------------------
# FIX-NAT-EN-SHARES-1 — "invest(s/ing) primarily in … shares"
# ---------------------------------------------------------------------------

class TestEnPrimarilyInShares:
    """FIX-NAT-EN-SHARES-1: English 'primarily in … shares' primary equity
    declaration must resolve to Renta Variable (not _RF_pending/Mixtos)."""

    def test_investing_primarily_in_listed_shares(self):
        """MAN JPN COREALPHA pattern: 'investing primarily in listed or traded shares'
        with secondary 'may also invest ... debt securities'."""
        text = _en(
            "the fund seeks long term gains by investing primarily in listed or traded "
            "shares (or related instruments) of issuers in japan, or which derive a "
            "substantial part of their revenue from japan. it may also invest in other "
            "asset classes, including debt securities, currencies, deposits and other "
            "funds and in other regions."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable", (
            f"'investing primarily in … shares' + 'may also invest … debt securities' "
            f"must return Renta Variable; got {result!r}"
        )

    def test_invests_primarily_in_shares(self):
        """'invests primarily in shares of companies' — no intermediate qualifier."""
        text = _en(
            "the fund invests primarily in shares of companies worldwide. "
            "it may also invest in bonds and other fixed income instruments "
            "on an ancillary basis."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable"

    def test_invest_primarily_in_shares_no_may_also(self):
        """'invest primarily in shares' without any secondary bond mention."""
        text = _en(
            "the fund will invest primarily in shares and equity-related "
            "securities of companies globally."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable"

    def test_may_also_invest_neutral_without_primarily(self):
        """'may also invest in debt securities' alone (no primary equity signal)
        must NOT flip a non-equity fund to RV — _minor_secondary_bond only helps
        when eq_dominant is True."""
        text = _en(
            "the fund invests primarily in government bonds and investment grade "
            "corporate bonds. it may also invest in debt securities of lower quality."
        )
        result = detect_nature_from_kiid(text)
        # Should NOT return "Renta Variable" — bond fund stays bond path
        assert result != "Renta Variable", (
            f"'may also invest in debt securities' in a bond fund must not produce "
            f"Renta Variable; got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-NAT-ES-SECBOND-1 — "también podrá invertir en bonos / renta fija"
# ---------------------------------------------------------------------------

class TestEsSecundarioBonos:
    """FIX-NAT-ES-SECBOND-1: 'también podrá invertir en bonos' is a Spanish
    secondary bond allowance and must not veto a confirmed equity mandate."""

    def test_tambien_podra_invertir_en_bonos_corporativos(self):
        """ClearBridge pattern: primary equity mandate + 'también podrá invertir en
        bonos corporativos' → Renta Variable."""
        text = _es(
            "el fondo invierte principalmente en valores de renta variable de empresas "
            "estadounidenses de mediana y gran capitalización bursátil. "
            "en menor medida, el fondo podrá invertir en valores de renta variable de "
            "fuera de ee. uu. "
            "el fondo también podrá invertir en bonos corporativos, bonos del estado y "
            "valores a corto plazo."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable", (
            f"Primary equity + 'también podrá invertir en bonos' must give "
            f"Renta Variable; got {result!r}"
        )

    def test_tambien_podra_invertir_en_renta_fija(self):
        """Variant with 'renta fija' instead of 'bonos'."""
        text = _es(
            "el fondo invierte principalmente en acciones de empresas europeas. "
            "el fondo también podrá invertir en renta fija y otros instrumentos "
            "de deuda con fines de gestión de liquidez."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable"

    def test_tambien_podra_en_bond_only_fund_stays_rf(self):
        """A fund without an equity primary mandate is unaffected by the new pattern
        — it must stay on the RF path."""
        text = _es(
            "el fondo invierte principalmente en bonos de alta calidad crediticia "
            "emitidos por estados europeos. "
            "el fondo también podrá invertir en deuda corporativa de grado de inversión."
        )
        result = detect_nature_from_kiid(text)
        assert result != "Renta Variable", (
            f"Bond-only fund must not be pulled to Renta Variable; got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-NAT-BLENDED-6040-1 — CNMV "renta variable mixta" product-type label
# ---------------------------------------------------------------------------

class TestBlenedMixto:
    """FIX-NAT-BLENDED-6040-1: 'renta variable mixta' in the Tipo de producto
    section must resolve to Mixtos even when bond_dominant=True fires from the
    split-mandate policy section."""

    def test_baelo_pattern_renta_variable_mixta(self):
        """GEST BOUTIQUE VI BAELO pattern:
        - 'tipo de producto: renta variable mixta internacional'
        - policy: '30-75% de la exposición total en renta variable y el resto en
          renta fija'
        → must return Mixtos."""
        text = _ddf(
            "renta variable mixta internacional.",
            (
                "la gestión toma como referencia la rentabilidad del índice "
                "60% s&p global dividend aristocrats total return index + "
                "40% bloomberg barclays euro aggregate total return index value. "
                "se invertirá, directa o indirectamente, 30-75% de la exposición "
                "total en renta variable y el resto en renta fija pública/privada "
                "(incluyendo depósitos e instrumentos del mercado monetario). "
                "la exposición a riesgo divisa será de 0-100%. se seleccionarán "
                "principalmente acciones con un historial ininterrumpido de aumento "
                "del dividendo anual durante los últimos 10 años. "
                "la inversión en renta variable de baja capitalización puede influir "
                "negativamente en la liquidez del fondo."
            )
        )
        result = detect_nature_from_kiid(text)
        assert result == "Mixtos", (
            f"'renta variable mixta' DDF label with split mandate must give "
            f"Mixtos; got {result!r}"
        )

    def test_renta_variable_mixta_without_el_resto(self):
        """Simpler 'renta variable mixta' label alone (no bond_dominant trigger)
        must also produce Mixtos."""
        text = _ddf(
            "renta variable mixta.",
            (
                "el fondo busca rentabilidad combinando acciones y bonos de forma "
                "equilibrada, con un objetivo de volatilidad moderada."
            )
        )
        result = detect_nature_from_kiid(text)
        assert result == "Mixtos"

    def test_pure_bond_fund_no_false_positive(self):
        """A pure bond fund with no 'renta variable mixta' label must NOT return
        Mixtos — confirm the guard does not produce false positives.
        NOTE: must NOT include the word 'acciones' at all (even 'no invierte en
        acciones' contains the substring 'invierte en acciones' which fires
        eq_dominant — that is pre-existing correct behaviour, not a bug)."""
        text = _es(
            "el fondo invierte principalmente en bonos gubernamentales de la "
            "zona euro con grado de inversión y vencimientos cortos. "
            "la cartera se centra exclusivamente en deuda soberana con bajo "
            "riesgo de crédito y elevada liquidez."
        )
        result = detect_nature_from_kiid(text)
        assert result != "Mixtos", (
            f"Pure bond fund must not be classified as Mixtos; got {result!r}"
        )
