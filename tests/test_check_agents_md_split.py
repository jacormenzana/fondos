# -*- coding: utf-8 -*-
"""scripts/audit/check_agents_md_split.py: calibration on throwaway git repositories (FND-0312, R-7: no DB)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit" / "check_agents_md_split.py"
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

import check_agents_md_split as cs  # noqa: E402

ORIGINAL = "# Title\nalpha rule\nbeta rule\ngamma rule\n\nepsilon note\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True, capture_output=True)


def _w(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _w(tmp_path, "AGENTS.md", ORIGINAL)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _run(repo, receivers, ledger=None):
    return cs.check(repo, "HEAD", "AGENTS.md", receivers, ledger)


def _split(repo, root_keeps="# Title\nalpha rule\n", sub="beta rule\ngamma rule\nepsilon note\n"):
    _w(repo, "AGENTS.md", root_keeps)
    _w(repo, "sub/AGENTS.md", sub)


def test_1_split_with_nothing_lost_is_clean(repo):
    _split(repo)
    r = _run(repo, ["sub/AGENTS.md"])
    assert r["unaccounted"] == [] and r["moved"] == 3 and r["removed"] == 3


def test_2_one_deleted_line_is_reported_exactly(repo):
    _split(repo, sub="beta rule\nepsilon note\n")
    assert _run(repo, ["sub/AGENTS.md"])["unaccounted"] == [("gamma rule", 1)]


def test_3_a_moved_line_altered_by_one_character_is_reported(repo):
    _split(repo, sub="beta rule\ngamma rulE\nepsilon note\n")
    assert _run(repo, ["sub/AGENTS.md"])["unaccounted"] == [("gamma rule", 1)]


def test_4_ledger_covers_a_deliberately_removed_line(repo):
    _split(repo, sub="beta rule\nepsilon note\n")
    _w(repo, "ledger.txt", "# deduped\ngamma rule ||| canonical in GOBIERNO_CONTROLES.md\n")
    r = _run(repo, ["sub/AGENTS.md"], repo / "ledger.txt")
    assert r["unaccounted"] == [] and r["ledgered"] == 1


def test_6_a_line_that_exists_only_in_a_non_receiver_still_counts_as_lost(repo):
    _split(repo, sub="beta rule\nepsilon note\n")
    _w(repo, "other/NOTES.md", "gamma rule\n")
    assert _run(repo, ["sub/AGENTS.md"])["unaccounted"] == [("gamma rule", 1)]


def test_7_a_line_removed_twice_but_moved_once_is_reported_once(repo):
    _w(repo, "AGENTS.md", "dup line\ndup line\nkeep\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "dup")
    _w(repo, "AGENTS.md", "keep\n")
    _w(repo, "sub/AGENTS.md", "dup line\n")
    assert _run(repo, ["sub/AGENTS.md"])["unaccounted"] == [("dup line", 1)]
    _w(repo, "sub/AGENTS.md", "dup line\ndup line\n")
    assert _run(repo, ["sub/AGENTS.md"])["unaccounted"] == []


def test_markup_only_lines_are_listed_and_never_fail(repo):
    _w(repo, "AGENTS.md", "keep\n---\n|---|---|\n<!-- AUTO:BEGIN x -->\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "markup")
    _w(repo, "AGENTS.md", "keep\n")
    r = _run(repo, [])
    assert r["unaccounted"] == [] and r["removed"] == 0
    assert sorted(ln for ln, _ in r["markup"]) == ["---", "<!-- AUTO:BEGIN x -->", "|---|---|"]


def test_unused_ledger_entry_is_reported(repo):
    _split(repo)
    _w(repo, "ledger.txt", "never existed ||| typo\n")
    assert _run(repo, ["sub/AGENTS.md"], repo / "ledger.txt")["unused_ledger"] == ["never existed"]


def test_ledger_entry_may_start_with_a_hash_and_comments_are_still_skipped(repo):
    """Regression (found in use): a heading entry '### X' was dropped as a comment, leaving its line 'unaccounted'."""
    _w(repo, "AGENTS.md", "### Old heading\nkeep\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "heading")
    _w(repo, "AGENTS.md", "keep\n")
    _w(repo, "ledger.txt", "# a comment, no separator\n\n### Old heading ||| replaced by a pointer\n")
    assert cs.load_ledger(repo / "ledger.txt") == {"### Old heading": 1}
    r = _run(repo, [], repo / "ledger.txt")
    assert r["unaccounted"] == [] and r["ledgered"] == 1


def test_ledger_entry_without_a_reason_is_an_error(repo):
    _w(repo, "ledger.txt", "gamma rule\n")
    with pytest.raises(ValueError, match="ledger entry"):
        cs.load_ledger(repo / "ledger.txt")


def test_5_unchanged_source_reports_nothing_removed(repo):
    assert _run(repo, [])["removed"] == 0


def test_cli_prints_utf8_when_the_windows_streams_are_cp1252(repo):
    """Redirected output on Windows is cp1252: an arrow in a reported line crashed the report with a traceback (FND-0312)."""
    _w(repo, "AGENTS.md", "# Title\nalpha rule\nbeta → rule\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "arrow")
    _split(repo, root_keeps="# Title\nalpha rule\n", sub="")
    (repo / "ledger.txt").write_text("unused → entry ||| reason\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    r = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), "--receivers", "sub/AGENTS.md", "--ledger", str(repo / "ledger.txt")],
                       capture_output=True, env=env, timeout=60)
    assert r.returncode == 1, r.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in r.stderr
    assert "unused → entry".encode("utf-8") in r.stdout               # the stdout warning that used to crash
    assert "UNACCOUNTED x1: beta → rule".encode("utf-8") in r.stderr  # and the stderr line, no longer a \\u2192 escape


def test_cli_exit_codes(repo):
    def cli(*args):
        return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), *args], capture_output=True, text=True, timeout=60)
    _split(repo, sub="beta rule\nepsilon note\n")
    bad = cli("--receivers", "sub/AGENTS.md")
    assert bad.returncode == 1 and "UNACCOUNTED x1: gamma rule" in bad.stderr
    _split(repo)
    assert cli("--receivers", "sub/AGENTS.md").returncode == 0
    assert cli("--receivers", "missing.md").returncode == 2
    assert cli("--base", "no-such-ref").returncode == 2
