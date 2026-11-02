# tests/test_post_commit.py
# -*- coding: utf-8 -*-
"""FND-0376: the installer of the post-commit hook (scripts/audit/post-commit.sh) that starts the automatic publication of master after each commit. The hook must never block or fail a commit, never push by
itself, and do nothing in a clone that lacks the publisher. R-7: no database, no network."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = _ROOT / "scripts" / "audit" / "post-commit.sh"
needs_sh = pytest.mark.skipif(shutil.which("sh") is None or shutil.which("git") is None, reason="sh and git are needed")


def hook_body() -> str:
    src = INSTALLER.read_text(encoding="utf-8")
    return src.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]


def test_the_installed_hook_never_blocks_a_commit_and_runs_the_publisher_detached():
    src = INSTALLER.read_text(encoding="utf-8")
    hook = hook_body()
    assert "auto_publish.py" in hook and ">> \"$ROOT/out/audit/auto_publish.log\"" in hook and ") >/dev/null 2>&1 &" in hook
    assert hook.rstrip().endswith("exit 0") and "set -e" not in hook                                     # a failure here can never fail the commit
    assert "git rev-parse --git-path hooks/post-commit" in src and "chmod +x" in src
    assert "push" not in hook and "--force" not in src                                                   # the hook starts the script; it never pushes, and nothing here forces


@needs_sh
def test_the_installer_writes_an_executable_hook_and_the_hook_is_harmless_without_the_publisher(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    done = subprocess.run(["sh", str(INSTALLER)], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 0 and "post-commit hook installed" in done.stdout
    hook = repo / ".git" / "hooks" / "post-commit"
    assert hook.exists() and hook.read_text(encoding="utf-8") == hook_body() + "\n"
    ran = subprocess.run(["sh", str(hook)], cwd=repo, capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0 and ran.stdout == ""                                                      # no scripts/audit/auto_publish.py in this clone: it does nothing, quietly
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c"], cwd=repo, check=True)   # a real commit through the installed hook succeeds
