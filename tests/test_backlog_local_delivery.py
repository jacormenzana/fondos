# tests/test_backlog_local_delivery.py
# -*- coding: utf-8 -*-
"""FND-0375: local delivery mode (BACKLOG_GITHUB_EVIDENCE_ENABLED = False). A plain commit on the first-parent history of master that says `Closes: FND-nnnn` is itself a delivery (no merge commit, no pull
request), with its full sha as the evidence; commits before the cutover are never deliveries; the `tests_pass` probe reads the local receipt; a closure leaves its evidence on the ticket. R-7: no database,
no network; git runs on a throwaway repository."""
from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "gov" / "backlog"), str(_ROOT / "scripts" / "gov" / "delivery"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_autopilot as run  # noqa: E402
import rfd_matrix_probe as probe  # noqa: E402
from shared import backlog_autopilot as ap_mod  # noqa: E402
from shared import config as cfg  # noqa: E402
from test_backlog_autopilot_run import FakeBc, Repo, decision  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
CUT = datetime(2000, 1, 1, tzinfo=timezone.utc)
TRAILER = "\nCo-Authored-By: t <t@t>"


def sha_of(r):
    return r.git("rev-parse", "HEAD")


# ── the cutover setting ─────────────────────────────────────────────────────────

def test_local_since_is_none_on_the_github_path_and_loud_when_malformed():
    assert run.local_since(SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=True, BACKLOG_LOCAL_DELIVERY_SINCE="")) is None
    got = run.local_since(SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False, BACKLOG_LOCAL_DELIVERY_SINCE="2026-11-02T22:22:43+01:00"))
    assert got.tzinfo is not None and got.astimezone(timezone.utc).hour == 21
    assert run.local_since(SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False, BACKLOG_LOCAL_DELIVERY_SINCE="2026-11-02T10:00:00")).tzinfo is not None   # a naive cutover is read as UTC
    for bad in ("", "yesterday"):
        with pytest.raises(ValueError):
            run.local_since(SimpleNamespace(BACKLOG_GITHUB_EVIDENCE_ENABLED=False, BACKLOG_LOCAL_DELIVERY_SINCE=bad))


def test_the_shipped_configuration_is_the_local_mode_with_a_valid_cutover():
    assert cfg.BACKLOG_GITHUB_EVIDENCE_ENABLED is False
    assert run.local_since(cfg) is not None


# ── plain commits as deliveries ─────────────────────────────────────────────────

@needs_git
def test_a_plain_commit_with_closes_is_its_own_delivery_and_its_files_are_the_union_of_the_work_on_the_ticket(tmp_path):
    r = Repo(tmp_path)
    r.commit("db/pg_backlog/x.sql", "a", "feat: ddl\n\nRefs: FND-0001" + TRAILER)
    r.commit("shared/a.py", "b", "feat: code\n\nCloses: FND-0001" + TRAILER)
    full = sha_of(r)
    delivered, cited = run.scan_merges("master", 365, cwd=r.root, plain_since=CUT)
    (meta, mo), = delivered["FND-0001"]
    assert mo.ref == full and len(mo.ref) == 40 and meta["sha"] == full and mo.pr is None
    assert mo.paths == ("db/pg_backlog/x.sql", "shared/a.py")                                  # the db/** change made on the way still waits for its DEPLOY fact
    assert mo.solution is None and ap_mod.merge_release(mo) == f"R{mo.year}.{mo.month:02d}-M{full[:7].upper()}"
    assert [c.paths for c in cited["FND-0001"]] == [("db/pg_backlog/x.sql",), ("shared/a.py",)]
    assert all(c.ts.tzinfo is not None for c in cited["FND-0001"])


@needs_git
def test_commits_made_in_the_same_second_keep_their_order_so_the_union_is_complete(tmp_path):
    r = Repo(tmp_path)
    r.env.update(GIT_AUTHOR_DATE="2026-11-02T22:30:00+00:00", GIT_COMMITTER_DATE="2026-11-02T22:30:00+00:00")
    r.commit("db/pg_backlog/x.sql", "a", "feat: ddl\n\nRefs: FND-0012" + TRAILER)
    r.commit("shared/a.py", "b", "feat: code\n\nCloses: FND-0012" + TRAILER)
    delivered, _cited = run.scan_merges("master", 365, cwd=r.root, plain_since=CUT)
    assert delivered["FND-0012"][0][1].paths == ("db/pg_backlog/x.sql", "shared/a.py")


