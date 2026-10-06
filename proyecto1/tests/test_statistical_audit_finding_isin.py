# proyecto1/tests/test_statistical_audit_finding_isin.py
# -*- coding: utf-8 -*-
"""FND-0234(d): the violator drift, the unambiguous drift label, and the runner's in-memory violators. R-7: pure.
The database parts are in test_statistical_audit_finding_isin_pg.py."""
import importlib.util
import os
import sys

import pandas as pd

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.compare_runs import compare_runs, compare_violators, drift_label  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit_fi", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_statistical_audit_fi"] = runner
_spec.loader.exec_module(runner)


def _v(*rows):
    return pd.DataFrame(rows, columns=["rule_id", "group_key", "isin"])


# ---------------------------------------------------------------- the label (the "same line twice" report)
def test_the_same_group_key_in_two_populations_gets_two_distinct_labels():
    """The 2026-10-05 drift report printed 'sortino|since_inception|0|v1 [kurtosis]' twice (GLOBAL and PEER:Monetario)."""
    key = "sortino|since_inception|0|v1"
    assert drift_label("GLOBAL", key) == key
    assert drift_label("PEER:Monetario", key) == f"{key} @ PEER:Monetario"
    assert drift_label("GLOBAL", key) != drift_label("PEER:Monetario", key)
    assert drift_label(None, key) == key and drift_label("", key) == key


def test_compare_runs_keeps_populations_apart_so_only_the_printing_was_ambiguous():
    def stats(glob, peer):
        return pd.DataFrame([("GLOBAL", "m|h|0|v1", "kurtosis", glob), ("PEER:X", "m|h|0|v1", "kurtosis", peer)],
                            columns=["population", "group_key", "stat_name", "stat_value"])
    result = compare_runs(stats(17.0, 0.3), stats(40.0, 36.0))
    assert len(result.deltas) == 2                                             # one row per population, no merge
    assert sorted(result.deltas["population"]) == ["GLOBAL", "PEER:X"]


# ---------------------------------------------------------------- violator drift
def test_new_resolved_and_unchanged_violators_are_counted_per_rule():
    previous = _v(("OC", "OC", "A"), ("OC", "OC", "B"), ("OC", "OC", "C"), ("MG", "MG", "A"))
    current = _v(("OC", "OC", "B"), ("OC", "OC", "C"), ("OC", "OC", "D"), ("OC", "OC", "E"), ("MG", "MG", "A"))
    out = compare_violators(previous, current).set_index("rule_id")
    assert out.loc["OC", ["new", "resolved", "unchanged"]].tolist() == [2, 1, 2]
    assert out.loc["MG", ["new", "resolved", "unchanged"]].tolist() == [0, 0, 1]


def test_a_rule_on_one_side_only_counts_all_its_isins_as_new_or_resolved():
    out = compare_violators(_v(("OLD", "OLD", "A"), ("OLD", "OLD", "B")), _v(("NEW", "NEW", "C"))).set_index("rule_id")
    assert out.loc["OLD", ["new", "resolved", "unchanged"]].tolist() == [0, 2, 0]
    assert out.loc["NEW", ["new", "resolved", "unchanged"]].tolist() == [1, 0, 0]


def test_the_rules_that_moved_most_come_first_and_empty_inputs_are_fine():
    previous = _v(("A", "A", "1"), ("B", "B", "1"), ("B", "B", "2"))
    current = _v(("A", "A", "1"), ("B", "B", "3"), ("B", "B", "4"), ("B", "B", "5"))
    assert compare_violators(previous, current)["rule_id"].tolist() == ["B", "A"]
    assert compare_violators(_v(), _v()).empty
    assert compare_violators(_v(), _v(("X", "X", "1")))["new"].tolist() == [1]


def test_the_same_rule_under_two_group_keys_is_kept_apart():
    out = compare_violators(_v(("R", "k1", "A")), _v(("R", "k2", "A")))
    assert len(out) == 2


# ---------------------------------------------------------------- the runner's in-memory side
def test_current_violators_come_from_the_findings_and_ignore_those_without_isins():
    run = runner.AuditRun("cost_attributes")
    run.findings = [
        {"rule_id": "OC", "group_key": "OC", "violating_isins": ("A", "B")},
        {"rule_id": "PAIR", "group_key": "PAIR"},                              # a group-level statistic: no ISINs
        {"rule_id": "X", "violating_isins": ("C",)},                          # group_key falls back to the rule id
    ]
    got = runner._current_violators(run)
    assert sorted(got.itertuples(index=False, name=None)) == [("OC", "OC", "A"), ("OC", "OC", "B"), ("X", "X", "C")]


def test_a_run_remembers_how_many_isin_detail_rows_it_persisted():
    assert runner.AuditRun("p2_metrics").persisted_isin_rows == 0
