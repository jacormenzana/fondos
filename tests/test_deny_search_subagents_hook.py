"""The PreToolUse hook denies Explore, allows Plan and every other subagent type, and fails open on bad input."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "deny_search_subagents.py"


def _run(stdin: str) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout


def _payload(subagent_type) -> str:
    return json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type, "prompt": "x"}})


def test_explore_is_denied_with_a_redirecting_reason():
    rc, out = _run(_payload("Explore"))
    assert rc == 0
    spec = json.loads(out)["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse" and spec["permissionDecision"] == "deny"
    assert "Grep, Glob and Read" in spec["permissionDecisionReason"]


def test_plan_and_other_types_are_allowed():
    for t in ("Plan", "general-purpose", "claude-code-guide", None):
        assert _run(_payload(t)) == (0, ""), t


def test_malformed_or_unexpected_input_fails_open():
    for bad in ("", "not json", "[]", json.dumps({"tool_input": "x"}), json.dumps({"tool_input": {}})):
        assert _run(bad) == (0, ""), bad


# ─── portability: no machine-specific interpreter in the settings, and a deny rule that needs none ────

SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
WRAPPER = HOOK.with_name("run_deny_search_subagents.sh")
needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="the hook command runs through a POSIX sh")


def test_explore_is_denied_by_a_rule_that_needs_no_interpreter():
    assert "Agent(Explore)" in json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]


def test_hook_command_names_no_interpreter_and_points_to_an_existing_wrapper():
    commands = [h["command"] for e in json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert commands and all("python" not in c and ":/" not in c for c in commands), commands
    assert all(WRAPPER.name in c for c in commands) and WRAPPER.is_file()


@needs_sh
def test_wrapper_runs_the_hook_with_the_interpreter_named_by_FONDOS_PYTHON():
    def run(stdin):
        return subprocess.run(["sh", str(WRAPPER)], input=stdin, capture_output=True, text=True, timeout=30,
                              env={**os.environ, "FONDOS_PYTHON": sys.executable})
    denied = run(_payload("Explore"))
    assert denied.returncode == 0 and json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = run(_payload("Plan"))
    assert (allowed.returncode, allowed.stdout) == (0, "")
