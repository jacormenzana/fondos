# tests/test_backlog_gate_workflow.py
# -*- coding: utf-8 -*-
"""Phase C slice 4 (FND-0334): the CI workflow of the signed attestation is pinned by reading it. Nothing here needs a database or the network."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "backlog-gate.yml"
SIGNERS = ROOT / ".github" / "backlog_gate_allowed_signers"
yaml = pytest.importorskip("yaml")


def load():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_job_is_named_backlog_gate_because_that_is_the_required_check_context():
    wf = load()
    assert list(wf["jobs"]) == ["backlog-gate"] and wf["jobs"]["backlog-gate"]["name"] == "backlog-gate"
    from shared import backlog_protection as prot
    assert prot.CHECK == "backlog-gate"                   # the context the sync looks for among the required status checks of master


def test_it_runs_on_push_pull_request_and_by_hand_with_read_only_permissions():
    wf = load()
    on = wf.get("on", wf.get(True))                       # PyYAML reads the bare key `on` as the boolean True
    assert set(on) == {"push", "pull_request", "workflow_dispatch"} and "base" in on["workflow_dispatch"]["inputs"]
    assert wf["permissions"] == {"contents": "read"}


def test_it_fetches_the_full_history_and_runs_the_verifier_on_the_chosen_base():
    steps = load()["jobs"]["backlog-gate"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    run = next(s for s in steps if "verify_attestation.py" in str(s.get("run", "")))
    assert "--base" in run["run"] and "steps.base.outputs.base" in run["run"]
    assert (ROOT / "scripts" / "audit" / "verify_attestation.py").is_file()


def test_untrusted_inputs_are_never_interpolated_into_a_shell_script():
    """`${{ github.event.inputs.base }}` and the other event fields go through `env:`, never inline in a `run:` block (script injection)."""
    for step in load()["jobs"]["backlog-gate"]["steps"]:
        run = str(step.get("run", ""))
        for danger in ("github.event.inputs", "github.event.pull_request", "github.event.before", "github.head_ref"):
            assert danger not in run, (step.get("name"), danger)


def test_the_signers_file_names_the_principal_the_verifier_expects_and_lists_no_key_until_the_owner_adds_one():
    from shared import backlog_attest as att
    text = SIGNERS.read_text(encoding="utf-8")
    assert att.PRINCIPAL in text and att.NAMESPACE in text
    assert att.ALLOWED_SIGNERS == ".github/backlog_gate_allowed_signers"
    assert not att.has_signer(text) or re.search(r"^backlog-gate-owner namespaces=\"backlog-gate\" ssh-", text, re.M)   # empty, or a well-formed line for the right principal
