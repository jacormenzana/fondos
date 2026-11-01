# tests/test_backlog_events_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3.1 phase `events` (db/pg_backlog/98_events.sql, applied by scripts/ops/migrate_backlog_v2.py --phase events) and the edits it needs in the earlier phases: reopening as a normal event with its
cause (and taking it back within an hour), holds that release by themselves, agents deciding collision pairs, the autopilot mode as an event, and the DEPLOY fact only from the login backlog_facts.
Hermetic: the throwaway container inside a rolled-back savepoint; never the live backlog. Statements marked "as <role>" run under SESSION AUTHORIZATION, so the privileges are the real ones.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_events_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from test_backlog_groups_pg import violations  # noqa: E402
from test_backlog_transitions_pg import ME, OWNER, SESSION, _base, _mk, refused, row, status, tr  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "autonomy", "events")
AUTO, DEPLOYER = "system:autopilot", "system:deploy"
EVIDENCE = "both tickets change the same function and the agent read both diffs: related, not duplicate work"


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    return pg_conn


def as_role(conn, role):
    conn.execute(f"SET LOCAL SESSION AUTHORIZATION {role}")


def as_owner(conn):
    conn.execute("RESET SESSION AUTHORIZATION")


def closed(conn, release="R2026.11-TEST-01"):
    ap = _mk(conn, "READY_FOR_DEPLOY")
    tr(conn, ap, "CLOSED", solution_type="CODE_FIX", release_version=release)
    return ap


def reopen(conn, ap, cause="INCOMPLETE_FIX", actor=ME, reason="the delivery did not cover the monthly write path", to="IN_PROGRESS"):
    return conn.execute("SELECT backlog.sp_reopen(%s, %s, %s, %s, %s, %s)", (ap, cause, reason, actor, SESSION, to)).fetchone()[0]


def rescind(conn, ap, actor=ME):
    return conn.execute("SELECT backlog.sp_rescind_reopen(%s, %s, %s)", (ap, actor, SESSION)).fetchone()[0]


def reopen_rows(conn, ap):
    return conn.execute("SELECT kind, actor, cause FROM backlog.reopen_event WHERE action_point_id = %s ORDER BY reopen_id", (ap,)).fetchall()


def propose_pair(conn, a, b):
    a, b = min(a, b), max(a, b)
    conn.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin) VALUES (%s, %s, 'OVERLAPS', 'PROPOSED', "
                 "'the commit touches a file cited by the other ticket (a test)', 'system:gate', 'GATE')", (a, b))
    return a, b


def queue(conn):
    return conn.execute("SELECT a, b FROM backlog.v_gate_queue ORDER BY 1, 2").fetchall()


# ---------------------------------------------------------------- the phase

def test_the_phase_creates_its_objects_is_idempotent_and_its_role_has_no_password_and_no_table_write(db):
    have, spec = mig.existing(db, "events"), mig.PHASES["events"]
    assert have["tables"] == sorted(spec["tables"]) and have["routines"] == sorted(spec["routines"]) and have["roles"] == ["backlog_facts"]
    mig.migrate(db, "events")
    assert db.execute("SELECT rolcanlogin, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = 'backlog_facts'").fetchone() == (True, False, False)
    assert db.execute("SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'backlog_facts'").fetchone()[0] is True
    granted = {n for n, ok in db.execute("SELECT p.proname, has_function_privilege('backlog_facts', p.oid, 'EXECUTE') FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace "
                                         "AND p.proname LIKE 'sp\\_%'").fetchall() if ok}
    assert granted == {"sp_record_fact"}
    for t in [r[0] for r in db.execute("SELECT c.relname FROM pg_class c WHERE c.relnamespace = 'backlog'::regnamespace AND c.relkind = 'r'").fetchall()]:
        assert db.execute("SELECT has_table_privilege('backlog_facts', %s, 'INSERT') OR has_table_privilege('backlog_facts', %s, 'UPDATE')", (f"backlog.{t}", f"backlog.{t}")).fetchone()[0] is False, t