@needs_git
def test_a_refs_only_commit_cites_the_ticket_but_delivers_nothing(tmp_path):
    r = Repo(tmp_path)
    r.commit("shared/a.py", "a", "wip\n\nRefs: FND-0002" + TRAILER)
    delivered, cited = run.scan_merges("master", 365, cwd=r.root, plain_since=CUT)
    assert delivered == {} and list(cited) == ["FND-0002"]


@needs_git
def test_the_solution_trailer_and_several_tickets_work_as_in_a_merge(tmp_path):
    r = Repo(tmp_path)
    r.commit("proyecto2/a.py", "a", "fix\n\nCloses: FND-0003\nCloses: FND-0004\nSolution: DATA_FIX" + TRAILER)
    delivered, _cited = run.scan_merges("master", 365, cwd=r.root, plain_since=CUT)
    assert sorted(delivered) == ["FND-0003", "FND-0004"] and delivered["FND-0003"][0][1].solution == "DATA_FIX"


@needs_git
def test_a_commit_before_the_cutover_is_never_a_delivery(tmp_path):
    r = Repo(tmp_path)
    r.env.update(GIT_AUTHOR_DATE="2020-01-01T10:00:00+00:00", GIT_COMMITTER_DATE="2020-01-01T10:00:00+00:00")
    r.commit("shared/old.py", "a", "old direct commit\n\nCloses: FND-0005" + TRAILER)
    del r.env["GIT_AUTHOR_DATE"], r.env["GIT_COMMITTER_DATE"]
    r.commit("shared/new.py", "b", "new\n\nCloses: FND-0006" + TRAILER)
    delivered, cited = run.scan_merges("master", 365, cwd=r.root, plain_since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert sorted(delivered) == ["FND-0006"] and sorted(cited) == ["FND-0006"]


@needs_git
def test_on_the_github_path_plain_commits_are_not_read(tmp_path):
    r = Repo(tmp_path)
    r.commit("shared/a.py", "a", "fix\n\nCloses: FND-0007" + TRAILER)
    assert run.scan_merges("master", 365, cwd=r.root) == ({}, {})


@needs_git
def test_merges_are_still_read_next_to_plain_commits_and_a_branch_commit_is_not_read_twice(tmp_path):
    r = Repo(tmp_path)
    r.pull_request(5, "feat/a", ["shared/a.py"], "feat: a\n\nCloses: FND-0008" + TRAILER)
    r.commit("shared/b.py", "b", "feat: b\n\nCloses: FND-0009" + TRAILER)
    delivered, cited = run.scan_merges("master", 365, cwd=r.root, plain_since=CUT)
    assert sorted(delivered) == ["FND-0008", "FND-0009"] and len(delivered["FND-0008"]) == 1 and delivered["FND-0008"][0][1].pr == 5
    assert delivered["FND-0009"][0][1].pr is None and len(cited["FND-0008"]) == 1


@needs_git
def test_a_later_plain_commit_with_code_on_a_closed_ticket_is_a_citation_for_the_reopening(tmp_path):
    r = Repo(tmp_path)
    r.commit("shared/a.py", "a", "feat\n\nCloses: FND-0010" + TRAILER)
    r.commit("shared/a.py", "b", "more work\n\nRefs: FND-0010" + TRAILER)
    _delivered, cited = run.scan_merges("master", 365, cwd=r.root, plain_since=CUT)
    first, second = cited["FND-0010"]
    assert second.ts >= first.ts and second.paths == ("shared/a.py",) and ap_mod.is_code_path(second.paths[0])


# ── the closure leaves its evidence ─────────────────────────────────────────────

class BrokenNoteBc(FakeBc):
    def append_log(self, ap, username, text):
        raise RuntimeError("log store down")


def closing():
    return decision(action=ap_mod.CLOSE, to="CLOSED", frm="READY_FOR_DEPLOY", solution="CODE_FIX", release="R2026.11-MABCDEF0", ref="b" * 40)


def test_a_closure_writes_its_evidence_on_the_ticket():
    bc = FakeBc()
    res = run.act([closing()], "ON", bc, {}, "S", out=lambda *_: None, proof={"FND-0001": ["tests_pass: local receipt: 2 passed at bbbbbbbbbbbb, digest 0123456789ab"]})
    assert res["closed"] == 1 and bc.moves[0][1] == "CLOSED"
    assert any("closing evidence: tests_pass: local receipt: 2 passed" in text for _ap, _who, text in bc.notes)


def test_a_failed_evidence_note_never_undoes_or_reports_the_closure_as_refused():
    bc = BrokenNoteBc()
    res = run.act([closing()], "ON", bc, {}, "S", out=lambda *_: None, proof={"FND-0001": ["x"]})
    assert res["closed"] == 1 and res["refused"] == 0 and len(bc.moves) == 1


def test_a_move_to_ready_for_deploy_writes_no_evidence_note_and_neither_does_a_closure_without_proof():
    bc = FakeBc()
    run.act([decision()], "ON", bc, {}, "S", out=lambda *_: None, proof={"FND-0001": ["x"]})
    run.act([closing()], "ON", bc, {}, "S", out=lambda *_: None)
    assert bc.notes == []


# ── one invocation moves AND closes a delivery (FND-0374) ───────────────────────

class WorldBc:
    """A tiny backlog: one ticket whose status really changes when the autopilot moves it, so the second look of the same invocation sees READY_FOR_DEPLOY."""
    BacklogUnavailable = FakeBc.BacklogUnavailable
    AUTOPILOT_ACTOR = FakeBc.AUTOPILOT_ACTOR
    _AUTOPILOT_DSN_ENV_VAR = "FONDOS_BACKLOG_PG_DSN_AUTOPILOT"

    def __init__(self, status="IN_PROGRESS"):
        self.status, self.moves, self.notes, self.records = status, [], [], 0

    def events_store_present(self): return True                                                            # noqa: E704
    def autopilot_mode(self): return "ON"                                                                  # noqa: E704
    def autonomy_store_present(self): return True                                                          # noqa: E704
    def autopilot_operation(self, actor): return "autopilot"                                               # noqa: E704
    def list_wait_events(self, *a, **k): return []                                                         # noqa: E704
    def rework_times(self): return {}                                                                      # noqa: E704
    def ops_event_keys(self, kind): return []                                                              # noqa: E704
    def list_logs(self, ids=None): return []                                                               # noqa: E704
    def auto_release_holds(self, actor=None): return 0                                                     # noqa: E704
    def make_session_id(self, *a): return "FND_20261102_SYNC_AUTOPILOT"                                    # noqa: E704
    def autopilot_track_record(self): return {"decisions": 0, "contradicted": 0, "reopened_closures": 0}   # noqa: E704

    def list_tickets(self, **k):
        return [{"action_point_id": "FND-0001", "status": self.status, "release_version": None, "solution_type": None, "closure_ts": None,
                 "update_ts": datetime(2026, 11, 2, tzinfo=timezone.utc)}]

    def record_ops_event(self, kind, ref, ap, source, payload, ts):
        self.records += 1
        return True

    def auto_transition(self, ap, to, **kw):
        self.moves.append((self.status, to))
        self.status = to
        return 1

    def append_log(self, ap, username, text):
        self.notes.append(text)
        return 1


@needs_git
def test_one_invocation_moves_a_delivery_to_ready_for_deploy_and_closes_it(monkeypatch, capsys, tmp_path):
    import shared
    meta = {"sha": "d" * 40, "parents": ["e" * 40], "author_ts": datetime(2026, 11, 2, 21, 0, tzinfo=timezone.utc), "subject": "feat: x"}
    delivered = {"FND-0001": [(meta, run.merge_object(meta, None, ["shared/a.py"]))]}
    world = WorldBc()
    monkeypatch.setenv("FONDOS_ACTOR", run.ACTOR)
    monkeypatch.setattr(shared, "backlog_client", world, raising=False)
    monkeypatch.setattr(cfg, "BACKLOG_AUTOPILOT_MODE", "ON")
    monkeypatch.setattr(run, "scan_merges", lambda *a, **k: (delivered, {}))
    monkeypatch.setattr(run, "_gather_gates", lambda candidates, *a, **k: ({t: [(True, "tests_pass: local receipt: 2 passed at dddddddddddd")] for t in candidates}, {}, {t: [] for t in candidates}, {t: [] for t in candidates}))
    for name, mod in (("backlog_modules", SimpleNamespace(run=lambda *a, **k: {"recorded": 0})), ("backlog_impact", SimpleNamespace(run=lambda *a, **k: {})),
                      ("backlog_digest", SimpleNamespace(write_if_due=lambda *a, **k: None, build_text=lambda *a, **k: ""))):
        monkeypatch.setitem(sys.modules, name, mod)
    assert run.main(["--apply", "--rev", "master"]) == 0
    assert world.moves == [("IN_PROGRESS", "READY_FOR_DEPLOY"), ("READY_FOR_DEPLOY", "CLOSED")] and world.status == "CLOSED"
    assert any("closing evidence: tests_pass: local receipt: 2 passed" in n for n in world.notes)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("autopilot ON: moved")]
    assert len(lines) == 2 and "moved 1" in lines[0] and "closed 1" in lines[1]


