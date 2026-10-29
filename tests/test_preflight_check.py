# tests/test_preflight_check.py
# -*- coding: utf-8 -*-
"""scripts/audit/preflight_check.py: the pure parsers and the verdict matrix, plus the git reader on a throwaway repository (R-7: no DB)."""
import json
import os
import posixpath
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


# ---------------- driver: parser + summarizer ----------------

def test_parse_driver_probe_reads_sac_and_events_ignoring_junk_lines():
    text = "SAC\t1\nEV\t2026-10-12 13:38:47\t\\Device\\HarddiskVolume3\\...\\libcrypto-3-x64-1b25.dll\nnot a line\nEV\tbad"
    sac, events = pf.parse_driver_probe(text)
    assert sac == "1"
    assert events == [("2026-10-12 13:38:47", "libcrypto-3-x64-1b25.dll")]


def test_parse_driver_probe_on_empty_or_garbled_text_is_quiet():
    assert pf.parse_driver_probe("") == (None, [])
    assert pf.parse_driver_probe("garbage\nmore garbage") == (None, [])


def test_same_interpreter_ignores_case_and_slash_direction():
    assert pf.same_interpreter(r"C:\data\envs\des\python.exe", "c:/data/envs/des/python.exe")
    assert not pf.same_interpreter(r"C:\data\envs\des\python.exe", r"C:\Users\jacor\.conda\envs\des\python.exe")


def test_same_interpreter_gives_the_windows_answer_on_a_posix_host(monkeypatch):
    """The comparison is about Windows paths whatever the host: os.path on Linux CI answered False (FND-0313)."""
    with monkeypatch.context() as m:
        m.setattr(os, "path", posixpath)
        assert pf.same_interpreter(r"C:\data\envs\des\python.exe", "c:/data/envs/des/python.exe")
        assert not pf.same_interpreter(r"C:\data\envs\des\python.exe", r"C:\Users\jacor\.conda\envs\des\python.exe")


def test_summarize_driver_ok_with_no_recent_blocks():
    s = pf.summarize_driver(True, "", "0", [], r"C:\data\envs\des\python.exe", r"C:\data\envs\des\python.exe")
    assert s["import_ok"] and s["blocks"] == 0 and s["newest_block"] is None and s["interpreter_ok"]


def test_summarize_driver_blocked_dedupes_files_and_keeps_order():
    events = [("13:38:47", "libcrypto.dll"), ("13:40:00", "libcrypto.dll"), ("14:10:41", "libssl.dll")]
    s = pf.summarize_driver(False, "OSError: blocked", "1", events, "python.exe", None)
    assert not s["import_ok"] and s["blocks"] == 3 and s["newest_block"] == "14:10:41"
    assert s["blocked_files"] == ["libcrypto.dll", "libssl.dll"]
    assert s["interpreter_ok"]                                     # no `expected` given -> never flags a mismatch


def test_summarize_driver_flags_the_wrong_conda_env():
    s = pf.summarize_driver(True, "", "0", [], r"C:\Users\jacor\.conda\envs\des\python.exe", r"C:\data\envs\des\python.exe")
    assert not s["interpreter_ok"]


# ---------------- verdict ----------------

