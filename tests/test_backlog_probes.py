# tests/test_backlog_probes.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4 (FND-0359), the Python half of the probe invalidation: an [impact] note invalidates the probe status of the tickets it reaches (scripts/audit/backlog_impact.py) and the autopilot
re-runs their closing conditions and records the result BEFORE it reads the delivery gate (scripts/audit/backlog_autopilot.py). The database half is tests/test_backlog_probes_pg.py.
No database, no git, no network (R-7)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_autopilot as script  # noqa: E402
import backlog_impact as run  # noqa: E402
from shared import backlog_client as real_bc  # noqa: E402
from shared import backlog_impact as ib  # noqa: E402

M = "shared/backlog_client.py"
FILES = [M, "shared/other.py", "AGENTS.md"]
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
OPEN = {f"FND-{i}" for i in range(1, 9)}


def cite(sha="a" * 40, tickets=("FND-1",), hours=3):
    return {t: [SimpleNamespace(ref=sha, ts=NOW - timedelta(hours=hours), paths=(M,))] for t in tickets}


def text_row(ap):
    return {"action_point_id": ap, "module_path": M, "symbol": None, "role": "CITED", "source": "TEXT", "evidence": None}


def fake_git(*args):
    return "\n".join(FILES)


def quiet(*_):
    return None


class FakeBc:
    """The client as the impact runner calls it, with the probe store and a record of every write."""

    class BacklogUnavailable(RuntimeError):
        pass

    def __init__(self, tickets=OPEN, store=True, logs=None, refuse=False):
        self.tickets, self.store, self.refuse, self.invalidated, self.notes = set(tickets), store, refuse, [], []
        self.logs = {a: [(t, ts) for t, ts in xs] for a, xs in (logs or {}).items()}

    def modules_store_present(self):
        return True

    def probes_store_present(self):
        return self.store

    def list_tickets(self, open_only=False):
        return [{"action_point_id": a, "status": "IN_PROGRESS"} for a in sorted(self.tickets)]

    def list_links(self, status=None):
        return [{"from_id": "FND-1", "to_id": f"FND-{i}", "link_type": "IMPACTS"} for i in range(2, 8)]      # six open tickets are impacted by the merged one: more than the cap of five notes per merge

    def list_ticket_modules(self):
        return []

    def list_logs(self, ids=None):
        return [{"action_point_id": a, "comment": c, "log_ts": ts} for a in (ids or []) for c, ts in self.logs.get(a, [])]

    def append_log(self, ap, actor, text):
        self.notes.append((ap, actor, text))

    def record_probe_event(self, ap, kind, passed, ref, actor=None):
        if self.refuse:
            raise self.BacklogUnavailable("live write refused")
        self.invalidated.append((ap, kind, passed, ref, actor))
        return len(self.invalidated)


def go(bc, apply=True, cited=None):
    return run.run(bc, cited or cite(), apply=apply, git=fake_git, out=quiet, now=NOW, enabled=True, mode="OFF")


# ---------------------------------------------------------------- the impact runner invalidates

def test_every_ticket_a_merge_may_affect_is_invalidated_even_beyond_the_five_notes_of_the_cap():
    bc = FakeBc()
    res = go(bc)
    assert res["written"] == 5 and res["invalidated"] == 6 and len(bc.invalidated) == 6                # six affected, five noted: the enforcement is not capped like the notes
    assert {i[0] for i in bc.invalidated} == {f"FND-{i}" for i in range(2, 8)}
    assert all(i[1:] == ("INVALIDATED", None, "IMPACT:aaaaaaaaaaaa", "system:autopilot") for i in bc.invalidated)


def test_a_ticket_told_within_the_cooldown_is_still_invalidated_because_the_cooldown_is_about_notes():
    told = {"FND-2": [("[impact] merge 111111111111 for FND-9 changed x", NOW - timedelta(hours=2))]}
    bc = FakeBc(logs=told)
    res = go(bc)
    assert res["cooldown"] == 1 and "FND-2" not in {n[0] for n in bc.notes} and "FND-2" in {i[0] for i in bc.invalidated}


