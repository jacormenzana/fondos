# tests/test_backlog_probes_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4 (FND-0359), phase `probes` (db/pg_backlog/99_probes.sql): an [impact] note invalidates a ticket's probe status and the delivery gate blocks its delivery closure until the closing
conditions are re-run and pass AFTER it. Hermetic PostgreSQL, the real gate in ENFORCE, the privileges are the real ones (SET LOCAL SESSION AUTHORIZATION). Run with python scripts/ops/run_pg_tests.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_relations_pg import blockers, close, enforce, rfd  # noqa: E402
from test_backlog_transitions_pg import FORCE_REASON, ME, OWNER, _base, _mk, refused, status, tr  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "autonomy", "events", "modules", "probes")
SYS = "system:autopilot"


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    enforce(pg_conn)
    return pg_conn


def rec(conn, ap, kind, passed=None, ref="IMPACT:aaaaaaaaaaaa", actor=SYS):
    return conn.execute("SELECT backlog.sp_record_probe_event(%s, %s, %s, %s, %s)", (ap, kind, passed, ref, actor)).fetchone()[0]


def condition(conn, ap):
    conn.execute("SELECT backlog.sp_add_close_condition(%s, 'manual', '[]'::jsonb, 'a manual acceptance condition for the test', %s)", (ap, ME))


def ready(conn, with_condition=True):
    """A ticket in READY_FOR_DEPLOY, optionally with one closing condition."""
    ap = _mk(conn)
    tr(conn, ap, "IN_PROGRESS")
    rfd(conn, ap)
    if with_condition:
        condition(conn, ap)
    return ap


def state(conn, ap):
    row = conn.execute("SELECT state FROM backlog.v_probe_status WHERE action_point_id = %s", (ap,)).fetchone()
    return row[0] if row else None


def kinds(conn, ap, to="CLOSED"):
    return [b["kind"] for b in blockers(conn, ap, to=to, delivery=True)]


# ---------------------------------------------------------------- the phase

def test_the_phase_creates_its_objects_replaces_the_stub_and_is_idempotent(db):
    have = mig.existing(db, "probes")
    assert have["tables"] == ["probe_event"] and have["views"] == ["v_probe_status"] and have["routines"] == ["fn_probe_blockers", "sp_record_probe_event"] and have["missing_requires"] == []
    a = ready(db)
    rec(db, a, "INVALIDATED")
    mig.migrate(db, "probes")                                                               # applying it again changes nothing
    mig.migrate(db, "relations")                                                            # and re-applying the gate does not put the stub over the real hook
    assert kinds(db, a) == ["STALE_PROBES"]


def test_before_the_phase_the_hook_is_a_stub_and_the_gate_has_no_probe_blocker(pg_conn):
    _base(pg_conn)
    for phase in PHASES[:-1]:
        mig.migrate(pg_conn, phase)
    a = ready(pg_conn)
    assert blockers(pg_conn, a, to="CLOSED", delivery=True) == [] and pg_conn.execute("SELECT backlog.fn_probe_blockers(%s, 'CLOSED', true)", (a,)).fetchone()[0] == []


# ---------------------------------------------------------------- the new kind and the migration of the work in flight

@pytest.fixture
def pre(pg_conn):
    """Everything but the probes phase, as production is the moment before it is applied: the tickets of the test exist BEFORE the gate does."""
    _base(pg_conn)
    for phase in PHASES[:-1]:
        mig.migrate(pg_conn, phase)
    enforce(pg_conn)
    return pg_conn


def active(conn, ap):
    return conn.execute("SELECT probe_kind, args::text, actor FROM backlog.v_close_condition WHERE action_point_id = %s ORDER BY condition_id", (ap,)).fetchall()


def test_the_phase_accepts_the_kind_tests_pass_and_still_refuses_an_unknown_one(db):
    a = _mk(db, "IN_PROGRESS")
    db.execute("SELECT backlog.sp_add_close_condition(%s, 'tests_pass', '[]'::jsonb, 'the CI of the change that delivers this ticket is green', %s)", (a, ME))
    assert active(db, a) == [("tests_pass", "[]", ME)]
    b = _mk(db, "IN_PROGRESS")
    refused(db, "check constraint", lambda c: c.execute("SELECT backlog.sp_add_close_condition(%s, 'bogus', '[]'::jsonb, 'a kind that does not exist', %s)", (b, ME)))
    db.execute("SELECT backlog.sp_add_close_condition(%s, 'tests_pass', %s::jsonb, 'with the sha of a commit as the argument', %s)", (b, json.dumps(["a" * 40]), ME))
    mig.migrate(db, "probes")                                                               # the widening is idempotent
    assert len(active(db, b)) == 1


