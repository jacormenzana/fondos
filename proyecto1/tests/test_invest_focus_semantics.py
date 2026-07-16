# proyecto1/tests/test_invest_focus_semantics.py
# -*- coding: utf-8 -*-
"""
Tests para las reglas INTER-15/16 y SC-C1/C2 en validate_all_semantic_consistency,
y para la corrección del THEME_SECTOR_MAPPING (alineación a DOMAIN_VALUES v20).

Reglas cubiertas:
  INTER-15 (SC-B1/B2): Theme='Megatrends'/'Inflation' → Investment_Focus='Thematic',
                        Sector_Focus=NULL
  INTER-16 (SC-B5):    Investment_Focus='Thematic' + Sector_Focus poblado → WARN
  SC-B6 / INTER-9:     Theme→Sector_Focus mismatch → auto-corrección
  SC-C1:               Family='Thematic Equity' + Investment_Focus='Broad' → WARN
  SC-C2:               Family='Equity Core' + Investment_Focus='Thematic' → WARN
  THEME_SECTOR_MAPPING: valores v20 correctos

Regla R-7: sin imports de pipeline.py ni core.io.
"""
from __future__ import annotations
import os, sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
_CORE_DIR  = os.path.join(_PROJ1_DIR, "core")
for _d in (_CORE_DIR, _PROJ1_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pytest
from classify_utils import (
    validate_all_semantic_consistency,
    THEME_SECTOR_MAPPING,
    _THEMATIC_ONLY_THEMES,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _record(**kwargs) -> dict:
    """Base fund record with the minimum fields for these tests."""
    base = {
        "Fund_Nature": "Renta Variable",
        "Family": "Thematic Equity",
        "Investment_Focus": None,
        "Theme": None,
        "Sector_Focus": None,
    }
    base.update(kwargs)
    return base


def _run(record: dict) -> dict:
    return validate_all_semantic_consistency(record)


# ─── INTER-15: SC-B1/B2 — Megatrends/Inflation force Thematic ───────────────

class TestInter15ThematicOnlyThemes:
    """Theme in _THEMATIC_ONLY_THEMES → Investment_Focus corrected to Thematic,
    Sector_Focus nulled regardless of original values."""

    @pytest.mark.parametrize("theme", ["Megatrends", "Inflation"])
    def test_sector_corrected_to_thematic(self, theme):
        rec = _record(Theme=theme, Investment_Focus="Sector",
                      Sector_Focus="Technology & Innovation")
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Investment_Focus"] == "Thematic", (
            f"Theme='{theme}' + Investment_Focus='Sector' must be corrected to 'Thematic'"
        )

    @pytest.mark.parametrize("theme", ["Megatrends", "Inflation"])
    def test_sector_focus_nulled(self, theme):
        rec = _record(Theme=theme, Investment_Focus="Sector",
                      Sector_Focus="Technology & Innovation")
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Sector_Focus"] is None, (
            f"Theme='{theme}' must null Sector_Focus"
        )

    @pytest.mark.parametrize("theme,old_sf", [
        ("Inflation",   "Inflation-Linked"),  # AXA/PIMCO/SISF pattern
        ("Megatrends",  "Multi-Theme"),       # Pictet pattern
    ])
    def test_invalid_sector_focus_nulled(self, theme, old_sf):
        rec = _record(Theme=theme, Investment_Focus="Thematic", Sector_Focus=old_sf)
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Sector_Focus"] is None
        assert cr["Investment_Focus"] == "Thematic"

    @pytest.mark.parametrize("theme", ["Megatrends", "Inflation"])
    def test_correct_state_no_critical_errors_for_focus_field(self, theme):
        """Already Thematic + Sector_Focus NULL → no InvestmentFocus correction fires."""
        rec = _record(Theme=theme, Investment_Focus="Thematic", Sector_Focus=None)
        result = _run(rec)
        focus_errors = [
            e for e in result["critical_errors"]
            if "InvestmentFocus-ThematicOnlyTheme" in e.get("rule", "")
        ]
        assert focus_errors == []

    @pytest.mark.parametrize("theme", ["Megatrends", "Inflation"])
    def test_critical_error_emitted(self, theme):
        """FIX-SEM-WARN-INFO: auto-correction now emits to warnings (INFO), not critical_errors."""
        rec = _record(Theme=theme, Investment_Focus="Sector",
                      Sector_Focus="Technology & Innovation")
        result = _run(rec)
        # Correction is successful → goes to warnings (→ DQ INFO), not critical_errors (→ WARN).
        rules = [e["rule"] for e in result["warnings"]]
        assert "InvestmentFocus-ThematicOnlyTheme" in rules

    def test_megatrends_thematic_no_sector_focus_emits_no_sector_focus_error(self):
        """Megatrends + Thematic + Sector_Focus already NULL: no SectorFocus error."""
        rec = _record(Theme="Megatrends", Investment_Focus="Thematic", Sector_Focus=None)
        result = _run(rec)
        sf_errors = [
            e for e in result["critical_errors"]
            if "SectorFocus-ThematicOnlyTheme" in e.get("rule", "")
        ]
        assert sf_errors == []


