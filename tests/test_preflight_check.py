# tests/test_preflight_check.py
# -*- coding: utf-8 -*-
"""scripts/audit/preflight_check.py: the pure parsers and the verdict matrix, plus the git reader on a throwaway repository (R-7: no DB)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "audit"))
sys.path.insert(0, str(ROOT))

import preflight_check as pf  # noqa: E402


# ---------------- parsers ----------------

def test_porcelain_parses_modified_untracked_rename_and_quoted_paths():
    text = " M shared/config.py\n?? db/oper/x.sql\nR  old/name.py -> new/name.py\nA  \"dir with space/f.py\"\nMM both.py\n"
    assert pf.parse_porcelain(text) == [
        (" M", "shared/config.py"), ("??", "db/oper/x.sql"), ("R ", "new/name.py"), ("A ", "dir with space/f.py"), ("MM", "both.py")]
    assert pf.parse_porcelain("") == []


def test_split_dirty_separates_known_unowned_untracked_and_tracked():
    d = pf.split_dirty(pf.parse_porcelain("?? db/oper/a.sql\n?? scratch.txt\n M AGENTS.md\n"))
    assert d == {"tracked": ["AGENTS.md"], "untracked": ["scratch.txt"], "unowned": ["db/oper/a.sql"], "mine": []}


@pytest.mark.parametrize("text, expected", [("0\t5\n", (0, 5)), ("  3 0 ", (3, 0)), ("", None), ("fatal: bad revision", None)])
def test_left_right_counts(text, expected):
    assert pf.parse_left_right(text) == expected


def test_process_matching_is_case_insensitive_and_never_returns_the_checker_or_itself():
    rows = [(1, r'C:\env\python.exe -m proyecto2.src.pipeline.run_pipeline --force'),
            (2, r'cmd /c "scripts\launch\P2_calculateIndicators.bat"'),
            (3, r'python.exe scripts/audit/preflight_check.py'),
            (4, r'python.exe -c "print(1)"'),
            (5, r'python.exe -m PYTEST -q tests')]
    got = pf.match_processes(rows, own_pid=5)
    assert [p for p, _ in got] == [1, 2]


def test_calc_version_is_read_from_the_source_text():
    assert pf.code_calc_version('X = 1\nCALC_VERSION: str = "20261011"  # v42 (FND-0253)\n') == "20261011"
    assert pf.code_calc_version('CALC_VERSION = "20260101"\n') == "20260101"
    assert pf.code_calc_version("nothing here") is None


def test_declared_own_files_are_listed_apart_and_do_not_count_as_another_sessions_work():
    entries = pf.parse_porcelain(" M AGENTS.md\n?? scripts/audit/preflight_check.py\n?? tests/test_preflight_check.py\n?? db/oper/z.sql\n")
    d = pf.split_dirty(entries, mine=("scripts/audit/preflight_check.py", "tests/"))
    assert d == {"tracked": ["AGENTS.md"], "untracked": [], "unowned": ["db/oper/z.sql"],
                 "mine": ["scripts/audit/preflight_check.py", "tests/test_preflight_check.py"]}
    # a Windows-style declaration matches the forward-slash paths git prints
    assert pf.split_dirty([("??", "scripts/audit/x.py")], mine=("scripts\\audit\\x.py",))["mine"] == ["scripts/audit/x.py"]


# ---------------- verdict ----------------

def _report(**over):
    r = {"git": {"dirty": {"tracked": [], "untracked": [], "unowned": ["db/oper/a"]}, "ahead": 0, "behind": 0, "stashes": 0, "worktrees": ["a"]},
         "processes": {"running": [], "lock_held": False},
         "db": {"lifecycle_installed": True, "production": ["s1"], "audit_age_days": 0.5, "dominant_version": "20261011"},
         "memory": {"recent": [], "recent_hours": 6.0}, "backlog": {"new": [], "since_hours": 24.0}, "calc_version": "20261011"}
    for k, v in over.items():
        r[k] = {**r[k], **v} if isinstance(v, dict) and isinstance(r.get(k), dict) else v
    return r


def test_a_quiet_repository_is_clear_and_unowned_dirs_do_not_count():
    v = pf.verdict(_report())
    assert v == {"level": "CLEAR", "cautions": [], "notes": []}


@pytest.mark.parametrize("over, fragment", [
    ({"git": {"dirty": {"tracked": ["AGENTS.md"], "untracked": [], "unowned": []}}}, "another session's work in flight"),
    ({"git": {"dirty": {"tracked": [], "untracked": ["new.py"], "unowned": []}}}, "another session's work in flight"),
    ({"processes": {"running": [{"pid": 7, "cmd": "run_pipeline"}]}}, "long job is in flight"),
    ({"processes": {"lock_held": True}}, "cycle lock"),
    ({"db": {"production": []}}, "no PRODUCTION scenario"),
    ({"db": {"p2_rows_last_10min": 12}}, "P2 run is in progress"),
])
def test_each_caution_trigger(over, fragment):
    v = pf.verdict(_report(**over))
    assert v["level"] == "CAUTION" and any(fragment in c for c in v["cautions"])


def test_notes_do_not_turn_the_verdict_into_caution():
    v = pf.verdict(_report(git={"ahead": 5, "behind": 1, "stashes": 2, "worktrees": ["a", "b"]},
                           db={"audit_age_days": 9.0, "dominant_version": "20261010"},
                           memory={"recent": ["x.md"]}, backlog={"new": [{"id": "FND-1", "status": "OPEN", "title": "t"}]}))
    assert v["level"] == "CLEAR"
    text = " ".join(v["notes"])
    for frag in ("5 local commit", "1 commit(s) on origin", "stash", "worktrees", "9.0 days", "20261010", "memory file", "ticket"):
        assert frag in text


def test_an_unreadable_database_is_a_note_not_silence():
    v = pf.verdict(_report(db={"error": "cannot connect: OperationalError"}))
    assert any("database section not read" in n for n in v["notes"])
    assert pf.verdict(_report(db={"error": "skipped"}))["notes"] == []


# ---------------- git reader on a throwaway repository ----------------

def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_read_git_sees_dirty_untracked_and_known_unowned_files(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "new.py").write_text("y = 1\n")
    (tmp_path / "db" / "oper").mkdir(parents=True)
    (tmp_path / "db" / "oper" / "z.sql").write_text("-- z\n")
    monkeypatch.setattr(pf, "ROOT", tmp_path)
    g = pf.read_git()
    assert g["dirty"]["tracked"] == ["a.py"]
    assert g["dirty"]["untracked"] == ["new.py"]
    assert g["dirty"]["unowned"] == ["db/oper/z.sql"]
    assert g["ahead"] is None and g["behind"] is None            # no origin/master: unreadable, not zero
    assert g["stashes"] == 0 and len(g["worktrees"]) == 1


# ---------------- CLI ----------------

def test_memory_section_flags_recent_files_with_their_session_id(tmp_path):
    (tmp_path / "MEMORY.md").write_text("# index\n")
    new = tmp_path / "project_new.md"
    new.write_text("---\nname: n\nmetadata:\n  originSessionId: abcdef12-0000\n---\nbody\n")
    old = tmp_path / "project_old.md"
    old.write_text("---\nname: o\nmetadata:\n  originSessionId: 99999999-1111\n---\nbody\n")
    t = time.time() - 40 * 3600
    os.utime(old, (t, t))
    m = pf.read_memory(str(tmp_path), recent_hours=6.0)
    assert m["recent"] == ["project_new.md"]
    sessions = {r["file"]: r["session"] for r in m["newest"]}
    assert sessions == {"project_new.md": "abcdef12", "project_old.md": "99999999"}


def test_cli_json_is_parseable_and_strict_maps_caution_to_exit_1(monkeypatch, capsys, tmp_path):
    rc = pf.main(["--no-db", "--json", "--memory-dir", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and set(out) >= {"git", "processes", "db", "memory", "backlog", "verdict", "calc_version"}
    bad = _report(processes={"running": [{"pid": 1, "cmd": "run_pipeline"}]})
    bad["verdict"] = pf.verdict(bad)
    monkeypatch.setattr(pf, "build_report", lambda args: bad)
    assert pf.main(["--strict"]) == 1
    assert pf.main([]) == 0
    assert "VERDICT: CAUTION" in capsys.readouterr().out


def test_the_cycle_lock_is_held_only_when_the_repos_own_predicate_says_so(monkeypatch, tmp_path):
    lock = tmp_path / "fondos_cycle.lock"
    monkeypatch.setattr(pf, "LOCK_FILE", lock)
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    import migrate_fund_metric_state_ols_calc_version as mig
    monkeypatch.setattr(mig, "LOCK_FILE", lock)
    assert pf.cycle_lock_held() is False                      # no file
    lock.write_text("")
    assert pf.cycle_lock_held() is False                      # a stale file nobody holds open is NOT a held lock