def test_the_work_in_flight_gets_the_default_condition_in_the_same_transaction_as_the_gate(pre):
    ip, ready_, opened, todo = _mk(pre, "IN_PROGRESS"), _mk(pre, "READY_FOR_DEPLOY"), _mk(pre, "OPEN"), _mk(pre, "TODO")
    own, retired = _mk(pre, "IN_PROGRESS"), _mk(pre, "IN_PROGRESS")
    condition(pre, own)
    condition(pre, retired)
    cid = pre.execute("SELECT condition_id FROM backlog.v_close_condition WHERE action_point_id = %s", (retired,)).fetchone()[0]
    pre.execute("SELECT backlog.sp_retire_close_condition(%s, 'retired on purpose before the gate existed', %s)", (cid, ME))
    mig.migrate(pre, "probes")
    for ap in (ip, ready_):
        rows = active(pre, ap)
        assert rows == [("tests_pass", "[]", "system:migration")]                              # the default: CI green, no argument
    assert active(pre, opened) == [] and active(pre, todo) == []                               # only the work in flight; a ticket that starts later defines its own
    assert [r[0] for r in active(pre, own)] == ["manual"]                                      # a ticket with a condition of its own is left alone
    assert active(pre, retired) == []                                                          # and so is one that retired its own: the migration does not undo a decision
    assert kinds(pre, ip) == [] and kinds(pre, ready_) == [] and kinds(pre, retired) == ["NO_PROBES"]
    reason = pre.execute("SELECT reason FROM backlog.close_condition WHERE action_point_id = %s AND actor = 'system:migration'", (ip,)).fetchone()[0]
    assert reason.startswith("default closing condition (owner directive 2026-11-01)")


def test_a_seeded_ticket_closes_with_nobody_doing_anything_and_an_unseeded_one_waits_for_its_creator(pre):
    r = _mk(pre, "IN_PROGRESS")
    tr(pre, r, "READY_FOR_DEPLOY", release_version="R2026.11-SEED")
    mig.migrate(pre, "probes")
    late = _mk(pre, "IN_PROGRESS")                                                             # started after the migration
    tr(pre, late, "READY_FOR_DEPLOY", release_version="R2026.11-SEED")
    close(pre, r)
    assert status(pre, r) == "CLOSED"
    refused(pre, "NO_PROBES", lambda c: close(c, late))
    assert status(pre, late) == "READY_FOR_DEPLOY"


def test_the_seeding_runs_once_so_re_applying_the_phase_does_not_give_a_later_ticket_a_default(pre):
    first = _mk(pre, "IN_PROGRESS")
    mig.migrate(pre, "probes")
    later = _mk(pre, "IN_PROGRESS")
    mig.migrate(pre, "probes")
    mig.migrate(pre, "probes", down=True)
    mig.migrate(pre, "probes")
    assert len(active(pre, first)) == 1 and active(pre, later) == [] and kinds(pre, later) == ["NO_PROBES"]


# ---------------------------------------------------------------- the invalidation blocks the delivery closure

def test_an_invalidated_ticket_with_closing_conditions_cannot_be_closed_as_a_delivery_until_a_passing_run_comes_after(db):
    a = ready(db)
    assert rec(db, a, "INVALIDATED") > 0 and state(db, a) == "STALE" and kinds(db, a) == ["STALE_PROBES"]
    refused(db, "STALE_PROBES", lambda c: close(c, a))
    assert status(db, a) == "READY_FOR_DEPLOY"
    assert rec(db, a, "RUN", True, "the autopilot re-ran 1 closing condition(s)") > 0 and state(db, a) == "FRESH" and kinds(db, a) == []
    close(db, a)
    assert status(db, a) == "CLOSED"


def test_a_failing_run_or_a_run_from_before_the_note_never_clears_it(db):
    a = ready(db)
    rec(db, a, "RUN", True, "an earlier pass")                                              # passed BEFORE the invalidation: it proves nothing about the change
    rec(db, a, "INVALIDATED")
    assert state(db, a) == "STALE" and kinds(db, a) == ["STALE_PROBES"]
    rec(db, a, "RUN", False, "the condition failed")
    assert state(db, a) == "STALE" and kinds(db, a) == ["STALE_PROBES"]
    rec(db, a, "RUN", True, "now it holds")
    assert state(db, a) == "FRESH" and kinds(db, a) == []


def test_a_second_note_after_the_pass_invalidates_again_one_per_merge(db):
    a = ready(db)
    rec(db, a, "INVALIDATED", ref="IMPACT:111111111111")
    rec(db, a, "RUN", True, "it holds")
    assert state(db, a) == "FRESH"
    assert rec(db, a, "INVALIDATED", ref="IMPACT:111111111111") is None and state(db, a) == "FRESH"          # the same merge twice changes nothing
    assert rec(db, a, "INVALIDATED", ref="IMPACT:222222222222") > 0 and state(db, a) == "STALE" and kinds(db, a) == ["STALE_PROBES"]


