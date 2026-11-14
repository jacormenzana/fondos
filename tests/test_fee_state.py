# -*- coding: utf-8 -*-
"""Package H, Slice 0: the pure fee-state definition, the accuracy gate and the read-only baseline script's core (no database, R-7)."""
from __future__ import annotations

import importlib.util
import itertools
import math
from pathlib import Path

import pytest

from shared.fee_state import (ENTRY_STATES, EXIT_STATES, PAIR_TOLERANCE_PP, accuracy_decision, derive_fee_state, pair_conflict, wilson_interval)

ROOT = Path(__file__).resolve().parents[1]


def _baseline():
    spec = importlib.util.spec_from_file_location("fee_state_baseline", ROOT / "scripts" / "audit" / "p1" / "fee_state_baseline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── derive_fee_state: the full truth table of (point, ceiling) ────────────────────────────────────────────────────

@pytest.mark.parametrize("point,ceiling,entry,exit_", [
    (None, None, "NOT_FOUND", "NOT_FOUND"),
    (0.0, None, "ZERO_CONFIRMED", "ZERO_CONFIRMED"),
    (0.0, 0.0, "ZERO_CONFIRMED", "ZERO_CONFIRMED"),
    (0.0, 5.0, "ZERO_CONFIRMED", "ZERO_CONFIRMED"),          # zero applied with a declared maximum is legitimate (IE000D08VXP3)
    (0.05, None, "EXTRACTED", "EXTRACTED"),
    (0.05, 5.0, "EXTRACTED", "EXTRACTED"),
    (None, 5.0, "ENTRY_CONDITIONAL", "EXIT_CONDITIONAL"),     # a maximum with no applied fee
    (None, 0.0, "ZERO_CONFIRMED", "ZERO_CONFIRMED"),          # a maximum of zero is a confirmed zero
])
def test_truth_table(point, ceiling, entry, exit_):
    assert derive_fee_state(point, ceiling, "entry") == entry
    assert derive_fee_state(point, ceiling, "exit") == exit_


def test_every_derived_state_belongs_to_its_vocabulary():
    values = (None, 0.0, 0.0001, 0.05, 25.0)
    for p, c in itertools.product(values, values):
        assert derive_fee_state(p, c, "entry") in ENTRY_STATES
        assert derive_fee_state(p, c, "exit") in EXIT_STATES


def test_negative_and_nan_are_read_as_null():
    assert derive_fee_state(-0.01, None) == "NOT_FOUND"
    assert derive_fee_state(-0.01, 5.0) == "ENTRY_CONDITIONAL"
    assert derive_fee_state(None, -1.0) == "NOT_FOUND"
    assert derive_fee_state(float("nan"), None) == "NOT_FOUND"
    assert derive_fee_state("x", None) == "NOT_FOUND"


def test_side_must_be_entry_or_exit():
    with pytest.raises(ValueError):
        derive_fee_state(None, None, "middle")


# ── pair_conflict: the applied fee above its own maximum ──────────────────────────────────────────────────────────

def test_pair_conflict_cases():
    assert pair_conflict(0.0525, 0.0) is True                 # LU1881514001: point 5.25 %, ceiling 0
    assert pair_conflict(0.05, 3.0) is True                   # LU0329430127: point 5 %, ceiling 3
    assert pair_conflict(0.05, 5.0) is False                  # equal
    assert pair_conflict(0.03, 5.0) is False                  # a maximum above the applied fee
    assert pair_conflict(0.0, 5.0) is False
    assert pair_conflict(None, 0.0) is False and pair_conflict(0.05, None) is False   # a missing number cannot conflict
    assert pair_conflict(-0.05, 0.0) is False                 # a negative number is invalid, never a conflict


def test_pair_conflict_tolerance_is_the_shared_cost_pair_tolerance():
    assert PAIR_TOLERANCE_PP == 0.005
    assert pair_conflict(0.05004, 5.0) is False               # within half a published unit
    assert pair_conflict(0.0502, 5.0) is True


# ── resolve_pair: the precedence of the plan (R6.4, R7.1 #2) ──────────────────────────────────────────────────────

from shared.fee_state import (PairEvidence, WARN_CONFLICT, WARN_NEGATIVE, WARN_STORED_POINT_CLEARED, fee_state_sql, resolve_pair)  # noqa: E402


def _flags(result):
    return {w.split(":")[0] for w in result.warnings}


def test_stale_point_zero_next_to_a_new_ceiling_is_cleared_fnd0406():
    """The 111 funds of FND-0406: a stored 0.0 from an old parser, the KID prints only an amount, the extractor now finds 4.96 %."""
    r = resolve_pair(PairEvidence(extractor_ceiling_pct=4.96), stored_point=0.0, stored_ceiling_pct=None)
    assert (r.point, r.ceiling_pct, r.source) == (None, 4.96, "this_run")
    assert derive_fee_state(r.point, r.ceiling_pct) == "ENTRY_CONDITIONAL"
    assert WARN_STORED_POINT_CLEARED in _flags(r)
    # a changed document is new evidence, not a contradiction: no warning
    assert resolve_pair(PairEvidence(extractor_ceiling_pct=4.96), 0.0, None, same_document=False).warnings == ()


def test_a_stored_point_that_the_current_ceiling_corroborates_survives():
    """The 257 funds: stored point 5.25 %, the extractor ceiling 5.25, the parser silent now."""
    r = resolve_pair(PairEvidence(extractor_ceiling_pct=5.25), stored_point=0.0525, stored_ceiling_pct=5.25)
    assert (r.point, r.ceiling_pct) == (0.0525, 5.25) and r.warnings == ()
    assert derive_fee_state(r.point, r.ceiling_pct) == "EXTRACTED"


def test_a_stale_ceiling_cannot_survive_next_to_current_evidence_f1b():
    """ZERO_CONFIRMED with a ceiling equal to the transaction cost (an old extractor's): the parser reads the zero, the extractor emits nothing."""
    r = resolve_pair(PairEvidence(parser_point=0.0), stored_point=0.0, stored_ceiling_pct=0.10)
    assert (r.point, r.ceiling_pct) == (0.0, None)
    assert derive_fee_state(r.point, r.ceiling_pct) == "ZERO_CONFIRMED"


def test_negation_beats_a_positive_ceiling_ie000d08vxp3():
    r = resolve_pair(PairEvidence(negation=True, extractor_ceiling_pct=5.0), stored_point=None, stored_ceiling_pct=None)
    assert (r.point, r.ceiling_pct, r.warnings) == (0.0, 5.0, ())
    assert derive_fee_state(r.point, r.ceiling_pct) == "ZERO_CONFIRMED"


def test_a_positive_parser_point_beats_a_negation():
    r = resolve_pair(PairEvidence(parser_point=0.0525, negation=True, extractor_ceiling_pct=0.0), None, None)
    assert r.point == 0.0525 and r.ceiling_pct == 0.0
    assert _flags(r) == {WARN_CONFLICT}        # LU1881514001 cluster: kept as it is, reported, never auto-picked


def test_no_evidence_keeps_the_stored_pair_whole():
    r = resolve_pair(PairEvidence(), stored_point=0.03, stored_ceiling_pct=3.0)
    assert (r.point, r.ceiling_pct, r.source, r.warnings) == (0.03, 3.0, "stored", ())


def test_the_parsers_conditional_value_fills_a_missing_ceiling():
    r = resolve_pair(PairEvidence(parser_conditional=0.05), stored_point=None, stored_ceiling_pct=None)
    assert (r.point, r.ceiling_pct) == (None, 5.0)
    assert derive_fee_state(r.point, r.ceiling_pct) == "ENTRY_CONDITIONAL"
    # the extractor's own ceiling is preferred when it exists
    assert resolve_pair(PairEvidence(parser_conditional=0.05, extractor_ceiling_pct=4.0), None, None).ceiling_pct == 4.0


def test_conflict_keeps_both_values_and_reports():
    r = resolve_pair(PairEvidence(parser_point=0.05, extractor_ceiling_pct=3.0), None, None)      # IE00B3Q8M574: prints 5 %, ceiling 3
    assert (r.point, r.ceiling_pct) == (0.05, 3.0) and _flags(r) == {WARN_CONFLICT}


def test_negative_and_nan_evidence_is_invalid_and_reported():
    r = resolve_pair(PairEvidence(parser_point=-0.01), stored_point=0.03, stored_ceiling_pct=3.0)
    assert WARN_NEGATIVE in _flags(r) and (r.point, r.ceiling_pct, r.source) == (0.03, 3.0, "stored")   # nothing valid left: stored kept
    r = resolve_pair(PairEvidence(extractor_ceiling_pct=float("nan")), None, None)
    assert WARN_NEGATIVE in _flags(r) and (r.point, r.ceiling_pct) == (None, None)
    assert resolve_pair(PairEvidence(), stored_point=-1.0, stored_ceiling_pct=None).point is None     # a negative stored value is read as NULL


def test_a_zero_with_no_ceiling_and_current_evidence_stays_a_confirmed_zero():
    r = resolve_pair(PairEvidence(parser_point=0.0), None, None)
    assert (r.point, r.ceiling_pct) == (0.0, None) and derive_fee_state(r.point, r.ceiling_pct, "exit") == "ZERO_CONFIRMED"


def test_each_side_resolves_on_its_own_evidence():
    entry = resolve_pair(PairEvidence(parser_point=0.05), stored_point=None, stored_ceiling_pct=None)
    exit_ = resolve_pair(PairEvidence(), stored_point=0.01, stored_ceiling_pct=1.0)      # no evidence on the exit side: untouched
    assert entry.point == 0.05 and (exit_.point, exit_.ceiling_pct, exit_.source) == (0.01, 1.0, "stored")


# ── reconcile_fee_pairs: the one call the pipeline makes ──────────────────────────────────────────────────────────

from shared.fee_state import RESOLVED_KEY, reconcile_fee_pairs  # noqa: E402

NO_STORED_EVIDENCE = {"Entry_Fee_Pct": None, "Entry_Fee_Pct_Max": None, "Exit_Fee_Pct": None, "Exit_Fee_Pct_Max": None}


def test_without_a_full_cost_pass_the_stored_pair_is_left_whole():
    """A CACHED cycle: the parser ran (it always does) but the extractor did not, so the pair must not be written at all."""
    record = {"Entry_Fee_Pct": 0.05, "Exit_Fee_Pct": 0.01, "Fund_Name": "x"}
    issues = reconcile_fee_pairs(record, {"Entry_Fee_Pct": 0.05, "Exit_Fee_Pct": 0.01}, {"Entry_Fee_Pct": 0.03, "Entry_Fee_Pct_Max": 3.0, "Exit_Fee_Pct": 0.0,
                                 "Exit_Fee_Pct_Max": None}, full_cost_pass=False)
    assert issues == [] and record == {"Fund_Name": "x"}                    # the four pair columns are gone, nothing else touched, nothing marked


def test_a_new_fund_always_gets_its_pair_derived_even_without_a_full_pass():
    record = {"Entry_Fee_Pct": 0.05}
    reconcile_fee_pairs(record, {"Entry_Fee_Pct": 0.05, "Exit_Fee_Pct": None}, None, full_cost_pass=False)
    assert record["Entry_Fee_Pct"] == 0.05 and record["Entry_Fee_Pct_Max"] is None and record[RESOLVED_KEY] == {"entry": True, "exit": True}


def test_the_fnd0406_fund_gets_a_conditional_entry_and_the_exit_side_is_untouched():
    """Stored point 0.0 (an old parser), the extractor now prints 4.96 %, the parser finds no point; the exit side has no evidence at all."""
    record = {"Entry_Fee_Pct": None, "Entry_Fee_Pct_Max": 4.96, "Exit_Fee_Pct": None}
    stored = {"Entry_Fee_Pct": 0.0, "Entry_Fee_Pct_Max": None, "Exit_Fee_Pct": 0.0, "Exit_Fee_Pct_Max": None}
    issues = reconcile_fee_pairs(record, {"Entry_Fee_Pct": None, "Exit_Fee_Pct": None}, stored, full_cost_pass=True)
    assert (record["Entry_Fee_Pct"], record["Entry_Fee_Pct_Max"]) == (None, 4.96) and record[RESOLVED_KEY] == {"entry": True}
    assert "Exit_Fee_Pct" not in record and "Exit_Fee_Pct_Max" not in record            # no exit evidence: the stored exit pair stays whole
    assert [(code, level) for code, level, _status, _msg in issues] == [(WARN_STORED_POINT_CLEARED + "_ENTRY", "INFO")]
    assert "0.0" in issues[0][3] and issues[0][3].startswith("entry fee")


def test_a_conflict_is_a_warn_and_both_values_are_written():
    record = {"Entry_Fee_Pct": 0.0525, "Entry_Fee_Pct_Max": 0.0}
    issues = reconcile_fee_pairs(record, {"Entry_Fee_Pct": 0.0525}, {**NO_STORED_EVIDENCE}, full_cost_pass=True, negation={"entry": True})
    assert (record["Entry_Fee_Pct"], record["Entry_Fee_Pct_Max"]) == (0.0525, 0.0)
    assert [(c, lv) for c, lv, _s, _m in issues] == [(WARN_CONFLICT + "_ENTRY", "WARN")]


def test_a_ceiling_above_the_schema_bound_is_dropped_and_reported():
    record = {}
    issues = reconcile_fee_pairs(record, {"Entry_Fee_Conditional": 0.30}, {**NO_STORED_EVIDENCE}, full_cost_pass=True)     # a 30 % "conditional" fee is a parse error
    assert record["Entry_Fee_Pct_Max"] is None and record["Entry_Fee_Pct"] is None
    assert [c for c, _lv, _s, _m in issues] == ["FEE_CEILING_OUT_OF_RANGE_ENTRY"]


def test_the_exit_side_resolves_on_its_own_negation():
    record = {"Exit_Fee_Pct_Max": 1.0}
    reconcile_fee_pairs(record, {"Exit_Fee_Pct": None}, {**NO_STORED_EVIDENCE}, full_cost_pass=True, negation={"exit": True})
    assert (record["Exit_Fee_Pct"], record["Exit_Fee_Pct_Max"]) == (0.0, 1.0)


# ── the SQL rendering ─────────────────────────────────────────────────────────────────────────────────────────────

def test_fee_state_sql_is_a_plain_expression_and_rejects_foreign_names():
    sql = fee_state_sql("entry_fee_pct", "entry_fee_pct_max", "entry")
    assert sql.startswith("CASE") and "'ENTRY_CONDITIONAL'" in sql and "'NaN'::float8" in sql
    assert "'EXIT_CONDITIONAL'" in fee_state_sql("exit_fee_pct", "exit_fee_pct_max", "exit")
    for bad in ("x; DROP TABLE t", "Entry", "a b", ""):
        with pytest.raises(ValueError):
            fee_state_sql(bad, "c")
    with pytest.raises(ValueError):
        fee_state_sql("p", "c", "middle")


# ── accuracy gate (plan R7.1 #3) ──────────────────────────────────────────────────────────────────────────────────

def test_wilson_matches_the_numbers_quoted_in_the_plan():
    lo, hi = wilson_interval(26, 30)
    assert hi == pytest.approx(0.9469, abs=5e-4)              # 4 errors of 30: upper bound just below 95 %
    assert wilson_interval(27, 30)[1] == pytest.approx(0.9654, abs=5e-4)
    assert wilson_interval(30, 30)[0] == pytest.approx(0.8865, abs=5e-4)   # 0 errors: the lower bound is still only 0.887
    assert wilson_interval(0, 10)[0] == 0.0 and wilson_interval(10, 10)[1] == 1.0


@pytest.mark.parametrize("errors,decision", [(0, "PASS"), (1, "PASS"), (2, "REVIEW"), (3, "REVIEW"), (4, "STOP"), (5, "STOP"), (30, "STOP")])
def test_accuracy_decision_at_n_30(errors, decision):
    assert accuracy_decision(errors, 30) == decision


def test_accuracy_decision_rejects_impossible_counts():
    with pytest.raises(ValueError):
        accuracy_decision(31, 30)
    with pytest.raises(ValueError):
        accuracy_decision(0, 0)
    with pytest.raises(ValueError):
        wilson_interval(1, 0)


def test_wilson_is_symmetric_and_ordered():
    for n in (10, 30, 60):
        for k in range(0, n + 1):
            lo, hi = wilson_interval(k, n)
            assert 0.0 <= lo <= k / n <= hi <= 1.0
            lo2, hi2 = wilson_interval(n - k, n)
            assert math.isclose(lo, 1 - hi2, abs_tol=1e-9)


# ── the baseline script's pure core ───────────────────────────────────────────────────────────────────────────────

def _row(isin, flag, ep, em, xp=None, xm=None, universe=1):
    return {"isin": isin, "in_current_universe": universe, "fee_known_flag": flag, "entry_fee_pct": ep, "entry_fee_pct_max": em,
            "exit_fee_pct": xp, "exit_fee_pct_max": xm, "kid_format": "PRIIPS_KID", "kiid_pdf_hash": None}


def test_baseline_measures_the_reported_defect_and_the_derived_distribution():
    b = _baseline()
    rows = [
        _row("A", "NOT_FOUND", 0.0, None),               # stale 0.0 under NOT_FOUND (the FND-0406 class)
        _row("B", "ENTRY_CONDITIONAL", 0.05, 5.0),       # a point under CONDITIONAL
        _row("C", "EXTRACTED", 0.05, 5.0),               # consistent
        _row("D", "ZERO_CONFIRMED", 0.0, 5.0),           # zero applied, maximum declared
        _row("E", "NOT_FOUND", None, None, universe=0),  # outside the universe
        _row("F", "EXTRACTED", 0.0525, 0.0),             # pair conflict
    ]
    result = b.analyze(rows)
    assert result["populations"] == {"universe": 5, "all": 6}
    assert result["universe"]["entry_derived"] == {"EXTRACTED": 3, "ZERO_CONFIRMED": 2, "ENTRY_CONDITIONAL": 0, "NOT_FOUND": 0}
    assert result["all"]["entry_derived"]["NOT_FOUND"] == 1
    assert result["universe"]["entry_conflicts"] == 1 and [c["isin"] for c in result["conflict_entry"]] == ["F"]
    transitions = {(t["stored"], t["derived"]): t["n"] for t in result["all"]["entry_transitions"]}
    assert transitions[("NOT_FOUND", "ZERO_CONFIRMED")] == 1 and transitions[("ENTRY_CONDITIONAL", "EXTRACTED")] == 1
    assert result["all"]["negatives"] == {"entry_fee_pct": 0, "entry_fee_pct_max": 0, "exit_fee_pct": 0, "exit_fee_pct_max": 0}


def test_baseline_counts_negatives_and_splits_the_exit_zero_stratum():
    b = _baseline()
    rows = [_row("A", "EXTRACTED", -0.01, None, xp=0.0, xm=None), _row("B", "EXTRACTED", 0.01, 1.0, xp=0.0, xm=0.0), _row("C", "EXTRACTED", 0.01, 1.0, xp=None, xm=0.0)]
    assert b.negatives(rows)["entry_fee_pct"] == 1
    strata = b.sample_strata(rows, "exit")
    assert strata["ZERO_CONFIRMED:point0_no_ceiling"] == 1
    assert strata["ZERO_CONFIRMED:point0_ceiling0_or_ceiling_only"] == 2


def test_baseline_gate_table_is_the_wilson_rule():
    b = _baseline()
    assert {g["errors"]: g["decision"] for g in b.gate_table(30)} == {0: "PASS", 1: "PASS", 2: "REVIEW", 3: "REVIEW", 4: "STOP", 5: "STOP", 6: "STOP", 7: "STOP", 8: "STOP"}


def test_entry_window_finds_the_row_and_is_bounded():
    b = _baseline()
    text = "x" * 500 + " Costes de entrada No cobramos comisión de entrada. 525 EUR Costes de salida 0 EUR " + "y" * 900
    window = b.entry_window(text)
    assert "Costes de entrada" in window and "525 EUR" in window     # the window keeps 20 characters of lead-in before the row
    assert len(window) <= b.WINDOW + 20
    assert b.entry_window("nothing here") == "" and b.entry_window("") == ""
    assert "0 EUR" in b.entry_window(text, "exit")


def test_consumer_map_scans_production_files_and_skips_tests_and_docs(tmp_path):
    b = _baseline()
    (tmp_path / "shared").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "doc").mkdir()
    (tmp_path / "shared" / "a.py").write_text("x = fm.Entry_Fee_Pct + Exit_Fee_Pct_Max\n", encoding="utf-8")
    (tmp_path / "shared" / "none.py").write_text("y = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "t.py").write_text("Entry_Fee_Pct\n", encoding="utf-8")
    (tmp_path / "doc" / "d.sql").write_text("fee_known_flag\n", encoding="utf-8")
    assert b.consumer_map(tmp_path) == {"shared/a.py": {"entry_fee_pct": 1, "exit_fee_pct": 1}}


def test_script_parses_its_arguments_without_a_database(capsys):
    b = _baseline()
    with pytest.raises(SystemExit) as exc:
        b.main(["--help"])
    assert exc.value.code == 0
    assert "Read-only baseline" in capsys.readouterr().out


# ── the two audit rules (PLAUSIBILITY, non-blocking) ──────────────────────────────────────────────────────────────

def test_the_pair_rules_flag_exactly_the_conflicts_and_never_block():
    import pandas as pd
    from shared.statistical_audit.catalog_invariants import COST_INVARIANTS
    from shared.statistical_audit.invariants import check_invariant
    rules = {r.rule_id: r for r in COST_INVARIANTS}
    entry, exit_ = rules["ENTRY_FEE_LE_ITS_MAX"], rules["EXIT_FEE_LE_ITS_MAX"]
    assert entry.bound_type == exit_.bound_type == "PLAUSIBILITY"
    frame = pd.DataFrame({"isin": ["A", "B", "C", "D", "E"],
                          "Entry_Fee_Pct": [0.05, 0.05, 0.0525, None, 0.0],
                          "Entry_Fee_Pct_Max": [5.0, 3.0, 0.0, 3.0, 5.0],
                          "Exit_Fee_Pct": [0.01, 0.0082, None, 0.01, 0.0],
                          "Exit_Fee_Pct_Max": [1.0, 0.0, 1.0, None, 0.0]})
    e = check_invariant(frame, entry)
    assert e.n_applicable == 4 and list(e.violations["isin"]) == ["B", "C"]          # D has no point: undecidable, not a violation
    x = check_invariant(frame, exit_)
    assert x.n_applicable == 3 and list(x.violations["isin"]) == ["B"]               # FR0014000EB4-like: 0.82 % applied, maximum 0


# ── one switch, one name (plan R7.1 #5) ───────────────────────────────────────────────────────────────────────────

def test_the_write_switch_has_exactly_one_name_and_is_on():
    import re
    from shared import config
    assert config.ATTRIBUTE_GROUP_WRITE_ENABLED is True       # ON since 2026-11-14 (owner standing order); OFF restores the per-column COALESCE
    names = set()
    for rel in ("shared", "proyecto1/core", "proyecto2", "proyecto3", "scripts"):
        for path in (ROOT / rel).rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            names.update(re.findall(r"\bATTRIBUTE_[A-Z_]*_WRITE_ENABLED\b", path.read_text(encoding="utf-8", errors="replace")))
    assert names == {"ATTRIBUTE_GROUP_WRITE_ENABLED"}, names
    registry = (ROOT / "doc" / "reglas" / "NORMAS_IMPLEMENTACION.md").read_text(encoding="utf-8")
    assert "ATTRIBUTE_GROUP_WRITE_ENABLED" in registry and "ATTRIBUTE_PAIR_WRITE_ENABLED" not in registry


def test_both_sides_of_one_fund_never_emit_the_same_dq_code():
    """Found by the first live sample (LU0132601682): fund_data_quality_issues is unique on (isin, check_code), and both sides cleared a stored point."""
    record = {"Entry_Fee_Pct_Max": 4.0, "Exit_Fee_Pct_Max": 2.0}
    stored = {"Entry_Fee_Pct": 0.0, "Entry_Fee_Pct_Max": 4.0, "Exit_Fee_Pct": 0.0, "Exit_Fee_Pct_Max": 2.0}
    issues = reconcile_fee_pairs(record, {"Entry_Fee_Pct": None, "Exit_Fee_Pct": None}, stored, full_cost_pass=True)
    codes = [code for code, _lv, _st, _msg in issues]
    assert sorted(codes) == ["FEE_STORED_POINT_CLEARED_ENTRY", "FEE_STORED_POINT_CLEARED_EXIT"] and len(set(codes)) == len(codes)
    assert record["Entry_Fee_Pct"] is None and record["Exit_Fee_Pct"] is None and record["Entry_Fee_Pct_Max"] == 4.0