@needs_git
def test_a_run_that_moved_nothing_does_not_look_twice(monkeypatch, capsys):
    import shared
    world = WorldBc(status="CLOSED")
    monkeypatch.setenv("FONDOS_ACTOR", run.ACTOR)
    monkeypatch.setattr(shared, "backlog_client", world, raising=False)
    monkeypatch.setattr(cfg, "BACKLOG_AUTOPILOT_MODE", "ON")
    monkeypatch.setattr(run, "scan_merges", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(run, "_gather_gates", lambda candidates, *a, **k: ({}, {}, {}, {}))
    for name, mod in (("backlog_modules", SimpleNamespace(run=lambda *a, **k: {"recorded": 0})), ("backlog_impact", SimpleNamespace(run=lambda *a, **k: {})),
                      ("backlog_digest", SimpleNamespace(write_if_due=lambda *a, **k: None, build_text=lambda *a, **k: ""))):
        monkeypatch.setitem(sys.modules, name, mod)
    assert run.main(["--apply", "--rev", "master"]) == 0
    assert len([ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("autopilot ON: moved")]) == 1


# ── the probe reads the local receipt ───────────────────────────────────────────

def test_tests_pass_reads_the_local_receipt_and_never_github_in_local_mode(monkeypatch):
    monkeypatch.setattr(cfg, "BACKLOG_GITHUB_EVIDENCE_ENABLED", False)
    never = lambda *a, **k: (_ for _ in ()).throw(AssertionError("GitHub was read"))                   # noqa: E731
    seen = []
    out = probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, read=never, local=lambda root, commit, path: seen.append(commit) or (True, "local receipt: ok"))
    assert out == (True, "tests_pass: local receipt: ok") and seen == ["c" * 40]
    assert probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, local=lambda *a: (None, "unverifiable"))[0] is None
    assert probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, local=lambda *a: (False, "no receipt"))[0] is False
    assert probe.probe_tests_pass("FND-1", evidence={})[0] is False                                      # no delivering commit yet