def test_a_delivery_with_no_closing_condition_cannot_be_closed_and_defining_one_is_all_it_takes(db):
    a = ready(db, with_condition=False)
    assert kinds(db, a) == ["NO_PROBES"]                                                    # a missing test is a failure, not a pass
    refused(db, "NO_PROBES", lambda c: close(c, a))
    assert status(db, a) == "READY_FOR_DEPLOY"
    condition(db, a)                                                                        # whoever works the ticket defines the probe ...
    assert kinds(db, a) == []
    close(db, a)                                                                            # ... and the delivery closes
    assert status(db, a) == "CLOSED"


def test_the_missing_probe_is_about_this_ticket_and_only_about_a_closure(db):
    has, lacks = ready(db), ready(db, with_condition=False)
    assert kinds(db, has) == [] and kinds(db, lacks) == ["NO_PROBES"]                       # another ticket's condition does not satisfy this one
    assert blockers(db, lacks, to="READY_FOR_DEPLOY", delivery=True) == [] and blockers(db, lacks, to="IN_PROGRESS", delivery=True) == []     # the hook speaks only for a closure
    close(db, has)
    assert status(db, has) == "CLOSED"


def test_an_invalidated_ticket_without_conditions_is_blocked_for_having_no_probe_and_after_defining_one_for_the_stale_one(db):
    a = ready(db, with_condition=False)
    assert rec(db, a, "INVALIDATED") > 0 and state(db, a) == "UNPROBED" and kinds(db, a) == ["NO_PROBES"]
    condition(db, a)
    assert state(db, a) == "STALE" and kinds(db, a) == ["STALE_PROBES"]                    # never both at once: the first is what the second replaces


def test_the_rule_covers_every_delivery_solution_but_not_a_closure_that_is_not_a_delivery_nor_the_move_to_ready(db):
    for sol in ("CODE_FIX", "DATA_FIX"):
        a = ready(db, with_condition=False)
        refused(db, "NO_PROBES", lambda c: close(c, a, sol))
        assert status(db, a) == "READY_FOR_DEPLOY"
    assert [b["kind"] for b in blockers(db, a, to="CLOSED", delivery=True)] == ["NO_PROBES"]
    assert blockers(db, a, to="CLOSED", delivery=False) == [] and blockers(db, a, to="READY_FOR_DEPLOY", delivery=False) == []     # a REJECTED or DUPLICATED ticket was not delivered
    x, y = _mk(db), _mk(db)
    tr(db, x, "CLOSED", solution_type="REJECTED", followup=y)
    assert status(db, x) == "CLOSED"
    z = _mk(db)
    tr(db, z, "IN_PROGRESS")
    rfd(db, z)                                                                              # reaching READY_FOR_DEPLOY needs no probe: the probe is the evidence for the closure
    assert status(db, z) == "READY_FOR_DEPLOY"


def test_in_audit_mode_a_missing_probe_is_logged_and_lets_the_closure_through_and_a_human_may_force_it_in_enforce(db):
    a = ready(db, with_condition=False)
    close(db, a, actor=OWNER, force=True, comment=FORCE_REASON)                              # the human override of the gate: counted as forced
    assert status(db, a) == "CLOSED" and db.execute("SELECT forced FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (a,)).fetchone()[0] is True
    b = ready(db, with_condition=False)
    db.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'AUDIT', 'human:jacor', 'back to audit for the test of the gate')")
    close(db, b)
    assert status(db, b) == "CLOSED"
    assert db.execute("SELECT blockers::text FROM backlog.gate_audit_event WHERE action_point_id = %s AND to_status = 'CLOSED'", (b,)).fetchone()[0].count("NO_PROBES") == 1


def test_only_a_delivery_closure_is_blocked_not_the_move_to_ready_for_deploy_nor_a_rejection(db):
    a = _mk(db)
    tr(db, a, "IN_PROGRESS")
    condition(db, a)
    rec(db, a, "INVALIDATED")
    assert kinds(db, a, to="READY_FOR_DEPLOY") == [] and blockers(db, a, to="CLOSED", delivery=False) == []       # the hook speaks only for p_to = CLOSED with p_delivery
    rfd(db, a)
    assert status(db, a) == "READY_FOR_DEPLOY"


def test_the_gate_modes_apply_audit_records_the_blocker_and_lets_it_through_and_a_human_may_force(db):
    a = ready(db)
    rec(db, a, "INVALIDATED")
    refused(db, "STALE_PROBES", lambda c: close(c, a))
    close(db, a, actor=OWNER, force=True, comment=FORCE_REASON)                                              # the human override of the gate, counted as forced
    assert status(db, a) == "CLOSED" and db.execute("SELECT forced FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (a,)).fetchone()[0] is True
    b = ready(db)
    rec(db, b, "INVALIDATED")
    db.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'AUDIT', 'human:jacor', 'back to audit for the test of the gate')")
    close(db, b)
    assert status(db, b) == "CLOSED"
    assert db.execute("SELECT blockers::text FROM backlog.gate_audit_event WHERE action_point_id = %s AND to_status = 'CLOSED'", (b,)).fetchone()[0].count("STALE_PROBES") == 1


