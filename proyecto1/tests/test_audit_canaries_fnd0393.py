# proyecto1/tests/test_audit_canaries_fnd0393.py
# -*- coding: utf-8 -*-
"""FND-0393 (plan D1): detector-liveness canaries. Every hard detector of a FULL audit run is fed a known-bad fixture through the run's own functions; one that does not fire marks the run
INCOMPLETE; the canaries never reach audit_finding. No database (R-7)."""
from __future__ import annotations

import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import shared.config as cfg  # noqa: E402
from shared.statistical_audit import canaries as cn  # noqa: E402
from shared.statistical_audit import completeness as cpl  # noqa: E402
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS  # noqa: E402
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS  # noqa: E402
from shared.statistical_audit.invariants import InvariantRule, check_invariant  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load("run_statistical_audit_canaries", ("scripts", "audit", "run_statistical_audit.py"))
HARD_P2 = [r for r in P2_INVARIANTS if r.bound_type == "HARD_INVARIANT"]
HARD_COST = [r for r in COST_INVARIANTS if r.bound_type == "HARD_INVARIANT"]


def test_every_hard_row_invariant_of_both_catalogs_has_a_fixture_that_really_violates_it():
    for rule in HARD_P2 + HARD_COST:
        fixture = cn.invariant_fixture(rule)
        assert fixture is not None and len(fixture) == 1, f"{rule.rule_id}: no known-bad fixture can be built"
        assert check_invariant(fixture, rule).n_violations == 1, rule.rule_id


def test_a_fixture_is_found_for_a_conditional_rule_a_string_rule_and_a_boolean_condition():
    cond = InvariantRule("C", "a <= b", when="a > 0")
    strings = InvariantRule("S", "kind != 'X' or v > 0", when="kind == 'X'")
    boolean = InvariantRule("B", "x > 0", when="not flag")
    for rule in (cond, strings, boolean):
        fixture = cn.invariant_fixture(rule)
        assert fixture is not None and check_invariant(fixture, rule).n_violations == 1, rule.rule_id
    assert cn.invariant_fixture(InvariantRule("OK", "x == x or x != x")) is None          # a rule nothing can violate is `unbuildable`, never a failure


def test_the_group_and_duplicate_fixtures_are_flagged_by_the_runs_own_code():
    for rule in COST_GROUP_CHECKS:
        scratch = runner.AuditRun(domain="cost_attributes")
        runner._run_group_checks(scratch, [rule], cn.group_fixture(rule), block="CANARY")
        assert cn.fired(scratch.findings, rule.rule_id), rule.rule_id
    assert runner._timeseries_duplicate_finding(cn.duplicate_series_rows(), "m|w")["rule_id"] == "TIMESERIES_DUPLICATE"
    assert runner._timeseries_duplicate_finding([("I", False, "2020-01", "2020-12", 12, 12, 0, 0)], "m|w") is None     # a clean series is not a duplicate


@pytest.mark.parametrize("domain,expected_ids", [("p2_metrics", {r.rule_id for r in HARD_P2} | {"TIMESERIES_DUPLICATE"}),
                                                  ("cost_attributes", {r.rule_id for r in HARD_COST} | {g.rule_id for g in COST_GROUP_CHECKS})])
def test_on_healthy_code_every_canary_fires_and_the_run_is_not_made_incomplete(domain, expected_ids):
    run = runner.AuditRun(domain=domain)
    summary = runner.run_canaries(run)
    assert summary["failed"] == [] and summary["unbuildable"] == [] and summary["fired"] == summary["checked"] == len(expected_ids)
    assert run.findings == [] and run.statistics == [] and run.skips == []                      # nothing a canary produces touches the run: it never reaches audit_finding
    assert run.canaries == summary
    assert cpl.run_status(run.skips, run.findings, cpl.SCOPE_FULL, domain) != "INCOMPLETE"


def test_a_detector_that_stopped_evaluating_marks_the_run_incomplete(monkeypatch):
    """The failure this exists for: the detector 'finds nothing' because it no longer evaluates the rows."""
    blind = lambda df, rule: SimpleNamespace(rule=rule, n_applicable=0, n_violations=0, violations=df.iloc[0:0])      # noqa: E731
    monkeypatch.setattr(runner, "check_invariant", blind)
    run = runner.AuditRun(domain="p2_metrics")
    summary = runner.run_canaries(run)
    assert set(summary["failed"]) == {r.rule_id for r in HARD_P2} and summary["fired"] == 1       # only the duplicate-series detector does not go through check_invariant
    assert {s.check for s in run.skips} == {"CANARY"} and all(s.kind == cpl.KIND_MISSING for s in run.skips)
    assert cpl.run_status(run.skips, run.findings, cpl.SCOPE_FULL, "p2_metrics") == "INCOMPLETE"
    assert len(cpl.missing(run.skips, cpl.SCOPE_FULL)) == len(HARD_P2)


def test_a_broken_duplicate_detector_and_a_broken_group_detector_are_caught_too(monkeypatch):
    monkeypatch.setattr(runner, "_timeseries_duplicate_finding", lambda rows, key: None)
    run = runner.AuditRun(domain="p2_metrics")
    assert runner.run_canaries(run)["failed"] == ["TIMESERIES_DUPLICATE"]
    monkeypatch.undo()
    monkeypatch.setattr(runner, "check_group_constancy", lambda df, rule: SimpleNamespace(rule=rule, n_groups_checked=0, n_violating_groups=0, violations=df.iloc[0:0]))
    cost = runner.AuditRun(domain="cost_attributes")
    assert runner.run_canaries(cost)["failed"] == sorted(g.rule_id for g in COST_GROUP_CHECKS)


def test_an_unbuildable_rule_is_reported_and_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(cn, "invariant_fixture", lambda rule: None)
    run = runner.AuditRun(domain="p2_metrics")
    summary = runner.run_canaries(run)
    assert summary["failed"] == [] and set(summary["unbuildable"]) == {r.rule_id for r in HARD_P2} and run.skips == []


def test_the_summary_is_what_the_manifest_records_and_the_check_is_registered_as_required():
    assert cn.summarize({"A": "fired", "B": "failed", "C": "unbuildable"}) == {"checked": 2, "fired": 1, "failed": ["B"], "unbuildable": ["C"]}
    assert "CANARY" in cpl.CHECKS and "CANARY" in cpl.REQUIRED_CHECKS
    run = runner.AuditRun(domain="p2_metrics")
    runner.run_canaries(run)
    manifest = runner._run_manifest(SimpleNamespace(rollback=lambda: None, execute=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db"))), run)
    assert manifest["canaries"] == run.canaries and manifest["canaries"]["failed"] == []


def test_the_switch_ships_on_and_main_runs_the_canaries_only_in_a_full_run():
    assert cfg.AUDIT_CANARIES_ENABLED is True
    src = open(os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"), encoding="utf-8").read()
    assert "if scope == cpl.SCOPE_FULL and AUDIT_CANARIES_ENABLED:" in src and "run_canaries(run)" in src
    assert cn.fired([], "X") is False and cn.fired([{"rule_id": "X"}], "X") is True
