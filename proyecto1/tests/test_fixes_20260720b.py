# test_fixes_20260720b.py
# R-7: no pipeline.py / core.io imports.
# Covers:
#   FIX-B6-TBILL-1     — government cash/T-Bill benchmark suppresses SC-H2
#   FIX-B6-MS-CAT-1    — Morningstar HY category vs IG fund → INFO (warnings) not WARN
#   FIX-B3-CRITICAL-MATERIALS-1 — (Technology & Innovation, Materials) benign sector pair

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from proyecto1.core.classify_utils import (
    validate_all_semantic_consistency,
    BMK_SECTOR_BENIGN_PAIRS,
)


# ── Shared minimal fund record builders ──────────────────────────────────────

def _rfc_hy_record():
    """Minimal RFC + High Yield fund record (NB SHORT DURATION EM DEBT profile)."""
    return {
        "Fund_Nature":     "Renta Fija Corto Plazo",
        "Credit_Quality":  "High Yield",
        "SRRI":            2,
        "Geography":       "Global",
        "Fund_Type":       "UCITS",
        "Hedging_Policy":  "EUR Hedged",
        "Sector_Focus":    None,
    }


def _rfc_ig_record():
    """Minimal RFC + Investment Grade fund record (ARCANO LOWVO profile)."""
    return {
        "Fund_Nature":     "Renta Fija Corto Plazo",
        "Credit_Quality":  "Investment Grade",
        "SRRI":            2,
        "Geography":       "Global",
        "Fund_Type":       "UCITS",
        "Hedging_Policy":  None,
        "Sector_Focus":    None,
    }


def _rv_tech_record():
    """Minimal RV + Technology sector (DWS CRITICAL TECHNOL profile)."""
    return {
        "Fund_Nature":     "Renta Variable",
        "Credit_Quality":  None,
        "SRRI":            6,
        "Geography":       "Global",
        "Fund_Type":       "UCITS",
        "Hedging_Policy":  None,
        "Sector_Focus":    "Technology & Innovation",
    }


# ── FIX-B6-TBILL-1 ───────────────────────────────────────────────────────────

