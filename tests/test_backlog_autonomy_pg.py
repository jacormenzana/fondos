# tests/test_backlog_autonomy_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3 W2 (FND-0357): db/pg_backlog/97_autonomy.sql applied by scripts/ops/migrate_backlog_v2.py --phase autonomy: the scoped login backlog_agent, sp_agent_transition (no force, no
reopening, no delivery closure), sp_record_link (a person's decision on a pair is not overwritten) and the rollback that keeps the history. Hermetic: the throwaway container inside a rolled-back
savepoint; never the live backlog. The statements marked "as the agent" run under SESSION AUTHORIZATION backlog_agent, so the privileges are the real ones.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_autonomy_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from test_backlog_transitions_pg import ME, OWNER, SESSION, _base, _mk, open_, refused, row, status, tr  # noqa: E402

PHASES = ("relations", "groups", "sync", "autonomy")
CONFIRM_EVIDENCE = "both tickets name each other and touch the same module: read by the agent"
ALLOWED = {"sp_open_ticket", "sp_agent_transition", "sp_record_link", "sp_append_log", "sp_annotate_wait", "sp_create_group", "sp_group_member", "sp_add_close_condition",
           "sp_retire_close_condition"}


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    return pg_conn


def as_agent(conn):
    conn.execute("SET LOCAL SESSION AUTHORIZATION backlog_agent")


def as_owner(conn):
    conn.execute("RESET SESSION AUTHORIZATION")


def agent_tr(conn, ap, to, actor=ME, comment="a progress note long enough for the log", **kw):
    args = dict(p_ap_id=ap, p_to=to, p_actor=actor, p_session_id=SESSION, p_comment=comment)
    args.update({f"p_{k}": v for k, v in kw.items()})
    names = ", ".join(f"{k} => %s" for k in args)
    return conn.execute(f"SELECT backlog.sp_agent_transition({names})", tuple(args.values())).fetchone()[0]


def link(conn, a, b, status_="PROPOSED", evidence="named in both texts", actor=ME, type_="RELATED"):
    return conn.execute("SELECT backlog.sp_record_link(%s, %s, %s, %s, %s, NULL, %s)", (a, b, type_, status_, evidence, actor)).fetchone()[0]


def latest(conn, a, b, type_="RELATED"):
    return conn.execute("SELECT status, username FROM backlog.backlog_link_event WHERE from_id = %s AND to_id = %s AND link_type = %s ORDER BY event_id DESC LIMIT 1", (a, b, type_)).fetchone()


# ---------------------------------------------------------------- the login and its privileges

def test_the_role_is_a_scoped_login_without_password_or_membership_and_holds_exactly_the_listed_routines(db):
    assert mig.existing(db, "autonomy")["roles"] == ["backlog_agent", "backlog_autopilot"]
    assert db.execute("SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'backlog_agent'").fetchone() == (True, False, False, False)
    assert db.execute("SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'backlog_agent'").fetchone()[0] is True              # the owner sets the password
    assert db.execute("SELECT pg_has_role('backlog_agent', 'backlog_writer', 'MEMBER')").fetchone()[0] is False
    rows = db.execute("SELECT p.proname, has_function_privilege('backlog_agent', p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname LIKE 'sp\\_%'").fetchall()
    granted = {n for n, ok in rows if ok}
    assert granted == ALLOWED, (sorted(granted - ALLOWED), sorted(ALLOWED - granted))
    for forbidden in ("sp_transition", "sp_set_gate", "sp_raise_hold", "sp_release_hold", "sp_ack_review", "sp_adjudicate_overlap", "sp_close_release", "sp_set_collision_mode"):
        assert forbidden not in granted


def test_the_login_has_no_write_privilege_on_any_table(db):
    tables = [r[0] for r in db.execute("SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace AND c.relkind = 'r'").fetchall()]
    assert tables
    for t in tables:
        for priv in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            assert db.execute("SELECT has_table_privilege('backlog_agent', %s, %s)", (f"backlog.{t}", priv)).fetchone()[0] is False, (t, priv)
        assert db.execute("SELECT has_table_privilege('backlog_agent', %s, 'SELECT')", (f"backlog.{t}",)).fetchone()[0] is True


def test_the_agent_cannot_call_sp_transition_or_write_a_table_directly(db):
    ap = _mk(db)
    as_agent(db)
    refused(db, "permission denied", lambda c: c.execute("SELECT backlog.sp_transition(p_ap_id => %s, p_to => 'IN_PROGRESS', p_actor => %s, p_session_id => %s, p_comment => 'x', p_force => true)", (ap, ME, SESSION)))
    refused(db, "permission denied", lambda c: c.execute("UPDATE backlog.BACKLOG SET status = 'CLOSED' WHERE action_point_id = %s", (ap,)))
    refused(db, "permission denied", lambda c: c.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'CONFIRMED', 'x', %s)", (ap, ap, ME)))
    refused(db, "permission denied", lambda c: c.execute("SELECT backlog.sp_raise_hold(%s, 'an agent raising a hold it may not', NULL, %s)", (ap, ME)))     # the gate and hold routines are the owner's (their grants are checked above)


# ---------------------------------------------------------------- opening and moving tickets as the agent

def test_the_agent_opens_a_ticket_and_moves_it_forward_with_its_own_login(db):
    as_agent(db)
    ap = open_(db, title="opened by the agent login")
    assert status(db, ap) == "OPEN"
    assert agent_tr(db, ap, "IN_PROGRESS") is not None
    assert status(db, ap) == "IN_PROGRESS"
    assert db.execute("SELECT actor, forced FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (ap,)).fetchone() == (ME, False)


def test_an_agent_transition_counts_towards_the_a2_shadow_stage_and_is_not_a_direct_write(db):
    ap = _mk(db)
    before = mig.shadow_stage(db)
    as_agent(db)
    agent_tr(db, ap, "IN_PROGRESS")
    as_owner(db)
    after = mig.shadow_stage(db)
    assert after["live_non_system_since_last_v00"] == before["live_non_system_since_last_v00"] + 1 and after["v00_total"] == before["v00_total"]


def test_the_agent_may_block_defer_reject_and_move_back_but_not_force_reopen_or_make_a_delivery_closure(db):
    ap, other, keep = _mk(db, "IN_PROGRESS"), _mk(db, "OPEN"), _mk(db, "OPEN")           # created as the owner connection: the agent login has no INSERT
    as_agent(db)
    agent_tr(db, ap, "BLOCKED", condition="waiting for the vendor to answer the question")
    agent_tr(db, ap, "IN_PROGRESS")
    agent_tr(db, ap, "READY_FOR_DEPLOY")
    refused(db, "delivery closure", agent_tr, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.10-TEST-01")
    refused(db, "delivery closure", agent_tr, ap, "CLOSED", solution_type="WORKAROUND", release_version="R2026.10-TEST-01", followup="FND-0001")
    refused(db, "delivery closure", agent_tr, ap, "CLOSED", solution_type="DATA_FIX", release_version="R2026.10-TEST-01")
    assert status(db, ap) == "READY_FOR_DEPLOY"
    agent_tr(db, other, "CLOSED", solution_type="REJECTED", followup=keep)                 # REJECTED cites the ticket that supersedes it: legal
    assert row(db, other)[0] == "CLOSED" and row(db, other)[2] == "REJECTED"
    refused(db, "reopening is a person", agent_tr, other, "IN_PROGRESS")                    # a CLOSED ticket stays closed for an agent
    refused(db, "(?s)illegal|not a legal|cannot|p_force", agent_tr, keep, "READY_FOR_DEPLOY")   # an illegal move needs p_force, which the wrapper never passes
    assert status(db, keep) == "OPEN"


def test_only_an_agent_identity_may_use_the_wrapper_and_the_generic_placeholder_is_refused(db):
    ap = _mk(db)
    as_agent(db)
    refused(db, "must be agent", agent_tr, ap, "IN_PROGRESS", actor="human:jacor")
    refused(db, "must be agent", agent_tr, ap, "IN_PROGRESS", actor="system:sync")
    refused(db, "not authorized", agent_tr, ap, "IN_PROGRESS", actor="agent:claude")
    refused(db, "does not exist", agent_tr, "FND-9999", "IN_PROGRESS")
    assert status(db, ap) == "OPEN"


# ---------------------------------------------------------------- links

def test_an_agent_proposes_freely_but_confirms_only_with_stated_evidence(db):
    a, b = _mk(db), _mk(db)
    as_agent(db)
    assert link(db, a, b) is not None and latest(db, a, b) == ("PROPOSED", ME)
    refused(db, "stated evidence", link, a, b, "CONFIRMED", "too short")
    assert link(db, a, b, "CONFIRMED", CONFIRM_EVIDENCE) is not None and latest(db, a, b) == ("CONFIRMED", ME)
    assert link(db, a, b, "REJECTED", "the owner's note says they are unrelated") is not None       # an agent's own decision can be reversed by an agent


def test_a_pair_decided_by_a_person_cannot_be_touched_by_an_agent(db):
    a, b, c = _mk(db), _mk(db), _mk(db)
    for x, y, st in ((a, b, "CONFIRMED"), (a, c, "REJECTED")):
        db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', %s, 'decided by the owner', %s)", (x, y, st, OWNER))
    as_agent(db)
    refused(db, "person's decision", link, a, b, "PROPOSED")
    refused(db, "person's decision", link, a, b, "REJECTED", "an agent disagrees with the owner here")
    refused(db, "person's decision", link, a, c, "CONFIRMED", CONFIRM_EVIDENCE)
    assert latest(db, a, b) == ("CONFIRMED", OWNER) and latest(db, a, c) == ("REJECTED", OWNER)
    as_owner(db)
    db.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'PROPOSED', 'the owner reopens the question', %s)", (a, b, OWNER))
    as_agent(db)
    assert link(db, a, b, "PROPOSED") is not None                                           # the person's latest event is only a proposal again: an agent may add to it


def test_the_depends_on_cycle_check_and_the_actor_rule_still_apply_to_the_agent(db):
    a, b = _mk(db), _mk(db)
    as_agent(db)
    link(db, a, b, "CONFIRMED", CONFIRM_EVIDENCE, type_="DEPENDS_ON")
    refused(db, "cycle", link, b, a, "CONFIRMED", CONFIRM_EVIDENCE, type_="DEPENDS_ON")
    refused(db, "must be agent", link, a, b, "PROPOSED", actor="human:jacor")


# ---------------------------------------------------------------- notes, groups, waits, close conditions

def test_the_agent_appends_a_note_to_an_existing_ticket_only(db):
    ap = _mk(db)
    as_agent(db)
    n = db.execute("SELECT backlog.sp_append_log(%s, %s, 'a note by the agent login')", (ap, ME)).fetchone()[0]
    assert n is not None
    refused(db, "does not exist", lambda c: c.execute("SELECT backlog.sp_append_log('FND-9999', %s, 'x')", (ME,)))
    refused(db, "must be agent", lambda c: c.execute("SELECT backlog.sp_append_log(%s, 'human:jacor', 'x')", (ap,)))


def test_the_agent_can_group_tickets_and_annotate_a_wait(db):
    ap = _mk(db, "IN_PROGRESS")
    as_agent(db)
    agent_tr(db, ap, "BLOCKED", condition="waiting for the vendor")
    assert db.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'now it names the blocking condition', NULL, 'the vendor confirms the format', NULL, NULL, 'DECLARED')", (ap, ME)).fetchone()[0] is not None
    g = db.execute("SELECT backlog.sp_create_group('V3-TEST-TRAIN', 'TRAIN', 'tickets released together in a test', %s)", (ME,)).fetchone()[0]
    assert g is not None
    assert db.execute("SELECT backlog.sp_group_member('V3-TEST-TRAIN', %s, 'ADD', 'it ships with the others', %s)", (ap, ME)).fetchone()[0] is not None


# ---------------------------------------------------------------- migration, idempotence and rollback

def test_the_phase_needs_the_earlier_ones(pg_conn):
    _base(pg_conn)
    with pytest.raises(RuntimeError, match="needs backlog"):
        mig.migrate(pg_conn, "autonomy")


def test_the_phase_is_idempotent_and_the_rollback_keeps_the_role_and_every_event_written_through_it(db):
    a, b = _mk(db), _mk(db)
    as_agent(db)
    link(db, a, b)
    as_owner(db)
    mig.migrate(db, "autonomy")                                                              # idempotent
    have, spec = mig.existing(db, "autonomy"), mig.PHASES["autonomy"]
    assert have["routines"] == sorted(spec["routines"]) and have["roles"] == ["backlog_agent", "backlog_autopilot"]
    mig.migrate(db, "autonomy", down=True)
    have = mig.existing(db, "autonomy")
    assert "sp_agent_transition" not in have["routines"] and "sp_record_link" not in have["routines"] and have["roles"] == ["backlog_agent", "backlog_autopilot"]
    assert latest(db, a, b) == ("PROPOSED", ME)                                               # the history stays
    assert db.execute("SELECT has_function_privilege('backlog_agent', 'backlog.sp_open_ticket(text,text,text,text,text,text,text,text,text,text,text)', 'EXECUTE')").fetchone()[0] is False
    mig.migrate(db, "autonomy")                                                              # applying it again resumes
    as_agent(db)
    assert link(db, a, b, "REJECTED", "reversed by the agent after the rollback test") is not None
