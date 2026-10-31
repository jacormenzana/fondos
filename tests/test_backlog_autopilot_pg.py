# tests/test_backlog_autopilot_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3 W3 (FND-0358), the database half: the W3 block of db/pg_backlog/97_autonomy.sql (phase `autonomy`): the fact kinds MERGE / DEPLOY / CYCLE, the scoped login backlog_autopilot and
sp_auto_transition, the autopilot's only move. Hermetic: the throwaway container inside a rolled-back savepoint; never the live backlog. Statements marked "as the autopilot" run under
SESSION AUTHORIZATION backlog_autopilot, so the privileges are the real ones.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_autopilot_pg.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from test_backlog_transitions_pg import ME, OWNER, SESSION, _base, _mk, refused, row, status  # noqa: E402

PHASES = ("relations", "groups", "sync", "autonomy")
AUTO = "system:autopilot"
SYNC = "system:sync"
MERGE_REF, MERGE2_REF = "a" * 40, "b" * 40
T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
NOTE = "[autopilot ON] MOVE FND-9999 on evidence, written by a test of the database half of the autopilot"
RELEASE = "R2026.11-PR17"


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    return pg_conn


def as_autopilot(conn):
    conn.execute("SET LOCAL SESSION AUTHORIZATION backlog_autopilot")


def as_owner(conn):
    conn.execute("RESET SESSION AUTHORIZATION")


def fact(conn, kind, ref, ap, ts=T0, paths=None, actor=SYNC):
    payload = '{"paths": %s}' % __import__("json").dumps(paths or ["shared/x.py"])
    return conn.execute("SELECT backlog.sp_record_ops_event(%s, %s, %s, 'REFS', %s::jsonb, %s, %s)", (kind, ref, ap, payload, ts, actor)).fetchone()[0]


def auto(conn, ap, to, ref=MERGE_REF, kind="MERGE", solution=None, release=None, comment=NOTE):
    return conn.execute("SELECT backlog.sp_auto_transition(%s, %s, %s, %s, %s, %s, %s, %s)", (ap, to, kind, ref, SESSION, comment, solution, release)).fetchone()[0]


def delivered(conn, state="IN_PROGRESS", paths=None, ts=T0):
    ap = _mk(conn, state)
    fact(conn, "MERGE", MERGE_REF, ap, ts=ts, paths=paths)
    return ap


def enforce_gate(conn):
    conn.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'ENFORCE', 'human:jacor', 'enforced by a test of the autopilot')")


# ---------------------------------------------------------------- the kinds and the login

def test_the_new_fact_kinds_are_accepted_and_an_unknown_kind_is_still_refused(db):
    ap = _mk(db)
    for kind, ref in (("MERGE", MERGE_REF), ("DEPLOY", "deploy-1"), ("CYCLE", "cycle-1")):
        assert fact(db, kind, ref, ap) is True
    assert fact(db, "MERGE", MERGE_REF, ap) is False                                         # idempotent: the same fact is not recorded twice
    refused(db, "ops_event_kind_check", fact, "FOO", "x", ap)


def test_the_login_is_scoped_with_no_password_membership_or_table_write(db):
    assert db.execute("SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'backlog_autopilot'").fetchone() == (True, False, False, False)
    assert db.execute("SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'backlog_autopilot'").fetchone()[0] is True
    assert db.execute("SELECT pg_has_role('backlog_autopilot', 'backlog_writer', 'MEMBER') OR pg_has_role('backlog_autopilot', 'backlog_agent', 'MEMBER')").fetchone()[0] is False
    rows = db.execute("SELECT p.proname, has_function_privilege('backlog_autopilot', p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname LIKE 'sp\\_%'").fetchall()
    assert {n for n, ok in rows if ok} == {"sp_auto_transition", "sp_record_ops_event", "sp_append_log"}
    assert db.execute("SELECT has_function_privilege('backlog_autopilot', 'backlog.fn_gate_blockers(text,text,boolean)', 'EXECUTE')").fetchone()[0] is True
    for t in [r[0] for r in db.execute("SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace AND c.relkind = 'r'").fetchall()]:
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert db.execute("SELECT has_table_privilege('backlog_autopilot', %s, %s)", (f"backlog.{t}", priv)).fetchone()[0] is False, (t, priv)


