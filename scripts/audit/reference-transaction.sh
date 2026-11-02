#!/bin/sh
# reference-transaction.sh -- Portable installer for the autopilot trigger (Backlog v3 W3, FND-0358).
#
# Usage (run once from repo root, in each clone, by the owner: this hook fires on EVERY reference update of the clone):
#   bash scripts/audit/reference-transaction.sh
#
# Installs .git/hooks/reference-transaction. Git calls it around every reference update with the state ($1: prepared | committed | aborted) and the updates on stdin
# (`<old> <new> <ref>`). Only the `committed` state of refs/remotes/origin/master matters: when a fetch moves it, the hook starts
# scripts/audit/backlog_autopilot.py --apply --quiet DETACHED (output in out/audit/backlog_autopilot_hook.log) and returns at once.
# FND-0369: it starts the autopilot only if shared/ and scripts/ are byte for byte what origin/master has (a clean master that is only behind is fast-forwarded first); otherwise it
# writes one line to the log saying why and does not start it. Re-run this installer after changing the hook.
# FND-0371: the same event also runs scripts/ops/prune_merged_branches.py --rule ancestor --apply --no-fetch (output in out/audit/prune_merged_branches_hook.log): it deletes the remote branches
# whose tip is already in origin/master. It writes nothing to the backlog, so the FND-0369 guard does not gate it: a clone that sits on a feature branch still prunes. Each of the two
# is skipped on its own when its script is missing.
# It NEVER blocks git: it always exits 0, runs the autopilot in the background, and does nothing if the script is missing (an old branch). What the autopilot may do is decided by
# BACKLOG_AUTOPILOT_MODE in shared/config.py and by its own database login; without FONDOS_BACKLOG_PG_DSN_AUTOPILOT it writes nothing.

set -e

HOOK="$(git rev-parse --git-path hooks/reference-transaction)"

cat > "$HOOK" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/audit/reference-transaction.sh (FND-0358) -- starts the Backlog v3 autopilot when origin/master moves. Never blocks git.
STATE="$1"
FIRE=0
while read -r OLD NEW REF; do
    if [ "$REF" = "refs/remotes/origin/master" ]; then FIRE=1; fi
done
[ "$STATE" = "committed" ] || exit 0
[ "$FIRE" = "1" ] || exit 0

CONDA_PYTHON="$HOME/.conda/envs/des/python.exe"
if [ -f "$CONDA_PYTHON" ]; then
    PYTHON="$CONDA_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    exit 0
fi

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
mkdir -p "$ROOT/out/audit" 2>/dev/null
AUTOPILOT="$ROOT/scripts/audit/backlog_autopilot.py"
PRUNE="$ROOT/scripts/ops/prune_merged_branches.py"
[ -f "$AUTOPILOT" ] || [ -f "$PRUNE" ] || exit 0
# FND-0369: the autopilot writes to the live backlog, so it runs the MERGED code or it does not run. It runs from this working tree, which may be on any branch or hold uncommitted edits, so
# before it starts the hook checks one thing with plain git: that shared/ and scripts/ are byte for byte what origin/master has. A clean master that is only BEHIND is brought forward first
# (fast-forward only, never a merge), so the normal case needs nobody; anything else (another branch with different code, an edit, an untracked file) is skipped with one line in the log.
# This logic lives in this installed hook, outside the working tree, so no branch can change it.
# FND-0371: the branches of auto-merged pull requests stay on GitHub (the merge is made with GITHUB_TOKEN, which starts no workflow), so this clone deletes them: only branches whose tip is
# already in origin/master, with plain git and no cron. It runs BEFORE the guard (it writes nothing to the backlog, and a clone on a feature branch must still prune) and takes seconds, so it
# does not wait for the autopilot. --no-fetch: this hook runs because a fetch just moved origin/master; fetching again could start it again.
(
    cd "$ROOT" || exit 0
    LOG="$ROOT/out/audit/backlog_autopilot_hook.log"
    if [ "$(git branch --show-current 2>/dev/null)" = "master" ] && git diff --quiet HEAD -- 2>/dev/null; then
        git merge --ff-only -q origin/master >/dev/null 2>&1
    fi
    if [ -f "$PRUNE" ]; then
        "$PYTHON" "$PRUNE" --rule ancestor --apply --no-fetch >> "$ROOT/out/audit/prune_merged_branches_hook.log" 2>&1
    fi
    [ -f "$AUTOPILOT" ] || exit 0
    if ! git diff --quiet origin/master -- shared scripts 2>/dev/null || [ -n "$(git ls-files --others --exclude-standard -- shared scripts 2>/dev/null)" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') autopilot NOT started: shared/ and scripts/ are not the merged code of origin/master (branch '$(git branch --show-current 2>/dev/null)'); switch to a clean master" >> "$LOG"
        exit 0
    fi
    "$PYTHON" "$AUTOPILOT" --apply --quiet >> "$LOG" 2>&1
) >/dev/null 2>&1 &
exit 0
EOF

chmod +x "$HOOK"
echo "reference-transaction hook installed at $HOOK"
