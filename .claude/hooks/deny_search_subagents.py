"""PreToolUse hook (matcher Agent|Task): deny the Explore subagent, allow every other subagent type.

Reads the hook JSON from stdin. Denies only when tool_input.subagent_type == "Explore" (search/read belongs in the
main context, CLAUDE.md "Subagents"); the Plan agent and any other type are allowed. Fails open: malformed or
unexpected input exits 0 with no output, so a bug here can never block work.
"""
import json
import sys

DENY_REASON = (
    "Explore subagents are denied in this repository (CLAUDE.md 'Subagents'): run Grep, Glob and Read directly "
    "in the main context. The 'Plan' subagent remains authorized for complex architectural reasoning."
)


def decide(payload) -> dict | None:
    """Return the hook response for a denial, or None to allow."""
    if not isinstance(payload, dict):
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or tool_input.get("subagent_type") != "Explore":
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": DENY_REASON,
        }
    }


def main() -> int:
    try:
        response = decide(json.load(sys.stdin))
    except Exception:
        return 0
    if response is not None:
        sys.stdout.write(json.dumps(response))
    return 0


if __name__ == "__main__":
    sys.exit(main())
