# tests/test_backlog_clusters_run.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4b slice 2 (FND-0360): the cluster step, the proof and the reports of scripts/audit/backlog_modules.py, with a fake client and a fake git. No database, no git, no network (R-7)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_modules as run  # noqa: E402
from shared import backlog_clusters as cl  # noqa: E402
from shared import backlog_modules as bm  # noqa: E402

FILES = ["shared/backlog_client.py", "shared/other.py", "tests/test_backlog_client.py"]
C, D = "c" * 40, "d" * 40
GOOD = {"sample": 30, "precision": 0.8, "recall": 0.6, "recall_text": 0.2, "positives": 40}
SMALL = {"sample": 4, "precision": 1.0, "recall": 1.0, "recall_text": 0.0, "positives": 4}


def trow(ap, module, role="TOUCHED"):
    return {"action_point_id": ap, "module_path": module, "symbol": None, "role": role, "source": "COMMIT"}


class FakeBc:
    """Two open tickets that touch the same module, a closed one, and a group store that remembers what is written to it."""

    def __init__(self, modules=None, events=(), links=()):
        self.modules = modules if modules is not None else [trow("FND-1", "shared/backlog_client.py"), trow("FND-2", "shared/backlog_client.py")]
        self.events, self.links, self.calls, self.refuse = list(events), list(links), [], set()

    def modules_store_present(self):
        return True

    def list_tickets(self, open_only=False):
        rows = [{"action_point_id": "FND-1", "title": "one", "scenario_description": "", "object_name": None, "status": "IN_PROGRESS", "priority": "HIGH"},
                {"action_point_id": "FND-2", "title": "two", "scenario_description": "", "object_name": None, "status": "OPEN", "priority": "LOW"},
                {"action_point_id": "FND-3", "title": "three", "scenario_description": "", "object_name": None, "status": "CLOSED", "priority": "LOW"}]
        return [r for r in rows if r["status"] != "CLOSED"] if open_only else rows

    def list_logs(self, ids=None):
        return []

    def ops_event_keys(self, kind):
        return {(C, "FND-1"), (D, "FND-2")}

    def list_ticket_modules(self):
        return self.modules

    def list_module_group_events(self):
        return self.events

    def list_links(self):
        return self.links

    def create_group(self, name, kind, description, actor=None):
        if name in self.refuse:
            raise RuntimeError("postgresql://secret@host refused")
        self.calls.append(("CREATE", name, kind, actor))
        self.events.append({"group_id": len(self.calls), "name": name, "action_point_id": None, "op": None, "actor": None})

    def group_member(self, group, ap_id, op, reason, actor=None):
        self.calls.append((op, group, ap_id, actor))
        gid = next(e["group_id"] for e in self.events if e["name"] == group)
        self.events.append({"group_id": gid, "name": group, "action_point_id": ap_id, "op": op, "actor": actor})


def fake_git(*args):
    if args[0] == "ls-files":
        return "\n".join(FILES)
    return "shared/backlog_client.py\n"


def quiet(*_):
    return None


# ---------------------------------------------------------------- the step and its switch

def test_mode_on_creates_the_group_and_adds_the_open_tickets_as_the_autopilot_and_a_rerun_writes_nothing():
    bc = FakeBc()
    res = run.cluster_step(bc, apply=True, out=quiet, mode="ON")
    assert res["active"] and res["clusters"] == 1 and res["written"] == 3 and res["refused"] == 0
    assert bc.calls == [("CREATE", "MOD.SHARED.BACKLOG_CLIENT", "MODULE", "system:autopilot"), ("ADD", "MOD.SHARED.BACKLOG_CLIENT", "FND-1", "system:autopilot"),
                        ("ADD", "MOD.SHARED.BACKLOG_CLIENT", "FND-2", "system:autopilot")]
    again = run.cluster_step(bc, apply=True, out=quiet, mode="ON")
    assert again["planned"] == 0 and again["written"] == 0 and len(bc.calls) == 3