# ─── INTER-16: SC-B5 — Thematic + valid Sector_Focus → WARN ─────────────────

class TestInter16ThematicWithSectorFocus:
    """Investment_Focus='Thematic' + valid Sector_Focus → WARN emitted, no auto-correction."""

    @pytest.mark.parametrize("theme,sf", [
        ("Healthcare", "Healthcare & Life Sciences"),  # ambiguous: could be Sector
        ("Real Estate", "Real Assets"),
    ])
    def test_warn_emitted(self, theme, sf):
        rec = _record(Theme=theme, Investment_Focus="Thematic", Sector_Focus=sf)
        result = _run(rec)
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "SectorFocus-Thematic" in warn_rules

    @pytest.mark.parametrize("theme,sf", [
        ("Healthcare", "Healthcare & Life Sciences"),
        ("Real Estate", "Real Assets"),
    ])
    def test_no_auto_correction(self, theme, sf):
        """WARN only: Sector_Focus must NOT be auto-nulled for ambiguous cases."""
        rec = _record(Theme=theme, Investment_Focus="Thematic", Sector_Focus=sf)
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Sector_Focus"] == sf, "Ambiguous Thematic+SF must not be auto-corrected"
        assert cr["Investment_Focus"] == "Thematic"


# ─── SC-B6 / INTER-9: Theme → Sector_Focus mismatch auto-corrected ───────────

class TestInter9ThemeSectorAutoCorrect:
    """When Theme→Sector_Focus mapping is known and SF differs, SF is auto-corrected."""

    def test_tech_theme_wrong_sf_corrected(self):
        rec = _record(
            Theme="Technology",
            Investment_Focus="Sector",
            Sector_Focus="Healthcare & Life Sciences",  # wrong
        )
        result = _run(rec)
        assert result["corrected_record"]["Sector_Focus"] == "Technology & Innovation"

    def test_healthcare_theme_wrong_sf_corrected(self):
        rec = _record(
            Theme="Healthcare",
            Investment_Focus="Sector",
            Sector_Focus="Technology & Innovation",  # wrong
        )
        result = _run(rec)
        assert result["corrected_record"]["Sector_Focus"] == "Healthcare & Life Sciences"

    def test_gold_maps_to_materials_mining(self):
        rec = _record(
            Theme="Gold",
            Investment_Focus="Sector",
            Sector_Focus="Materials & Mining",
        )
        result = _run(rec)
        # Correct state → no Theme-Sector critical_error
        rules = [e["rule"] for e in result["critical_errors"]]
        assert "Theme-Sector" not in rules

    def test_real_estate_maps_to_real_assets_v20(self):
        """SC-B6: Real Estate → 'Real Assets' (v20), not old 'Real Estate & Infrastructure'."""
        rec = _record(
            Theme="Real Estate",
            Investment_Focus="Sector",
            Sector_Focus="Real Assets",
        )
        result = _run(rec)
        rules = [e["rule"] for e in result["critical_errors"]]
        assert "Theme-Sector" not in rules, (
            "Real Estate + Real Assets is a correct v20 combination"
        )

    def test_financials_maps_to_financial_services_v20(self):
        rec = _record(
            Theme="Financials",
            Investment_Focus="Sector",
            Sector_Focus="Financial Services",
        )
        result = _run(rec)
        rules = [e["rule"] for e in result["critical_errors"]]
        assert "Theme-Sector" not in rules

    def test_consumer_brands_maps_to_consumer_v20(self):
        rec = _record(
            Theme="Consumer Brands",
            Investment_Focus="Sector",
            Sector_Focus="Consumer",
        )
        result = _run(rec)
        rules = [e["rule"] for e in result["critical_errors"]]
        assert "Theme-Sector" not in rules

    def test_cybersecurity_added_to_mapping(self):
        """Cybersecurity → Technology & Innovation was missing from old mapping."""
        rec = _record(
            Theme="Cybersecurity",
            Investment_Focus="Sector",
            Sector_Focus="Technology & Innovation",
        )
        result = _run(rec)
        rules = [e["rule"] for e in result["critical_errors"]]
        assert "Theme-Sector" not in rules


