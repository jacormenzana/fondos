# tests/test_backlog_impact.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4 (FND-0359): the pure rules of shared/backlog_impact.py and the runner scripts/audit/backlog_impact.py with a fake client and a fake git. No database, no git, no network (R-7)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_impact as run  # noqa: E402
from shared import backlog_impact as ib  # noqa: E402
from shared import backlog_modules as bm  # noqa: E402

FILES = ["shared/backlog_client.py", "shared/other.py", "shared/third.py", "tests/test_backlog_client.py", "AGENTS.md", "doc/reglas/NORMAS_BACKLOG.md"]
TRACKED, BY_BASE = bm.index_tracked(FILES)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40
M = "shared/backlog_client.py"


def cite(ref, ts, paths):
    return SimpleNamespace(ref=ref, ts=ts, paths=tuple(paths))


def frow(ap, module, role="TOUCHED", source="COMMIT"):
    return {"action_point_id": ap, "module_path": module, "symbol": None, "role": role, "source": source, "evidence": None}


def merge(tickets=("FND-1",), sha=SHA):
    return {"sha": sha, "ts": NOW, "paths": (M,), "tickets": set(tickets)}


OPEN = {"FND-1", "FND-2", "FND-3", "FND-4", "FND-5", "FND-6", "FND-7"}


# ---------------------------------------------------------------- what a merge is

def test_merges_group_the_tickets_they_cite_and_only_those_inside_the_window_are_kept_oldest_first():
    old, new = NOW - timedelta(days=5), NOW - timedelta(hours=3)
    cited = {"FND-1": [cite("b" * 40, new, [M]), cite("c" * 40, old, [M])], "FND-2": [cite("b" * 40, new, [M])]}
    got = ib.merges_from(cited, NOW - timedelta(days=ib.IMPACT_WINDOW_DAYS))
    assert [(m["sha"][0], sorted(m["tickets"])) for m in got] == [("b", ["FND-1", "FND-2"])]


def test_the_modules_of_a_merge_fold_tests_in_and_leave_documents_and_registries_out():
    assert ib.merge_modules(["tests/test_backlog_client.py", "AGENTS.md", "doc/reglas/NORMAS_BACKLOG.md", "shared/other.py"], TRACKED, BY_BASE) == {M, "shared/other.py"}


# ---------------------------------------------------------------- who is affected

def test_a_confirmed_impacts_or_overlaps_link_with_a_cited_ticket_is_the_strongest_signal_in_either_direction():
    links = [("FND-1", "FND-2", "IMPACTS"), ("FND-3", "FND-1", "OVERLAPS"), ("FND-1", "FND-4", "RELATED"), ("FND-5", "FND-6", "IMPACTS")]
    got = ib.plan_notes(merge(), {M}, OPEN, links, [], False)
    assert [(t, s) for t, s, _e, _h in got] == [("FND-2", "LINK"), ("FND-3", "LINK")]               # RELATED is not a signal; a link between two tickets the merge does not cite is not either


def test_the_tickets_the_merge_cites_and_the_closed_ones_are_never_noted():
    links = [("FND-1", "FND-2", "IMPACTS"), ("FND-1", "FND-9", "IMPACTS")]
    rows = [frow("FND-2", M, "CITED", "TEXT"), frow("FND-1", M, "CITED", "TEXT")]
    got = ib.plan_notes(merge(("FND-1", "FND-2")), {M}, OPEN, links, rows, False)
    assert got == []                                                                                # FND-1 and FND-2 are the merge's own, FND-9 is not open


def test_a_text_citation_of_a_changed_module_is_a_signal_unless_five_open_tickets_cite_it():
    rows = [frow("FND-2", M, "CITED", "TEXT")]
    assert [(t, s) for t, s, _e, _h in ib.plan_notes(merge(), {M}, OPEN, [], rows, False)] == [("FND-2", "PATH")]
    hub = [frow(f"FND-{i}", M, "CITED", "TEXT") for i in range(2, 7)]
    assert ib.plan_notes(merge(), {M}, OPEN, [], hub, False) == []                                  # five citers: a tool every ticket mentions


def test_module_facts_count_only_when_the_clusters_have_proved_themselves():
    rows = [frow("FND-2", M, "TARGET", "DECLARED"), frow("FND-3", M, "TOUCHED")]
    assert ib.plan_notes(merge(), {M}, OPEN, [], rows, False) == []
    got = ib.plan_notes(merge(), {M}, OPEN, [], rows, True)
    assert [(t, s) for t, s, _e, _h in got] == [("FND-2", "TARGET"), ("FND-3", "TOUCHED")]


