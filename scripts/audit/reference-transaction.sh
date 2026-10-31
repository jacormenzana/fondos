#!/bin/sh
# reference-transaction.sh -- Portable installer for the autopilot trigger (Backlog v3 W3, FND-0358).
#
# Usage (run once from repo root, in each clone, by the owner: this hook fires on EVERY reference update of the clone):
#   bash scripts/audit/reference-transaction.sh
#
# Installs .git/hooks/reference-transaction. Git calls it around every reference update with the state ($1: prepared | committed | aborted) and the updates on stdin
# (`<old> <new> <ref>`). Only the `committed` state of refs/remotes/origin/master matters: when a fetch moves it, the hook starts
# scripts/audit/backlog_autopilot.py --apply --quiet DETACHED (output in out/audit/backlog_autopilot_hook.log) and returns at once.
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
AUTOPILOT="$ROOT/scripts/audit/backlog_autopilot.py"
[ -f "$AUTOPILOT" ] || exit 0
mkdir -p "$ROOT/out/audit" 2>/dev/null
( "$PYTHON" "$AUTOPILOT" --apply --quiet >> "$ROOT/out/audit/backlog_autopilot_hook.log" 2>&1 & ) >/dev/null 2>&1
exit 0
EOF

chmod +x "$HOOK"
echo "reference-transaction hook installed at $HOOK"
