# proyecto1/tests/test_applicability_drop_fnd0393.py
# -*- coding: utf-8 -*-
"""FND-0393 (plan D1, second half): applicability-drop rule. A FULL audit run compares what each rule tested with the median of its last stored FULL runs; a rule that tested more than 50 % fewer rows has
probably lost its input. SHADOW prints and records it, ON adds a BLOCK1 WARN finding, OFF computes nothing. No database (R-7): the history reader is replaced by a fake."""
from __future__ import annotations

import importlib.util
import os
import sys

import pandas as pd
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import shared.config as cfg  # noqa: E402
from shared.statistical_audit import applicability as ap  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load("run_statistical_audit_apdrop", ("scripts", "audit", "stat", "run_statistical_audit.py"))
RULE = "WINDOW_FISHER_IDENTITY"


def _history(rule=RULE, values=(2_465_360, 2_466_681, 2_466_000, 2_465_900, 2_466_200), extra=()):
    rows = [(f"run{i}", f"2026-11-0{9 - i}", rule, v, 0) for i, v in enumerate(values)] + list(extra)
    return pd.DataFrame(rows, columns=["run_id", "finished_at", "rule_id", "n_applicable", "n_violating"])


# ---------- the pure rule ----------

def test_a_rule_that_tested_far_fewer_rows_than_its_median_is_a_drop():
    drops = ap.applicability_drops({RULE: 400_000}, _history())
    assert [d["rule_id"] for d in drops] == [RULE]
    d = drops[0]
    assert d["current"] == 400_000 and d["runs"] == 5 and d["median"] == pytest.approx(2_466_000)
    assert d["change"] == pytest.approx(400_000 / 2_466_000 - 1.0)


def test_the_cut_is_strictly_more_than_half():
    h = _history(values=(1000, 1000, 1000))
    assert ap.applicability_drops({RULE: 500}, h) == []              # exactly 50 % below: not a drop
    assert len(ap.applicability_drops({RULE: 499}, h)) == 1          # just past it: a drop


def test_a_rule_with_too_little_history_is_never_judged():
    assert ap.applicability_drops({RULE: 1}, _history(values=(1000, 1000))) == []            # two runs: no usual level yet
    assert len(ap.applicability_drops({RULE: 1}, _history(values=(1000, 1000, 1000)))) == 1  # three runs: judged


def test_a_median_of_zero_has_no_basis_and_a_rule_absent_from_this_run_is_not_a_drop():
    assert ap.applicability_drops({RULE: 0}, _history(values=(0, 0, 0))) == []
    assert ap.applicability_drops({"SOME_OTHER_RULE": 1}, _history()) == []                  # RULE did not run: that is a skip of the run header, not a drop
    assert ap.applicability_drops({RULE: 1}, None) == [] and ap.applicability_drops({RULE: 1}, _history().iloc[0:0]) == []


def test_a_rule_new_to_the_catalogue_does_not_alarm():
    assert ap.applicability_drops({"BRAND_NEW_RULE": 5, RULE: 2_466_000}, _history()) == []


def test_the_run_own_stored_row_is_not_its_own_history():
    h = _history(values=(1000, 1000, 1000, 1000)).copy()
    h.loc[0, ["run_id", "n_applicable"]] = ["me", 10]                # this run, persisted already with the low count
    assert len(ap.applicability_drops({RULE: 10}, h, exclude_run_id="me")) == 1   # judged against the other three runs, still a drop
    assert ap.applicability_drops({RULE: 10}, h.iloc[:2], exclude_run_id="me") == []   # one other run left: not enough history


def test_the_finding_has_the_shape_of_the_other_block1_escalations():
    d = ap.applicability_drops({RULE: 400_000}, _history())[0]
    f = ap.applicability_drop_finding(d)
    assert (f["block"], f["rule_id"], f["rule_class"], f["severity"], f["group_key"]) == ("BLOCK1", "APPLICABILITY_DROP", "STATISTICAL_ANOMALY", "WARN", RULE)
    assert f["value"] == 400_000 and f["threshold"] == -0.5 and f["distance"] == d["change"]
    assert RULE in f["evidence"] and "2,466,000" in f["evidence"]


def test_current_applicability_reads_the_entries_the_run_records():
    stats = [ap.applicability_entry(RULE, 120, 3), ("GLOBAL", "return_ann", {"n_valid": 9}, 9), ap.applicability_entry("B", 0, 0)]
    assert ap.current_applicability(stats) == {RULE: 120, "B": 0}