def test_the_history_of_reopenings_is_backfilled_once_for_the_tickets_that_exist(pg_conn):
    _base(pg_conn)
    for phase in PHASES[:-1]:
        mig.migrate(pg_conn, phase)
    for ap in ("FND-0177", "FND-0034"):
        pg_conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, session_id) VALUES (%s, 't', 'FND', 'BUG', 'N/A', 'IN_PROGRESS', 'LOW', %s)", (ap, SESSION))
    mig.migrate(pg_conn, "events")
    mig.migrate(pg_conn, "events")
    got = pg_conn.execute("SELECT action_point_id, cause, actor FROM backlog.reopen_event ORDER BY 1").fetchall()
    assert got == [("FND-0034", "DOWNSTREAM_EFFECT", "system:backfill"), ("FND-0177", "COVERAGE_GAP", "system:backfill")]       # FND-0193 and FND-0157 do not exist here; no duplicates on the second run


# ---------------------------------------------------------------- reopening as a normal event

@pytest.mark.parametrize("actor, role", [(ME, "backlog_agent"), (AUTO, "backlog_autopilot")])
def test_any_actor_reopens_a_closed_ticket_with_a_cause_and_it_is_not_forced_nor_a_violation(db, actor, role):
    ap = closed(db)
    as_role(db, role)
    event = reopen(db, ap, "REGRESSION", actor)
    as_owner(db)
    assert status(db, ap) == "IN_PROGRESS" and row(db, ap)[:5] == ("IN_PROGRESS", None, None, None, None)
    assert db.execute("SELECT forced FROM backlog.status_event WHERE event_id = %s", (event,)).fetchone()[0] is False
    assert reopen_rows(db, ap) == [("REOPEN", actor, "REGRESSION")]
    assert db.execute("SELECT prior ->> 'solution_type', prior ->> 'release_version' FROM backlog.reopen_event WHERE action_point_id = %s", (ap,)).fetchone() == ("CODE_FIX", "R2026.11-TEST-01")
    assert violations(db, "V-FORCED", ap) == []


def test_a_person_reopens_too_and_a_plain_transition_reopening_records_the_cause_other(db):
    a, b = closed(db), closed(db)
    reopen(db, a, "WRONG_ROOT_CAUSE", OWNER, "the owner's instruction: the root cause was a different module")
    tr(db, b, "TODO")                                                                         # the old way: a bare sp_transition
    assert reopen_rows(db, a) == [("REOPEN", OWNER, "WRONG_ROOT_CAUSE")] and reopen_rows(db, b)[0][2] == "OTHER"
    assert row(db, b)[0] == "TODO"
    c = closed(db)
    reopen(db, c, "ENVIRONMENT")
    d = closed(db)
    tr(db, d, "IN_PROGRESS")
    assert reopen_rows(db, d)[0][2] == "OTHER"                                                # the cause of the previous call does not leak into the next one


def test_a_reopening_is_refused_for_an_unknown_cause_a_ticket_that_is_not_closed_or_does_not_exist(db):
    ap = closed(db)
    refused(db, "not one of the known causes", reopen, ap, "BECAUSE")
    refused(db, "is IN_PROGRESS, not CLOSED", reopen, _mk(db, "IN_PROGRESS"))
    refused(db, "does not exist", reopen, "FND-9999")
    refused(db, "reopens to", reopen, ap, "OTHER", ME, "a closed ticket cannot go straight to blocked", "BLOCKED")
    assert status(db, ap) == "CLOSED" and reopen_rows(db, ap) == []


def test_the_same_actor_takes_a_reopening_back_within_the_hour_and_the_closure_returns_as_it_was(db):
    ap = closed(db)
    before = row(db, ap)
    reopen(db, ap)
    event = rescind(db, ap)
    assert row(db, ap) == before and status(db, ap) == "CLOSED"
    assert [k for k, _, _ in reopen_rows(db, ap)] == ["REOPEN", "RESCIND"]
    assert db.execute("SELECT from_status, to_status, actor FROM backlog.status_event WHERE event_id = %s", (event,)).fetchone() == ("IN_PROGRESS", "CLOSED", ME)
    assert db.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0] == 0     # a routine write, not a V-00


