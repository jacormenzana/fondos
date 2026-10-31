# tests/test_auto_pr_workflow.py
# -*- coding: utf-8 -*-
"""Backlog v3 W1: the auto-PR workflow is pinned by reading it (nothing here needs a database or the network)."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "auto-pr.yml"
GATE = ROOT / ".github" / "workflows" / "backlog-gate.yml"
yaml = pytest.importorskip("yaml")


def load(path=WORKFLOW):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_text():
    return "\n".join(str(s.get("run", "")) for s in load()["jobs"]["auto-pr"]["steps"])


def test_it_runs_only_on_push_to_work_branches_never_on_master_or_publish_or_pull_request_target():
    wf = load()
    on = wf.get("on", wf.get(True))                       # PyYAML reads the bare key `on` as the boolean True
    assert set(on) == {"push"} and on["push"]["branches"] == ["feat/**", "fix/**", "chore/**", "docs/**"]
    assert "pull_request_target" not in wf.get("on", wf.get(True))


def test_permissions_are_the_two_the_job_needs_and_nothing_else():
    assert load()["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_untrusted_inputs_reach_the_script_through_env_never_interpolated_into_run():
    for step in load()["jobs"]["auto-pr"]["steps"]:
        run = str(step.get("run", ""))
        assert "${{" not in run, step.get("name")         # every expression goes through `env:`
    env = next(s for s in load()["jobs"]["auto-pr"]["steps"] if "run" in s)["env"]
    assert env["BRANCH"] == "${{ github.ref_name }}" and env["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_a_workflow_changing_pull_request_is_labelled_for_the_owner_and_never_auto_merged():
    text = run_text()
    marker = "grep -q '^\\.github/workflows/'"
    assert marker in text and "needs-owner" in text
    start = text.index(marker)
    split = text.index("\nelse", start)                   # the `else` of THAT if, not the earlier one around `gh pr create`
    assert "needs-owner" in text[start:split] and "--auto" not in text[start:split]
    assert text.count("--auto") == 1 and text.index("--auto") > split     # the auto-merge sits only in the other branch


def test_it_opens_a_pull_request_only_when_none_is_open_and_the_branch_is_ahead_of_master():
    text = run_text()
    assert "gh pr list --head" in text and "--state open" in text and "gh pr create --base master" in text
    assert "rev-list --count origin/master..HEAD" in text


def test_the_attestation_job_is_advisory_so_publication_does_not_wait_for_a_signature():
    steps = load(GATE)["jobs"]["backlog-gate"]["steps"]
    run = next(s for s in steps if "verify_attestation.py" in str(s.get("run", "")))["run"]
    assert "--advisory" in run