def test_the_breadth_is_refreshed_from_git_every_run_so_a_stale_recorded_breadth_never_decides():
    stale = [{**trow("FND-1", "shared/backlog_client.py"), "evidence": "commit cccccccccccc, 40 modules"},
             {**trow("FND-2", "shared/backlog_client.py"), "evidence": "commit dddddddddddd, 40 modules"}]       # recorded when only a sweeping commit had touched them
    res = run.cluster_step(FakeBc(modules=stale), apply=False, git=fake_git, out=quiet, mode="ON")
    assert res["clusters"] == 1 and res["planned"] == 3                                    # git says the commits changed 1 module each: the fresh breadth wins
    fresh = {"FND-1": [{"module": "shared/backlog_client.py", "role": "TOUCHED", "source": "COMMIT", "evidence": "commit cccccccccccc, 9 modules"}]}
    text_row = {**trow("FND-1", "shared/backlog_client.py"), "role": "CITED", "source": "TEXT"}               # the same ticket and module, but a proposal from the text: no commit behind it
    rows = bm.refresh_evidence([trow("FND-1", "shared/backlog_client.py"), trow("FND-1", "shared/other.py"), text_row], fresh)
    assert [r.get("evidence") for r in rows] == ["commit cccccccccccc, 9 modules", None, None]            # only the COMMIT fact that git still reports is refreshed


def test_a_dry_run_plans_and_writes_nothing():
    bc = FakeBc()
    res = run.cluster_step(bc, apply=False, out=quiet, mode="ON")
    assert res["planned"] == 3 and res["written"] == 0 and bc.calls == []


def test_mode_off_does_nothing_and_does_not_even_read_the_groups():
    class Strict(FakeBc):
        def list_module_group_events(self):
            raise AssertionError("must not read")
    res = run.cluster_step(Strict(), apply=True, out=quiet, mode="OFF")
    assert res["active"] is False and res["why"] == "off"


def test_auto_writes_nothing_until_the_clusters_prove_themselves(monkeypatch):
    bc = FakeBc()
    monkeypatch.setattr(run, "proof", lambda *a, **k: SMALL)
    res = run.cluster_step(bc, apply=True, out=quiet, mode="AUTO")
    assert res["active"] is False and "unproven" in res["why"] and bc.calls == []
    monkeypatch.setattr(run, "proof", lambda *a, **k: GOOD)
    res = run.cluster_step(bc, apply=True, out=quiet, mode="AUTO")
    assert res["active"] is True and res["written"] == 3


def test_a_refused_event_is_counted_and_never_echoes_a_connection_string():
    bc = FakeBc()
    bc.refuse = {"MOD.SHARED.BACKLOG_CLIENT"}
    lines = []
    res = run.cluster_step(bc, apply=True, out=lines.append, mode="ON")
    assert res["refused"] == 1 and res["written"] == 0 and not any("secret" in s for s in lines)     # the rest of that group is skipped, nothing is added to a group that does not exist


def test_a_removal_by_an_agent_stands_and_the_derivation_does_not_add_the_ticket_again():
    events = [{"group_id": 1, "name": "MOD.SHARED.BACKLOG_CLIENT", "action_point_id": "FND-1", "op": "ADD", "actor": "system:autopilot"},
              {"group_id": 1, "name": "MOD.SHARED.BACKLOG_CLIENT", "action_point_id": "FND-2", "op": "ADD", "actor": "system:autopilot"},
              {"group_id": 1, "name": "MOD.SHARED.BACKLOG_CLIENT", "action_point_id": "FND-2", "op": "REMOVE", "actor": "agent:claude-opus-5-5"}]
    res = run.cluster_step(FakeBc(events=events), apply=True, out=quiet, mode="ON")
    assert res["planned"] == 0