def test_only_the_actor_who_reopened_rescinds_only_while_nothing_else_happened_and_only_in_the_hour(db):
    ap = closed(db)
    reopen(db, ap)
    refused(db, "only the actor who reopened", rescind, ap, "agent:claude-sonnet-5-5")
    tr(db, ap, "BLOCKED", condition="waiting for the vendor to answer the question")                 # the ticket moved
    refused(db, "has moved since", rescind, ap)
    other = closed(db)
    refused(db, "no reopening to take back", rescind, other)
    old = closed(db)
    event = reopen(db, old)
    db.execute("INSERT INTO backlog.reopen_event (action_point_id, kind, actor, cause, reason, prior, status_event_id, event_ts) "
               "SELECT action_point_id, 'REOPEN', actor, cause, reason, prior, status_event_id, clock_timestamp() - interval '2 hours' FROM backlog.reopen_event WHERE action_point_id = %s", (old,))
    refused(db, "one-hour window", rescind, old)                                                # the latest reopening is two hours old


def test_the_old_forced_reopening_no_longer_counts_as_a_violation(db):
    ap = closed(db)
    tr(db, ap, "IN_PROGRESS", force=True, comment="forced the old way, with a reason of well over forty characters")
    assert violations(db, "V-FORCED", ap) == []


# ---------------------------------------------------------------- holds release by themselves

def hold(conn, ap, code):
    return conn.execute("SELECT backlog.sp_raise_hold(%s, 'a hold raised in a test for the release rules', %s, 'system:sync')", (ap, code)).fetchone()[0]


def open_holds(conn, ap):
    return sorted(c for (c,) in conn.execute("SELECT violation_code FROM backlog.v_open_hold WHERE action_point_id = %s", (ap,)).fetchall())


def release_holds(conn, actor=AUTO):
    return conn.execute("SELECT backlog.fn_auto_release_holds(%s)", (actor,)).fetchone()[0]


def test_system_holds_release_when_no_pair_is_undecided_and_a_persons_hold_stays(db):
    a, b = _mk(db), _mk(db)
    hold(db, a, "V-COLLISION")
    hold(db, a, "V-HOOK-BYPASS")
    hold(db, a, "V-SOMETHING-ELSE")                                                            # not a system collision code: a person's to release
    assert release_holds(db) == 2 and open_holds(db, a) == ["V-SOMETHING-ELSE"]
    assert db.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s AND comment LIKE '%%released automatically%%'", (a,)).fetchone()[0] == 2
    assert release_holds(db) == 0                                                              # idempotent


def test_a_hold_stays_while_a_pair_of_its_ticket_is_undecided_and_goes_when_it_is_decided(db):
    a, b = _mk(db), _mk(db)
    hold(db, a, "V-COLLISION")
    pa, pb = propose_pair(db, a, b)
    assert release_holds(db) == 0 and open_holds(db, a) == ["V-COLLISION"]
    as_role(db, "backlog_agent")
    db.execute("SELECT backlog.sp_agent_adjudicate_overlap(%s, %s, 'RELATED', NULL, %s, %s)", (pa, pb, EVIDENCE, ME))
    as_owner(db)
    assert open_holds(db, a) == []                                                             # the core released it with the decision


def test_only_an_agent_or_a_system_actor_may_run_the_automatic_release(db):
    refused(db, "must be an authorized agent", release_holds, OWNER)
    refused(db, "must be an authorized agent", release_holds, "agent:claude")                     # the generic placeholder is never authorized


# ---------------------------------------------------------------- an agent decides a collision pair

def agent_adjudicate(conn, a, b, decision="RELATED", reason=EVIDENCE, actor=ME):
    return conn.execute("SELECT backlog.sp_agent_adjudicate_overlap(%s, %s, %s, NULL, %s, %s)", (a, b, decision, reason, actor)).fetchone()[0]


def test_an_agent_decides_a_pair_with_stated_evidence_and_the_queue_empties(db):
    a, b = propose_pair(db, _mk(db), _mk(db))
    as_role(db, "backlog_agent")
    assert agent_adjudicate(db, a, b) is not None
    as_owner(db)
    assert queue(db) == []
    assert db.execute("SELECT decision, actor FROM backlog.gate_adjudication WHERE a = %s AND b = %s", (a, b)).fetchone() == ("RELATED", ME)
    assert db.execute("SELECT link_type, status, username FROM backlog.v_backlog_link WHERE from_id = %s AND to_id = %s AND link_type = 'RELATED'", (a, b)).fetchone() == ("RELATED", "CONFIRMED", ME)


