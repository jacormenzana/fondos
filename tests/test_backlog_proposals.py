# tests/test_backlog_proposals.py
# -*- coding: utf-8 -*-
"""FND-0378: opening a ticket proposes relationships and groupings in the same call (plan step 2.8a): the closest open tickets by text, the modules the text cites and the open tickets that change them,
returned on the result and written as one log line. Information only: never a link, never a gate, never an error for the caller. No database, no network (R-7): the session and the ranking are fakes."""
from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from shared import backlog_client as bc
from shared import backlog_proposals as bp

AGENT, HUMAN, SYSTEM = "agent:claude-sonnet-5-5", "human:jacor", "system:autopilot"


# ── pure rules ──────────────────────────────────────────────────────────────────

def rows(*items):
    return [{"action_point_id": a, "module_path": m, "role": r} for a, m, r in items]


def test_same_module_tickets_are_open_ones_that_change_the_module_most_shared_first():
    r = rows(("FND-0002", "shared/a.py", "TARGET"), ("FND-0002", "shared/b.py", "TOUCHED"), ("FND-0003", "shared/a.py", "TOUCHED"), ("FND-0004", "shared/a.py", "CITED"),
             ("FND-0005", "shared/a.py", "TARGET"), ("FND-0006", "shared/z.py", "TARGET"), ("FND-0009", "shared/a.py", "TARGET"))
    got = bp.same_module_tickets({"shared/a.py", "shared/b.py"}, r, open_ids={"FND-0002", "FND-0003", "FND-0004", "FND-0006", "FND-0009"}, exclude="FND-0009")
    assert got == ["FND-0002", "FND-0003"]                      # most shared modules first; CITED never counts, FND-0005 is closed, FND-0006 is elsewhere, the new ticket itself is excluded


def test_the_list_of_same_module_tickets_is_capped():
    r = rows(*[(f"FND-{i:04d}", "shared/a.py", "TOUCHED") for i in range(1, 30)])
    assert len(bp.same_module_tickets({"shared/a.py"}, r, {f"FND-{i:04d}" for i in range(1, 30)})) == bp.MAX_SAME_MODULE


def test_the_note_says_what_was_found_and_that_nothing_was_recorded_and_is_none_when_empty():
    note = bp.note_text([(0.31, "FND-0007", "OPEN", "Overlapping title")], ["shared/a.py"], ["FND-0002"])
    assert note.startswith("[proposals at opening]") and "FND-0007 [OPEN] Overlapping title (0.31)" in note and "shared/a.py" in note and "FND-0002" in note
    assert "a rank, not a verdict" in note and "nothing was recorded as a link" in note
    assert bp.note_text([], [], []) is None
    assert "closest open tickets" not in bp.note_text([], ["shared/a.py"], [])


# ── open_ticket plumbing (fake session) ─────────────────────────────────────────

class Cur:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append(re.sub(r"\s+", " ", sql))
        return Cur(("FND-0900",))


@pytest.fixture
def world(monkeypatch):
    notes, conn = [], FakeConn()

    @contextmanager
    def session(*a, **k):
        yield conn

    monkeypatch.setattr(bc, "_session", session)
    monkeypatch.setattr(bc, "append_log", lambda ap, who, text: notes.append((ap, who, text)) or 1)
    return SimpleNamespace(notes=notes, conn=conn, monkeypatch=monkeypatch)


def opened(world, actor=AGENT, **kw):
    world.monkeypatch.setenv("FONDOS_ACTOR", actor)
    return bc.open_ticket(title="t", nature="TSK", object_type="N/A", priority="LOW", session_id="FND_20261103_DEV_TEST", description="touches shared/backlog_client.py", first_log="first", **kw)


GOT = {"neighbours": [(0.4, "FND-0007", "OPEN", "Near")], "modules": ["shared/backlog_client.py"], "same_module": ["FND-0002"]}


def test_an_agents_open_returns_the_proposals_and_writes_one_note_on_the_new_ticket(world):
    seen = []
    out = opened(world, proposals=lambda text, apid: seen.append((text, apid)) or GOT)
    assert str(out) == "FND-0900" and out.neighbours == ((0.4, "FND-0007", "OPEN", "Near"),) and out.modules == ("shared/backlog_client.py",) and out.same_module == ("FND-0002",)
    assert seen[0][1] == "FND-0900" and "touches shared/backlog_client.py" in seen[0][0] and "first" in seen[0][0]
    assert len(world.notes) == 1 and world.notes[0][0] == "FND-0900" and world.notes[0][1] == AGENT and world.notes[0][2] == out.proposal_note
    assert "FND-0007" in out.proposal_note and "sp_open_ticket" in " ".join(world.conn.calls) and not any("backlog_link_event" in c for c in world.conn.calls)   # no link was recorded


