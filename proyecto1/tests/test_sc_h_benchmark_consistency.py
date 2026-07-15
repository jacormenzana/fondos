# proyecto1/tests/test_sc_h_benchmark_consistency.py
# -*- coding: utf-8 -*-
"""
Unit tests for SC-H Benchmark Consistency cluster (2026-07-15).

Tests cover:
  SC-H vocabulary (BMK_CONSISTENT/TOLERATED, bmk_tok_credit, bmk_geography, ...)
  SC-H1 nature check  (activate existing INTER-18 / validate_benchmark_nature)
  SC-H2 credit check  (new: Credit_Quality ↔ benchmark credit pole)
  SC-H3 geography check (new: Geography ↔ benchmark geography)
  Confidence down-weighting (LOW/MEDIUM → INFO level)
  Benign-pair suppression and EM-sovereign carve-out

R-7: no imports from pipeline.py or core.io.
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
    # SC-H vocabulary
    BMK_CONSISTENT,
    BMK_TOLERATED,
    BMK_BENIGN_SOURCE_PAIRS,
    BMK_GEO_BENIGN_PAIRS,
    BMK_SECTOR_BENIGN_PAIRS,
    bmk_tok_credit,
    bmk_tok_duration,
    bmk_tok_cap,
    bmk_geography,
    bmk_sector,
    bmk_severity_nature,
    # Master validator (SC-H rules live inside it)
    validate_all_semantic_consistency,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _rec(nature: str, credit: str = None, geography: str = None) -> dict:
    """Minimal fund_master record for SC-H testing."""
    return {
        "Fund_Nature":    nature,
        "Credit_Quality": credit,
        "Geography":      geography,
    }


def _h2_codes(val_result: dict) -> list[str]:
    """Collect SC-H2 rule codes from critical_errors + warnings."""
    codes = []
    for item in val_result.get("critical_errors", []):
        if "SC-H2" in item.get("message", "") or "H2" in item.get("rule", ""):
            codes.append("H2-critical")
    for item in val_result.get("warnings", []):
        if "SC-H2" in item.get("message", "") or "H2" in item.get("rule", ""):
            codes.append("H2-warn")
    return codes


def _h3_codes(val_result: dict) -> list[str]:
    codes = []
    for item in val_result.get("warnings", []):
        if "SC-H3" in item.get("message", "") or "H3" in item.get("rule", ""):
            codes.append("H3-warn")
    return codes


# ─── 1. Vocabulary sanity ─────────────────────────────────────────────────────

class TestBmkVocabulary:
    """BMK_CONSISTENT / BMK_TOLERATED have the correct membership."""

    def test_rv_consistent_equity_only(self):
        assert "Equity" in BMK_CONSISTENT["Renta Variable"]
        assert "Fixed Income" not in BMK_CONSISTENT["Renta Variable"]

    def test_monetario_consistent_money_market_and_rate(self):
        assert BMK_CONSISTENT["Monetario"] == frozenset({"Money Market", "Rate"})

    def test_mixtos_consistent_includes_equity_and_fi(self):
        assert "Equity" in BMK_CONSISTENT["Mixtos"]
        assert "Fixed Income" in BMK_CONSISTENT["Mixtos"]

    def test_rv_tolerated_commodity_and_mixed(self):
        assert "Commodity" in BMK_TOLERATED["Renta Variable"]
        assert "Mixed" in BMK_TOLERATED["Renta Variable"]

    def test_benign_source_pairs_rate_fi(self):
        assert frozenset({"Rate", "Fixed Income"}) in BMK_BENIGN_SOURCE_PAIRS

    def test_geo_benign_includes_japan_asia(self):
        assert frozenset({"Japan", "Asia-Pacific"}) in BMK_GEO_BENIGN_PAIRS

    def test_sector_benign_includes_inflation_linked(self):
        assert frozenset({"Inflation-Linked", "Inflation"}) in BMK_SECTOR_BENIGN_PAIRS


# ─── 2. Token extractors ──────────────────────────────────────────────────────

class TestBmkTokCredit:
    @pytest.mark.parametrize("name,expected", [
        ("JPM Global High Yield Bond",      "High Yield"),
        ("iBoxx EUR Liquid High Yield",     "High Yield"),
        ("Bloomberg Barclays US HY Index",  "High Yield"),   # ' hy ' token
        ("ICE BofA Euro Corporate Bond",    "Corporate"),
        ("JPM GBI-EM Global Diversified",   "Government"),   # sovereign token
        ("Bloomberg Euro Aggregate Bond",   "Aggregate"),
        ("Markit iBoxx Investment Grade",   "Investment Grade"),
        ("Citi WorldBIG Inflation Linked",  "Inflation-Linked"),
        ("MSCI World Equity Index",         None),           # equity → no credit token
    ])
    def test_credit_extraction(self, name, expected):
        assert bmk_tok_credit(name.lower()) == expected


class TestBmkTokDuration:
    @pytest.mark.parametrize("name,expected", [
        ("iBoxx EUR 1-3y Government",    "Short"),
        ("iBoxx EUR Short Term Bond",    "Short"),
        ("iBoxx EUR 7-10y Bond",         "Long"),
        ("Ultrashort Duration EUR",      "Ultra-Short"),
        ("MSCI World Equity",            None),
    ])
    def test_duration_extraction(self, name, expected):
        assert bmk_tok_duration(name.lower()) == expected


class TestBmkGeography:
    @pytest.mark.parametrize("name,expected", [
        ("MSCI Europe Index",            "Europe"),
        ("MSCI Emerging Markets Index",  "Global"),          # Emergentes→Global
        ("MSCI USA",                     "North America"),
        ("MSCI Japan",                   "Japan"),
        ("MSCI Asia Pacific",            "Asia-Pacific"),
        ("MSCI World (global fund)",     "Global"),
        ("iBoxx EUR Corporate",          None),              # no geo token
    ])
    def test_geography_extraction(self, name, expected):
        assert bmk_geography(name) == expected


# ─── 3. bmk_severity_nature (single source of truth) ─────────────────────────

class TestBmkSeverityNature:
    def test_equity_fund_equity_benchmark_ok(self):
        assert bmk_severity_nature("Renta Variable", "Equity", "HIGH") == "OK"

    def test_equity_fund_fi_benchmark_critical(self):
        assert bmk_severity_nature("Renta Variable", "Fixed Income", "HIGH") == "CRITICAL"

    def test_equity_fund_fi_benchmark_low_conf_warn(self):
        assert bmk_severity_nature("Renta Variable", "Fixed Income", "LOW") == "WARN"

    def test_equity_fund_commodity_benchmark_info(self):
        assert bmk_severity_nature("Renta Variable", "Commodity", "HIGH") == "INFO"

    def test_fi_flexible_equity_benchmark_critical(self):
        assert bmk_severity_nature("Renta Fija Flexible", "Equity", "HIGH") == "CRITICAL"

    def test_monetario_rate_ok(self):
        assert bmk_severity_nature("Monetario", "Rate", "HIGH") == "OK"

    def test_unknown_nature_returns_info_for_any_ac(self):
        # Unknown nature → empty consistent + tolerated → CRITICAL, but MEDIUM → WARN
        sev = bmk_severity_nature("UnknownNature", "Equity", "MEDIUM")
        assert sev == "WARN"


# ─── 4. SC-H1 — nature check (activate INTER-18) ─────────────────────────────

class TestScH1Nature:
    """INTER-18 / validate_benchmark_nature is now fed with real data."""

    def test_equity_fund_fi_benchmark_warns(self):
        """Renta Variable + Fixed Income benchmark → SC-H1 warning."""
        rec = _rec("Renta Variable")
        result = validate_all_semantic_consistency(
            rec,
            ext_asset_class="Fixed Income",
            ext_role="asset_proxy",
        )
        rules = [w["rule"] for w in result["warnings"]]
        assert "Benchmark-Nature" in rules

    def test_equity_fund_equity_benchmark_ok(self):
        """Consistent combination → no benchmark warning."""
        rec = _rec("Renta Variable")
        result = validate_all_semantic_consistency(
            rec,
            ext_asset_class="Equity",
            ext_role="asset_proxy",
        )
        rules = [w["rule"] for w in result["warnings"]]
        assert "Benchmark-Nature" not in rules

    def test_hurdle_rate_role_suppressed(self):
        """benchmark_role='hurdle_rate' must never trigger SC-H1."""
        rec = _rec("Renta Variable")
        result = validate_all_semantic_consistency(
            rec,
            ext_asset_class="Rate",
            ext_role="hurdle_rate",
        )
        rules = [w["rule"] for w in result["warnings"]]
        assert "Benchmark-Nature" not in rules

    def test_none_asset_class_no_warn(self):
        rec = _rec("Renta Variable")
        result = validate_all_semantic_consistency(rec, ext_asset_class=None)
        rules = [w["rule"] for w in result["warnings"]]
        assert "Benchmark-Nature" not in rules


# ─── 5. SC-H2 — Credit_Quality ↔ benchmark credit pole ──────────────────────

class TestScH2Credit:
    """Known B6 cases: IG fund with HY benchmark → SC-H2 fires."""

    def test_ig_fund_hy_benchmark_high_conf_critical(self):
        """AXA/MS pattern: IG credit quality + HY benchmark at HIGH confidence
        → critical_error (→ WARN DQ level)."""
        rec = _rec("Renta Fija Corto Plazo", credit="Investment Grade")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="JPM Global High Yield Bond Index",
            ext_confidence="HIGH",
        )
        assert "H2-critical" in _h2_codes(result), (
            "IG fund with HY benchmark at HIGH confidence should emit critical SC-H2"
        )

    def test_ig_fund_hy_benchmark_low_conf_info(self):
        """LOW confidence benchmark → demoted to INFO (warnings list)."""
        rec = _rec("Renta Fija Corto Plazo", credit="Investment Grade")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="JPM Global High Yield Bond Index",
            ext_confidence="LOW",
        )
        assert "H2-warn" in _h2_codes(result)
        assert "H2-critical" not in _h2_codes(result)

    def test_hy_fund_hy_benchmark_no_sc_h2(self):
        """HY fund with HY benchmark → no conflict."""
        rec = _rec("Renta Fija Flexible", credit="High Yield")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="ICE BofA Euro High Yield Index",
            ext_confidence="HIGH",
        )
        assert not _h2_codes(result), "Consistent HY↔HY should not fire SC-H2"

    def test_fi_fund_ig_benchmark_hy_credit_warns(self):
        """Reverse case: fund says HY but benchmark is IG → also a conflict."""
        rec = _rec("Renta Fija Flexible", credit="High Yield")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="Bloomberg Euro Aggregate Bond Index",
            ext_confidence="HIGH",
        )
        assert _h2_codes(result), "HY fund with IG/Aggregate benchmark should fire SC-H2"

    def test_em_sovereign_suppression(self):
        """EM sovereign fund correctly HY, benchmark says 'Government sovereign EM':
        the EM-sovereign carve-out must suppress SC-H2."""
        rec = _rec("Renta Fija Flexible", credit="High Yield")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="JPM EMBI Global Diversified Sovereign EM Index",
            ext_confidence="HIGH",
        )
        assert not _h2_codes(result), (
            "EM sovereign benchmark should not fire SC-H2 against a HY EM fund"
        )

    def test_equity_nature_not_checked(self):
        """SC-H2 only applies to FI natures; equity fund → no SC-H2."""
        rec = _rec("Renta Variable", credit=None)
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="JPM Global High Yield Bond Index",
            ext_confidence="HIGH",
        )
        assert not _h2_codes(result)

    def test_hurdle_rate_role_suppressed_h2(self):
        """hurdle_rate role → SC-H2 must be suppressed."""
        rec = _rec("Renta Fija Corto Plazo", credit="Investment Grade")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="JPM Global High Yield Bond Index",
            ext_role="hurdle_rate",
            ext_confidence="HIGH",
        )
        assert not _h2_codes(result)

    def test_no_benchmark_name_no_sc_h2(self):
        """ext_benchmark_name=None → SC-H2 silent (no data to check against)."""
        rec = _rec("Renta Fija Corto Plazo", credit="Investment Grade")
        result = validate_all_semantic_consistency(rec, ext_benchmark_name=None)
        assert not _h2_codes(result)

    # ── Known fixed cases from B6 audit ──────────────────────────────────────

    @pytest.mark.parametrize("name,credit,nature", [
        # AXA WF US HIGH YIE: IG credit, HY benchmark
        ("ICE BofA US High Yield Constrained", "Investment Grade", "Renta Fija Corto Plazo"),
        # UBS Floating Rate Income: IG credit, High Yield underlying
        ("ICE BofA BB-B Rated Non-Financial Developed Markets HY", "Investment Grade",
         "Renta Fija Corto Plazo"),
    ])
    def test_known_b6_cases_fire_sc_h2(self, name, credit, nature):
        """Regression: funds from the B6 audit must fire SC-H2 until reclassified."""
        rec = _rec(nature, credit=credit)
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name=name,
            ext_confidence="HIGH",
        )
        assert _h2_codes(result), f"Known B6 case should fire SC-H2: {name!r}"


# ─── 6. SC-H3 — Geography ↔ benchmark geography ──────────────────────────────

class TestScH3Geography:
    """Geography in fund_master vs geography derived from benchmark name."""

    def test_europe_fund_us_benchmark_warns(self):
        """Europe fund benchmarked to US index → conflict → SC-H3 warning."""
        rec = _rec("Renta Variable", geography="Europe")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="S&P 500 Index (US Large Cap)",
            ext_confidence="HIGH",
        )
        assert _h3_codes(result), "Europe fund with US benchmark should fire SC-H3"

    def test_europe_fund_europe_benchmark_ok(self):
        """Consistent Europe↔Europe → no SC-H3."""
        rec = _rec("Renta Variable", geography="Europe")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="MSCI Europe Net Return Index",
            ext_confidence="HIGH",
        )
        assert not _h3_codes(result)

    def test_japan_fund_asia_pacific_benchmark_benign(self):
        """Japan fund benchmarked to Asia-Pacific → benign pair, no SC-H3."""
        rec = _rec("Renta Variable", geography="Japan")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="MSCI Asia Pacific Index",
            ext_confidence="HIGH",
        )
        assert not _h3_codes(result), (
            "Japan ↔ Asia-Pacific is a benign pair; SC-H3 should be suppressed"
        )

    def test_global_fund_no_sc_h3(self):
        """Global fund (fund_master sentinel) → SC-H3 suppressed."""
        rec = _rec("Mixtos", geography="Global")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="MSCI Europe Index",
            ext_confidence="HIGH",
        )
        assert not _h3_codes(result)

    def test_no_geography_no_sc_h3(self):
        """fund_master Geography=None → SC-H3 suppressed (no data)."""
        rec = _rec("Renta Variable", geography=None)
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="MSCI USA Index",
            ext_confidence="HIGH",
        )
        assert not _h3_codes(result)

    def test_hurdle_rate_suppressed_h3(self):
        """hurdle_rate role → SC-H3 suppressed."""
        rec = _rec("Renta Variable", geography="Europe")
        result = validate_all_semantic_consistency(
            rec,
            ext_benchmark_name="S&P 500 Index",
            ext_role="hurdle_rate",
            ext_confidence="HIGH",
        )
        assert not _h3_codes(result)


# ─── 7. No-regression: backward-compatibility ─────────────────────────────────

class TestBackwardCompatibility:
    """validate_all_semantic_consistency still works with the old 1-arg signature."""

    def test_one_arg_signature_unchanged(self):
        """Calling with only fund_record (old callers) must not error."""
        rec = _rec("Renta Variable")
        result = validate_all_semantic_consistency(rec)
        assert "is_valid" in result
        assert "corrected_record" in result

    def test_two_arg_old_style(self):
        """Old style: fund_record + ext_asset_class positional."""
        rec = _rec("Renta Variable")
        result = validate_all_semantic_consistency(rec, "Equity")
        assert "is_valid" in result