def test_a_ticket_gets_one_note_with_its_strongest_signal_and_the_notes_are_ranked():
    links = [("FND-1", "FND-4", "IMPACTS")]
    rows = [frow("FND-4", M, "CITED", "TEXT"), frow("FND-5", M, "CITED", "TEXT"), frow("FND-6", M, "TOUCHED")]
    got = ib.plan_notes(merge(), {M}, OPEN, links, rows, True)
    assert [(t, s) for t, s, _e, _h in got] == [("FND-4", "LINK"), ("FND-5", "PATH"), ("FND-6", "TOUCHED")]
    assert len({t for t, *_ in got}) == len(got)


def test_a_merge_notes_at_most_five_tickets_and_at_most_one_on_the_weakest_signal():
    many = [frow(f"T-{i}", M, "CITED", "TEXT") for i in range(1, 5)]
    weak = [frow(f"W-{i}", M) for i in range(1, 5)]
    open_ids = {r["action_point_id"] for r in many + weak} | {"FND-1"}
    got = ib.plan_notes(merge(), {M}, open_ids, [], many + weak, True)
    assert [s for _t, s, _e, _h in got] == ["PATH"] * 4 + ["TOUCHED"]                               # four strong notes and ONE weak one, though four tickets are weak
    capped = ib.plan_notes(merge(), {M}, open_ids, [], many + weak, True, max_notes=2)
    assert len(capped) == 2


def test_without_enough_strong_signals_the_weak_one_still_gets_only_one_place():
    one = [frow("T-1", M, "CITED", "TEXT")]
    weak = [frow(f"W-{i}", M) for i in range(1, 5)]
    open_ids = {"T-1", "FND-1"} | {r["action_point_id"] for r in weak}
    got = ib.plan_notes(merge(), {M}, open_ids, [], one + weak, True)
    assert [s for _t, s, _e, _h in got] == ["PATH", "TOUCHED"]                                      # room for five, but three of the four weak tickets are left out


def test_a_ticket_that_holds_one_changed_module_weakly_and_another_as_its_target_is_noted_for_the_target():
    rows = [frow("FND-2", "shared/a.py"), frow("FND-2", "shared/b.py", "TARGET", "DECLARED")]
    got = ib.plan_notes(merge(), {"shared/a.py", "shared/b.py"}, OPEN, [], rows, True)
    assert [(t, s) for t, s, _e, _h in got] == [("FND-2", "TARGET")] and "shared/b.py" in got[0][2] and got[0][3] == ["shared/a.py", "shared/b.py"]


def test_a_sweeping_merge_and_a_merge_with_no_module_note_nothing():
    rows = [frow("FND-2", "shared/x.py", "CITED", "TEXT")]
    wide = {f"shared/m{i}.py" for i in range(ib.MERGE_SWEEP_MODULES + 1)} | {"shared/x.py"}
    assert ib.plan_notes(merge(), wide, OPEN, [("FND-1", "FND-2", "IMPACTS")], rows, True) == []
    assert ib.plan_notes(merge(), set(), OPEN, [("FND-1", "FND-2", "IMPACTS")], rows, True) == []
    edge = {f"shared/m{i}.py" for i in range(ib.MERGE_SWEEP_MODULES - 1)} | {"shared/x.py"}
    assert len(ib.plan_notes(merge(), edge, OPEN, [], rows, True)) == 1                              # exactly at the limit still counts


# ---------------------------------------------------------------- the note

def test_the_note_names_the_merge_the_change_and_the_signal_and_carries_the_key_that_stops_a_rerun():
    text = ib.note_text(merge(("FND-1", "FND-9")), {M, "shared/other.py"}, "FND-2", "PATH", f"its text cites {M}")
    assert text.startswith("[impact] merge aaaaaaaaaaaa for FND-1, FND-9 changed shared/backlog_client.py (+1 more): re-check whether this ticket is resolved or regressed.")
    assert "Signal PATH: its text cites shared/backlog_client.py" in text and text.endswith("(IMPACT:aaaaaaaaaaaa)")
    assert ib.already(SHA, [text]) and not ib.already("b" * 40, [text]) and not ib.already(SHA, ["unrelated", None])


