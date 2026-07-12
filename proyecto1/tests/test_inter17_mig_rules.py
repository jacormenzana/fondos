# proyecto1/tests/test_inter17_mig_rules.py
# -*- coding: utf-8 -*-
"""
Tests para INTER-17 (SC-D1/D2: Credit_Quality/Duration_Profile en fondos equity)
y reglas MIG-1..MIG-4 (migración de valores legado pre-MODIFY).

Reglas cubiertas:
  INTER-17 SC-D1: Credit_Quality != 'Not Applicable' en Renta Variable/Alternativo/Estructurado
                  → corregido a 'Not Applicable'
  INTER-17 SC-D2: Duration_Profile != 'Not Applicable' en los mismos → corregido
  MIG-1: Distribution_Frequency='BIANNUAL' → 'Semi-Annual'
  MIG-2: Hedging_Policy='PARTIAL' → 'Partially Hedged'
  MIG-3: Derivatives_Usage 'NO'→'None', 'LIMITED'→'Hedging Only', 'YES'→WARN
  MIG-4: Liquidity_Profile 'T1'→'Daily', 'T5'→WARN

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
from classify_utils import validate_all_semantic_consistency


def _run(record: dict) -> dict:
    return validate_all_semantic_consistency(record)


def _base(**kwargs) -> dict:
    base = {"Fund_Nature": None, "Family": None}
    base.update(kwargs)
    return base


# ─── INTER-17 SC-D1/D2: Credit_Quality / Duration_Profile on equity ──────────

class TestInter17EquityBondAttributes:

    @pytest.mark.parametrize("nature", ["Renta Variable", "Alternativo", "Estructurado"])
    def test_credit_quality_corrected_to_not_applicable(self, nature):
        rec = _base(Fund_Nature=nature, Credit_Quality="Investment Grade")
        result = _run(rec)
        assert result["corrected_record"]["Credit_Quality"] == "Not Applicable"

    @pytest.mark.parametrize("nature", ["Renta Variable", "Alternativo", "Estructurado"])
    def test_duration_profile_corrected_to_not_applicable(self, nature):
        rec = _base(Fund_Nature=nature, Duration_Profile="Short")
        result = _run(rec)
        assert result["corrected_record"]["Duration_Profile"] == "Not Applicable"

    @pytest.mark.parametrize("nature", ["Renta Variable", "Alternativo", "Estructurado"])
    def test_already_not_applicable_untouched(self, nature):
        rec = _base(Fund_Nature=nature,
                    Credit_Quality="Not Applicable",
                    Duration_Profile="Not Applicable")
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Credit_Quality"] == "Not Applicable"
        assert cr["Duration_Profile"] == "Not Applicable"
        cq_warns = [w for w in result["warnings"] if "CreditQuality" in w["rule"]]
        dp_warns = [w for w in result["warnings"] if "DurationProfile" in w["rule"]]
        assert cq_warns == [] and dp_warns == []

    def test_fi_fund_credit_quality_preserved(self):
        """RF Flexible keeps its Credit_Quality — INTER-17 must not fire for FI natures."""
        rec = _base(Fund_Nature="Renta Fija Flexible", Credit_Quality="High Yield")
        result = _run(rec)
        assert result["corrected_record"]["Credit_Quality"] == "High Yield"

    def test_fi_fund_duration_profile_preserved(self):
        rec = _base(Fund_Nature="Renta Fija Corto Plazo", Duration_Profile="Short")
        result = _run(rec)
        assert result["corrected_record"]["Duration_Profile"] == "Short"

    def test_mixtos_excluded_from_correction(self):
        """Mixtos has FI component — INTER-17 must NOT fire for Mixtos."""
        rec = _base(Fund_Nature="Mixtos", Credit_Quality="Mixed",
                    Duration_Profile="Not Applicable")
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Credit_Quality"] == "Mixed"
        assert cr["Duration_Profile"] == "Not Applicable"

    def test_warn_emitted_for_credit_quality_correction(self):
        rec = _base(Fund_Nature="Renta Variable",
                    Credit_Quality="Investment Grade",
                    Duration_Profile="Ultra-Short")
        result = _run(rec)
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "CreditQuality-Nature" in warn_rules
        assert "DurationProfile-Nature" in warn_rules


# ─── MIG-1: Distribution_Frequency='BIANNUAL' → 'Semi-Annual' ────────────────

class TestMig1DistributionFrequency:

    def test_biannual_migrated(self):
        rec = _base(Distribution_Frequency="BIANNUAL")
        result = _run(rec)
        assert result["corrected_record"]["Distribution_Frequency"] == "Semi-Annual"

    def test_valid_values_untouched(self):
        for val in ["Monthly", "Quarterly", "Semi-Annual", "Annual"]:
            rec = _base(Distribution_Frequency=val)
            result = _run(rec)
            assert result["corrected_record"]["Distribution_Frequency"] == val

    def test_warn_emitted(self):
        result = _run(_base(Distribution_Frequency="BIANNUAL"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Allowed-Values:Distribution_Frequency" in warn_rules


# ─── MIG-2: Hedging_Policy='PARTIAL' → 'Partially Hedged' ────────────────────

class TestMig2HedgingPolicy:

    def test_partial_migrated(self):
        rec = _base(Hedging_Policy="PARTIAL")
        result = _run(rec)
        assert result["corrected_record"]["Hedging_Policy"] == "Partially Hedged"

    def test_valid_values_untouched(self):
        for val in ["Hedged", "Unhedged", "Partially Hedged"]:
            rec = _base(Hedging_Policy=val)
            result = _run(rec)
            assert result["corrected_record"]["Hedging_Policy"] == val

    def test_warn_emitted(self):
        result = _run(_base(Hedging_Policy="PARTIAL"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Allowed-Values:Hedging_Policy" in warn_rules


# ─── MIG-3: Derivatives_Usage legacy values ───────────────────────────────────

class TestMig3DerivativesUsage:

    def test_no_migrated_to_none(self):
        result = _run(_base(Derivatives_Usage="NO"))
        assert result["corrected_record"]["Derivatives_Usage"] == "None"

    def test_limited_migrated_to_hedging_only(self):
        result = _run(_base(Derivatives_Usage="LIMITED"))
        assert result["corrected_record"]["Derivatives_Usage"] == "Hedging Only"

    def test_yes_not_auto_corrected(self):
        """'YES' is ambiguous — must NOT be auto-corrected, only warned."""
        result = _run(_base(Derivatives_Usage="YES"))
        assert result["corrected_record"]["Derivatives_Usage"] == "YES"

    def test_yes_emits_warn(self):
        result = _run(_base(Derivatives_Usage="YES"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Allowed-Values:Derivatives_Usage" in warn_rules

    def test_valid_values_untouched(self):
        for val in ["None", "Hedging Only", "Investment", "Both"]:
            result = _run(_base(Derivatives_Usage=val))
            assert result["corrected_record"]["Derivatives_Usage"] == val

    @pytest.mark.parametrize("legacy,expected", [
        ("NO", "None"), ("LIMITED", "Hedging Only"),
    ])
    def test_migration_warn_emitted(self, legacy, expected):
        result = _run(_base(Derivatives_Usage=legacy))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Allowed-Values:Derivatives_Usage" in warn_rules


# ─── MIG-4: Liquidity_Profile legacy codes ────────────────────────────────────

class TestMig4LiquidityProfile:

    def test_t1_migrated_to_daily(self):
        result = _run(_base(Liquidity_Profile="T1"))
        assert result["corrected_record"]["Liquidity_Profile"] == "Daily"

    def test_t5_not_auto_corrected(self):
        """T5 mapping is unclear — must NOT be auto-corrected."""
        result = _run(_base(Liquidity_Profile="T5"))
        assert result["corrected_record"]["Liquidity_Profile"] == "T5"

    def test_t5_emits_warn(self):
        result = _run(_base(Liquidity_Profile="T5"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Allowed-Values:Liquidity_Profile" in warn_rules

    def test_valid_values_untouched(self):
        for val in ["Daily", "Weekly", "Bi-Weekly", "Monthly", "Not Applicable"]:
            result = _run(_base(Liquidity_Profile=val))
            assert result["corrected_record"]["Liquidity_Profile"] == val

    def test_t1_migration_warn_emitted(self):
        result = _run(_base(Liquidity_Profile="T1"))
        warn_rules = [w["rule"] for w in result["warnings"]]
        assert "Allowed-Values:Liquidity_Profile" in warn_rules
