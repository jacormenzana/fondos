# -*- coding: utf-8 -*-
"""scripts/audit/pre-commit.sh: the installed hook checks what is COMMITTED, not the shared working tree (FND-0314, R-7: no DB).

A stand-in scripts/audit/sync_agents_md.py fails unless marker.txt reads "ok", so each test says which version of the marker the check saw.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "audit" / "pre-commit.sh"
FAKE_SYNC = ('import sys\n'
             'ok = open("marker.txt", encoding="utf-8").read().strip() == "ok"\n'
             'print("OK" if ok else "FAIL: marker is not ok")\n'
             'sys.exit(0 if ok else 1)\n')

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="the hook is a POSIX sh script")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
                          cwd=repo, capture_output=True, text=True, check=check)


def _marker(repo: Path, text: str) -> None:
    (repo / "marker.txt").write_text(text + "\n", encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "scripts" / "audit").mkdir(parents=True)
    (tmp_path / "scripts" / "audit" / "sync_agents_md.py").write_text(FAKE_SYNC, encoding="utf-8", newline="\n")
    _marker(tmp_path, "ok")
    subprocess.run(["sh", str(INSTALLER)], cwd=tmp_path, check=True, capture_output=True)    # installs into THIS repo's hooks dir
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_a_bad_working_tree_does_not_block_a_good_staged_state(repo):
    """The FND-0312 failure in reverse: another session's dirty file must not decide this commit."""
    _marker(repo, "ok")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _marker(repo, "someone else's uncommitted edit")
    r = _git(repo, "commit", "-m", "a", check=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_bad_staged_state_is_blocked_even_when_the_working_tree_looks_fine(repo):
    """The FND-0312 failure itself: the commit content drifts, the working tree hides it."""
    _marker(repo, "drifted")
    _git(repo, "add", "marker.txt")
    _marker(repo, "ok")
    r = _git(repo, "commit", "-m", "bad", check=False)
    assert r.returncode == 1 and "FAIL: marker is not ok" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_commit_by_path_is_checked_on_the_content_it_will_commit(repo):
    """`git commit -- <path>` commits the working-tree version of that path through a temporary index."""
    _marker(repo, "drifted")
    _git(repo, "add", "marker.txt")                       # index says drifted ...
    _marker(repo, "ok ")                                  # ... the path commit takes the working-tree content (differs from HEAD, still reads ok)
    assert _git(repo, "commit", "-m", "by path ok", "--", "marker.txt", check=False).returncode == 0
    _marker(repo, "drifted again")
    r = _git(repo, "commit", "-m", "by path bad", "--", "marker.txt", check=False)
    assert r.returncode == 1 and "FAIL" in r.stdout + r.stderr


def test_a_staged_state_without_the_sync_script_is_skipped_with_a_warning(repo):
    _git(repo, "rm", "-q", "scripts/audit/sync_agents_md.py")
    r = _git(repo, "commit", "-m", "no script", check=False)
    assert r.returncode == 0 and "skipping AGENTS.md sync check" in r.stderr, r.stdout + r.stderr
