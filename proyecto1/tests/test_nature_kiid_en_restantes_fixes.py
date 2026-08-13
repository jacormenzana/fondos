# -*- coding: utf-8 -*-
"""
Regression tests for four Restantes-fallback root causes fixed 2026-08-13.

  FIX-RV-EN-PRIMARILY-EQUITIES-1  — "primarily/mainly in [X] equities"
  FIX-MIXTOS-ACCIONES-BONOS-CSV-1 — "acciones, bonos" Spanish CSV multi-asset
  FIX-MIXTOS-EN-EQUITIES-BONDS-1  — "equities, bonds" English CSV multi-asset
  FIX-RV-ARTIFICIAL-INTELLIGENCE-1— AI thematic equity fund via KIID header

R-7 compliant: no pipeline.py / core.io import.
All tests call detect_nature_from_kiid() directly.
"""

import pytest
from proyecto1.core.classify_utils import detect_nature_from_kiid

# ---------------------------------------------------------------------------
# Helpers — window-framing boilerplate (mirror existing test conventions)
# ---------------------------------------------------------------------------

_PAD = " " * 600  # keeps a short header in the first-600-char zone

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


def _en(objective: str) -> str:
    """Wrap objective in a typical English PRIIPs/UCITS header."""
    return _PAD + _EN_HDR + objective


def _es(objective: str) -> str:
    """Wrap objective in a typical Spanish PRIIPs/DDF header."""
    return _PAD + _ES_HDR + objective


def _ai_header(objective: str) -> str:
    """KIID with 'Artificial Intelligence Fund' in the first 600 chars
    (product name section, as Polar Capital DDF format places it)."""
    hdr = (
        "Nombre del fondo: Artificial Intelligence Fund\n"
        "Nombre de la Clase de Acciones: Acciones de clase T EUR\n"
        "Nombre del productor: FundRock Management Company (Ireland) Limited\n"
        "Datos de contacto: www.polarcapital.co.uk\n"
        "Autoridad competente: Banco Central de Irlanda\n"
    )
    # Pad to push objective text into the detection window
    pad = " " * 500
    return hdr + pad + objective


# ---------------------------------------------------------------------------
# FIX-RV-EN-PRIMARILY-EQUITIES-1
# "primarily/mainly in [modifier] equities" → Renta Variable
# ---------------------------------------------------------------------------

