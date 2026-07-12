# proyecto1/tests/test_inter18_19_mmf_style.py
# -*- coding: utf-8 -*-
"""
Tests for INTER-18 (SC-E1/E2: MMF_Structure applicability by Fund_Nature)
and INTER-19 (SC-E3: Style_Profile on Restantes funds).

Rules covered:
  SC-E1: Fund_Nature != 'Monetario' + MMF_Structure != 'Not Applicable'
         → auto-corrected to 'Not Applicable'
  SC-E2: Fund_Nature='Monetario' + MMF_Structure='Not Applicable'
         → WARN (missing MMFR structure)
  SC-E3: Fund_Nature='Restantes' + Style_Profile populated
         → WARN rule='StyleProfile-Nature' (stale attribute from prior classification)

R-7: no imports of pipeline.py or core.io.
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
from classify_utils import validate_all_semantic_consistency


def _run(record: dict) -> dict:
    return validate_all_semantic_consistency(record)


def _base(**kwargs) -> dict:
    base = {"Fund_Nature": None, "Family": None}
    base.update(kwargs)
    return base


# ─── INTER-18 SC-E1: MMF_Structure on non-Monetario → 'Not Applicable' ────────

class TestInter18ScE1NonMonetario:

    @pytest.mark.parametrize("mmf_val", ["CNAV", "LVNAV", "VNAV", "Standard MMF"])
    @pytest.mark.parametrize("nature", [
        "Renta Variable", "Renta Fija Flexible", "Renta Fija Corto Plazo",
        "Mixtos", "Alternativo", "Estructurado", "Restantes",
    ])
    def test_mmf_structure_corrected_to_not_applicable(self, nature, mmf_val):
        result = _run(_base(Fund_Nature=nature, MMF_Structure=mmf_val))
        assert result["corrected_record"]["MMF_Structure"] == "Not Applicable"

    @pytest.mark.parametrize("nature", ["Renta Variable", "Mixtos", "Alternativo"])
    def test_warn_emitted_for_correction(self, nature):
        result = _run(_base(Fund_Nature=nature, MMF_Structure="LVNAV"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "MMFStructure-Nature" in warn_rules

    @pytest.mark.parametrize("nature", [
        "Renta Variable", "Renta Fija Flexible", "Renta Fija Corto Plazo",
        "Mixtos", "Alternativo", "Estructurado", "Restantes",
    ])
    def test_not_applicable_already_untouched(self, nature):
        result = _run(_base(Fund_Nature=nature, MMF_Structure="Not Applicable"))
        assert result["corrected_record"]["MMF_Structure"] == "Not Applicable"
        warns = [w for w in result["warnings"] if "MMFStructure" in w["rule"]]
        assert warns == []

    def test_null_mmf_structure_on_non_monetario_untouched(self):
        result = _run(_base(Fund_Nature="Renta Variable", MMF_Structure=None))
        assert result["corrected_record"].get("MMF_Structure") is None
        warns = [w for w in result["warnings"] if "MMFStructure" in w["rule"]]
        assert warns == []


# ─── INTER-18 SC-E2: Monetario with MMF_Structure='Not Applicable' → WARN ─────

class TestInter18ScE2Monetario:

    def test_monetario_not_applicable_emits_warn(self):
        result = _run(_base(Fund_Nature="Monetario", MMF_Structure="Not Applicable"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "MMFStructure-Nature" in warn_rules

    def test_monetario_not_applicable_not_auto_corrected(self):
        """SC-E2 is WARN-only; we cannot auto-correct without knowing the real structure."""
        result = _run(_base(Fund_Nature="Monetario", MMF_Structure="Not Applicable"))
        assert result["corrected_record"]["MMF_Structure"] == "Not Applicable"

    @pytest.mark.parametrize("mmf_val", ["CNAV", "LVNAV", "VNAV", "Standard MMF"])
    def test_monetario_with_valid_structure_no_warn(self, mmf_val):
        result = _run(_base(Fund_Nature="Monetario", MMF_Structure=mmf_val))
        warns = [w for w in result["warnings"] if "MMFStructure" in w["rule"]]
        assert warns == [], f"Unexpected warn for Monetario+{mmf_val}: {warns}"

    def test_monetario_null_mmf_structure_no_warn(self):
        """NULL means 'not yet extracted'; SC-E2 only fires on explicit 'Not Applicable'."""
        result = _run(_base(Fund_Nature="Monetario", MMF_Structure=None))
        warns = [w for w in result["warnings"] if "MMFStructure" in w["rule"]]
        assert warns == []


# ─── INTER-18 combined: SC-E1 takes precedence when nature is non-Monetario ───

class TestInter18ScE1TakesPrecedence:

    def test_renta_variable_cnav_corrected_not_warned_for_e2(self):
        """SC-E1 fires (correction to 'Not Applicable'). SC-E2 must NOT fire (it's not Monetario)."""
        result = _run(_base(Fund_Nature="Renta Variable", MMF_Structure="CNAV"))
        assert result["corrected_record"]["MMF_Structure"] == "Not Applicable"
        # SC-E2 warn must not appear (E2 only applies to Monetario)
        e2_warn = [w for w in result["warnings"]
                   if "MMFStructure-Nature" in w["rule"]
                   and "debería tener estructura" in w.get("message", "")]
        assert e2_warn == []


# ─── INTER-19 SC-E3: Style_Profile on Restantes → WARN ────────────────────────

class TestInter19ScE3Restantes:

    @pytest.mark.parametrize("style", ["Growth", "Value", "Blend", "Income",
                                        "Low Volatility", "Quality", "Momentum"])
    def test_style_profile_on_restantes_emits_warn(self, style):
        result = _run(_base(Fund_Nature="Restantes", Style_Profile=style))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "StyleProfile-Nature" in warn_rules

    @pytest.mark.parametrize("style", ["Growth", "Value", "Blend"])
    def test_style_profile_on_restantes_not_auto_corrected(self, style):
        """SC-E3 is WARN-only; we must not null Style_Profile automatically."""
        result = _run(_base(Fund_Nature="Restantes", Style_Profile=style))
        assert result["corrected_record"]["Style_Profile"] == style

    def test_style_not_applicable_on_restantes_no_warn(self):
        result = _run(_base(Fund_Nature="Restantes", Style_Profile="Not Applicable"))
        warns = [w for w in result["warnings"] if "StyleProfile-Nature" in w["rule"]]
        assert warns == []

    def test_null_style_on_restantes_no_warn(self):
        result = _run(_base(Fund_Nature="Restantes", Style_Profile=None))
        warns = [w for w in result["warnings"] if "StyleProfile-Nature" in w["rule"]]
        assert warns == []

    @pytest.mark.parametrize("nature", ["Renta Variable", "Mixtos", "Alternativo"])
    def test_style_on_non_restantes_no_warn(self, nature):
        """INTER-19 must NOT fire for valid equity/mixed natures."""
        result = _run(_base(Fund_Nature=nature, Style_Profile="Growth"))
        warns = [w for w in result["warnings"] if "StyleProfile-Nature" in w["rule"]]
        assert warns == []

    def test_restantes_no_style_profile_no_warn(self):
        result = _run(_base(Fund_Nature="Restantes"))
        warns = [w for w in result["warnings"] if "StyleProfile-Nature" in w["rule"]]
        assert warns == []