def _report(**over):
    r = {"driver": {"import_ok": True, "detail": "", "blocks": 0, "newest_block": None, "blocked_files": [],
                    "lookback_hours": 6.0, "interpreter": "x", "expected_interpreter": "x", "interpreter_ok": True},
         "git": {"dirty": {"tracked": [], "untracked": [], "unowned": ["db/oper/a"]}, "ahead": 0, "behind": 0, "stashes": 0, "worktrees": ["a"]},
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


# ── driver: caution when blocked, a note (never a hard stop) for a stale block or a wrong interpreter ──

def test_driver_blocked_is_a_caution_that_names_the_transient_window():
    v = pf.verdict(_report(driver={"import_ok": False, "blocks": 2, "newest_block": "14:10:42"}))
    assert v["level"] == "CAUTION"
    assert any("transient" in c for c in v["cautions"])


def test_driver_blocked_with_no_recent_block_is_still_a_caution_but_differently_worded():
    v = pf.verdict(_report(driver={"import_ok": False, "blocks": 0}))
    assert v["level"] == "CAUTION"
    assert any("RC 106" in c for c in v["cautions"])


def test_driver_loads_but_recent_blocks_exist_is_a_note_not_a_caution():
    v = pf.verdict(_report(driver={"blocks": 1, "newest_block": "14:10:42", "blocked_files": ["libssl.dll"]}))
    assert v["level"] == "CLEAR"
    assert any("RC 106" in n for n in v["notes"])


def test_a_mismatched_interpreter_is_a_note_never_a_hard_failure_cross_platform():
    v = pf.verdict(_report(driver={"interpreter_ok": False, "interpreter": "/usr/bin/python3", "expected_interpreter": r"C:\data\envs\des\python.exe"}))
    assert v["level"] == "CLEAR"
    assert any("-n des" in n for n in v["notes"])


def test_no_driver_section_at_all_changes_nothing_backward_compat():
    r = _report()
    del r["driver"]
    assert pf.verdict(r) == {"level": "CLEAR", "cautions": [], "notes": []}


# ---------------- render ----------------

def test_render_shows_the_driver_section_first_and_lists_blocked_files():
    r = _report(driver={"import_ok": False, "detail": "OSError: blocked", "sac_state": "1", "blocks": 2,
                        "newest_block": "2026-10-12 14:10:42", "blocked_files": ["libssl.dll", "libcrypto.dll"],
                        "interpreter_ok": False, "interpreter": "/usr/bin/python3", "expected_interpreter": r"C:\data\envs\des\python.exe"})
    r["verdict"] = pf.verdict(r)
    out = pf.render(r)
    lines = out.splitlines()
    assert lines[1].startswith("[0] driver:")
    assert "BLOCKED" in lines[1] and "SAC=1" in lines[1] and "MISMATCH" in lines[1]
    assert "blocked: libssl.dll" in out and "blocked: libcrypto.dll" in out
    assert "expected" in out


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
    assert rc == 0 and set(out) >= {"driver", "git", "processes", "db", "memory", "backlog", "verdict", "calc_version"}
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


def test_gate_line_shows_the_audit_window_and_says_when_enforce_can_be_authorized():
    import preflight_check as pf
    base = {"mode": "AUDIT", "hours_in_audit": 12.34, "tickets_48h": 2, "required_tickets": 5, "violations_48h": 0, "clear": False}
    assert pf.gate_line({}) == "" and pf.gate_line(None) == ""
    assert pf.gate_line(base) == "delivery gate: mode AUDIT, 12.3 h in audit, 2 of 5 different tickets moved in the last 48 h, 0 violation(s) in them -> window not clear yet"
    assert pf.gate_line(dict(base, hours_in_audit=50.0, tickets_48h=6, clear=True)).endswith("-> ENFORCE CAN BE AUTHORIZED (window clear)")
    assert "->" not in pf.gate_line(dict(base, mode="ENFORCE", hours_in_audit=None))


def test_a2_line_shows_progress_actors_and_asks_for_more_actors_when_there_are_few():
    import preflight_check as pf
    sp = {"live_non_system_since_last_v00": 10, "v00_total": 0}
    assert pf.a2_line({}, []) == ""
    one = pf.a2_line(sp, [("agent:a", 10)])
    assert one == "A-2 gate: 10 of 50 real transitions since the last V-00 (V-00 total 0); by agent:a x10 (1 distinct actor(s)) -> NEEDS MORE ACTORS before the revoke"
    three = pf.a2_line(sp, [("agent:a", 6), ("human:x", 3), ("human:y", 1)])
    assert three.endswith("(3 distinct actor(s))") and "NEEDS MORE" not in three
    assert pf.a2_line(dict(sp, live_non_system_since_last_v00=0), []) == "A-2 gate: 0 of 50 real transitions since the last V-00 (V-00 total 0)"


def test_iam_line_says_not_applied_or_counts_logins_and_persons_and_names_unbound_writers():
    import preflight_check as pf
    assert pf.iam_line(None) == ""
    assert pf.iam_line({"routines": {"sp_append_log": False}}).startswith("IAM: step iam not applied")
    applied = {"routines": {"sp_append_log": True}, "active_logins": 3, "active_persons": ["a.gomez", "j.cormenzana"], "writers": [], "unbound_writers": []}
    assert pf.iam_line(applied) == "IAM: 3 bound login(s), 2 distinct human person(s)"
    assert pf.iam_line(dict(applied, writers=[{"login": "w", "bound": True}])).endswith("every writing login is bound")
    assert pf.iam_line(dict(applied, unbound_writers=["fondos_writer"])).endswith("UNBOUND logins that can still open or move a ticket: fondos_writer")


def _binding(event_id=1, flags=("RECENT",), **kw):
    row = {"event_id": event_id, "login_name": "iam_qa", "bound_actor": "human:m.ruiz_qa", "person": "human:m.ruiz", "active": True, "declared_by": "human:j.cormenzana_owner",
           "set_by": "fondos_owner", "flags": list(flags)}
    row.update(kw)
    return row


def test_binding_attention_lists_only_what_was_written_in_the_last_24_hours():
    import preflight_check as pf
    audit = [_binding(1, ["SELF_BINDING"]), _binding(2, ["RECENT", "DECLARER_NOT_BOUND_TO_SET_BY"]), _binding(3, ["RECENT"], active=False, declared_by=None)]
    assert [r["event_id"] for r in pf.binding_attention(audit)] == [2, 3] and pf.binding_attention(None) == [] and pf.binding_attention([]) == []
    lines = pf.binding_lines(audit)
    assert len(lines) == 2 and all(line.startswith("[ATENCION] binding #") for line in lines)
    assert "declared by human:j.cormenzana_owner through database login fondos_owner [DECLARER_NOT_BOUND_TO_SET_BY]" in lines[0]
    assert "REVOKED" in lines[1] and "(nobody: legacy)" in lines[1]


def test_the_off_hours_flag_is_shown_on_the_attention_line():
    import preflight_check as pf
    lines = pf.binding_lines([_binding(5, ["OFF_HOURS", "RECENT"])])
    assert len(lines) == 1 and lines[0].endswith("[OFF_HOURS]")


def test_a_recent_binding_makes_the_verdict_a_caution_and_an_old_flagged_one_only_adds_a_count():
    import preflight_check as pf
    base = {"git": {"changed": [], "unowned": []}, "processes": {"running": []}, "memory": {}, "db": {"error": "skipped"}}
    recent = pf.verdict(dict(base, backlog={"iam": {"binding_audit": [_binding(1)]}}))
    assert recent["level"] == "CAUTION" and any("login binding(s) written in the last 24 h" in c for c in recent["cautions"])
    assert "login binding" not in " ".join(pf.verdict(dict(base, backlog={"iam": {"binding_audit": [_binding(1, ["SELF_BINDING"])]}}))["cautions"])
    cat = {"routines": {"sp_append_log": True}, "active_logins": 1, "active_persons": ["human:m.ruiz"], "writers": [], "unbound_writers": [], "binding_audit": [_binding(1, ["SELF_BINDING"])]}
    assert pf.iam_line(cat).endswith("; 1 older binding(s) carry audit flags (backlog.v_binding_audit)")


def test_a2_line_marks_actors_no_bound_login_backs_only_once_iam_exists():
    import preflight_check as pf
    sp = {"live_non_system_since_last_v00": 5, "v00_total": 0}
    actors = [("agent:claude-sonnet-5-5", 4), ("human:jacor", 1)]
    assert "UNBOUND" not in pf.a2_line(sp, actors)                                                                           # no IAM: lines are unchanged
    line = pf.a2_line(sp, actors, bindings=[{"actor": "agent:%", "active": True}, {"actor": "human:j.cormenzana_owner", "active": True}])
    assert "agent:claude-sonnet-5-5 x4" in line and "human:jacor [UNBOUND] x1" in line
    assert "UNBOUND" in pf.a2_line(sp, actors, bindings=[{"actor": "agent:%", "active": False}])


def test_gate_line_mentions_a_recorded_executive_override():
    import preflight_check as pf
    base = {"mode": "ENFORCE", "hours_in_audit": None, "tickets_48h": 0, "required_tickets": 5, "violations_48h": 0, "clear": False, "last_override_ts": None}
    assert "override" not in pf.gate_line(base)
    assert pf.gate_line(dict(base, last_override_ts="2026-10-29 01:02:03+01:00")).endswith("[executive override recorded 2026-10-29 01:02]")
