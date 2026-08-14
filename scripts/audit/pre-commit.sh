#!/bin/sh
# pre-commit.sh — Portable installer for the AGENTS.md sync pre-commit hook.
#
# Usage (run once from repo root):
#   bash scripts/audit/pre-commit.sh
#
# Installs .git/hooks/pre-commit that runs sync_agents_md.py --check
# before every commit. Any sentinel drift exits 1 and blocks the commit.
# Fix with: python scripts/audit/sync_agents_md.py --write

set -e

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"

# Resolve the Python interpreter: prefer the Conda 'des' environment,
# fall back to whatever 'python' resolves to on PATH.
CONDA_PYTHON="/Users/Administrador/anaconda3/envs/des/python.exe"
if [ -f "$CONDA_PYTHON" ]; then
    PYTHON="$CONDA_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo "ERROR: No Python interpreter found. Install Python or activate the 'des' Conda env." >&2
    exit 1
fi

cat > "$HOOK" <<EOF
#!/bin/sh
# Auto-installed by scripts/audit/pre-commit.sh — verifies AGENTS.md sentinel blocks are in sync.
# Drift in any sentinel block blocks the commit; fix with:
#   python scripts/audit/sync_agents_md.py --write
exec "$PYTHON" scripts/audit/sync_agents_md.py --check
EOF

chmod +x "$HOOK"
echo "Pre-commit hook installed at $HOOK"
echo "  Python: $PYTHON"