def test_a_dry_run_invalidates_nothing_and_neither_does_a_database_without_the_probes_phase():
    bc = FakeBc()
    assert go(bc, apply=False)["invalidated"] == 0 and bc.invalidated == []
    early = FakeBc(store=False)
    res = go(early)
    assert res["invalidated"] == 0 and early.invalidated == [] and res["written"] == 5                  # the notes go on


def test_a_refused_write_stops_the_invalidation_without_failing_the_run():
    bc = FakeBc(refuse=True)
    res = go(bc)
    assert res["invalidated"] == 0 and bc.invalidated == []


def test_a_client_that_cannot_say_whether_the_store_exists_counts_as_not_applied():
    class Blind(FakeBc):
        def probes_store_present(self):
            raise RuntimeError("down")
    res = go(Blind())
    assert res["invalidated"] == 0 and res["written"] == 5


def test_the_plan_without_caps_reaches_every_affected_ticket_and_the_default_keeps_them():
    links = [("FND-1", f"T-{i}", "IMPACTS") for i in range(1, 9)]
    rows = [{**text_row(f"W-{i}"), "role": "TOUCHED", "source": "COMMIT"} for i in range(1, 5)]
    open_ids = {f"T-{i}" for i in range(1, 9)} | {r["action_point_id"] for r in rows} | {"FND-1"}
    merge = {"sha": "a" * 40, "ts": NOW, "paths": (M,), "tickets": {"FND-1"}}
    capped = ib.plan_notes(merge, {M}, open_ids, links, rows, True)
    free = ib.plan_notes(merge, {M}, open_ids, links, rows, True, max_notes=None, weak_limit=None)
    assert len(capped) == 5 and len(free) == 12                                                      # eight linked and four weak: the cap keeps five, the enforcement keeps all twelve
    assert len(ib.plan_notes(merge, {M}, open_ids, [], rows, True)) == 1 and len(ib.plan_notes(merge, {M}, open_ids, [], rows, True, max_notes=None, weak_limit=None)) == 4


# ---------------------------------------------------------------- the autopilot re-runs the probes and records the result before it reads the gate

class AutopilotBc:
    def __init__(self, store=True, fail_record=False):
        self.calls, self.store, self.fail_record = [], store, fail_record
        self.AUTOPILOT_ACTOR = "system:autopilot"

    def probes_store_present(self):
        if self.store is None:
            raise RuntimeError("down")
        return self.store

    def gate_blockers(self, ap, to, delivery):
        self.calls.append(("gate", ap, to, delivery))
        return []

    def close_conditions(self):
        self.calls.append(("conditions",))
        return {"FND-1": [("manual", [])], "FND-2": [("pushed", ["x"])]}

    def record_probe_event(self, ap, kind, passed, ref, actor=None):
        if self.fail_record:
            raise RuntimeError("no login")
        self.calls.append(("record", ap, kind, passed, ref, actor))
        return 1


@pytest.fixture
def fake_probe(monkeypatch):
    mod = ModuleType("rfd_matrix_probe")
    seen = {}
    mod.seen = seen
    mod.evaluate = lambda wanted, evidence=None: seen.update(evidence=evidence) or [("FND-1", "NO", [(True, "pushed"), (None, "a manual condition")]), ("FND-2", "OK", [(True, "pushed"), (True, "flag")])]
    monkeypatch.setitem(sys.modules, "rfd_matrix_probe", mod)
    monkeypatch.setattr(script.rel, "read_incident_facts", lambda: (True, {}))
    monkeypatch.setattr(script.rel, "incident_reasons", lambda ready, incidents, present, enabled: {})
    return mod


CFG = SimpleNamespace(AUDIT_INCIDENT_LOOP_ENABLED=False)
CANDIDATES = {"FND-1": [({"sha": "1" * 40}, SimpleNamespace(ref="1" * 40))], "FND-2": [({"sha": "0" * 40}, SimpleNamespace(ref="0" * 40)), ({"sha": "2" * 40}, SimpleNamespace(ref="2" * 40))]}
TICKETS = {"FND-1": {"status": "READY_FOR_DEPLOY"}, "FND-2": {"status": "READY_FOR_DEPLOY"}}


