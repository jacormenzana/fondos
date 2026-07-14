# proyecto1/tests/test_geography_fixes_20260714.py
# -*- coding: utf-8 -*-
"""
Regression tests for geography detection fixes applied in sessions 9 and 10
of pipelineP1Audit (2026-07-14).

R-7: no imports of pipeline.py or core.io.

Changes covered:

  detect_geography() — name-based extractor (classify_utils.py):

    FIX-GEO-9a (2026-07-14): "jpn" abbreviation added as Japan signal.
      Fidelity and other managers use "JPN" in abbreviated fund names
      (e.g. "FIDELITY F JAP JPN A ACC") where "japan" never appears.

    FIX-GEO-9b (2026-07-14): ex-Japan negation guard.
      "ex japan"/"ex-japan"/"ex jpn" in name → Japan detector skips,
      preventing false positive on funds like "MSCI AC Asia ex Japan".

    FIX-GEO-9c (2026-07-14): removed bare "treasury" from EEUU signals.
      "treasury" alone is a generic government-bond term used for European
      funds ("Morningstar Eurozone Treasury Bond") → false EEUU.
      Replaced with compound "us treasury".

    FIX-GEO-9d (2026-07-14): r"\\basi\\b" regex added as Asia signal.
      "ASI" is a Fidelity abbreviation for Asia ("FIDELITY F ASI EQ ESG").
      Plain "asi" is unsafe (matches "basil"/"casual"); word boundary required.

  detect_geography_from_kiid() — KIID-text extractor (classify_utils.py):

    FIX-GEO-10a (2026-07-14): added "residentes de" to _GEO_NEGATION_MARKERS.
      The standard Regulation S disclaimer "el producto no está abierto a
      residentes de los estados unidos de américa" appears in thousands of
      Spanish-language KIIDs regardless of the fund's actual geography.
      Before this fix, bare "estados unidos" fired EEUU even for European
      funds (confirmed on CPR Silver Age FR0010836163).

    FIX-GEO-10b (2026-07-14): Latinoamérica conjunction guard.
      When "latinoamérica"/"latin america" appears within 150 chars of
      "estados unidos"/"canadá", the fund invests in both US/Canadian AND
      Latin American markets (multi-region). The LatAm signal is skipped;
      EEUU or Global fires from the next match.
      Confirmed on Findlay Park American Fund (IE0002458671).
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR    = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", ".."))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from classify_utils import detect_geography, detect_geography_from_kiid


def _ddf(objective_text: str) -> str:
    """Wrap objective_text in a minimal DDF KIID structure so that
    _detect_kiid_format() recognises it as DDF (window 500-5000) and
    _extract_window() places the content inside the active window."""
    header = "documento de datos fundamentales\n"
    padding = "x" * 500
    return header + padding + "\n" + objective_text


# ──────────────────────────────────────────────────────────────────────────────
# detect_geography() — name-based
# ──────────────────────────────────────────────────────────────────────────────

class TestDetectGeographyJpnAbbreviation:
    """FIX-GEO-9a: 'jpn' is accepted as a Japan abbreviation."""

    @pytest.mark.parametrize("name", [
        "fidelity f jap jpn a acc",
        "jpn equity fund",
        "ishares jpn index",
        "ubs japan jpn",
    ])
    def test_jpn_fires_japan(self, name):
        assert detect_geography(name) == "Japón", (
            f"Expected 'Japón' for {name!r} (jpn abbreviation)"
        )


class TestDetectGeographyExJapanGuard:
    """FIX-GEO-9b: ex-japan / ex-jpn in name blocks Japan detection."""

    @pytest.mark.parametrize("name", [
        "msci ac asia ex japan",
        "ishares msci asia ex japan",
        "fidelity asia ex-japan growth",
        "bny mellon ex jpn equity",
    ])
    def test_ex_japan_does_not_fire_japan(self, name):
        result = detect_geography(name)
        assert result != "Japón", (
            f"'Japón' must not fire for ex-Japan fund: {name!r} (got {result!r})"
        )

    @pytest.mark.parametrize("name,expected", [
        # ex-japan → Asia fires (Asia is the covering universe)
        ("msci ac asia ex japan", "Asia"),
        # no region-specific term after ex-japan suppression → None
        ("fund ex japan growth", None),
    ])
    def test_ex_japan_correct_outcome(self, name, expected):
        assert detect_geography(name) == expected, (
            f"Expected {expected!r} for {name!r}"
        )


class TestDetectGeographyTreasuryFixed:
    """FIX-GEO-9c: bare 'treasury' no longer fires EEUU; 'us treasury' still does."""

    @pytest.mark.parametrize("name", [
        "morningstar eurozone treasury bond",
        "db european treasury bond fund",
        "pimco global treasury index",
        "bnp treasury euro government bond",
    ])
    def test_bare_treasury_does_not_fire_eeuu(self, name):
        result = detect_geography(name)
        assert result != "EEUU", (
            f"'EEUU' must not fire for non-US treasury fund: {name!r} (got {result!r})"
        )

    @pytest.mark.parametrize("name", [
        "bgf us treasury bond",
        "pimco us treasury short duration",
        "vanguard us treasury index",
    ])
    def test_us_treasury_compound_fires_eeuu(self, name):
        assert detect_geography(name) == "EEUU", (
            f"Expected 'EEUU' for {name!r} ('us treasury' compound)"
        )


class TestDetectGeographyAsiAbbreviation:
    """FIX-GEO-9d: r'\\basi\\b' fires Asia; does not match sub-strings."""

    @pytest.mark.parametrize("name", [
        "fidelity f asi eq esg",
        "blackrock asi equity fund",
    ])
    def test_asi_word_boundary_fires_asia(self, name):
        assert detect_geography(name) == "Asia", (
            f"Expected 'Asia' for {name!r} (\\basi\\b abbreviation)"
        )

    @pytest.mark.parametrize("name", [
        "fidelity basic materials fund",   # 'basi' inside 'basic'
        "ubs casualty insurance",          # 'asi' inside 'casualty'
        "amundi asian opportunities",      # 'asi' inside 'asian' — but 'asian' also fires Asia
    ])
    def test_asi_no_false_positive_substrings(self, name):
        result = detect_geography(name)
        # "amundi asian opportunities" correctly returns Asia via "asian" keyword;
        # the others must not return Asia via false \\basi\\b match
        if "asian" in name.lower():
            assert result == "Asia"
        else:
            assert result != "Asia", (
                f"'Asia' must not fire for {name!r} (false \\basi\\b substring match)"
            )


# ──────────────────────────────────────────────────────────────────────────────
# detect_geography_from_kiid() — KIID-text extractor
# ──────────────────────────────────────────────────────────────────────────────

class TestDetectGeographyFromKiidResidentesGuard:
    """FIX-GEO-10a: 'residentes de' negates bare 'estados unidos' in KIID text."""

    def test_residentes_eeuu_disclaimer_does_not_fire_eeuu(self):
        """US-resident restriction alone must not return EEUU."""
        text = _ddf(
            "Invertirá principalmente en renta variable europea. "
            "El producto no está abierto a residentes de los estados unidos de américa. "
            "Objetivo: lograr una revalorización del capital."
        )
        result = detect_geography_from_kiid(text)
        assert result != "EEUU", (
            f"'estados unidos' inside 'residentes de los estados unidos' must not "
            f"fire EEUU; got {result!r}"
        )

    def test_objective_with_disclaimer_returns_europa(self):
        """Fund objective 'renta variable europea' + residentes disclaimer → Europa."""
        text = _ddf(
            "El Fondo invierte principalmente en renta variable europea relacionada "
            "con la temática del envejecimiento de la población. "
            "El producto no está abierto a residentes de los estados unidos."
        )
        result = detect_geography_from_kiid(text)
        assert result == "Europa", (
            f"Expected 'Europa' when objective says 'renta variable europea' and "
            f"only US-resident disclaimer contains 'estados unidos'; got {result!r}"
        )

    def test_genuine_eeuu_fund_still_fires(self):
        """A fund with explicit EEUU objective must still return EEUU."""
        text = _ddf(
            "Invertirá principalmente en valores de estados unidos. "
            "Objetivo: invertir en el mercado bursátil de estados unidos."
        )
        assert detect_geography_from_kiid(text) == "EEUU"


class TestDetectGeographyFromKiidLatAmConjunctionGuard:
    """FIX-GEO-10b: 'latinoamérica' after 'estados unidos'/'canadá' is multi-region → skipped."""

    def test_latam_after_eeuu_not_latam(self):
        """When 'latinoamérica' is conjoined with 'estados unidos', must not return LatAm.
        Either EEUU or Global is acceptable (both are correct for a multi-region fund)."""
        text = _ddf(
            "Invierte en valores de renta variable estadounidense del mercado "
            "de estados unidos. Invierte principalmente en valores de empresas "
            "estadounidenses, canadienses y latinoamericanas que coticen en "
            "mercados reconocidos de estados unidos, canadá y latinoamérica."
        )
        result = detect_geography_from_kiid(text)
        assert result != "Latinoamérica", (
            f"'Latinoamérica' must not fire when 'estados unidos' precedes it in "
            f"conjunction; got {result!r}"
        )

    def test_pure_latam_fund_still_fires(self):
        """A genuine LatAm-only fund must still return Latinoamérica."""
        text = _ddf(
            "Objetivo: invertir principalmente en mercados de latinoamérica. "
            "El fondo invierte en brasil, méxico, colombia y chile."
        )
        result = detect_geography_from_kiid(text)
        assert result == "Latinoamérica", (
            f"Expected 'Latinoamérica' for genuine LatAm fund; got {result!r}"
        )

    def test_latam_after_canada_not_latam(self):
        """'canadá y latinoamérica' conjunction → LatAm skipped."""
        text = _ddf(
            "Invierte en valores de renta variable de canadá y latinoamérica, "
            "principalmente en los mercados de canadá, estados unidos y latinoamérica."
        )
        result = detect_geography_from_kiid(text)
        assert result != "Latinoamérica", (
            f"'Latinoamérica' must not fire when 'canadá' precedes it; "
            f"got {result!r}"
        )
