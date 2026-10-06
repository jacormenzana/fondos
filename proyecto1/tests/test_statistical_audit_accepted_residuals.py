# proyecto1/tests/test_statistical_audit_accepted_residuals.py
# -*- coding: utf-8 -*-
"""FND-0234(e): the accepted-residual baseline (pure parts) and the ISIN capture it depends on.

R-7: no pipeline or core.io imports. The database parts are in test_statistical_audit_accepted_residuals_pg.py.
"""
import importlib.util
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.accepted_residuals import (  # noqa: E402
    ACCEPTED_CLASS, apply_accepted, expiry_after,
)
from shared.statistical_audit.group_checks import GroupConstancyRule  # noqa: E402
from shared.statistical_audit.invariants import InvariantRule  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _load("run_statistical_audit", ("scripts", "audit", "run_statistical_audit.py"))
cli = _load("accept_audit_residual", ("scripts", "audit", "accept_audit_residual.py"))

NEXT_WEEK = date.today() + timedelta(days=7)


def _finding(rule="OC_NOT_CONTAMINATED", isins=("A", "B", "C"), **kw):
    f = {"block": "BLOCK5", "rule_id": rule, "rule_class": "HARD_INVARIANT", "severity": "ALARM",
         "group_key": rule, "distance": float(len(isins)), "evidence": f"{len(isins)}/100 applicable rows violate",
         "violating_isins": tuple(isins)}
    f.update(kw)
    return f


def _accepted(rule="OC_NOT_CONTAMINATED", isins=("A", "B", "C"), ap="FND-0034", until=NEXT_WEEK):
    return {rule: {i: (ap, until) for i in isins}}


# ---------------------------------------------------------------- apply_accepted
def test_all_violators_accepted_downgrades_to_info_and_accepted_class():
    out, notes = apply_accepted([_finding()], _accepted())
    assert out[0]["severity"] == "INFO" and out[0]["rule_class"] == ACCEPTED_CLASS
    assert "ACCEPTED RESIDUAL (FND-0034" in out[0]["evidence"] and "3/3 ISINs" in out[0]["evidence"]
    assert len(notes) == 1 and "3 ISINs accepted" in notes[0]


def test_a_new_violator_keeps_the_alarm_and_counts_only_the_new_ones():
    out, notes = apply_accepted([_finding(isins=("A", "B", "Z"))], _accepted(isins=("A", "B")))
    f = out[0]
    assert f["severity"] == "ALARM" and f["rule_class"] == "HARD_INVARIANT"
    assert f["violating_isins"] == ("Z",) and f["distance"] == 1.0
    assert "1 NEW ISINs" in f["evidence"] and "2 more accepted" in f["evidence"]
    assert "NEW" in notes[0]


def test_input_findings_are_not_mutated():
    original = _finding()
    snapshot = dict(original)
    apply_accepted([original], _accepted())
    assert original == snapshot


def test_other_rules_and_findings_without_isins_pass_through_untouched():
    other = _finding(rule="MGMT_LE_TOTAL")
    no_isins = _finding(isins=())
    out, notes = apply_accepted([other, no_isins], _accepted())
    assert out == [other, no_isins] and notes == []


def test_nothing_accepted_changes_nothing():
    f = _finding()
    out, notes = apply_accepted([f], {})
    assert out == [f] and notes == []


def test_acceptance_for_a_different_isin_set_does_not_leak():
    f = _finding(isins=("A", "B"))
    out, _ = apply_accepted([f], _accepted(isins=("X", "Y")))
    assert out == [f]


def test_the_earliest_expiry_is_reported():
    acc = {"OC_NOT_CONTAMINATED": {"A": ("FND-0034", date(2027, 5, 1)), "B": ("FND-0034", date(2027, 2, 1))}}
    out, _ = apply_accepted([_finding(isins=("A", "B"))], acc)
    assert "until 2027-02-01" in out[0]["evidence"]


def test_expiry_after_is_capped_and_validated():
    today = date(2026, 10, 5)
    assert expiry_after(90, today) == today + timedelta(days=90)
    assert expiry_after(10_000, today) == today + timedelta(days=180)
    with pytest.raises(ValueError):
        expiry_after(0, today)


def test_an_accepted_finding_no_longer_blocks_mode_check():
    out, _ = apply_accepted([_finding()], _accepted())
    run = runner.AuditRun("cost_attributes")
    run.findings = out
    assert runner._has_blocking_findings(run) is False
    run.findings = [_finding()]
    assert runner._has_blocking_findings(run) is True


# ---------------------------------------------------------------- the ISIN capture (the case bug)
def test_isin_column_is_found_whatever_its_case():
    assert runner._isin_column(pd.DataFrame({"ISIN": [1]})) == "ISIN"
    assert runner._isin_column(pd.DataFrame({"isin": [1]})) == "isin"
    assert runner._isin_column(pd.DataFrame({"x": [1]})) is None


def test_invariant_findings_name_their_isins_on_the_cost_frames_that_spell_it_ISIN():
    """The 2026-10-05 finding: the cost frames use ISIN, the lookup was lowercase, so every cost ALARM said '0 ISINs'."""
    frame = pd.DataFrame({"ISIN": ["A", "B", "C"], "x": [-1.0, 5.0, -2.0]})
    run = runner.AuditRun("cost_attributes")
    runner._run_invariants(run, [InvariantRule("X_NONNEG", "x >= 0", description="d")], [frame], block="BLOCK5")
    assert run.findings[0]["violating_isins"] == ("A", "C")
    assert "across 2 ISINs" in run.findings[0]["evidence"]


def test_invariant_findings_still_name_their_isins_on_lowercase_frames():
    frame = pd.DataFrame({"isin": ["A", "B"], "x": [-1.0, 5.0]})
    run = runner.AuditRun("p2_metrics")
    runner._run_invariants(run, [InvariantRule("X_NONNEG", "x >= 0", description="d")], [frame], block="BLOCK5")
    assert run.findings[0]["violating_isins"] == ("A",)


def test_group_constancy_findings_name_the_violating_isins():
    frame = pd.DataFrame({"ISIN": ["A", "A", "B", "B"], "v": [1.0, 1.0, 1.0, 2.0]})
    run = runner.AuditRun("cost_attributes")
    runner._run_group_checks(run, [GroupConstancyRule("V_CONSTANT", "ISIN", "v", 2, "d")], frame)
    assert run.findings[0]["violating_isins"] == ("A",)


# ---------------------------------------------------------------- the CLI's pure parts
def test_cli_validates_ap_reason_and_days():
    assert cli.validate("FND-0034", "x" * 25, 90) == []
    assert any("action point" in p for p in cli.validate("nope", "x" * 25, 90))
    assert any("--reason" in p for p in cli.validate("FND-0034", "short", 90))
    assert any("--days" in p for p in cli.validate("FND-0034", "x" * 25, 0))


def test_cli_violators_selection():
    findings = [_finding(isins=("A", "B", "C"))]
    assert cli.violators(findings, "OC_NOT_CONTAMINATED") == (["A", "B", "C"], True)
    assert cli.violators(findings, "OC_NOT_CONTAMINATED", {"B", "Q"}) == (["B"], True)
    assert cli.violators(findings, "NOPE") == ([], False)
