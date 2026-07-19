# -*- coding: utf-8 -*-
"""
Regression tests for FIX-GEO-ASHARES-1 and FIX-GEO-EM-2 (2026-07-19).

R-7 compliant: no pipeline.py / core.io import.
Tests call detect_geography_from_kiid() directly.
"""

import pytest
from proyecto1.core.classify_utils import detect_geography_from_kiid

# ---------------------------------------------------------------------------
# Window-priming helpers (mirror test_b1_classif_20260718.py pattern)
# A real DDF/PRIIPS objective window always has a header so the function's
# _get_obj_bounds sees the correct window. We prime with a neutral header.
# ---------------------------------------------------------------------------

_EN_HDR = (
    "key information document\n"
    "product: test fund\n"
    "isin: ie00xxxxxxxxxx\n"
    "manufacturer: test asset management\n"
    "objectives\n"
    "investment objective\n"
    "objective: "
)

_ES_HDR = (
    "documento de datos fundamentales\n"
    "producto: fondo de prueba\n"
    "isin: es0000000000\n"
    "gestor: gestora de prueba\n"
    "objetivos de inversión\n"
    "objetivo: "
)

_HDR_PAD = " " * 600  # ensures header is in the first 600 chars (pre-window)


def _make_kiid(header: str, objective: str) -> str:
    return _HDR_PAD + header + objective


# ---------------------------------------------------------------------------
# FIX-GEO-ASHARES-1 — A-Shares permissive sleeve must NOT assign China
# ---------------------------------------------------------------------------

class TestASharesGuard:
    """FIX-GEO-ASHARES-1: 'a-shares' in a minority-sleeve context must not
    resolve geography to China."""

    def test_up_to_10pct_ashares_not_china(self):
        """'invest up to 10% in China A-Shares' = minority sleeve → not China."""
        text = _make_kiid(_EN_HDR, (
            "the fund invests globally in equities of large-cap companies. "
            "the fund may invest up to 10% of its assets in china a-shares "
            "via the stock connect programme."
        ))
        result = detect_geography_from_kiid(text)
        assert result != "China", (
            f"A-Shares permissive sleeve must not assign China; got {result!r}"
        )

    def test_up_to_20pct_ashares_not_china(self):
        """MS GLBL BRAND / MS GLBL OPPORTUNITY pattern: 'up to 20% in A-Shares'."""
        text = _make_kiid(_EN_HDR, (
            "the fund seeks to invest in global brands and high quality companies. "
            "the fund may allocate up to 20% of its net assets in china a-shares "
            "through the stock connect mechanism."
        ))
        result = detect_geography_from_kiid(text)
        assert result != "China"

    def test_genuine_china_ashares_returns_china(self):
        """Primary China A-Shares mandate (no percentage cap) must still fire China."""
        text = _make_kiid(_EN_HDR, (
            "the fund invests primarily in a-shares listed on the shanghai "
            "and shenzhen stock exchanges. the portfolio focuses on chinese "
            "domestic equity markets."
        ))
        result = detect_geography_from_kiid(text)
        assert result == "China", (
            f"Primary A-Shares mandate must return China; got {result!r}"
        )

    def test_gran_china_still_china(self):
        """'gran china' (not guarded) still resolves to China."""
        text = _make_kiid(_ES_HDR, (
            "el fondo invierte principalmente en acciones de empresas del área "
            "de gran china, incluyendo china continental, hong kong y taiwán."
        ))
        result = detect_geography_from_kiid(text)
        assert result == "China"


# ---------------------------------------------------------------------------
# FIX-GEO-EM-2 — 'países emergentes' in enumeration/permissive context
# ---------------------------------------------------------------------------

class TestEmergentesGuard:
    """FIX-GEO-EM-2: 'países emergentes' after a new risk/enumeration prefix
    must not assign Geography=Emergentes."""

    def test_podra_invertir_en_not_emergentes(self):
        """'podrá invertir en ... países emergentes' = permissive investment
        clause, not a primary EM mandate."""
        text = _make_kiid(_ES_HDR, (
            "el fondo invierte principalmente en valores de renta fija europea "
            "de alta calidad crediticia. el fondo también podrá invertir en "
            "países emergentes de manera complementaria."
        ))
        result = detect_geography_from_kiid(text)
        assert result != "Emergentes", (
            f"'podrá invertir en países emergentes' must not assign Emergentes; "
            f"got {result!r}"
        )

    def test_incluidos_paises_emergentes_not_emergentes(self):
        """'incluidos los países emergentes' = trailing inclusion clause."""
        text = _make_kiid(_ES_HDR, (
            "el fondo invierte a nivel mundial en bonos de deuda soberana "
            "de distintos estados, incluidos los países emergentes, "
            "con foco en países de la zona euro."
        ))
        result = detect_geography_from_kiid(text)
        assert result != "Emergentes"

    def test_ocde_o_paises_emergentes_not_emergentes(self):
        """'países de la OCDE o de países emergentes' = multi-region enumeration."""
        text = _make_kiid(_ES_HDR, (
            "el fondo puede invertir en valores emitidos en países de la ocde o de "
            "países emergentes, con especial foco en la zona euro y estados unidos."
        ))
        result = detect_geography_from_kiid(text)
        assert result != "Emergentes"

    def test_puede_invertir_en_not_emergentes(self):
        """'puede invertir en ... países emergentes'."""
        text = _make_kiid(_ES_HDR, (
            "el fondo invierte principalmente en acciones europeas. "
            "puede invertir en países emergentes hasta un 10% del patrimonio."
        ))
        result = detect_geography_from_kiid(text)
        assert result != "Emergentes"

    def test_genuine_em_primary_mandate_fires(self):
        """'invierte principalmente en mercados emergentes' is a real EM signal
        and must NOT be suppressed.
        NOTE: text must not contain 'latinoamérica' since that signal fires
        earlier in the _GEO_OBJ_PATTERNS list (correct product behavior)."""
        text = _make_kiid(_ES_HDR, (
            "el fondo invierte principalmente en mercados emergentes, "
            "buscando oportunidades de crecimiento en asia, oriente medio "
            "y áfrica subsahariana."
        ))
        result = detect_geography_from_kiid(text)
        assert result == "Emergentes", (
            f"Primary EM mandate must return Emergentes; got {result!r}"
        )

    def test_predominantemente_em_not_suppressed(self):
        """'predominantemente en países emergentes' = genuine primary EM signal;
        must NOT be guarded (even when fund name says Europe — that's a B2 issue,
        not a classification bug)."""
        text = _make_kiid(_ES_HDR, (
            "el fondo invierte predominantemente en países emergentes europeos "
            "y de asia central, principalmente en bonos de deuda soberana "
            "emitidos por estados con calificación investment grade."
        ))
        result = detect_geography_from_kiid(text)
        assert result == "Emergentes", (
            f"'predominantemente en países emergentes' must still return Emergentes; "
            f"got {result!r}"
        )
