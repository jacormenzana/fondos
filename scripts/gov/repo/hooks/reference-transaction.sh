#!/bin/sh
# reference-transaction.sh -- Portable installer for the autopilot trigger (Backlog v3 W3, FND-0358; clean copy since FND-0379).
#
# Usage (run once from repo root, in each clone, by the owner: this hook fires on EVERY reference update of the clone; re-run it after changing the hook):
#   bash scripts/audit/reference-transaction.sh
#
# Installs .git/hooks/reference-transaction. Git calls it around every reference update with the state ($1: prepared | committed | aborted) and the updates on stdin
# (`<old> <new> <ref>`). Only the `committed` state of refs/remotes/origin/master matters: when a fetch or a push moves it, the hook starts the autopilot DETACHED (output in
# out/audit/backlog_autopilot_hook.log) and returns at once.
# FND-0379 (replaces the FND-0369 guard): the autopilot writes to the live backlog, so it must run the MERGED code. It no longer runs from the working tree and no longer needs the tree to equal
# origin/master: the hook keeps a CLEAN CLONE under out/autopilot_clone, checks origin/master out in it (detached, forced) and runs scripts/audit/backlog_autopilot.py FROM THERE, so the code is the
# merged code by construction and whatever the working tree holds (another branch, edits, untracked files, another session's work) is irrelevant. The copy reads the MAIN tree's .env and writes to the
# MAIN tree's out/ (FONDOS_ENV_FILE, FONDOS_OUT_DIR). One run at a time (a lock directory); a push that lands while a run is in progress is picked up by that run's next turn.
# FND-0371: the same event also runs scripts/ops/prune_merged_branches.py --rule ancestor --apply --no-fetch (output in out/audit/prune_merged_branches_hook.log): it deletes the remote branches
# whose tip is already in origin/master. It writes nothing to the backlog and runs from the working tree. Each of the two is skipped on its own when its script is missing.
# It NEVER blocks git: it always exits 0, runs everything in the background, and does nothing if there is nothing to run (an old origin/master without the autopilot). What the autopilot may do
# is decided by BACKLOG_AUTOPILOT_MODE in shared/config.py and by its own database login; without FONDOS_BACKLOG_PG_DSN_AUTOPILOT it writes nothing.

set -e

HOOK="$(git rev-parse --git-path hooks/reference-transaction)"

cat > "$HOOK" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/audit/reference-transaction.sh (FND-0358, FND-0379) -- starts the Backlog v3 autopilot from a clean clone of origin/master when origin/master moves. Never blocks git.
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
PRUNE="$ROOT/scripts/ops/prune_merged_branches.py"
(
    cd "$ROOT" || exit 0
    LOG="$ROOT/out/audit/backlog_autopilot_hook.log"
    stamp() { date '+%Y-%m-%d %H:%M:%S'; }
    if [ -f "$PRUNE" ]; then
        "$PYTHON" "$PRUNE" --rule ancestor --apply --no-fetch >> "$ROOT/out/audit/prune_merged_branches_hook.log" 2>&1
    fi
    CLEAN="$ROOT/out/autopilot_clone"
    LOCK="$ROOT/out/autopilot_clone.lock"
    # one run at a time; a lock directory nobody has touched for 30 minutes belongs to a dead run
    if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then rmdir "$LOCK" 2>/dev/null; fi
    mkdir "$LOCK" 2>/dev/null || exit 0
    trap 'rmdir "$LOCK" 2>/dev/null' EXIT
    # EVERY git command on the clone names its git directory and work tree explicitly: if the clone is ever damaged (an empty or corrupt .git), git must fail, never walk up and operate on the
    # MAIN repository, where `checkout -f` would destroy uncommitted work. A clone that is not a valid repository is deleted and made again.
    cg() { git --git-dir="$CLEAN/.git" --work-tree="$CLEAN" -c core.autocrlf=false "$@"; }
    DONE=""
    for ROUND in 1 2 3; do
        # the commit the run is FOR: read at the start of each turn, so a push that lands during a run is picked up by the next turn
        SHA="$(git rev-parse -q --verify refs/remotes/origin/master 2>/dev/null)" || break
        [ "$SHA" = "$DONE" ] && break
        if [ ! -d "$CLEAN/.git" ] || ! cg rev-parse --git-dir >/dev/null 2>&1; then
            rm -rf "$CLEAN"
            git -c core.autocrlf=false clone -q --no-hardlinks --no-checkout "$ROOT" "$CLEAN" >> "$LOG" 2>&1 || { echo "$(stamp) autopilot NOT started: the clean clone could not be created" >> "$LOG"; break; }
        fi
        cg fetch -q "$ROOT" "+refs/remotes/origin/master:refs/remotes/origin/master" >> "$LOG" 2>&1 \
            && cg checkout -q -f --detach "$SHA" >> "$LOG" 2>&1 \
            || { echo "$(stamp) autopilot NOT started: the clean clone could not be brought to origin/master ${SHA}" >> "$LOG"; break; }
        cg clean -fdxq -- shared scripts >> "$LOG" 2>&1
        AUTOPILOT="$CLEAN/scripts/audit/backlog_autopilot.py"
        if [ ! -f "$AUTOPILOT" ]; then echo "$(stamp) autopilot not run: origin/master ${SHA} has no scripts/audit/backlog_autopilot.py" >> "$LOG"; break; fi
        PYTHONDONTWRITEBYTECODE=1 FONDOS_ENV_FILE="$ROOT/.env" FONDOS_OUT_DIR="$ROOT/out" "$PYTHON" "$AUTOPILOT" --apply --quiet >> "$LOG" 2>&1
        DONE="$SHA"
    done
) >/dev/null 2>&1 &
exit 0
EOF

chmod +x "$HOOK"
echo "reference-transaction hook installed at $HOOK"
