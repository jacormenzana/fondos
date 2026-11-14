# proyecto1/tests/test_family_scope_and_applicability_fnd0392.py
# -*- coding: utf-8 -*-
"""FND-0392 (WS0.5 of FND-0391): per-rule applicability stored in every run (D1) and evaluation by fund family.

R-7: no pipeline or core.io imports. A rule that finds nothing used to leave nothing: `n_applicable` lived only in the evidence of a VIOLATING finding, so after the FND-0250 funds left the
universe nobody could say whether WINDOW_FISHER_IDENTITY still tested live rows.
"""
import importlib.util
import os
import sys

import pandas as pd
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.family_scope import collapse_flags, family_key, group_by_family  # noqa: E402
from shared.statistical_audit import persistence  # noqa: E402
from shared.statistical_audit.applicability import (  # noqa: E402
    APPLICABILITY_POPULATION, STAT_APPLICABLE, STAT_VIOLATING, applicability_entry,
)
from shared.statistical_audit.compare_runs import compare_runs  # noqa: E402
from shared.statistical_audit.invariants import InvariantRule  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load("run_statistical_audit", ("scripts", "audit", "stat", "run_statistical_audit.py"))


def _stats(run):
    return {s[1]: s for s in run.statistics if s[0] == APPLICABILITY_POPULATION}


# ------------------------------------------------------------------------------------------ D1: applicability
def test_the_entry_has_the_shape_persist_run_writes_and_rejects_nonsense():
    assert applicability_entry("R", 245, 40) == (APPLICABILITY_POPULATION, "R", {STAT_APPLICABLE: 245, STAT_VIOLATING: 40}, 245)
    with pytest.raises(ValueError):
        applicability_entry("R", 3, 5)
    with pytest.raises(ValueError):
        applicability_entry("R", -1, 0)


def test_a_clean_rule_leaves_its_applicability_and_no_finding():
    run = runner.AuditRun("p2_metrics")
    frame = pd.DataFrame({"isin": ["A", "B", "C"], "x": [1.0, 2.0, 3.0]})
    runner._run_invariants(run, [InvariantRule("X_NONNEG", "x >= 0", description="d")], [frame], block="BLOCK5")
    assert run.findings == []                                         # nothing violated: no finding, as before
    assert _stats(run)["X_NONNEG"][2] == {STAT_APPLICABLE: 3, STAT_VIOLATING: 0}      # ... but the history now says it tested 3 rows


def test_a_violating_rule_records_both_counts_and_keeps_its_finding():
    run = runner.AuditRun("p2_metrics")
    frame = pd.DataFrame({"isin": ["A", "B", "C", "D"], "x": [-1.0, 2.0, -3.0, 4.0]})
    runner._run_invariants(run, [InvariantRule("X_NONNEG", "x >= 0", description="d")], [frame], block="BLOCK5")
    assert len(run.findings) == 1 and "2/4 applicable rows violate" in run.findings[0]["evidence"]
    assert _stats(run)["X_NONNEG"][2] == {STAT_APPLICABLE: 4, STAT_VIOLATING: 2} and _stats(run)["X_NONNEG"][3] == 4


def test_applicability_counts_only_the_rows_the_rule_could_judge():
    run = runner.AuditRun("p2_metrics")
    frame = pd.DataFrame({"isin": list("ABCDE"), "x": [1.0, -1.0, 1.0, -1.0, 1.0], "on": [1, 1, 1, 0, 0]})
    runner._run_invariants(run, [InvariantRule("X_WHEN_ON", "x >= 0", when="on == 1", description="d")], [frame], block="BLOCK5")
    assert _stats(run)["X_WHEN_ON"][2] == {STAT_APPLICABLE: 3, STAT_VIOLATING: 1}


def test_a_rule_whose_columns_are_missing_is_a_skip_not_an_applicability_row():
    run = runner.AuditRun("p2_metrics")
    runner._run_invariants(run, [InvariantRule("NEEDS_Y", "y >= 0", description="d")], [pd.DataFrame({"x": [1.0]})], block="BLOCK5")
    assert _stats(run) == {} and len(run.skips) == 1                   # the header's skips already say it did not run (FND-0261)


def test_the_rows_persist_and_never_reach_the_drift_report():
    entry = applicability_entry("WINDOW_FISHER_IDENTITY", 245, 0)
    rows = persistence._statistic_rows("rid", "p2_metrics", entry[0], entry[1], entry[2], entry[3], "cv")
    assert [(r[4], r[5], r[7]) for r in rows] == [(STAT_APPLICABLE, 245.0, 245), (STAT_VIOLATING, 0.0, 245)]
    frame = persistence.statistics_to_frame([entry, ("GLOBAL", "g|x", {"n_valid": 5, "mean": 1.5}, 5)])
    result = compare_runs(frame, frame)
    assert set(result.deltas["stat_name"]) == {"n_valid", "mean"} and result.new_groups == [] and result.dropped_groups == []


def test_the_runner_records_every_invariant_it_evaluates_and_the_duplicate_check():
    src = open(os.path.join(_ROOT, "scripts", "audit", "stat", "run_statistical_audit.py"), encoding="utf-8").read()
    assert src.count("run.record_applicability(") == 2 and 'record_applicability("TIMESERIES_DUPLICATE"' in src
    assert src.index("run.record_applicability(rule.rule_id") < src.index("if result.n_violations == 0:")      # BEFORE the early continue: a clean rule is recorded


# ------------------------------------------------------------------------------------------ evaluation by family
FAM = {"A1": "F-A", "A2": "F-A", "A3": "F-A", "B1": "F-B", "C1": None, "D1": ""}


def test_a_fund_without_a_family_is_a_family_of_one_never_merged_with_another():
    assert family_key("A1", FAM) == "F-A"
    assert family_key("C1", FAM) == "ISIN:C1" and family_key("D1", FAM) == "ISIN:D1" and family_key("ZZ", FAM) == "ISIN:ZZ"
    assert group_by_family(["C1", "D1"], FAM) == {"ISIN:C1": ("C1",), "ISIN:D1": ("D1",)}


def test_share_classes_collapse_into_one_family_and_duplicates_count_once():
    assert group_by_family(["A3", "A1", "B1", "A1", "C1"], FAM) == {"F-A": ("A1", "A3"), "F-B": ("B1",), "ISIN:C1": ("C1",)}


def test_collapse_flags_gives_one_entry_per_family_with_the_largest_deviation_as_representative():
    out = collapse_flags({"A1": 12.0, "A2": -151.0, "A3": 60.0, "B1": 70.0, "C1": 55.0}, FAM)
    assert sorted(out) == ["F-A", "F-B", "ISIN:C1"]
    assert out["F-A"] == {"isins": ("A1", "A2", "A3"), "representative": "A2", "score": -151.0, "n_flagged": 3}     # three classes, one phenomenon
    assert out["F-B"]["representative"] == "B1" and out["ISIN:C1"]["n_flagged"] == 1


def test_ties_pick_the_first_isin_so_the_result_never_depends_on_dict_order():
    a = collapse_flags({"A2": 50.0, "A1": 50.0}, FAM)
    b = collapse_flags({"A1": 50.0, "A2": 50.0}, FAM)
    assert a == b and a["F-A"]["representative"] == "A1"
