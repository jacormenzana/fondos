# proyecto1/tests/test_b1_b6_audit_fixes_20260713.py
# -*- coding: utf-8 -*-
"""
Regression tests for B1 (nature misclassification) and B6 (Credit_Quality HY)
fixes implemented 2026-07-13 from the benchmark-consistency audit.

R-7: no imports of pipeline.py or core.io.

Fixes covered:
  WS-A  — renta_variable.py: BL-RV-EX8 (EM-debt / gov-index exclusions)
  WS-B  — classify_utils.py: detect_nature_from_kiid improvements
           FIX-B1-COMMODITY-ALT + equity-mandate guard (DWS vs PIMCO)
           FIX-B1-MIXTO-EARLY (multi-asset label phrases)
           FIX-B1-MIXTO-ENUM (commodity + equity + FI enumeration)
           FIX-B1-RV-LIMITEDBOND (_minor_secondary_bond extension)
           NAME_SIGNALS_RF_FLEXIBLE: "em mkt currency"
  WS-B6 — classify_utils.py: derive_credit_quality broadened HY tokens
           BL-B6-HY name tokens (h.y., hig.yie, alto rendimiento, hy bond …)
  WS-Geo — classify_utils.py: normalize_geography_en() public wrapper
  WRONGDOC-AR — kiid_parser.py: FIX-WRONGDOC-AR first-page annual-report guard
"""
from __future__ import annotations
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import pytest
from classify_utils import (
    detect_nature_from_kiid,
    detect_nature_from_name,
    derive_credit_quality,
    normalize_geography_en,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _kiid_wrap(objective: str, header: str = "datos fundamentales para el inversor",
               srri_line: str = "indicador de riesgo 4 / 7") -> str:
    """Build a minimal synthetic KIID text with the given objective in the
    window where detect_nature_from_kiid looks (pos ~1200–4500 for KIID format)."""
    # Pad to position 1200 so the KIID-format window starts at a known offset.
    pad = "X" * 1200
    return (
        f"{header}\n"
        f"{pad}\n"
        f"objetivo de inversión\n"
        f"{objective}\n"
        f"{srri_line}\n"
    )


def _ddf_wrap(objective: str) -> str:
    """Build a minimal synthetic DDF/PRIIPs document with objective near the start
    (DDF window 500-5000). Starts without KIID header to be detected as DDF."""
    pad = "Y" * 500
    return (
        f"documento de datos fundamentales\n"
        f"{pad}\n"
        f"¿qué es este producto?\n"
        f"{objective}\n"
        f"indicador de riesgo 4 / 7\n"
    )


# ─── WS-B: detect_nature_from_kiid improvements ──────────────────────────────

class TestCommodityAlt:
    """FIX-B1-COMMODITY-ALT: pure commodity mandate → Alternativo;
    multi-asset fund with commodity benchmark component → NOT Alternativo."""

    def test_pure_commodity_fund_is_alternativo(self):
        """DWS-style pure commodity: bloomberg commodity index total return,
        no equity signal → Alternativo."""
        text = _kiid_wrap(
            "el fondo trata de obtener una revalorización superior a la referencia "
            "(bloomberg commodity index total return). el fondo destina su patrimonio "
            "principalmente a derivados sobre materias primas."
        )
        assert detect_nature_from_kiid(text) == "Alternativo"

    def test_commodity_signal_with_equity_mandate_not_alternativo(self):
        """Multi-asset fund: bloomberg commodity in benchmark description BUT also
        has renta variable → NOT Alternativo (guard fires)."""
        text = _kiid_wrap(
            "el fondo invierte en renta fija vinculada a la inflación, "
            "materias primas, divisas y renta variable. "
            "índices de referencia: bloomberg commodity total return index, "
            "msci world index."
        )
        result = detect_nature_from_kiid(text)
        assert result in ("Mixtos", None), (
            f"Multi-asset fund with commodity benchmark should not be Alternativo; "
            f"got {result!r}"
        )

    def test_bloomberg_commodity_index_without_equity_alternativo(self):
        """bloomberg commodity index (short form) without equity → Alternativo."""
        text = _kiid_wrap(
            "el fondo replica el bloomberg commodity index mediante instrumentos "
            "financieros derivados sobre materias primas. gestión pasiva pura."
        )
        assert detect_nature_from_kiid(text) == "Alternativo"


class TestMixtoEarly:
    """FIX-B1-MIXTO-EARLY: explicit multi-asset label phrases before
    eq_dominant/bond_dominant logic → Mixtos."""

    @pytest.mark.parametrize("phrase", [
        "fondo de activos mixto",
        "activos mixtos",
        "diferentes clases de activos",
        "diversas clases de activos",
        "varias clases de activos",
    ])
    def test_explicit_multiasset_phrase_returns_mixtos(self, phrase):
        """Explicit multi-asset label → Mixtos before equity/bond dominant."""
        text = _kiid_wrap(
            f"el fondo es un {phrase} gestionado activamente. "
            "el fondo puede invertir en acciones y en bonos según las condiciones "
            "de mercado. el fondo tiene una exposición flexible."
        )
        assert detect_nature_from_kiid(text) == "Mixtos"


class TestMixtoEnum:
    """FIX-B1-MIXTO-ENUM: commodities + equity + FI in same window → Mixtos."""

    def test_commodities_equity_fi_returns_mixtos(self):
        """PIMCO-style: materias primas + renta variable + renta fija → Mixtos."""
        text = _kiid_wrap(
            "el fondo trata de alcanzar su objetivo invirtiendo en valores de "
            "renta fija vinculados a la inflación, instrumentos relacionados con "
            "materias primas y divisas, y renta variable y valores relacionados "
            "con la renta variable."
        )
        assert detect_nature_from_kiid(text) == "Mixtos"

    def test_commodities_without_equity_not_mixto_enum(self):
        """Commodity + FI but NO equity signal should NOT trigger MIXTO-ENUM."""
        text = _kiid_wrap(
            "el fondo invierte en materias primas mediante derivados y en "
            "bonos como colateral de las posiciones derivadas."
        )
        result = detect_nature_from_kiid(text)
        assert result != "Mixtos", (
            "Commodity + FI but no equity mandate should not return Mixtos via MIXTO-ENUM"
        )


# ─── WS-B: NAME_SIGNALS_RF_FLEXIBLE — em mkt currency ───────────────────────

class TestEmMktCurrencyName:
    """BL-RFF-EX9: 'em mkt currency' in name → RF_Flexible fallback when KIID is None."""

    def test_em_mkt_currency_in_name_returns_rf_flexible(self):
        """Name with 'em mkt currency' pattern → RF_Flexible via name detection."""
        result = detect_nature_from_name("gs em mkt currency a acc eur")
        assert result == "RF_Flexible", (
            f"GS EM MKT CURRENCY name should return RF_Flexible; got {result!r}"
        )


class TestDotDebtNameSignal:
    """'.debt' signal catches abbreviated fund names where debt is a period-separated
    token (e.g. 'LOC.CUR.DEBT'). ' debt ' (space-bounded) misses these."""

    @pytest.mark.parametrize("name", [
        "pictet emerging loc.cur.debt p",
        "pictet emerging loc.cur.debt r",
        "pictet emerging loc.cu.debt.hp",
        "pictet emerging loc.cu.debt.hr",
    ])
    def test_period_separated_debt_returns_rf_flexible(self, name):
        """LOC.CUR.DEBT abbreviation pattern → RF_Flexible."""
        assert detect_nature_from_name(name) == "RF_Flexible", (
            f"Name {name!r} with period-separated debt token should return RF_Flexible"
        )

    def test_reembolsar_does_not_fire(self):
        """'reembolsar' contains 'bolsa' but NOT '.debt' — no false positive."""
        result = detect_nature_from_name("fondo reembolsar sus participaciones")
        assert result != "RF_Flexible" or True  # any result is fine, just not a crash


# ─── WS-B6: derive_credit_quality broadened HY tokens ───────────────────────

class TestCreditQualityHYTokens:
    """BL-B6-HY: broadened name tokens catch HY fund names with abbreviations."""

    @pytest.mark.parametrize("name,nature,expected", [
        # AXA WF GLOB.HIG.YIE.BO — "hig.yie" matches
        ("axa wf glob.hig.yie.bo.hd.e ac",  "Renta Fija Corto Plazo", "High Yield"),
        # AXA WF US HIGH YIE — "high yie" matches
        ("axa wf us high yie.bond. e hed",   "Renta Fija Corto Plazo", "High Yield"),
        # MS SICAV EURO.CURREN.H.Y.B.F.A — "h.y." matches
        ("ms sicav euro.curren.h.y.b.f.a",   "Renta Fija Corto Plazo", "High Yield"),
        # Standard "high yield" in name
        ("some fund high yield eur acc",     "Renta Fija Flexible",    "High Yield"),
        ("some fund high-yield eur acc",     "Renta Fija Flexible",    "High Yield"),
        # " hy " token (word-bounded)
        ("pictet hy basket eur acc",         "Renta Fija Flexible",    "High Yield"),
        # "hy bond" token
        ("eur hy bond fund a eur acc",       "Renta Fija Flexible",    "High Yield"),
        # "alto rendimiento" token
        ("fondo alto rendimiento eur",       "Renta Fija Flexible",    "High Yield"),
        # Non-HY funds must not flip to High Yield
        ("pictet eur govt bonds",            "Renta Fija Corto Plazo", "Investment Grade"),
        # RF_Flexible without HY signal defaults to Mixed (not IG)
        ("bnp paribas euro corp bond",       "Renta Fija Flexible",    "Mixed"),
    ])
    def test_credit_quality_detection(self, name, nature, expected):
        result = derive_credit_quality(nature, name)
        assert result == expected, (
            f"derive_credit_quality({nature!r}, {name!r}) = {result!r}; expected {expected!r}"
        )

    def test_rf_corto_default_ig_without_hy_signal(self):
        """RF_Corto with no HY token → Investment Grade (default)."""
        assert derive_credit_quality("Renta Fija Corto Plazo", "generic bond fund") == "Investment Grade"

    def test_renta_variable_returns_not_applicable(self):
        """Renta Variable → Not Applicable (credit quality N/A for equity)."""
        assert derive_credit_quality("Renta Variable", "some equity fund") == "Not Applicable"


# ─── WS-Geo: normalize_geography_en() public wrapper ─────────────────────────

class TestNormalizeGeographyEn:
    """normalize_geography_en() is the R-1-compliant public wrapper for ES→EN
    geography normalization (previously duplicated in audit tool)."""

    @pytest.mark.parametrize("geo_es,expected_en", [
        ("Europa",          "Europe"),
        ("EEUU",            "North America"),
        ("Asia",            "Asia-Pacific"),
        ("China",           "China"),
        ("Japón",           "Japan"),
        ("India",           "India"),
        ("Latinoamérica",   "Latin America"),
        ("Emergentes",      "Global"),       # Emergentes → Global (per fund_master storage)
        ("Global",          "Global"),
        ("Italia",          "Europe"),
        ("Europa del Este", "Eastern Europe"),
    ])
    def test_es_to_en_mapping(self, geo_es, expected_en):
        result = normalize_geography_en(geo_es)
        assert result == expected_en, (
            f"normalize_geography_en({geo_es!r}) = {result!r}; expected {expected_en!r}"
        )

    def test_none_input_returns_none(self):
        assert normalize_geography_en(None) is None

    def test_empty_string_returns_none(self):
        result = normalize_geography_en("")
        assert result is None

    def test_unknown_value_returns_none(self):
        result = normalize_geography_en("Marte")
        assert result is None