def test_the_distribution_is_the_fatigue_guard_figure():
    d = ib.distribution([0, 0, 1, 1, 2, 2, 3, 5])
    assert d == {"merges": 8, "median": 2, "p90": 5, "max": 5, "silent": 0.25} and ib.distribution([])["merges"] == 0


# ---------------------------------------------------------------- the runner

class FakeBc:
    def __init__(self, present=True, rows=None, links=(), logs=None):
        self.present, self.links, self.appended = present, list(links), []
        self.rows = rows if rows is not None else [frow("FND-2", M, "CITED", "TEXT")]
        self.logs = {a: [x if isinstance(x, tuple) else (x, NOW - timedelta(days=3)) for x in xs] for a, xs in (logs or {}).items()}      # (text, time); a bare text is an old note

    def modules_store_present(self):
        return self.present

    def list_tickets(self, open_only=False):
        return [{"action_point_id": a, "status": "OPEN"} for a in sorted(OPEN)]

    def list_links(self, status=None):
        return [{"from_id": a, "to_id": b, "link_type": t} for a, b, t in self.links]

    def list_ticket_modules(self):
        return self.rows

    def list_logs(self, ids=None):
        return [{"action_point_id": a, "comment": c, "log_ts": ts} for a in (ids or []) for c, ts in self.logs.get(a, [])]

    def append_log(self, ap, actor, text):
        self.appended.append((ap, actor, text))
        self.logs.setdefault(ap, []).append((text, NOW))
        return len(self.appended)


def fake_git(*args):
    return "\n".join(FILES)


def quiet(*_):
    return None


def cited_now(sha=SHA, tickets=("FND-1",), age_hours=3, paths=(M,)):
    return {t: [cite(sha, NOW - timedelta(hours=age_hours), paths)] for t in tickets}


def test_the_switch_off_and_a_missing_phase_do_nothing_and_read_nothing():
    class Strict(FakeBc):
        def list_tickets(self, open_only=False):
            raise AssertionError("must not read")
    assert run.run(Strict(), cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=False)["enabled"] is False
    early = FakeBc(present=False)
    res = run.run(early, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True)
    assert res["present"] is False and early.appended == []