def test_the_re_run_is_recorded_before_the_gate_is_read_so_a_probe_that_just_passed_no_longer_blocks_this_run(fake_probe):
    bc = AutopilotBc()
    script._gather_gates(CANDIDATES, TICKETS, bc, CFG, record=True)
    kinds = [c[0] for c in bc.calls]
    assert kinds.index("record") < kinds.index("gate") and kinds.count("gate") == 2 and kinds.count("record") == 2


def test_the_probes_are_given_the_latest_delivering_merge_of_each_ticket_as_the_evidence_for_tests_pass(fake_probe):
    script._gather_gates(CANDIDATES, TICKETS, AutopilotBc(), CFG, record=False)
    assert fake_probe.seen["evidence"] == {"FND-1": "1" * 40, "FND-2": "2" * 40}                    # the LATEST merge of each (a ticket can have delivered more than once)


def test_a_run_passes_only_when_every_condition_held_and_a_manual_one_is_not_a_pass(fake_probe):
    bc = AutopilotBc()
    script._gather_gates(CANDIDATES, TICKETS, bc, CFG, record=True)
    got = {c[1]: (c[2], c[3], c[5]) for c in bc.calls if c[0] == "record"}
    assert got == {"FND-1": ("RUN", False, "system:autopilot"), "FND-2": ("RUN", True, "system:autopilot")}


def test_a_dry_run_records_nothing_and_still_reads_the_gate(fake_probe):
    bc = AutopilotBc()
    probes, _inc, _rfd, del_b = script._gather_gates(CANDIDATES, TICKETS, bc, CFG, record=False)
    assert [c[0] for c in bc.calls].count("record") == 0 and set(del_b) == {"FND-1", "FND-2"} and probes["FND-2"] == [(True, "pushed"), (True, "flag")]


def test_a_missing_store_or_a_failing_login_records_nothing_and_never_changes_the_verdicts(fake_probe):
    for bc in (AutopilotBc(store=False), AutopilotBc(store=None), AutopilotBc(fail_record=True)):
        probes, _i, _r, del_b = script._gather_gates(CANDIDATES, TICKETS, bc, CFG, record=True)
        assert probes["FND-2"] == [(True, "pushed"), (True, "flag")] and set(del_b) == {"FND-1", "FND-2"}      # the probes are what they are; only the record is missing


def test_a_missing_probe_is_rendered_as_a_reason_that_tells_the_creator_what_to_do():
    text = script.blocker_text({"kind": "NO_PROBES", "hint": "a delivery closes only against an acceptance probe: define one with backlog_client.add_close_condition(ticket, kind, args, reason)"})
    assert text.startswith("kind=NO_PROBES, hint=a delivery closes only against an acceptance probe") and "add_close_condition" in text


def test_a_stale_probe_blocker_is_rendered_as_a_readable_reason():
    b = {"kind": "STALE_PROBES", "since": "2026-11-02T10:00:00+00:00", "conditions": 2}
    assert script.blocker_text(b) == "kind=STALE_PROBES, since=2026-11-02T10:00:00+00:00, conditions=2"


# ---------------------------------------------------------------- the client

def test_the_client_checks_the_kind_before_it_connects_and_names_the_exact_routine():
    with pytest.raises(ValueError, match="kind must be"):
        real_bc.record_probe_event("FND-1", "BOGUS", None, "ref", actor="system:autopilot")
    assert real_bc.PROBE_KINDS == ("INVALIDATED", "RUN") and real_bc._SIG_RECORD_PROBE == "backlog.sp_record_probe_event(text,text,boolean,text,text)"


def test_the_switch_and_the_hooks_are_in_place():
    src = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    assert "_gather_gates(candidates, tickets, bc, cfg, record=bool(args.apply and mode != \"OFF\"))" in src
    ddl = (_ROOT / "db" / "pg_backlog" / "99_probes.sql").read_text(encoding="utf-8")
    assert "STALE_PROBES" in ddl and "p_delivery AND p_to = 'CLOSED'" in ddl
    rel = (_ROOT / "db" / "pg_backlog" / "40_relations_v2.sql").read_text(encoding="utf-8")
    assert "backlog.fn_probe_blockers(p_ap_id, p_to, p_delivery)" in rel and "IF to_regprocedure('backlog.fn_probe_blockers(text,text,boolean)') IS NULL THEN" in rel