# ---------- the runner wiring ----------

class _Run:
    domain = "p2_metrics"

    def __init__(self, applicable):
        self.statistics = [ap.applicability_entry(r, n, 0) for r, n in applicable.items()]
        self.findings = []
        self.applicability_drops = "untouched"


class _Conn:
    def __init__(self):
        self.rolled_back = 0

    def rollback(self):
        self.rolled_back += 1


def _patch(monkeypatch, mode, history):
    monkeypatch.setattr(runner, "AUDIT_APPLICABILITY_DROP_MODE", mode)
    monkeypatch.setattr(runner, "load_applicability_history", lambda conn, domain, rule_id=None, runs=5: history)


def test_off_computes_nothing(monkeypatch):
    _patch(monkeypatch, "OFF", _history())
    run = _Run({RULE: 1})
    runner._apply_applicability_drops(_Conn(), run, "me")
    assert run.applicability_drops == "untouched" and run.findings == []


def test_shadow_records_the_drop_but_adds_no_finding(monkeypatch):
    _patch(monkeypatch, "SHADOW", _history())
    run = _Run({RULE: 400_000})
    runner._apply_applicability_drops(_Conn(), run, "me")
    assert [d["rule_id"] for d in run.applicability_drops] == [RULE] and run.findings == []


def test_shadow_records_an_empty_list_when_nothing_dropped(monkeypatch):
    _patch(monkeypatch, "SHADOW", _history())
    run = _Run({RULE: 2_465_000})
    runner._apply_applicability_drops(_Conn(), run, "me")
    assert run.applicability_drops == [] and run.findings == []        # [] (checked, none) is different from None (not computed)


def test_on_adds_one_warn_finding_per_drop(monkeypatch):
    same_runs = [(f"run{i}", f"2026-11-0{9 - i}", "OTHER", 1000, 0) for i in range(5)]               # the SAME five runs also stored the second rule
    _patch(monkeypatch, "ON", _history(extra=same_runs))
    run = _Run({RULE: 400_000, "OTHER": 100, "FINE": 5})
    runner._apply_applicability_drops(_Conn(), run, "me")
    assert sorted(f["group_key"] for f in run.findings) == ["OTHER", RULE]
    assert {f["severity"] for f in run.findings} == {"WARN"} and {f["rule_id"] for f in run.findings} == {"APPLICABILITY_DROP"}


def test_an_unreadable_history_never_fails_the_run(monkeypatch):
    def boom(conn, domain, rule_id=None, runs=5):
        raise RuntimeError("relation audit_statistic does not exist")
    monkeypatch.setattr(runner, "AUDIT_APPLICABILITY_DROP_MODE", "ON")
    monkeypatch.setattr(runner, "load_applicability_history", boom)
    run, conn = _Run({RULE: 1}), _Conn()
    runner._apply_applicability_drops(conn, run, "me")
    assert run.applicability_drops is None and run.findings == [] and conn.rolled_back == 1


def test_only_the_last_five_runs_count_even_when_more_are_stored(monkeypatch):
    old = [("old%d" % i, "2026-01-0%d" % (i + 1), RULE, 5_000_000, 0) for i in range(5)]      # older and much larger: must not move the median
    h = pd.concat([_history(), pd.DataFrame(old, columns=_history().columns)], ignore_index=True)
    _patch(monkeypatch, "SHADOW", h)
    run = _Run({RULE: 2_000_000})                                       # against the last five (about 2.47 M): fine; against all ten it would be a drop
    runner._apply_applicability_drops(_Conn(), run, "me")
    assert run.applicability_drops == []


def test_the_shipped_defaults_and_the_catalogue_entry():
    assert cfg.AUDIT_APPLICABILITY_DROP_MODE == "SHADOW"                # ships SHADOW (D-0): judged on real FULL manifests before ON
    assert ap.APPLICABILITY_DROP_PCT == 0.5 and ap.APPLICABILITY_DROP_RUNS == 5
    assert any(r["rule_id"] == "APPLICABILITY_DROP" and r["block"] == "BLOCK1" for r in runner._PROCEDURAL_RULES)


def test_the_manifest_carries_the_drops_only_when_they_were_computed():
    import inspect
    src = inspect.getsource(runner._run_manifest)
    assert 'run.applicability_drops is not None' in src and 'manifest["applicability_drops"]' in src
