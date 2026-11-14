#!/bin/sh
# post-commit.sh -- Portable installer for the automatic publication of master (FND-0376; plan ACTION_PLAN_BACKLOG_REFACTORING, Revision 2, R2.7; Directive D-0: no recurring manual `git push`).
#
# Usage (run once from repo root, in each clone, by the owner: this hook fires on EVERY commit of the clone):
#   bash scripts/gov/repo/hooks/post-commit.sh
#
# Installs .git/hooks/post-commit. After each commit it starts scripts/gov/delivery/auto_publish.py DETACHED (output in out/audit/auto_publish.log) and returns at once: it never blocks or fails a commit.
# auto_publish pushes master to origin (never forced) when it is ahead; a commit with `Closes:` waits for its local test receipt (scripts/gov/delivery/delivery_attest.py), which publishes by itself when it
# finishes; a failed push is retried with backoff, by the deploy watcher and by the next commit, and alerts within 15 minutes. `python scripts/gov/delivery/auto_publish.py --now` publishes a hotfix at once.
# Re-run this installer after changing the hook. To remove it: delete .git/hooks/post-commit.
set -e
HOOK="$(git rev-parse --git-path hooks/post-commit)"
cat > "$HOOK" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/gov/repo/hooks/post-commit.sh (FND-0376) -- starts the automatic publication of master after a commit. Never blocks git.
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
PUBLISH="$ROOT/scripts/gov/delivery/auto_publish.py"
[ -f "$PUBLISH" ] || exit 0
mkdir -p "$ROOT/out/audit" 2>/dev/null
( cd "$ROOT" && "$PYTHON" "$PUBLISH" >> "$ROOT/out/audit/auto_publish.log" 2>&1 ) >/dev/null 2>&1 &
exit 0
EOF
chmod +x "$HOOK"
echo "post-commit hook installed at $HOOK"