# ─── SC-C1: Thematic Equity + Broad → WARN ───────────────────────────────────

class TestScC1ThematicEquityBroad:

    def test_thematic_equity_broad_warns(self):
        rec = _record(Family="Thematic Equity", Investment_Focus="Broad")
        result = _run(rec)
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Family-InvestmentFocus" in warn_rules

    @pytest.mark.parametrize("focus", ["Sector", "Thematic"])
    def test_thematic_equity_valid_focus_no_warn(self, focus):
        rec = _record(Family="Thematic Equity", Investment_Focus=focus,
                      Theme="Technology",
                      Sector_Focus="Technology & Innovation" if focus == "Sector" else None)
        result = _run(rec)
        c1_warns = [
            w for w in result["warnings"]
            if w["rule"] == "Family-InvestmentFocus" and "SC-C1" in w["message"]
        ]
        assert c1_warns == []


# ─── SC-C2: Equity Core + Thematic → WARN ───────────────────────────────────

class TestScC2EquityCoreThematic:

    def test_equity_core_thematic_warns(self):
        rec = _record(Family="Equity Core", Investment_Focus="Thematic",
                      Theme="Megatrends")
        result = _run(rec)
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Family-InvestmentFocus" in warn_rules

    @pytest.mark.parametrize("focus", ["Broad", "Sector"])
    def test_equity_core_valid_focus_no_c2_warn(self, focus):
        rec = _record(Family="Equity Core", Investment_Focus=focus,
                      Theme="Technology" if focus == "Sector" else "Core/General",
                      Sector_Focus="Technology & Innovation" if focus == "Sector" else None)
        result = _run(rec)
        c2_warns = [
            w for w in result["warnings"]
            if w["rule"] == "Family-InvestmentFocus" and "SC-C2" in w["message"]
        ]
        assert c2_warns == []


# ─── THEME_SECTOR_MAPPING constant ───────────────────────────────────────────

class TestThemeSectorMappingConstant:

    def test_v20_real_assets_not_old_value(self):
        assert THEME_SECTOR_MAPPING["Real Estate"] == "Real Assets"
        assert THEME_SECTOR_MAPPING.get("Real Estate") != "Real Estate & Infrastructure"

    def test_v20_financial_services_not_old_value(self):
        assert THEME_SECTOR_MAPPING["Financials"] == "Financial Services"
        assert THEME_SECTOR_MAPPING["Insurance"] == "Financial Services"

    def test_v20_consumer_not_old_value(self):
        assert THEME_SECTOR_MAPPING["Consumer Brands"] == "Consumer"
        assert THEME_SECTOR_MAPPING.get("Consumer Brands") != "Consumer Discretionary"

    def test_cybersecurity_added(self):
        assert "Cybersecurity" in THEME_SECTOR_MAPPING
        assert THEME_SECTOR_MAPPING["Cybersecurity"] == "Technology & Innovation"

    def test_thematic_only_themes_not_in_mapping(self):
        """Megatrends and Inflation must NOT be in THEME_SECTOR_MAPPING."""
        for theme in _THEMATIC_ONLY_THEMES:
            assert theme not in THEME_SECTOR_MAPPING, (
                f"'{theme}' is a Thematic-Only theme and must not be in THEME_SECTOR_MAPPING"
            )

    def test_thematic_only_themes_constant(self):
        assert "Megatrends" in _THEMATIC_ONLY_THEMES
        assert "Inflation" in _THEMATIC_ONLY_THEMES
        assert "Technology" not in _THEMATIC_ONLY_THEMES
        assert "Healthcare" not in _THEMATIC_ONLY_THEMES
