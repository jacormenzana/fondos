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
