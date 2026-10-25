"""The PreToolUse hook denies Explore, allows Plan and every other subagent type, and fails open on bad input."""
import json
import subprocess
import sys
from pathlib import Path

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