class TestEnPrimarilyInEquities:
    """FIX-RV-EN-PRIMARILY-EQUITIES-1: English equity primacy declaration with
    a geographic or style qualifier between the adverb and 'equities' must
    resolve to Renta Variable."""

    def test_primarily_in_nordic_equities(self):
        """EVLI NORDIC pattern: 'primarily in nordic equities' — geographic
        modifier breaks the bare 'primarily in equities' substring."""
        text = _en(
            "the fund invests its assets primarily in nordic equities. "
            "the fund's target is to exceed the return of the benchmark index. "
            "the fund may also invest its assets in derivatives contracts."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable", (
            f"'primarily in nordic equities' must return Renta Variable; got {result!r}"
        )

    def test_mainly_in_listed_equities_with_parenthetical(self):
        """LUX DEFENCE pattern: 'mainly (at least 51%) in listed equities' —
        a parenthetical percentage between 'mainly' and 'in' breaks the match."""
        text = _en(
            "the sub-fund's investment objective is to invest mainly (at least 51%) "
            "in listed equities in security/defence sectors. investment in the other "
            "sectors listed in the investment objectives is restricted to a maximum "
            "of 49% of net assets."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable", (
            f"'mainly (at least 51%) in listed equities' must return Renta Variable; "
            f"got {result!r}"
        )

    def test_principally_in_european_equities(self):
        """Generic form with 'principally' and a geographic qualifier."""
        text = _en(
            "the fund invests principally in european equities, primarily targeting "
            "large-cap companies with strong fundamentals."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable", (
            f"'principally in european equities' must return Renta Variable; "
            f"got {result!r}"
        )

    def test_guard_bond_fund_with_equities_far_away(self):
        """Bond fund whose objective says 'primarily invests in bonds' should NOT
        be caught by the new regex even if 'equities' appears later in the window."""
        text = _en(
            "the fund primarily invests in investment grade bonds and other fixed income "
            "securities. up to 10% may be allocated to equities on an ancillary basis."
        )
        result = detect_nature_from_kiid(text)
        assert result != "Renta Variable", (
            f"Bond fund with distant 'equities' must NOT return Renta Variable; "
            f"got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-MIXTOS-ACCIONES-BONOS-CSV-1
# "acciones, bonos" Spanish comma-separated multi-asset enumeration → Mixtos
# ---------------------------------------------------------------------------

class TestAccionesBoncosCSV:
    """FIX-MIXTOS-ACCIONES-BONOS-CSV-1: Spanish comma-separated enumeration
    'acciones, bonos e instrumentos del mercado monetario' must trigger Mixtos."""

    def test_fvs_multiple_opportunities_pattern(self):
        """FVS MULTIPLE OPPORTUNITIES II pattern: 'entre ellos acciones, bonos e
        instrumentos del mercado monetario'."""
        text = _es(
            "el subfondo podrá invertir en todos los instrumentos de inversión "
            "admitidos por el reglamento de gestión, entre ellos acciones, bonos e "
            "instrumentos del mercado monetario, participaciones en OICVM u OIC, "
            "derivados y depósitos a largo plazo."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Mixtos", (
            f"'acciones, bonos e instrumentos del mercado monetario' must return "
            f"Mixtos; got {result!r}"
        )

    def test_acciones_bonos_csv_simple(self):
        """Minimal form: 'acciones, bonos' alone."""
        text = _es(
            "el fondo invierte en acciones, bonos y otros activos financieros "
            "de todo el mundo."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Mixtos", (
            f"'acciones, bonos y otros activos' must return Mixtos; got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-MIXTOS-EN-EQUITIES-BONDS-1
# "equities, bonds" English comma-separated multi-asset enumeration → Mixtos
# ---------------------------------------------------------------------------

class TestEquitiesBondsCSV:
    """FIX-MIXTOS-EN-EQUITIES-BONDS-1: English enumeration 'equities, bonds and
    money market instruments' must trigger Mixtos."""

    def test_ffg_global_flexible_pattern(self):
        """FFG GLOBAL FLEXIBLE CONVICTIONS pattern: 'made in equities, bonds and
        money market instruments or in cash'."""
        text = _en(
            "the objective of the sub-fund is to achieve a medium-turn return "
            "exceeding a bond investment in eur. investments in the sub-fund are "
            "without geographic, sectoral and monetary restrictions and made in "
            "equities, bonds and money market instruments or in cash."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Mixtos", (
            f"'made in equities, bonds and money market instruments' must return "
            f"Mixtos; got {result!r}"
        )

    def test_equities_bonds_csv_simple(self):
        """Minimal form: 'equities, bonds' comma-separated."""
        text = _en(
            "the fund allocates between equities, bonds and other asset classes "
            "based on prevailing market conditions."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Mixtos", (
            f"'equities, bonds and other asset classes' must return Mixtos; "
            f"got {result!r}"
        )

    def test_guard_large_equity_mandate_not_mixtos(self):
        """Equity fund with ≥60% minimum equity mandate that also lists bonds
        must NOT be classified Mixtos even if 'equities, bonds' appears."""
        text = _en(
            "the fund invests at least 80% in equities, bonds convertibles and "
            "other equity-linked instruments. the manager targets long-term "
            "capital appreciation through concentrated equity selection."
        )
        result = detect_nature_from_kiid(text)
        # Should be Renta Variable (large equity mandate), not Mixtos
        assert result != "Mixtos" or result == "Renta Variable", (
            f"80% equity mandate must not classify as Mixtos; got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-RV-ARTIFICIAL-INTELLIGENCE-1
# "Artificial Intelligence Fund" in KIID product-name header → Renta Variable
# ---------------------------------------------------------------------------

class TestArtificialIntelligenceFund:
    """FIX-RV-ARTIFICIAL-INTELLIGENCE-1: A KIID whose product name (first 600 chars)
    says 'Artificial Intelligence Fund' must resolve to Renta Variable even when
    the investment objective text is outside the window or lacks equity keywords."""

    def test_polar_capital_ai_fund_pattern(self):
        """Polar Capital AI Fund pattern: 'Artificial Intelligence Fund' in
        the KIID product name; objective text is administrative (no equity signal)."""
        text = _ai_header(
            "se puede obtener más información y documentación sobre la empresa en inglés, "
            "incluidos los informes anuales y semestrales más recientes e históricos, "
            "en la página web: www.polarcapital.co.uk. consulte a su asesor financiero "
            "para más información."
        )
        result = detect_nature_from_kiid(text)
        assert result == "Renta Variable", (
            f"'Artificial Intelligence Fund' in KIID header must return Renta Variable; "
            f"got {result!r}"
        )

    def test_guard_ai_bond_fund_not_equity(self):
        """Hypothetical 'Artificial Intelligence Bond Fund' in the header —
        the bond signal in the header must suppress the equity classification."""
        hdr = (
            "Nombre del fondo: Artificial Intelligence Bond Fund\n"
            "Nombre del productor: Test Manager\n"
        )
        pad = " " * 500
        text = hdr + pad + (
            "el fondo invierte principalmente en bonos corporativos y titulos de deuda "
            "emitidos por empresas tecnologicas."
        )
        result = detect_nature_from_kiid(text)
        assert result != "Renta Variable", (
            f"'Artificial Intelligence Bond Fund' (with 'bond' in header) must NOT "
            f"return Renta Variable; got {result!r}"
        )
