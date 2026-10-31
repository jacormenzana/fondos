#!/bin/sh
# commit-msg.sh -- Portable installer for the Backlog v2 commit-msg gate, the pre-rebase guard and the pre-push collision gate (FND-0334, Phase C slices 2 and 3).
#
# Usage (run once from repo root, in each clone):
#   bash scripts/audit/commit-msg.sh
#
# Installs three hooks, the same way scripts/audit/pre-commit.sh installs the sentinel check:
#   .git/hooks/commit-msg  runs scripts/audit/commit_msg_gate.py <message file>. It validates the FND tokens, moves a `Refs:` ticket forward, writes a gate_check row and appends
#                          `Backlog-Gate: <nonce>` (or `offline`). It NEVER blocks a commit, except for a malformed ticket token such as `fnd0193` (and, if the owner turns the strict switch on, a missing ticket). Backlog unreachable,
#                          slow (3 s budget) or any error: the commit goes through with `Backlog-Gate: offline`.
#   .git/hooks/pre-rebase  refuses to rebase `master` (published history is never rewritten; branches are rebased freely).
#   .git/hooks/pre-push    runs scripts/audit/pre_push_gate.py: in BLOCK mode of the collision gate it refuses to publish commits on tickets with an UNDECIDED collision pair and
#                          prints the shas and pairs; in AUDIT it warns; with the gate OFF (the default) it does nothing. Backlog unreachable or any error: the push goes through.
# Only the exit status 3 of the Python scripts refuses a commit or a push: a crash of either script never blocks anything.
# The hooks run the checked-out copy of scripts/audit/commit_msg_gate.py; if it is missing (an old branch) the commit-msg hook does nothing.
# `git commit --no-verify` skips both: the commit then carries no trailer and backlog_sync.py reports it as a bypass once BACKLOG_HOOK_EPOCH is set.

set -e

COMMIT_MSG="$(git rev-parse --git-path hooks/commit-msg)"
PRE_REBASE="$(git rev-parse --git-path hooks/pre-rebase)"
PRE_PUSH="$(git rev-parse --git-path hooks/pre-push)"

cat > "$COMMIT_MSG" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/audit/commit-msg.sh (FND-0334) -- the Backlog v2 commit-msg gate. Never blocks, except a malformed FND token.

# Resolve interpreter: prefer Conda 'des' env, fall back to system python.
CONDA_PYTHON="$HOME/.conda/envs/des/python.exe"
if [ -f "$CONDA_PYTHON" ]; then
    PYTHON="$CONDA_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "WARNING: No Python interpreter found; commit-msg gate skipped." >&2
    exit 0
fi

ROOT="$(git rev-parse --show-toplevel)"
GATE="$ROOT/scripts/audit/commit_msg_gate.py"
[ -f "$GATE" ] || exit 0
"$PYTHON" "$GATE" "$1"
RC=$?
# 3 = the gate rejected the message (a malformed ticket token, or a missing ticket with the strict switch on). ANY other status, including a crash of the script, lets the commit go through.
[ "$RC" -eq 3 ] && exit 1
exit 0
EOF

cat > "$PRE_REBASE" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/audit/commit-msg.sh (FND-0334) -- refuses to rebase master. $1 = upstream, $2 = the branch being rebased (empty = the current one).
BRANCH="${2:-$(git symbolic-ref --short -q HEAD)}"
if [ "$BRANCH" = "master" ]; then
    echo "pre-rebase: refusing to rebase master (published history). Branch from origin/master and rebase the branch." >&2
    exit 1
fi
exit 0
EOF

cat > "$PRE_PUSH" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/audit/commit-msg.sh (FND-0334) -- the collision gate at publication. $1 = remote name, $2 = url; the ref updates arrive on stdin.
CONDA_PYTHON="$HOME/.conda/envs/des/python.exe"
if [ -f "$CONDA_PYTHON" ]; then
    PYTHON="$CONDA_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "WARNING: No Python interpreter found; pre-push gate skipped." >&2
    exit 0
fi

ROOT="$(git rev-parse --show-toplevel)"
GATE="$ROOT/scripts/audit/pre_push_gate.py"
[ -f "$GATE" ] || exit 0
"$PYTHON" "$GATE" "$@"
RC=$?
# 3 = refused: an undecided collision pair on the tickets of this push (BLOCK mode). Any other status, including a crash, lets the push go through.
[ "$RC" -eq 3 ] && exit 1
exit 0
EOF

chmod +x "$COMMIT_MSG" "$PRE_REBASE" "$PRE_PUSH"
echo "commit-msg hook installed at $COMMIT_MSG"
echo "pre-rebase hook installed at $PRE_REBASE"
echo "pre-push hook installed at $PRE_PUSH"
