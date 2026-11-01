# tests/test_backlog_autopilot_run.py
# -*- coding: utf-8 -*-
"""Backlog v3 W3 slice 3 (FND-0358): scripts/audit/backlog_autopilot.py (mode, merge facts read from a real throwaway git repository, the actions in SHADOW and ON with a fake client) and the
git hook that starts it. No live database, no network (R-7)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_autopilot as run  # noqa: E402
import backlog_sync as sync  # noqa: E402
from shared import backlog_autopilot as ap_mod  # noqa: E402
from shared import backlog_client as real  # noqa: E402

HAVE_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAVE_GIT, reason="git is needed")
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- pure

def test_the_actor_constant_equals_the_clients_and_the_modes_can_only_be_lowered():
    assert run.ACTOR == real.AUTOPILOT_ACTOR == "system:autopilot"
    assert run.effective_mode("ON") == "ON" and run.effective_mode("SHADOW") == "SHADOW" and run.effective_mode("OFF") == "OFF"
    assert run.effective_mode("ON", "SHADOW") == "SHADOW" and run.effective_mode("SHADOW", "OFF") == "OFF"
    assert run.effective_mode("SHADOW", "ON") == "SHADOW" and run.effective_mode("OFF", "ON") == "OFF"      # --mode never raises the ceiling
    assert run.effective_mode("on") == "OFF" and run.effective_mode("") == "OFF"                           # an unknown ceiling is OFF, never ON
    with pytest.raises(ValueError):
        run.effective_mode("ON", "FULL")


def test_the_configured_ceiling_is_a_known_mode_and_the_database_mode_is_the_real_switch():
    from shared import config
    assert config.BACKLOG_AUTOPILOT_MODE in ("OFF", "SHADOW", "ON")
    # the ceiling is ON only because the stored mode lowers it, and an unreadable stored mode caps the run at SHADOW (a missing mode event never switches the autopilot on)
    assert run.resolve_mode("ON", None, None) == "SHADOW" and run.resolve_mode("ON", None, "bogus") == "SHADOW"
    assert run.resolve_mode("ON", None, "SHADOW") == "SHADOW" and run.resolve_mode("ON", None, "ON") == "ON" and run.resolve_mode("ON", None, "OFF") == "OFF"
    assert run.resolve_mode("SHADOW", None, "ON") == "SHADOW" and run.resolve_mode("ON", "SHADOW", "ON") == "SHADOW" and run.resolve_mode("OFF", None, None) == "OFF"


def test_merge_log_parsing_keeps_only_real_merges_and_orders_nothing_by_itself():
    text = ("a" * 40 + "\x1f" + ("b" * 40 + " " + "c" * 40) + "\x1f2026-11-01T10:00:00+01:00\x1fMerge pull request #5 from x/y\x1e"
            + "d" * 40 + "\x1f" + "e" * 40 + "\x1f2026-11-01T11:00:00+01:00\x1fnot a merge\x1e")
    got = run.parse_merges(text)
    assert [(m["sha"][:1], m["subject"]) for m in got] == [("a", "Merge pull request #5 from x/y")] and got[0]["parents"] == ["b" * 40, "c" * 40]


def test_a_branch_delivers_the_tickets_its_commits_close_with_the_solution_they_state():
    msgs = ["feat: x\n\nCloses: FND-0001\nCo-Authored-By: a <a@b.c>\n", "fix: y\n\nCloses: FND-0002\nSolution: data_fix\nCo-Authored-By: a <a@b.c>\n",
            "docs: z\n\nRefs: FND-0003\nCo-Authored-By: a <a@b.c>\n", "wip\n\nCloses: FND-0001\nSolution: DATA_FIX\nCo-Authored-By: a <a@b.c>\n"]
    assert run.deliveries(msgs) == {"FND-0001": "DATA_FIX", "FND-0002": "DATA_FIX"}                          # Refs alone delivers nothing
    assert run.deliveries(["a\n\nCloses: FND-0001\nSolution: CODE_FIX\nX: y\n", "b\n\nCloses: FND-0001\nSolution: DATA_FIX\nX: y\n"]) == {"FND-0001": "CODE_FIX/DATA_FIX"}   # a conflict is not automatic
    assert run.deliveries(["a\n\nCloses: FND-0001\nX: y\n"]) == {"FND-0001": None}


def test_the_merge_object_takes_the_release_month_in_utc_and_the_pull_request_from_the_subject():
    m = {"sha": "a" * 40, "subject": "Merge pull request #17 from x/y", "author_ts": datetime.fromisoformat("2026-11-30T23:30:00-05:00")}     # = 2026-12-01 04:30 UTC
    mo = run.merge_object(m, None, ["z.py", "db/a.sql"])
    assert (mo.pr, mo.year, mo.month, mo.paths, mo.solution) == (17, 2026, 12, ("db/a.sql", "z.py"), None)
    assert ap_mod.merge_release(mo) == "R2026.12-PR17"


def test_the_merge_carries_its_author_time_in_utc_so_a_rejection_can_be_compared_with_it_and_the_run_hands_the_rework_times_to_the_engine():
    m = {"sha": "a" * 40, "subject": "Merge pull request #17 from x/y", "author_ts": datetime.fromisoformat("2026-11-30T23:30:00-05:00")}
    assert run.merge_object(m, None, ["z.py"]).ts == datetime(2026, 12, 1, 4, 30, tzinfo=timezone.utc)
    src = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    assert "bc.rework_times()" in src and "reworked=reworked" in src                                  # FND-0368: without these two the engine never learns that a delivery was rejected


def test_blockers_are_rendered_as_one_readable_line():
    assert run.blocker_text({"kind": "DEPENDS_ON", "ticket": "FND-0009", "note": None}) == "kind=DEPENDS_ON, ticket=FND-0009" and run.blocker_text("a hold") == "a hold"


# ---------------------------------------------------------------- act(): SHADOW notes, ON moves, refusals

class FakeBc:
    BacklogUnavailable = real.BacklogUnavailable
    AUTOPILOT_ACTOR = real.AUTOPILOT_ACTOR

    def __init__(self, refuse=None, down=False):
        self.moves, self.notes, self.refuse, self.down = [], [], refuse, down

    def auto_transition(self, ap, to, **kw):
        if self.down:
            raise real.BacklogUnavailable("FONDOS_BACKLOG_PG_DSN_AUTOPILOT is not set")
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.moves.append((ap, to, kw))
        return 99

    def reopen(self, ap, cause, reason, **kw):
        if self.down:
            raise real.BacklogUnavailable("FONDOS_BACKLOG_PG_DSN_AUTOPILOT is not set")
        if self.refuse:
            raise RuntimeError(self.refuse)
        self.reopens = getattr(self, "reopens", []) + [(ap, cause, reason, kw)]
        return 98

    def append_log(self, ap, username, text):
        self.notes.append((ap, username, text))
        return 1


def decision(action=ap_mod.MOVE, to="READY_FOR_DEPLOY", ref="a" * 40, reasons=(), frm="IN_PROGRESS", solution=None, release=None):
    return ap_mod.Decision(ap="FND-0001", action=action, from_status=frm, to_status=to, evidence_ref=ref, solution_type=solution, release=release, reasons=reasons)


def test_shadow_writes_one_note_per_decision_and_moves_nothing_even_on_a_rerun():
    bc, notes = FakeBc(), {}
    d = decision()
    first = run.act([d], "SHADOW", bc, notes, "S", out=lambda *_: None)
    again = run.act([d], "SHADOW", bc, notes, "S", out=lambda *_: None)
    assert bc.moves == [] and len(bc.notes) == 1 and bc.notes[0][1] == "system:autopilot" and "[autopilot SHADOW] MOVE FND-0001" in bc.notes[0][2]
    assert first["noted"] == 1 and again["skipped"] == 1 and again["noted"] == 0
    assert run.act([decision(ref="b" * 40)], "SHADOW", bc, notes, "S", out=lambda *_: None)["noted"] == 1          # another merge is another decision


def test_on_makes_the_move_with_its_evidence_and_the_release_only_for_a_closure():
    bc = FakeBc()
    res = run.act([decision(), decision(action=ap_mod.CLOSE, to="CLOSED", frm="READY_FOR_DEPLOY", solution="CODE_FIX", release="R2026.11-PR17", ref="b" * 40)], "ON", bc, {}, "S", out=lambda *_: None)
    assert res["moved"] == 1 and res["closed"] == 1 and bc.notes == []
    (ap1, to1, kw1), (ap2, to2, kw2) = bc.moves
    assert (to1, kw1["evidence_ref"], kw1["solution_type"], kw1["release_version"]) == ("READY_FOR_DEPLOY", "a" * 40, None, None)
    assert (to2, kw2["solution_type"], kw2["release_version"]) == ("CLOSED", "CODE_FIX", "R2026.11-PR17") and kw2["comment"].startswith("[autopilot ON] CLOSE FND-0001")


def test_on_notes_a_wait_instead_of_acting_and_a_refusal_of_the_database_becomes_a_note_once():
    bc, notes = FakeBc(), {}
    run.act([decision(action=ap_mod.WAIT, reasons=("delivery gate: a prerequisite is open",))], "ON", bc, notes, "S", out=lambda *_: None)
    assert bc.moves == [] and "delivery gate: a prerequisite is open" in bc.notes[0][2]
    bc2, notes2 = FakeBc(refuse="sp_auto_transition: the merge changes db/**; no DEPLOY event\nDETAIL: more"), {}
    res = run.act([decision()], "ON", bc2, notes2, "S", out=lambda *_: None)
    again = run.act([decision()], "ON", bc2, notes2, "S", out=lambda *_: None)
    assert res["refused"] == 1 and res["noted"] == 1 and "refused by the database: sp_auto_transition: the merge changes db/**" in bc2.notes[0][2] and "DETAIL" not in bc2.notes[0][2]
    assert again["skipped"] == 1 and len(bc2.notes) == 1


def test_a_missing_login_is_a_configuration_problem_not_a_refusal_so_it_raises():
    with pytest.raises(real.BacklogUnavailable):
        run.act([decision()], "ON", FakeBc(down=True), {}, "S", out=lambda *_: None)


# ---------------------------------------------------------------- v3.1 events in act(): REOPEN, UNBLOCK, UNDEFER

def test_on_reopens_with_the_incomplete_fix_cause_and_unblocks_and_undefers_with_their_own_evidence():
    bc = FakeBc()
    res = run.act([decision(action=ap_mod.REOPEN, to="IN_PROGRESS", frm="CLOSED", ref="c" * 40),
                   ap_mod.Decision(ap="FND-0002", action=ap_mod.UNBLOCK, from_status="BLOCKED", to_status="IN_PROGRESS", evidence_kind="BLOCKER", evidence_ref="FND-0009"),
                   ap_mod.Decision(ap="FND-0003", action=ap_mod.UNDEFER, from_status="DEFERRED", to_status="TODO", evidence_kind="REVISIT", evidence_ref="2026-11-01")],
                  "ON", bc, {}, "S", out=lambda *_: None)
    assert (res["reopened"], res["unblocked"], res["undeferred"], res["moved"]) == (1, 1, 1, 0) and bc.notes == []
    (rap, cause, reason, kw), = bc.reopens
    assert (rap, cause, kw["actor"]) == ("FND-0001", "INCOMPLETE_FIX", "system:autopilot") and reason.startswith("[autopilot ON] REOPEN FND-0001")
    (_a, _t, kw_unblock), (_b, _u, kw_undefer) = bc.moves
    assert (kw_unblock["evidence_kind"], kw_unblock["evidence_ref"], kw_unblock["release_version"]) == ("BLOCKER", "FND-0009", None)
    assert (kw_undefer["evidence_kind"], kw_undefer["evidence_ref"]) == ("REVISIT", "2026-11-01")


def test_in_shadow_an_event_is_only_a_note_and_a_refused_reopen_becomes_a_wait_note():
    bc = FakeBc()
    run.act([decision(action=ap_mod.REOPEN, to="IN_PROGRESS", frm="CLOSED")], "SHADOW", bc, {}, "S", out=lambda *_: None)
    assert getattr(bc, "reopens", []) == [] and bc.moves == [] and "[autopilot SHADOW] REOPEN FND-0001" in bc.notes[0][2]
    bc2 = FakeBc(refuse="sp_reopen: FND-0001 is IN_PROGRESS, not CLOSED")
    res = run.act([decision(action=ap_mod.REOPEN, to="IN_PROGRESS", frm="CLOSED")], "ON", bc2, {}, "S", out=lambda *_: None)
    assert res["refused"] == 1 and res["reopened"] == 0 and "refused by the database: sp_reopen" in bc2.notes[0][2]


# ---------------------------------------------------------------- v3.1 pure helpers: the database mode, the waits, the branch log

def test_wait_facts_take_the_latest_matching_wait_and_only_a_ticket_id_counts_as_a_blocker():
    tickets = {"FND-0001": {"status": "BLOCKED"}, "FND-0002": {"status": "BLOCKED"}, "FND-0003": {"status": "DEFERRED"}, "FND-0004": {"status": "IN_PROGRESS"}, "FND-0005": {"status": "DEFERRED"}}
    rows = [dict(action_point_id="FND-0001", kind="BLOCKED", blocker_ref="FND-0008", revisit_by=None),
            dict(action_point_id="FND-0001", kind="BLOCKED", blocker_ref="FND-0009", revisit_by=None),               # the latest wins
            dict(action_point_id="FND-0002", kind="BLOCKED", blocker_ref="the owner", revisit_by=None),               # free text: no fact
            dict(action_point_id="FND-0003", kind="DEFERRED", blocker_ref=None, revisit_by=date(2026, 11, 1)),
            dict(action_point_id="FND-0004", kind="BLOCKED", blocker_ref="FND-0008", revisit_by=None),               # no longer blocked
            dict(action_point_id="FND-0005", kind="DEFERRED", blocker_ref=None, revisit_by=None)]                    # deferred on an event, not a date
    assert run.wait_facts(rows, tickets) == ({"FND-0001": "FND-0009"}, {"FND-0003": date(2026, 11, 1)})


def test_an_aware_time_stays_and_a_naive_one_is_read_as_utc():
    assert run.aware(None) is None and run.aware(datetime(2026, 11, 1)).tzinfo == timezone.utc and run.aware(NOW) is NOW


def test_the_branch_log_gives_the_latest_author_time_that_cites_each_ticket():
    text = ("2026-11-01T10:00:00+00:00\x1ffeat: a\n\nRefs: FND-0001\nCo-Authored-By: t <t@t>\n\x1e"
            "2026-11-02T10:00:00+00:00\x1ffeat: b\n\nCloses: FND-0001\nCo-Authored-By: t <t@t>\n\x1e"
            "2026-11-03T10:00:00+00:00\x1fchore: c, cites nothing\n\x1e")
    commits = run.parse_branch_log(text)
    assert len(commits) == 3 and run.citations_of(commits) == {"FND-0001": datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)}


# ---------------------------------------------------------------- git: a real throwaway repository

class Repo:
    def __init__(self, tmp_path):
        self.root = tmp_path / "repo"
        self.root.mkdir()
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        self.git("init", "-q", "-b", "master")
        self.commit("README", "x", "init")

    def git(self, *a):
        p = subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True, encoding="utf-8", env=self.env, timeout=60)
        assert p.returncode == 0, (a, p.stderr)
        return p.stdout.strip()

    def commit(self, name, text, message):
        f = self.root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message)

    def pull_request(self, number, branch, files, message, when=None):
        self.git("checkout", "-q", "-b", branch)
        for name in files:
            self.commit(name, branch, message)
        self.git("checkout", "-q", "master")
        saved = dict(self.env)
        if when:                                                                                            # the merge commit gets this date (author and committer)
            self.env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        try:
            self.git("merge", "--no-ff", "-q", branch, "-m", f"Merge pull request #{number} from me/{branch}")
        finally:
            self.env = saved


@needs_git
def test_the_merges_of_a_real_history_become_facts_for_the_tickets_their_commits_close(tmp_path):
    r = Repo(tmp_path)
    r.pull_request(5, "feat/a", ["shared/a.py"], "feat: do a\n\nCloses: FND-0001\nCo-Authored-By: t <t@t>")
    r.pull_request(6, "feat/b", ["db/b.sql", "tests/b.py"], "feat: do b\n\nCloses: FND-0002\nSolution: DATA_FIX\nCo-Authored-By: t <t@t>")
    r.pull_request(7, "docs/c", ["doc/c.md"], "docs: only refs\n\nRefs: FND-0003\nCo-Authored-By: t <t@t>")
    got = run.collect_merges("master", 365, cwd=r.root)
    assert sorted(got) == ["FND-0001", "FND-0002"]                                                         # the Refs-only branch delivers nothing
    (m1, mo1), = got["FND-0001"]
    assert (mo1.pr, mo1.paths, mo1.solution, mo1.ref) == (5, ("shared/a.py",), None, m1["sha"])
    (_m2, mo2), = got["FND-0002"]
    assert (mo2.pr, mo2.paths, mo2.solution) == (6, ("db/b.sql", "tests/b.py"), "DATA_FIX")


@needs_git
def test_every_merge_that_cites_a_ticket_is_a_citation_with_its_files_even_when_it_delivers_nothing(tmp_path):
    r = Repo(tmp_path)
    r.pull_request(5, "feat/a", ["shared/a.py"], "feat: do a\n\nCloses: FND-0001\nCo-Authored-By: t <t@t>")
    r.pull_request(7, "docs/c", ["doc/c.md"], "docs: only refs\n\nRefs: FND-0003\nCo-Authored-By: t <t@t>")
    r.pull_request(8, "fix/d", ["shared/d.py", "tests/d.py"], "fix: more work\n\nRefs: FND-0001\nCo-Authored-By: t <t@t>")
    delivered, cited = run.scan_merges("master", 365, cwd=r.root)
    assert sorted(delivered) == ["FND-0001"] and sorted(cited) == ["FND-0001", "FND-0003"]
    first, second = cited["FND-0001"]
    assert first.paths == ("shared/a.py",) and second.paths == ("shared/d.py", "tests/d.py") and first.ts.tzinfo is not None and second.ts >= first.ts
    assert cited["FND-0003"][0].paths == ("doc/c.md",) and not any(ap_mod.is_code_path(p) for p in cited["FND-0003"][0].paths)


@needs_git
def test_a_merge_older_than_the_window_is_not_read(tmp_path):
    r = Repo(tmp_path)
    assert run.collect_merges("master", 30, cwd=r.root) == {}                                               # no merge at all
    r.pull_request(5, "feat/old", ["a.py"], "feat: a\n\nCloses: FND-0001\nCo-Authored-By: t <t@t>", when="2026-01-15T10:00:00+00:00")
    r.pull_request(6, "feat/new", ["b.py"], "feat: b\n\nCloses: FND-0002\nCo-Authored-By: t <t@t>")
    assert sorted(run.collect_merges("master", 3650, cwd=r.root)) == ["FND-0001", "FND-0002"]
    recent = run.collect_merges("master", 30, cwd=r.root)
    assert sorted(recent) == ["FND-0002"] and recent["FND-0002"][0][1].pr == 6


@needs_git
def test_the_sync_now_counts_a_closes_trailer_as_a_refs_one(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    r.commit("a.py", "a", "feat: deliver\n\nCloses: FND-0001\nCo-Authored-By: t <t@t>")
    monkeypatch.setattr(sync, "ROOT", r.root)
    commits = sync.parse_log(sync.git_log("master"))
    top = commits[0]
    assert top["refs"] == ["FND-0001"] and top["mentions"] == ["FND-0001"]
    assert sync.citations(top) == [("FND-0001", "REFS")]


# ---------------------------------------------------------------- the hook and the installer

def test_the_hook_installer_only_reacts_to_origin_master_never_blocks_and_runs_detached():
    text = (_ROOT / "scripts" / "audit" / "reference-transaction.sh").read_text(encoding="utf-8")
    assert 'REF" = "refs/remotes/origin/master"' in text and '"$STATE" = "committed"' in text
    assert "--apply --quiet" in text and "& ) >/dev/null 2>&1" in text and text.count("exit 0") >= 4
    assert "exit 1" not in text and "exit 3" not in text                                                    # it can never refuse a reference update


@needs_git
@pytest.mark.skipif(shutil.which("sh") is None, reason="sh is needed")
def test_installed_hook_starts_the_autopilot_in_the_background_only_when_origin_master_moves(tmp_path):
    r = Repo(tmp_path)
    (r.root / "scripts" / "audit").mkdir(parents=True)
    marker = r.root / "ran.txt"
    (r.root / "scripts" / "audit" / "backlog_autopilot.py").write_text(f"import sys\nopen(r'{marker}', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n", encoding="utf-8")
    inst = subprocess.run(["sh", str(_ROOT / "scripts" / "audit" / "reference-transaction.sh")], cwd=r.root, capture_output=True, text=True, env=r.env, timeout=60)
    assert inst.returncode == 0, inst.stderr
    head = r.git("rev-parse", "HEAD")
    r.git("update-ref", "refs/heads/other", head)                                                          # an unrelated reference: nothing starts
    time.sleep(1.0)
    assert not marker.exists()
    r.git("update-ref", "refs/remotes/origin/master", head)                                                # what a fetch does
    for _ in range(50):
        if marker.exists():
            break
        time.sleep(0.2)
    assert marker.exists() and marker.read_text(encoding="utf-8").strip() == "--apply --quiet"


# ---------------------------------------------------------------- the process: no login, no write

def test_apply_without_the_autopilot_login_or_a_reachable_backlog_fails_cleanly_and_writes_nothing(tmp_path):
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", "FONDOS_BACKLOG_PG_DSN_AUTOPILOT", "FONDOS_BACKLOG_PG_DSN_HUMAN", "FONDOS_BACKLOG_PG_DSN_AGENT")}
    env.update(FONDOS_ENV_FILE=str(empty_env_file), FONDOS_BACKLOG_PG_DSN="postgresql://w:pw@127.0.0.1:1/none", FONDOS_PG_DSN="postgresql://a:pw@127.0.0.1:1/none")
    r = subprocess.run([sys.executable, "-X", "utf8", "scripts/audit/backlog_autopilot.py", "--apply"], cwd=_ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert r.returncode == 2 and "Traceback" not in r.stderr, (r.returncode, r.stderr[-400:])
