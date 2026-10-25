#!/bin/sh
# Runs deny_search_subagents.py with the first Python interpreter that exists, so the PreToolUse hook does not hard-code one machine's path.
# Order: $FONDOS_PYTHON (explicit override) -> the repo's conda env -> python3 -> python. Never blocks work: with no interpreter it warns and exits 0.
# The guarantee that Explore is denied is the `Agent(Explore)` rule in permissions.deny (.claude/settings.json); this hook only adds the redirecting reason.
here=$(dirname "$0")
for py in "$FONDOS_PYTHON" "C:/data/envs/des/python.exe" python3 python; do
    [ -n "$py" ] || continue
    if command -v "$py" >/dev/null 2>&1; then
        exec "$py" "$here/deny_search_subagents.py"
    fi
done
echo "deny_search_subagents: no Python interpreter found; the Agent(Explore) deny rule still applies" >&2
exit 0