def test_a_system_actor_writes_the_note_and_a_person_only_gets_the_proposals(world):
    opened(world, actor=SYSTEM, proposals=lambda t, a: GOT)
    assert len(world.notes) == 1
    world.notes.clear()
    out = opened(world, actor=HUMAN, proposals=lambda t, a: GOT)
    assert out.proposal_note and out.neighbours and world.notes == []                                        # a person's call never writes the extra line


def test_proposals_can_be_turned_off_and_an_empty_result_writes_nothing(world):
    ran = []
    world.monkeypatch.setattr(bc, "default_proposals", lambda text, apid: ran.append(apid) or GOT)
    out = opened(world, proposals=False)
    assert out.neighbours == () and out.proposal_note is None and world.notes == [] and ran == []             # the default proposer is not even called
    assert opened(world).neighbours and ran == ["FND-0900"]                                                  # and with no argument the default one is
    world.notes.clear()
    out = opened(world, proposals=lambda t, a: {"neighbours": [], "modules": [], "same_module": []})
    assert out.proposal_note is None and world.notes == []


def test_a_proposer_that_fails_never_fails_the_open(world):
    def boom(text, apid):
        raise RuntimeError("the ranking could not read the tickets")
    out = opened(world, proposals=boom)
    assert str(out) == "FND-0900" and out.neighbours == () and out.proposal_note is None and world.notes == []


def test_a_failing_note_never_fails_the_open_either(world):
    world.monkeypatch.setattr(bc, "append_log", lambda *a: (_ for _ in ()).throw(RuntimeError("log store down")))
    out = opened(world, proposals=lambda t, a: GOT)
    assert str(out) == "FND-0900" and out.neighbours


def test_a_call_with_an_injected_connection_proposes_nothing(world):
    asked = []
    world.monkeypatch.setenv("FONDOS_ACTOR", AGENT)
    out = bc.open_ticket(title="t", nature="TSK", object_type="N/A", priority="LOW", session_id="FND_20261103_DEV_TEST", description="d", first_log="l", conn=world.conn,
                         proposals=lambda t, a: asked.append(1) or GOT)
    assert str(out) == "FND-0900" and asked == [] and out.proposal_note is None                              # transactions of tests and of the hermetic suites are left alone


# ── the default proposer ────────────────────────────────────────────────────────

def test_the_default_proposer_ranks_by_text_derives_the_cited_modules_and_finds_the_open_tickets_on_them(monkeypatch):
    seen = {}
    fake_links = SimpleNamespace(neighbours=lambda text, exclude=None: seen.update(text=text, exclude=exclude) or [(0.5, "FND-0007", "OPEN", "Near")])
    monkeypatch.setitem(sys.modules, "backlog_links", fake_links)
    monkeypatch.setattr(bc, "list_tickets", lambda **k: [{"action_point_id": "FND-0002"}, {"action_point_id": "FND-0003"}])
    monkeypatch.setattr(bc, "list_ticket_modules", lambda **k: rows(("FND-0002", "shared/backlog_client.py", "TOUCHED"), ("FND-0003", "shared/config.py", "TARGET"), ("FND-0008", "shared/backlog_client.py", "TARGET")))
    got = bc.default_proposals("fix open_ticket in shared/backlog_client.py and a file nobody has: nothing_here.py", "FND-0900")
    assert got["neighbours"] == [(0.5, "FND-0007", "OPEN", "Near")] and got["modules"] == ["shared/backlog_client.py"]
    assert got["same_module"] == ["FND-0002"]                                                                # FND-0008 is not open, FND-0003 is on another module
    assert seen["exclude"] == "FND-0900" and "backlog_client.py" in seen["text"]


def test_a_text_that_cites_no_module_does_not_read_the_module_facts(monkeypatch):
    monkeypatch.setitem(sys.modules, "backlog_links", SimpleNamespace(neighbours=lambda text, exclude=None: []))
    monkeypatch.setattr(bc, "list_ticket_modules", lambda **k: (_ for _ in ()).throw(AssertionError("module facts were read")))
    got = bc.default_proposals("a text with no file in it", "FND-0900")
    assert got == {"neighbours": [], "modules": [], "same_module": []}