def test_a_dry_run_plans_and_writes_nothing_and_apply_writes_one_note_as_the_autopilot():
    bc = FakeBc()
    dry = run.run(bc, cited_now(), apply=False, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert dry["planned"] == 1 and dry["written"] == 0 and bc.appended == []
    res = run.run(bc, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert res["written"] == 1 and bc.appended[0][:2] == ("FND-2", "system:autopilot") and "[impact] merge aaaaaaaaaaaa" in bc.appended[0][2] and "(IMPACT:aaaaaaaaaaaa)" in bc.appended[0][2]


def test_a_rerun_writes_nothing_because_the_note_carries_the_merge_as_its_key():
    bc = FakeBc()
    run.run(bc, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    again = run.run(bc, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert again["written"] == 0 and again["skipped"] == 1 and len(bc.appended) == 1


def test_several_merges_that_affect_the_same_ticket_give_it_one_note_that_carries_every_key():
    many = {"FND-1": [cite(c * 40, NOW - timedelta(hours=h), [M]) for c, h in (("a", 5), ("b", 4), ("c", 3))]}
    bc = FakeBc()
    res = run.run(bc, many, apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert res["planned"] == 3 and res["tickets"] == 1 and res["written"] == 1 and len(bc.appended) == 1
    text = bc.appended[0][2]
    assert text.startswith("[impact] 3 merges (aaaaaaaaaaaa, bbbbbbbbbbbb, cccccccccccc) for FND-1") and all(f"(IMPACT:{c * 12})" in text for c in "abc")
    again = run.run(bc, many, apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert again["written"] == 0 and again["skipped"] == 3


def test_a_ticket_told_within_the_cooldown_is_not_told_again_and_one_told_long_ago_is():
    recent = FakeBc(logs={"FND-2": [("[impact] merge 111111111111 for FND-9 changed x", NOW - timedelta(hours=2))]})
    res = run.run(recent, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert res["cooldown"] == 1 and res["written"] == 0 and recent.appended == []
    old = FakeBc(logs={"FND-2": [("[impact] merge 111111111111 for FND-9 changed x", NOW - timedelta(hours=ib.IMPACT_COOLDOWN_HOURS + 6))]})
    assert run.run(old, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")["written"] == 1
    other = FakeBc(logs={"FND-2": [("a progress note, not an impact one", NOW - timedelta(hours=1))]})
    assert run.run(other, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")["written"] == 1


def test_the_combined_note_reports_the_strongest_signal_and_all_the_changed_modules_of_its_merges():
    a, b = merge(sha="a" * 40), merge(sha="b" * 40)
    text = ib.combined_text([(a, {M}, "PATH", "its text cites x"), (b, {M, "shared/other.py"}, "LINK", "IMPACTS link with FND-1")])
    assert "Strongest signal LINK: IMPACTS link with FND-1" in text and "changed shared/backlog_client.py (+1 more)" in text and "PATH" not in text


def test_the_combined_note_of_one_merge_is_the_plain_note_and_the_cooldown_boundary_is_exact():
    m = merge()
    assert ib.combined_text([(m, {M}, "PATH", "its text cites x")]) == ib.note_text(m, {M}, "FND-2", "PATH", "its text cites x")
    edge = [{"comment": "[impact] merge", "log_ts": NOW - timedelta(hours=ib.IMPACT_COOLDOWN_HOURS)}]
    assert ib.recently_noted(edge, NOW) is True and ib.recently_noted([{**edge[0], "log_ts": NOW - timedelta(hours=ib.IMPACT_COOLDOWN_HOURS, seconds=1)}], NOW) is False
    assert ib.recently_noted([{"comment": "[impact] merge", "log_ts": None}], NOW) is False


def test_a_merge_older_than_the_window_is_not_noted():
    bc = FakeBc()
    res = run.run(bc, cited_now(age_hours=24 * (ib.IMPACT_WINDOW_DAYS + 1)), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert res["merges"] == 0 and bc.appended == []


def test_a_run_writes_at_most_the_run_cap(monkeypatch):
    rows = [frow(f"FND-{i}", M, "CITED", "TEXT") for i in range(2, 6)]
    bc = FakeBc(rows=rows)
    monkeypatch.setattr(ib, "MAX_NOTES_PER_RUN", 2)
    res = run.run(bc, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert res["planned"] == 4 and res["written"] == 2


def test_the_notes_read_the_breadth_from_the_fresh_facts_and_not_from_the_one_recorded_long_ago():
    stale = [{**frow("FND-2", M), "evidence": "commit cccccccccccc, 40 modules"}]                    # recorded when only a sweeping commit had touched the module
    fresh = {"FND-2": [{"module": M, "role": "TOUCHED", "source": "COMMIT", "evidence": "commit cccccccccccc, 1 module"}]}
    without = run.run(FakeBc(rows=stale), cited_now(), apply=False, git=fake_git, out=quiet, now=NOW, enabled=True, mode="ON")
    with_fresh = run.run(FakeBc(rows=stale), cited_now(), apply=False, git=fake_git, out=quiet, now=NOW, enabled=True, mode="ON", desired=fresh)
    assert without["planned"] == 0 and with_fresh["planned"] == 1                                    # the stale breadth drops the fact, the fresh one keeps it


def test_a_refused_write_stops_the_notes_without_failing_the_run():
    class Refusing(FakeBc):
        class BacklogUnavailable(RuntimeError):
            pass

        def append_log(self, ap, actor, text):
            raise self.BacklogUnavailable("live write refused")

    rows = [frow(f"FND-{i}", M, "CITED", "TEXT") for i in range(2, 5)]
    bc = Refusing(rows=rows)
    res = run.run(bc, cited_now(), apply=True, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")
    assert res["unavailable"] is True and res["written"] == 0 and res["planned"] == 3


def test_the_replay_reports_the_distribution_and_never_writes():
    bc = FakeBc(links=[("FND-1", "FND-3", "IMPACTS")])
    lines = []
    out = run.replay(bc, {**cited_now(), **cited_now(sha="b" * 40, tickets=("FND-4",), age_hours=900)}, git=fake_git, mode="OFF", out=lines.append)
    assert out["dist"]["merges"] == 2 and out["dist"]["max"] >= 1 and bc.appended == [] and "notes per merge: median" in lines[0]


def test_the_autopilot_calls_the_impact_step_and_the_switch_exists():
    src = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    assert "impact_run.run(bc, cited" in src
    assert 'BACKLOG_IMPACT_NOTES_ENABLED: bool = True' in (_ROOT / "shared" / "config.py").read_text(encoding="utf-8")
    assert run.ACTOR == "system:autopilot"