def test_the_agent_cannot_make_the_autopilots_move_and_the_autopilot_cannot_make_the_agents(db):
    assert db.execute("SELECT has_function_privilege('backlog_agent', p.oid, 'EXECUTE') FROM pg_proc p WHERE p.proname = 'sp_auto_transition'").fetchone()[0] is False
    ap = delivered(db)
    as_autopilot(db)
    refused(db, "permission denied", lambda c: c.execute("SELECT backlog.sp_transition(p_ap_id => %s, p_to => 'READY_FOR_DEPLOY', p_actor => %s, p_session_id => %s, p_comment => 'x', p_force => true)", (ap, AUTO, SESSION)))
    refused(db, "permission denied", lambda c: c.execute("SELECT backlog.sp_agent_transition(p_ap_id => %s, p_to => 'READY_FOR_DEPLOY', p_actor => %s, p_session_id => %s, p_comment => 'x')", (ap, ME, SESSION)))
    refused(db, "permission denied", lambda c: c.execute("UPDATE backlog.BACKLOG SET status = 'CLOSED' WHERE action_point_id = %s", (ap,)))
    assert status(db, ap) == "IN_PROGRESS"


# ---------------------------------------------------------------- the move to READY_FOR_DEPLOY

def test_a_recorded_merge_moves_an_in_progress_ticket_to_ready_for_deploy_as_the_autopilot_never_forced(db):
    ap = delivered(db)
    as_autopilot(db)
    event = auto(db, ap, "READY_FOR_DEPLOY")
    as_owner(db)
    assert status(db, ap) == "READY_FOR_DEPLOY"
    assert db.execute("SELECT actor, forced, to_status FROM backlog.status_event WHERE event_id = %s", (event,)).fetchone() == (AUTO, False, "READY_FOR_DEPLOY")
    assert db.execute("SELECT username FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id DESC LIMIT 1", (ap,)).fetchone()[0] == AUTO


def test_a_move_without_evidence_is_refused(db):
    ap = _mk(db, "IN_PROGRESS")
    other = delivered(db)                                                                   # the merge is recorded, but for ANOTHER ticket
    as_autopilot(db)
    refused(db, "has no evidence", auto, ap, "READY_FOR_DEPLOY")
    refused(db, "has no evidence", auto, ap, "READY_FOR_DEPLOY", ref="c" * 40)
    refused(db, "evidence of a move is a MERGE", auto, other, "READY_FOR_DEPLOY", kind="COMMIT")
    refused(db, "does not exist", auto, "FND-9999", "READY_FOR_DEPLOY")
    assert status(db, ap) == "IN_PROGRESS" and status(db, other) == "IN_PROGRESS"


@pytest.mark.parametrize("state, to", [("OPEN", "READY_FOR_DEPLOY"), ("OPEN", "CLOSED"), ("IN_PROGRESS", "CLOSED"), ("READY_FOR_DEPLOY", "IN_PROGRESS"), ("BLOCKED", "READY_FOR_DEPLOY"),
                                       ("DEFERRED", "READY_FOR_DEPLOY"), ("IN_PROGRESS", "TODO")])
def test_only_the_two_automatic_edges_exist(db, state, to):
    ap = delivered(db, state)
    as_autopilot(db)
    refused(db, "not an automatic move", auto, ap, to, solution="CODE_FIX" if to == "CLOSED" else None, release=RELEASE if to == "CLOSED" else None)
    as_owner(db)
    assert status(db, ap) == state


def test_the_move_to_ready_for_deploy_carries_neither_a_solution_nor_a_release(db):
    ap = delivered(db)
    as_autopilot(db)
    refused(db, "carries neither", auto, ap, "READY_FOR_DEPLOY", solution="CODE_FIX")
    refused(db, "carries neither", auto, ap, "READY_FOR_DEPLOY", release=RELEASE)


# ---------------------------------------------------------------- the closure

def test_a_code_fix_closes_with_the_release_label_and_a_closed_ticket_stays_closed_for_the_autopilot(db):
    ap = delivered(db, "READY_FOR_DEPLOY")
    as_autopilot(db)
    auto(db, ap, "CLOSED", solution="CODE_FIX", release=RELEASE)
    as_owner(db)
    st, closure_ts, solution, deployment_ts, release, _ = row(db, ap)
    assert (st, solution, release) == ("CLOSED", "CODE_FIX", RELEASE) and closure_ts is not None and deployment_ts is not None
    as_autopilot(db)
    refused(db, "not an automatic move", auto, ap, "IN_PROGRESS")


@pytest.mark.parametrize("solution", ["WORKAROUND", "REJECTED", "DUPLICATED", None])
def test_only_code_fix_and_data_fix_close_automatically(db, solution):
    ap = delivered(db, "READY_FOR_DEPLOY")
    as_autopilot(db)
    refused(db, "only CODE_FIX and DATA_FIX", auto, ap, "CLOSED", solution=solution, release=RELEASE)


def test_a_closure_needs_its_release_label(db):
    ap = delivered(db, "READY_FOR_DEPLOY")
    as_autopilot(db)
    refused(db, "release label", auto, ap, "CLOSED", solution="CODE_FIX")