def test_run_keeps_the_clusters_only_when_asked_and_the_facts_step_is_unchanged():
    bc = FakeBc()
    res = run.run(bc, apply=False, git=fake_git, out=quiet)
    assert "clusters" not in res and res["present"]
    res = run.run(bc, apply=False, git=fake_git, out=quiet, clusters=True, mode="ON")
    assert res["clusters"]["planned"] == 3 and bc.calls == []


def test_run_hands_the_fresh_facts_to_the_next_step_so_the_impact_notes_read_the_same_breadth():
    res = run.run(FakeBc(), apply=False, git=fake_git, out=quiet)
    assert set(res["_desired"]) == {"FND-1", "FND-2"} and all(f["evidence"].startswith("commit ") for fs in res["_desired"].values() for f in fs if f["source"] == "COMMIT")
    class Early(FakeBc):
        def modules_store_present(self):
            return False
    assert "_desired" not in run.run(Early(), apply=False, git=fake_git, out=quiet)                       # before the phase there is nothing to hand over


def test_a_failing_cluster_step_never_takes_the_facts_down_with_it():
    class Broken(FakeBc):
        def list_module_group_events(self):
            raise RuntimeError("down")
    res = run.run(Broken(), apply=False, git=fake_git, out=quiet, clusters=True, mode="ON")
    assert res["present"] and res["clusters"]["active"] is False and "error" in res["clusters"]["why"]


def test_the_autopilot_asks_for_the_clusters_and_the_mode_is_a_config_constant():
    src = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    assert "modules_run.run(bc, apply=True, out=lambda *_: None, clusters=True)" in src
    cfg = (_ROOT / "shared" / "config.py").read_text(encoding="utf-8")
    assert 'BACKLOG_MODULE_CLUSTERS_MODE: str = "AUTO"' in cfg and run._config_mode() in cl.MODES


# ---------------------------------------------------------------- the proof

def test_the_proof_is_measured_once_a_day_and_a_broken_cache_is_measured_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(run, "measure", lambda bc, git=None: calls.append(1) or {"ev": GOOD})
    cache = tmp_path / "out" / "proof.json"
    assert run.proof(None, cache=cache) == GOOD and run.proof(None, cache=cache) == GOOD and len(calls) == 1
    cache.write_text("not json", encoding="utf-8")
    assert run.proof(None, cache=cache) == GOOD and len(calls) == 2
    cache.write_text('{"day": "1999-01-01", "ev": {}}', encoding="utf-8")                      # a stale day is measured again
    assert run.proof(None, cache=cache) == GOOD and len(calls) == 3


def test_measure_scores_the_clusters_against_the_decisions_on_record_and_never_writes():
    links = [{"from_id": "FND-1", "to_id": "FND-2", "link_type": "OVERLAPS", "status": "CONFIRMED"},
             {"from_id": "FND-1", "to_id": "FND-3", "link_type": "OVERLAPS", "status": "REJECTED"},
             {"from_id": "FND-1", "to_id": "FND-3", "link_type": "RELATED", "status": "CONFIRMED"}]
    bc = FakeBc(links=links)
    res = run.measure(bc, fake_git)
    assert res["ev"]["sample"] == 1 and res["ev"]["precision"] == 1.0 and res["ev"]["positives"] == 1 and res["ev"]["recall"] == 1.0
    assert bc.calls == [] and res["sim"]["commits"] == 2


def test_the_reports_print_and_write_nothing():
    bc = FakeBc(links=[{"from_id": "FND-1", "to_id": "FND-2", "link_type": "OVERLAPS", "status": "CONFIRMED"}])
    lines = []
    run.show_measure(bc, fake_git, lines.append)
    text = "\n".join(lines)
    assert "precision 100% on 1 decided pair(s)" in text and "top 10 clusters" in text and "T0 simulation" in text and "unproven" in text
    lines.clear()
    run.show_clusters(bc, fake_git, lines.append)
    assert lines[0].startswith("shared/backlog_client.py: 2 open ticket(s)") and any("FND-1 [IN_PROGRESS]" in s for s in lines) and bc.calls == []