def test_tbill_benchmark_suppresses_sc_h2_on_hy_fund():
    """ICE BofA 3M US T-Bill is a cash-hurdle benchmark; SC-H2 must not fire
    even when fund Credit_Quality=High Yield (NB SHORT DURATION EM DEBT case)."""
    result = validate_all_semantic_consistency(
        _rfc_hy_record(),
        ext_asset_class="Rate",
        ext_role="asset_proxy",
        ext_benchmark_name="ICE BofA 3M US Treasury Bill",
        ext_confidence="HIGH",
    )
    sc_h2_errors = [
        e for e in result.get("critical_errors", [])
        if e.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    sc_h2_warns = [
        w for w in result.get("warnings", [])
        if w.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    assert not sc_h2_errors, (
        f"FIX-B6-TBILL-1: T-Bill benchmark should not trigger SC-H2 critical_error; "
        f"got: {sc_h2_errors}"
    )
    assert not sc_h2_warns, (
        f"FIX-B6-TBILL-1: T-Bill benchmark should not trigger SC-H2 warning; "
        f"got: {sc_h2_warns}"
    )


def test_overnight_rate_suppresses_sc_h2():
    """ESTR overnight rate benchmark (another cash-hurdle form) also suppressed."""
    result = validate_all_semantic_consistency(
        _rfc_hy_record(),
        ext_asset_class="Rate",
        ext_role="asset_proxy",
        ext_benchmark_name="Euro Short-Term Rate (€STR)",
        ext_confidence="HIGH",
    )
    sc_h2_all = [
        e for e in result.get("critical_errors", []) + result.get("warnings", [])
        if e.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    assert not sc_h2_all, (
        f"FIX-B6-TBILL-1: ESTR overnight rate should not trigger SC-H2; got: {sc_h2_all}"
    )


def test_genuine_hy_benchmark_still_fires_sc_h2_on_ig_fund():
    """Regression: a genuine HY benchmark (ICE BofA HY) vs IG fund still fires SC-H2."""
    result = validate_all_semantic_consistency(
        _rfc_ig_record(),
        ext_asset_class="Fixed Income",
        ext_role="asset_proxy",
        ext_benchmark_name="ICE BofA Global High Yield",
        ext_confidence="HIGH",
    )
    sc_h2_fires = [
        e for e in result.get("critical_errors", []) + result.get("warnings", [])
        if e.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    assert sc_h2_fires, (
        "Regression: genuine HY benchmark vs IG fund should still fire SC-H2"
    )


# ── FIX-B6-MS-CAT-1 ──────────────────────────────────────────────────────────

def test_morningstar_hy_cat_vs_ig_fund_is_info_not_warn():
    """Morningstar Eurozone HY Bond category vs IG fund → SC-H2 in warnings (INFO),
    NOT in critical_errors (WARN DQ).  ARCANO LOWVO case."""
    result = validate_all_semantic_consistency(
        _rfc_ig_record(),
        ext_asset_class="Fixed Income",
        ext_role="asset_proxy",
        ext_benchmark_name="Morningstar Eurozone HY Bond",
        ext_confidence="HIGH",
    )
    sc_h2_critical = [
        e for e in result.get("critical_errors", [])
        if e.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    sc_h2_info = [
        w for w in result.get("warnings", [])
        if w.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    assert not sc_h2_critical, (
        f"FIX-B6-MS-CAT-1: Morningstar HY category vs IG fund should NOT be "
        f"critical_error; got: {sc_h2_critical}"
    )
    assert sc_h2_info, (
        "FIX-B6-MS-CAT-1: Morningstar HY category vs IG fund should be in "
        "warnings (INFO DQ level) — signal survives, severity downgraded"
    )


def test_morningstar_ig_vs_hy_fund_still_critical():
    """Morningstar IG category vs HY fund is NOT downgraded — only the HY-vs-IG
    direction is Morningstar-category-specific; the reverse is a genuine conflict."""
    result = validate_all_semantic_consistency(
        _rfc_hy_record(),
        ext_asset_class="Fixed Income",
        ext_role="asset_proxy",
        ext_benchmark_name="Morningstar Euro Corporate Bond",
        ext_confidence="HIGH",
    )
    sc_h2_critical = [
        e for e in result.get("critical_errors", [])
        if e.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    assert sc_h2_critical, (
        "Regression: Morningstar IG category vs HY fund should remain critical_error"
    )


def test_non_morningstar_hy_benchmark_vs_ig_still_critical():
    """A non-Morningstar HY benchmark vs IG fund stays critical (e.g. ICE BofA)."""
    result = validate_all_semantic_consistency(
        _rfc_ig_record(),
        ext_asset_class="Fixed Income",
        ext_role="asset_proxy",
        ext_benchmark_name="ICE BofA European High Yield",
        ext_confidence="HIGH",
    )
    sc_h2_critical = [
        e for e in result.get("critical_errors", [])
        if e.get("rule") == "Benchmark-Credit-SC-H2"
    ]
    assert sc_h2_critical, (
        "Regression: non-Morningstar HY benchmark vs IG fund should remain critical_error"
    )


# ── FIX-B3-CRITICAL-MATERIALS-1 ──────────────────────────────────────────────

def test_technology_materials_is_in_benign_pairs():
    """('Technology & Innovation', 'Materials') must be in BMK_SECTOR_BENIGN_PAIRS.
    DWS CRITICAL TECHNOL — fund sector=Tech, Morningstar bmk=Global Basic Materials."""
    pair = frozenset({"Technology & Innovation", "Materials"})
    assert pair in BMK_SECTOR_BENIGN_PAIRS, (
        f"FIX-B3-CRITICAL-MATERIALS-1: {pair} missing from BMK_SECTOR_BENIGN_PAIRS; "
        f"got: {sorted(str(s) for s in BMK_SECTOR_BENIGN_PAIRS)}"
    )


def test_existing_benign_pairs_not_removed():
    """Regression: pre-existing benign pairs are still present."""
    expected = [
        frozenset({"Inflation-Linked", "Inflation"}),
        frozenset({"Real Assets", "Real Estate"}),
        frozenset({"Healthcare & Life Sciences", "Technology & Innovation"}),
        frozenset({"Energy & Resources", "Technology & Innovation"}),
    ]
    for pair in expected:
        assert pair in BMK_SECTOR_BENIGN_PAIRS, (
            f"Regression: pre-existing pair {pair} was removed from BMK_SECTOR_BENIGN_PAIRS"
        )
