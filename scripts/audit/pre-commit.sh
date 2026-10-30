#!/bin/sh
# pre-commit.sh — Portable installer for the AGENTS.md sync pre-commit hook.
#
# Usage (run once from repo root):
#   bash scripts/audit/pre-commit.sh
#
# Installs .git/hooks/pre-commit that runs sync_agents_md.py --check on EXACTLY what is about to be committed
# (FND-0314): the staged state is exported with `git checkout-index` (it honours GIT_INDEX_FILE, so
# `git commit -- <paths>` is covered) and checked there. The working tree is never read: several sessions share
# it, and another session's uncommitted file used to make a commit pass here and fail in clean-checkout CI.
# Any sentinel drift exits 1 and blocks the commit. Fix with: python scripts/audit/sync_agents_md.py --write
# (then stage the regenerated file). Never use `git stash` to isolate the tree: stashes are shared by every session.

set -e

HOOK="$(git rev-parse --git-path hooks/pre-commit)"

cat > "$HOOK" <<'EOF'
#!/bin/sh
# Auto-installed by scripts/audit/pre-commit.sh (FND-0314) — verifies the AGENTS.md sentinel blocks of the STAGED state.
# Drift blocks the commit; fix with: python scripts/audit/sync_agents_md.py --write, then stage the file.

# Resolve interpreter: prefer Conda 'des' env, fall back to system python.
CONDA_PYTHON="$HOME/.conda/envs/des/python.exe"
if [ -f "$CONDA_PYTHON" ]; then
    PYTHON="$CONDA_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "WARNING: No Python interpreter found; skipping AGENTS.md sync check." >&2
    exit 0
fi

TMP="$(mktemp -d 2>/dev/null)" || { echo "WARNING: cannot create a temp dir; skipping AGENTS.md sync check." >&2; exit 0; }
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

git checkout-index -a --prefix="$TMP/" || { echo "ERROR: cannot export the staged state for the AGENTS.md sync check." >&2; exit 1; }
if [ ! -f "$TMP/scripts/audit/sync_agents_md.py" ]; then
    echo "WARNING: the staged state has no scripts/audit/sync_agents_md.py; skipping AGENTS.md sync check." >&2
    exit 0
fi
cd "$TMP" && "$PYTHON" scripts/audit/sync_agents_md.py --check
EOF

chmod +x "$HOOK"
echo "Pre-commit hook installed at $HOOK"