def test_tests_pass_with_the_github_switch_on_still_reads_github(monkeypatch):
    monkeypatch.setattr(cfg, "BACKLOG_GITHUB_EVIDENCE_ENABLED", True)
    got = []
    out = probe.probe_tests_pass("FND-1", evidence={"FND-1": "c" * 40}, git=lambda cmd: (0, f"{'c' * 40}"), read=lambda *a: got.append(a[2]) or (True, "green"),
                                 local=lambda *a: (_ for _ in ()).throw(AssertionError("the receipt was read")))
    assert out == (True, "tests_pass: green") and got == ["c" * 40]


@needs_git
def test_a_real_receipt_makes_the_real_probe_true_and_a_missing_one_false(tmp_path, monkeypatch):
    import delivery_attest as da
    monkeypatch.setattr(cfg, "BACKLOG_GITHUB_EVIDENCE_ENABLED", False)
    r = Repo(tmp_path)
    r.commit("scripts/tool.py", "x", "feat\n\nCloses: FND-0011" + TRAILER)
    sha = sha_of(r)
    path = r.root / "receipts.jsonl"
    verify = lambda root, commit, _p: da.verify(r.root, commit, path)                                  # noqa: E731 -- the probe's receipts file, redirected to the repository of the test
    assert probe.probe_tests_pass("FND-0011", evidence={"FND-0011": sha}, local=verify)[0] is False
    da.attest(r.root, "HEAD", "full", runner=lambda s, sel, d: (0, "3 passed in 0.1s\n", 0.1, [["pytest"]]), receipts_path=path)
    ok, detail = probe.probe_tests_pass("FND-0011", evidence={"FND-0011": sha}, local=verify)
    assert ok is True and sha[:12] in detail and "3 passed" in detail