def test_an_agent_cannot_revert_decide_without_evidence_decide_twice_or_decide_as_someone_else(db):
    a, b = propose_pair(db, _mk(db), _mk(db))
    as_role(db, "backlog_agent")
    refused(db, "40 or more characters", agent_adjudicate, a, b, "RELATED", "too short")
    refused(db, "REVERT needs a person", agent_adjudicate, a, b, "REVERT", EVIDENCE)
    refused(db, "must be an authorized agent", agent_adjudicate, a, b, "RELATED", EVIDENCE, OWNER)
    agent_adjudicate(db, a, b, "NONE")
    refused(db, "is not undecided", agent_adjudicate, a, b, "OVERLAPS")                              # a decided pair stays decided: nobody overwrites it
    assert db.execute("SELECT decision FROM backlog.gate_adjudication WHERE a = %s AND b = %s", (a, b)).fetchall() == [("NONE",)]


def test_the_autopilot_cannot_adjudicate_and_the_agent_cannot_set_the_autopilot_mode(db):
    a, b = propose_pair(db, _mk(db), _mk(db))
    as_role(db, "backlog_autopilot")
    refused(db, "permission denied", agent_adjudicate, a, b)
    as_owner(db)
    as_role(db, "backlog_agent")
    refused(db, "permission denied", lambda c: c.execute("SELECT backlog.sp_set_autopilot_mode('ON', 'an agent tries to switch the autopilot on', %s)", (ME,)))


# ---------------------------------------------------------------- the autopilot mode is an event

def set_mode(conn, mode, actor, reason="a mode change recorded by a test"):
    return conn.execute("SELECT backlog.sp_set_autopilot_mode(%s, %s, %s)", (mode, reason, actor)).fetchone()[0]


def mode(conn):
    return conn.execute("SELECT backlog.fn_autopilot_mode()").fetchone()[0]


def test_the_mode_defaults_to_shadow_a_person_sets_any_and_the_autopilot_moves_only_between_shadow_and_on(db):
    assert mode(db) == "SHADOW"
    set_mode(db, "ON", AUTO)
    assert mode(db) == "ON"
    refused(db, "moves only SHADOW <-> ON", set_mode, "OFF", AUTO)
    set_mode(db, "SHADOW", AUTO)
    assert mode(db) == "SHADOW"
    set_mode(db, "OFF", OWNER)
    assert mode(db) == "OFF"
    refused(db, "moves only SHADOW <-> ON", set_mode, "ON", AUTO)                                  # the autopilot never lifts an OFF a person set
    set_mode(db, "ON", OWNER)
    assert mode(db) == "ON"


def test_only_a_person_or_the_autopilot_sets_the_mode_and_the_events_are_append_only(db):
    refused(db, "only a person or the autopilot", set_mode, "ON", ME)
    refused(db, "is not OFF, SHADOW or ON", set_mode, "FULL", OWNER)
    set_mode(db, "ON", OWNER)
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.autopilot_mode_event SET mode = 'OFF'"))
    refused(db, "append-only", lambda c: c.execute("DELETE FROM backlog.autopilot_mode_event"))


# ---------------------------------------------------------------- the DEPLOY fact cannot be forged

def fact(conn, ap, ref="migrate-events-1", kind="DEPLOY"):
    return conn.execute("SELECT backlog.sp_record_fact(%s, %s, %s, '{\"phase\": \"events\"}'::jsonb, now())", (kind, ref, ap)).fetchone()[0]


def test_the_facts_login_records_a_deploy_as_system_deploy_and_only_a_deploy(db):
    ap = _mk(db)
    as_role(db, "backlog_facts")
    assert fact(db, ap) is True and fact(db, ap) is False                                         # idempotent
    refused(db, "only a DEPLOY fact", fact, ap, "x", "CYCLE")
    refused(db, "only a DEPLOY fact", fact, ap, "x", "COMMIT")
    refused(db, "does not exist", fact, "FND-9999")
    as_owner(db)
    assert db.execute("SELECT kind, actor, source FROM backlog.ops_event WHERE action_point_id = %s", (ap,)).fetchone() == ("DEPLOY", DEPLOYER, "OTHER")