# ---------------------------------------------------------------- the door

def test_an_invalidation_of_a_ticket_that_is_not_in_progress_or_ready_is_not_written_and_the_ids_are_checked(db):
    o = _mk(db)
    assert rec(db, o, "INVALIDATED") is None and db.execute("SELECT count(*) FROM backlog.probe_event WHERE action_point_id = %s", (o,)).fetchone()[0] == 0
    refused(db, "does not exist", lambda c: rec(c, "FND-999999", "RUN", True, "x"))
    refused(db, "kind must be", lambda c: rec(c, o, "BOGUS"))
    refused(db, "a RUN says whether it passed", lambda c: rec(c, o, "RUN", None, "x"))
    refused(db, "a RUN says whether it passed", lambda c: rec(c, o, "INVALIDATED", True, "x"))


def test_a_run_that_repeats_the_last_one_is_not_written_again(db):
    a = ready(db)
    assert rec(db, a, "RUN", False, "failed") > 0 and rec(db, a, "RUN", False, "failed again") is None
    assert rec(db, a, "RUN", True, "passed") > 0 and rec(db, a, "RUN", True, "passed again") is None
    rec(db, a, "INVALIDATED")
    assert rec(db, a, "RUN", True, "passed after the note") > 0


def test_only_a_system_actor_records_and_neither_an_agent_nor_a_person_decides_that_a_probe_passed(db):
    a = ready(db)
    refused(db, "only a system actor", lambda c: rec(c, a, "RUN", True, "an agent says it passed", actor=ME))
    refused(db, "only a system actor", lambda c: rec(c, a, "RUN", True, "a person says it passed", actor=OWNER))
    refused(db, "check constraint", lambda c: c.execute("INSERT INTO backlog.probe_event (kind, action_point_id, passed, ref, actor) VALUES ('RUN', %s, true, 'x', 'agent:claude-opus-5-5')", (a,)))


def test_the_table_is_append_only_and_the_privileges_are_the_autopilots(db):
    a = ready(db)
    rec(db, a, "INVALIDATED")
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.probe_event SET passed = true WHERE action_point_id = %s", (a,)))
    refused(db, "append-only", lambda c: c.execute("DELETE FROM backlog.probe_event WHERE action_point_id = %s", (a,)))
    db.execute("SET LOCAL SESSION AUTHORIZATION backlog_autopilot")
    assert rec(db, a, "RUN", True, "re-run by the autopilot") > 0
    db.execute("RESET SESSION AUTHORIZATION")
    db.execute("SET LOCAL SESSION AUTHORIZATION backlog_agent")
    assert db.execute("SELECT count(*) FROM backlog.v_probe_status").fetchone()[0] == 1                       # an agent reads the status ...
    refused(db, "permission denied", lambda c: rec(c, a, "RUN", True, "an agent writes it", actor=SYS))      # ... and cannot record a fact
    refused(db, "permission denied", lambda c: c.execute("INSERT INTO backlog.probe_event (kind, action_point_id, passed, ref, actor) VALUES ('RUN', %s, true, 'x', 'system:x')", (a,)))
    db.execute("RESET SESSION AUTHORIZATION")
    assert db.execute("SELECT has_function_privilege('public', 'backlog.sp_record_probe_event(text,text,boolean,text,text)', 'EXECUTE')").fetchone()[0] is False


def test_the_client_reads_the_status_and_knows_the_store_is_there(db):
    a = ready(db)
    rec(db, a, "INVALIDATED")
    assert bc.probes_store_present(conn=db) is True
    got = bc.probe_status([a], conn=db)
    assert [(r["action_point_id"], r["state"], r["conditions"], r["last_run_passed"]) for r in got] == [(a, "STALE", 1, None)]


# ---------------------------------------------------------------- rollback

def test_the_rollback_restores_the_stub_keeps_the_history_and_the_phase_applies_again(db):
    a = ready(db)
    rec(db, a, "INVALIDATED")
    assert kinds(db, a) == ["STALE_PROBES"]
    mig.migrate(db, "probes", down=True)
    assert kinds(db, a) == [] and mig.existing(db, "probes")["views"] == [] and mig.existing(db, "probes")["routines"] == ["fn_probe_blockers"]
    assert db.execute("SELECT count(*) FROM backlog.probe_event").fetchone()[0] == 1                       # the history stays
    close(db, a)                                                                                         # with the hook off the gate lets the closure through
    mig.migrate(db, "probes")
    assert state(db, a) == "STALE"