def test_a_merge_that_changes_db_closes_only_after_the_deploy_event(db):
    ap = delivered(db, "READY_FOR_DEPLOY", paths=["db/pg_backlog/97_autonomy.sql", "tests/t.py"])
    as_autopilot(db)
    refused(db, "no DEPLOY event", auto, ap, "CLOSED", solution="CODE_FIX", release=RELEASE)
    as_owner(db)
    fact(db, "DEPLOY", "migrate-autonomy", ap, ts=T0 + timedelta(hours=1))
    as_autopilot(db)
    auto(db, ap, "CLOSED", solution="CODE_FIX", release=RELEASE)
    as_owner(db)
    assert status(db, ap) == "CLOSED"


def test_a_data_fix_needs_a_cycle_after_the_merge_and_an_active_closing_condition(db):
    ap = delivered(db, "READY_FOR_DEPLOY")
    as_autopilot(db)
    refused(db, "CYCLE event", auto, ap, "CLOSED", solution="DATA_FIX", release=RELEASE)
    as_owner(db)
    fact(db, "CYCLE", "cycle-before", ap, ts=T0 - timedelta(hours=2))                         # a cycle BEFORE the merge does not count
    as_autopilot(db)
    refused(db, "CYCLE event", auto, ap, "CLOSED", solution="DATA_FIX", release=RELEASE)
    as_owner(db)
    fact(db, "CYCLE", "cycle-after", ap, ts=T0 + timedelta(hours=2))
    as_autopilot(db)
    refused(db, "closing condition", auto, ap, "CLOSED", solution="DATA_FIX", release=RELEASE)
    as_owner(db)
    db.execute("SELECT backlog.sp_add_close_condition(%s, 'manual', '[\"the share is back under the limit\"]'::jsonb, 'the data is only right when the share is back under the limit', %s)", (ap, SYNC))
    as_autopilot(db)
    auto(db, ap, "CLOSED", solution="DATA_FIX", release=RELEASE)
    as_owner(db)
    assert row(db, ap)[2] == "DATA_FIX"


def test_the_delivery_gate_of_phase_b_still_applies_inside_the_autopilots_move(db):
    ap, prerequisite = delivered(db, "READY_FOR_DEPLOY"), _mk(db, "IN_PROGRESS")
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'DEPENDS_ON', 'CONFIRMED', 'it needs the other first', %s)", (ap, prerequisite, OWNER))
    enforce_gate(db)
    as_autopilot(db)
    refused(db, "delivery gate", auto, ap, "CLOSED", solution="CODE_FIX", release=RELEASE)
    as_owner(db)
    assert status(db, ap) == "READY_FOR_DEPLOY"


def test_the_autopilots_moves_are_not_direct_writes_and_count_as_system_transitions(db):
    ap = delivered(db)
    before = mig.shadow_stage(db)
    as_autopilot(db)
    auto(db, ap, "READY_FOR_DEPLOY")
    as_owner(db)
    after = mig.shadow_stage(db)
    assert after["v00_total"] == before["v00_total"]                                          # no V-00
    assert after["live_non_system_since_last_v00"] == before["live_non_system_since_last_v00"]   # system:* never counts towards the A-2 exit


# ---------------------------------------------------------------- notes, idempotence and rollback

def test_the_autopilot_appends_a_note_to_an_existing_ticket_as_itself(db):
    ap = _mk(db)
    as_autopilot(db)
    assert db.execute("SELECT backlog.sp_append_log(%s, %s, %s)", (ap, AUTO, NOTE)).fetchone()[0] is not None
    refused(db, "must be agent", lambda c: c.execute("SELECT backlog.sp_append_log(%s, 'human:jacor', 'x')", (ap,)))


def test_the_phase_is_idempotent_and_the_rollback_keeps_the_roles_the_widened_kinds_and_every_fact(db):
    ap = delivered(db)
    as_autopilot(db)
    auto(db, ap, "READY_FOR_DEPLOY")
    as_owner(db)
    mig.migrate(db, "autonomy")                                                               # again: nothing changes
    mig.migrate(db, "autonomy", down=True)
    have = mig.existing(db, "autonomy")
    assert "sp_auto_transition" not in have["routines"] and have["roles"] == ["backlog_agent", "backlog_autopilot"]
    assert db.execute("SELECT count(*) FROM backlog.ops_event WHERE kind = 'MERGE' AND action_point_id = %s", (ap,)).fetchone()[0] == 1
    assert fact(db, "DEPLOY", "after-rollback", ap) is True                                   # the kinds stay widened
    assert db.execute("SELECT count(*) FROM backlog.status_event WHERE action_point_id = %s AND actor = %s", (ap, AUTO)).fetchone()[0] == 1
    mig.migrate(db, "autonomy")
    assert db.execute("SELECT has_function_privilege('backlog_autopilot', p.oid, 'EXECUTE') FROM pg_proc p WHERE p.proname = 'sp_auto_transition'").fetchone()[0] is True