def test_no_other_login_can_forge_a_deploy_not_even_through_the_event_routine(db):
    ap = _mk(db)
    for role in ("backlog_autopilot", "backlog_agent"):
        as_role(db, role)
        refused(db, "(permission denied|only through backlog.sp_record_fact)", lambda c: c.execute("SELECT backlog.sp_record_ops_event('DEPLOY', 'forged', %s, 'OTHER', '{}'::jsonb, now(), %s)", (ap, AUTO)))
        refused(db, "permission denied", fact, ap)
        as_owner(db)
    as_role(db, "backlog_autopilot")
    assert db.execute("SELECT backlog.sp_record_ops_event('MERGE', %s, %s, 'REFS', '{}'::jsonb, now(), %s)", ("a" * 40, ap, AUTO)).fetchone()[0] is True      # its own kinds are untouched
    as_owner(db)
    assert db.execute("SELECT count(*) FROM backlog.ops_event WHERE kind = 'DEPLOY'").fetchone()[0] == 0


def test_the_guard_alone_stops_an_insert_by_a_login_that_could_reach_the_table(db):
    ap = _mk(db)
    db.execute("GRANT INSERT ON backlog.ops_event TO backlog_autopilot")                         # even with a table grant, the trigger refuses a DEPLOY row
    db.execute("GRANT USAGE ON SEQUENCE backlog.ops_event_event_id_seq TO backlog_autopilot")
    as_role(db, "backlog_autopilot")
    refused(db, "only through backlog.sp_record_fact", lambda c: c.execute("INSERT INTO backlog.ops_event (kind, ref, action_point_id, source, actor, fact_ts) VALUES ('DEPLOY', 'x', %s, 'OTHER', %s, now())", (ap, AUTO)))
    as_owner(db)
    db.execute("INSERT INTO backlog.ops_event (kind, ref, action_point_id, source, actor, fact_ts) VALUES ('DEPLOY', 'by-a-superuser', %s, 'OTHER', 'system:deploy', now())", (ap,))     # a superuser (a migration, a backfill) still can


# ---------------------------------------------------------------- privileges and rollback

def test_the_new_routines_belong_to_the_roles_that_need_them_and_to_nobody_else(db):
    def can(role, name):
        return db.execute("SELECT bool_or(has_function_privilege(%s, p.oid, 'EXECUTE')) FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = %s", (role, name)).fetchone()[0]
    for name in ("sp_reopen", "sp_rescind_reopen", "fn_auto_release_holds", "fn_autopilot_mode"):
        assert can("backlog_agent", name) and can("backlog_autopilot", name) and not can("backlog_facts", name), name
    assert can("backlog_agent", "sp_agent_adjudicate_overlap") and not can("backlog_autopilot", "sp_agent_adjudicate_overlap") and not can("backlog_facts", "sp_agent_adjudicate_overlap")
    assert can("backlog_autopilot", "sp_set_autopilot_mode") and not can("backlog_agent", "sp_set_autopilot_mode")
    assert can("backlog_facts", "sp_record_fact") and not can("backlog_agent", "sp_record_fact") and not can("backlog_autopilot", "sp_record_fact")
    assert not can("backlog_agent", "fn_adjudicate_core") and not can("backlog_autopilot", "fn_adjudicate_core")        # the core is called only by the routines, never granted


def test_the_rollback_drops_the_routines_and_the_guard_and_keeps_the_history_the_role_and_the_facts(db):
    ap = closed(db)
    reopen(db, ap)
    as_role(db, "backlog_facts")
    fact(db, ap)
    as_owner(db)
    mig.migrate(db, "events", down=True)
    have = mig.existing(db, "events")
    assert have["routines"] == [] and have["tables"] == sorted(mig.PHASES["events"]["tables"]) and have["roles"] == ["backlog_facts"]
    assert db.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'backlog.ops_event'::regclass AND tgname = 'deploy_fact_guard'").fetchone()[0] == 0
    assert len(reopen_rows(db, ap)) == 1 and db.execute("SELECT count(*) FROM backlog.ops_event WHERE kind = 'DEPLOY'").fetchone()[0] == 1
    mig.migrate(db, "events")                                                                   # applying it again resumes
    assert mig.existing(db, "events")["routines"] == sorted(mig.PHASES["events"]["routines"])


