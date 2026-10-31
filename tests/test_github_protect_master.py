# tests/test_github_protect_master.py
# -*- coding: utf-8 -*-
"""Protection of master (FND-0336 / O-5; scripts/ops/github_protect_master.py). Pure: no network (R-7).

The required checks are required BY NAME, so a renamed workflow job would leave every pull request waiting for a check nobody reports: the first test ties the
list to the job names in .github/workflows."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import github_protect_master as gpm  # noqa: E402


PUBLISHER_WORKFLOWS = ("auto-pr.yml",)          # Backlog v3 W1: opens the pull request and enables auto-merge; it reports no check that master could require


def _check_workflows() -> list:
    return [f for f in sorted((_ROOT / ".github" / "workflows").glob("*.yml")) if f.name not in PUBLISHER_WORKFLOWS]


def _workflow_job_names() -> list:
    yaml = pytest.importorskip("yaml")
    names = []
    for f in _check_workflows():
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        for job_id, job in (doc.get("jobs") or {}).items():
            names.append(job.get("name") or job_id)
    return names


def test_required_checks_are_the_workflow_job_names_except_the_gate_job_that_the_owner_enables_later():
    assert sorted(gpm.REQUIRED_CHECKS + (gpm.GATE_CHECK,)) == sorted(_workflow_job_names())          # every job is either required now, or is backlog-gate (required once the key is committed)
    assert gpm.GATE_CHECK not in gpm.REQUIRED_CHECKS and gpm.checks(True) == gpm.REQUIRED_CHECKS + ("backlog-gate",)


def test_backlog_gate_is_required_only_when_asked_for_and_then_it_is_mandatory():
    assert gpm.protection_payload()["required_status_checks"]["contexts"] == list(gpm.REQUIRED_CHECKS)
    assert gpm.protection_payload(True)["required_status_checks"]["contexts"] == list(gpm.REQUIRED_CHECKS) + ["backlog-gate"]
    four, five = list(gpm.REQUIRED_CHECKS), list(gpm.REQUIRED_CHECKS) + ["backlog-gate"]
    for with_gate, contexts, expected in ((False, four, True), (False, five, True), (True, five, True), (True, four, False), (False, four[:3], False), (True, five[:4] + ["x"], False)):
        assert gpm._contexts_ok(contexts, with_gate) is expected, (with_gate, contexts)


def test_every_check_workflow_runs_on_pull_requests():
    yaml = pytest.importorskip("yaml")
    for f in _check_workflows():
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        triggers = doc.get("on") if "on" in doc else doc.get(True)          # YAML 1.1 reads the key `on` as True
        assert "pull_request" in triggers, f"{f.name} does not run on pull requests: its checks could never be required"


def test_the_payload_is_the_owner_decision():
    p = gpm.protection_payload()
    assert p["enforce_admins"] is True                                       # administrators included
    assert p["required_pull_request_reviews"]["required_approving_review_count"] == 0
    assert p["required_status_checks"] == {"strict": False, "contexts": list(gpm.REQUIRED_CHECKS)}
    assert p["allow_force_pushes"] is False and p["allow_deletions"] is False and p["restrictions"] is None


def _public(protected=True, level="everyone", contexts=None):
    return {"protected": protected, "protection": {"enabled": protected, "required_status_checks": {
        "enforcement_level": level, "contexts": list(gpm.REQUIRED_CHECKS if contexts is None else contexts), "checks": []}}}


def _failed(results):
    return [n for n, ok, _ in results if not ok]


def test_public_evaluation_accepts_only_the_specified_state():
    assert _failed(gpm.evaluate_public(_public())) == []
    assert _failed(gpm.evaluate_public(_public(protected=False, level="off", contexts=[]))) == [
        "branch is protected", "the required checks are exactly the CI jobs (plus backlog-gate once enabled)", "the checks are enforced for everyone, administrators included"]
    assert _failed(gpm.evaluate_public(_public(level="non_admins"))) == ["the checks are enforced for everyone, administrators included"]
    assert _failed(gpm.evaluate_public(_public(contexts=list(gpm.REQUIRED_CHECKS)[:3]))) == ["the required checks are exactly the CI jobs (plus backlog-gate once enabled)"]
    assert _failed(gpm.evaluate_public({"protected": True, "protection": {"enabled": True, "required_status_checks": {
        "enforcement_level": "everyone", "contexts": [], "checks": [{"context": c} for c in gpm.REQUIRED_CHECKS]}}})) == []   # the `checks` form is read too


def _admin(**over):
    p = {"required_pull_request_reviews": {"required_approving_review_count": 0}, "enforce_admins": {"enabled": True},
         "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
         "required_status_checks": {"contexts": list(gpm.REQUIRED_CHECKS)}}
    p.update(over)
    return p


def test_admin_evaluation_flags_each_missing_rule():
    assert _failed(gpm.evaluate_admin(_admin())) == []
    assert _failed(gpm.evaluate_admin(_admin(required_pull_request_reviews=None))) == ["a pull request is required before merging"]
    assert _failed(gpm.evaluate_admin(_admin(enforce_admins={"enabled": False}))) == ["administrators are included"]
    assert _failed(gpm.evaluate_admin(_admin(allow_force_pushes={"enabled": True}))) == ["force pushes are refused"]
    assert _failed(gpm.evaluate_admin(_admin(allow_deletions={"enabled": True}))) == ["deletions are refused"]
    assert _failed(gpm.evaluate_admin(_admin(required_status_checks={"contexts": []}))) == ["the checks are required"]


def test_apply_without_a_token_is_a_usage_error_and_dry_run_touches_nothing(monkeypatch):
    monkeypatch.delenv("GITHUB_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(gpm, "_call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network call expected")))
    assert gpm.main(["--apply"]) == gpm.RC_USAGE
    assert gpm.main(["--remove", "--apply"]) == gpm.RC_USAGE
    assert gpm.main(["--remove"]) == gpm.RC_OK                                # dry run of the emergency path