# ---------------------------------------------------------------- the autopilot's track record (Backlog v3.1 mode promotion and demotion)

def note(conn, ap, action="MOVE", ref="a" * 40, text=None, mode="SHADOW"):
    """A note written by the autopilot through its own routine, exactly as the runner writes it."""
    from shared import backlog_autopilot as ap_mod
    d = ap_mod.Decision(ap=ap, action=action, from_status="IN_PROGRESS", to_status="READY_FOR_DEPLOY", evidence_ref=ref)
    # written with the clock's time: in production every note is its own transaction, here a whole test is one and now() would stand still
    conn.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment, log_ts) VALUES (%s, %s, %s, clock_timestamp())", (ap, AUTO, text or ap_mod.shadow_note(d, mode)))


def record(conn, window_days=30):
    from shared import backlog_client as bc
    return bc.autopilot_track_record(window_days, conn=conn)


def autopilot_closed(conn, days_ago=0):
    """A ticket whose LATEST closure before any reopening is the autopilot's (its closure event is added `days_ago` days back, as the autopilot's own would be)."""
    ap = closed(conn)
    conn.execute("INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, reason, event_ts) VALUES (%s, 'READY_FOR_DEPLOY', 'CLOSED', %s, 'closed by the autopilot on evidence', "
                 "clock_timestamp() - make_interval(days => %s))", (ap, AUTO, days_ago))
    return ap


def test_the_track_record_counts_distinct_shadow_decisions_to_act_and_never_waits(db):
    a, b = _mk(db, "IN_PROGRESS"), _mk(db, "IN_PROGRESS")
    note(db, a)
    note(db, a, ref="b" * 40)                                                                    # another merge is another decision
    note(db, b, action="WAIT")                                                                   # a wait is not a decision to act
    note(db, b, mode="ON")                                                                       # an applied decision is not a SHADOW one
    assert record(db) == {"mode": "SHADOW", "decisions": 2, "contradicted": 0, "reopened_closures": 0}


def test_the_decision_count_restarts_at_every_mode_event(db):
    a = _mk(db, "IN_PROGRESS")
    note(db, a)
    assert record(db)["decisions"] == 1
    set_mode(db, "ON", OWNER)
    assert record(db)["decisions"] == 0 and record(db)["mode"] == "ON"
    note(db, a, ref="c" * 40)
    assert record(db)["decisions"] == 1


def test_a_decision_followed_by_a_reopening_of_its_ticket_is_contradicted_unless_the_reopening_is_rescinded(db):
    a = closed(db)
    note(db, a)
    assert record(db)["contradicted"] == 0
    reopen(db, a)
    assert record(db)["contradicted"] == 1
    rescind(db, a)
    assert record(db)["contradicted"] == 0 and record(db)["decisions"] == 1


def test_only_reopened_autopilot_closures_inside_the_window_count_and_a_rescinded_reopening_does_not(db):
    by_person = closed(db)
    reopen(db, by_person)                                                                        # a person's closure: not the autopilot's record
    old = autopilot_closed(db, days_ago=50)
    db.execute("INSERT INTO backlog.reopen_event (action_point_id, kind, actor, cause, reason, event_ts) VALUES (%s, 'REOPEN', %s, 'OTHER', 'reopened long ago, written by a test', "
               "clock_timestamp() - interval '40 days')", (old, ME))
    assert record(db)["reopened_closures"] == 0 and record(db, window_days=60)["reopened_closures"] == 1          # outside a 30-day window, inside a 60-day one
    mine = autopilot_closed(db)
    assert record(db)["reopened_closures"] == 0                                                  # closed, not reopened
    reopen(db, mine)
    assert record(db)["reopened_closures"] == 1
    rescind(db, mine)
    assert record(db)["reopened_closures"] == 0
