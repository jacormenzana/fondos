# tests/test_backlog_identity_pg.py
# -*- coding: utf-8 -*-
"""Identity constraints of Backlog v2 (FND-0337; db/pg_backlog/30_transitions.sql fn_actor_authorized, 32_human_role.sql, 31_revoke_raw_update.sql author trigger).

Owner decision of 2026-10-27: human actors write ONLY through a role reserved for the owner (backlog_human); agents must name their model (agent:claude, the generic
placeholder of the history, is refused for new writes). The rule looks at session_user (who logged in), so the tests switch the SESSION authorization, which is how a real
login differs from a SET ROLE. Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_identity_pg.py
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
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import ME, SESSION, _base, _mk, open_, tr  # noqa: E402

HUMAN = "human:jacor"


@pytest.fixture
def db(pg_conn):
    """Step A-1 as updated by FND-0337; the owner-only role does NOT exist yet."""
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    return pg_conn


@pytest.fixture
def dbh(db):
    """Step 32 applied: the role backlog_human exists and the rule binds."""
    mig.migrate(db, "human")
    return db


def as_login(conn, role, fn, *a, **kw):
    """Run fn as a session that LOGGED IN as `role` (session_user changes, unlike SET ROLE)."""
    conn.execute(f"SET SESSION AUTHORIZATION {role}")
    try:
        return fn(conn, *a, **kw)
    finally:
        try:
            conn.execute("RESET SESSION AUTHORIZATION")
        except psycopg.errors.InFailedSqlTransaction:        # the call failed inside a savepoint: its rollback restores the session
            pass


def denied(conn, role, fn, *a, match="not authorized", **kw):
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match=match):
        with conn.transaction():
            as_login(conn, role, fn, *a, **kw)


def authorized(conn, role, actor):
    return as_login(conn, role, lambda c: c.execute("SELECT backlog.fn_actor_authorized(%s)", (actor,)).fetchone()[0])


# --- the rule itself --------------------------------------------------------------------------------------------------------------------------

def test_before_the_owner_role_exists_human_actors_are_not_enforced_but_the_generic_agent_is_refused(db):
    assert db.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'backlog_human'").fetchone()[0] == 0
    assert authorized(db, "backlog_writer", HUMAN) is True                                      # not yet enforced: nothing is broken before the owner applies step 32
    assert authorized(db, "backlog_writer", ME) is True
    assert authorized(db, "backlog_writer", "system:autodetect") is True
    assert authorized(db, "backlog_writer", "agent:claude") is False                            # always: an agent names its model
    assert authorized(db, "backlog_writer", "claude") is False and authorized(db, "backlog_writer", None) is False   # not canonical


def test_once_the_role_exists_human_actors_need_it(dbh):
    assert authorized(dbh, "backlog_writer", HUMAN) is False                                    # the shared connection can no longer write as the owner
    assert authorized(dbh, "backlog_human", HUMAN) is True                                      # the owner-only login can
    assert authorized(dbh, "backlog_human", ME) is True                                         # and it may also act as an agent identity
    assert authorized(dbh, "backlog_writer", ME) is True and authorized(dbh, "backlog_writer", "system:release") is True
    assert authorized(dbh, "postgres", HUMAN) is True                                           # a superuser (the DDL role) passes
    assert authorized(dbh, "backlog_writer", "agent:claude") is False and authorized(dbh, "backlog_human", "agent:claude") is False


def test_the_function_looks_at_the_login_not_at_set_role(dbh):
    """A session that logged in as the shared writer cannot become the owner by SET ROLE: the writer is not a member of backlog_human."""
    dbh.execute("SET SESSION AUTHORIZATION backlog_writer")
    try:
        with pytest.raises(psycopg.errors.Error):
            with dbh.transaction():
                dbh.execute("SET ROLE backlog_human")
        assert dbh.execute("SELECT backlog.fn_actor_authorized(%s)", (HUMAN,)).fetchone()[0] is False
    finally:
        dbh.execute("RESET SESSION AUTHORIZATION")


# --- the routines enforce it ------------------------------------------------------------------------------------------------------------------

def test_open_ticket_and_transition_refuse_a_human_actor_from_the_shared_connection(dbh):
    ap = _mk(dbh, "OPEN")
    denied(dbh, "backlog_writer", lambda c: open_(c, actor=HUMAN))
    denied(dbh, "backlog_writer", lambda c: tr(c, ap, "TODO", actor=HUMAN))
    assert dbh.execute("SELECT status FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0] == "OPEN"        # nothing changed
    assert dbh.execute("SELECT count(*) FROM backlog.status_event WHERE actor = %s", (HUMAN,)).fetchone()[0] == 0


def test_open_ticket_and_transition_accept_a_human_actor_from_the_owner_login(dbh):
    ap = as_login(dbh, "backlog_human", lambda c: open_(c, actor=HUMAN))
    as_login(dbh, "backlog_human", lambda c: tr(c, ap, "TODO", actor=HUMAN))
    assert dbh.execute("SELECT reporter, status FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone() == (HUMAN, "TODO")
    assert [r[0] for r in dbh.execute("SELECT actor FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id", (ap,)).fetchall()] == [HUMAN, HUMAN]


def test_the_generic_agent_identity_is_refused_by_the_routines_and_a_named_model_is_accepted(db):
    ap = _mk(db, "OPEN")
    denied(db, "backlog_writer", lambda c: tr(c, ap, "TODO", actor="agent:claude"), match="not authorized")
    denied(db, "backlog_writer", lambda c: open_(c, actor="agent:claude"), match="not authorized")
    as_login(db, "backlog_writer", lambda c: tr(c, ap, "TODO", actor=ME))                       # a named model works, with or without the owner role


def test_assigning_work_to_a_human_stays_allowed_from_the_shared_connection(dbh):
    ap = _mk(dbh, "OPEN")
    as_login(dbh, "backlog_writer", lambda c: tr(c, ap, "IN_PROGRESS", actor=ME, assignee=HUMAN))
    assert dbh.execute("SELECT assignee FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0] == HUMAN       # an assignment is not writing AS the human


def test_close_release_follows_the_rule_through_the_alias(dbh):
    a, b = _mk(dbh, "IN_PROGRESS"), _mk(dbh, "IN_PROGRESS")
    for x in (a, b):
        tr(dbh, x, "READY_FOR_DEPLOY", release_version="R2026.10-ID-01")
    denied(dbh, "backlog_writer", lambda c: c.execute("CALL backlog.sp_close_release('R2026.10-ID-01', 'jacor')"), match="not authorized")
    as_login(dbh, "backlog_writer", lambda c: c.execute("CALL backlog.sp_close_release('R2026.10-ID-01', 'agent:claude-opus-5-5')"))   # an agent closes under its own name
    assert dbh.execute("SELECT count(*) FROM backlog.backlog WHERE release_version = 'R2026.10-ID-01' AND status = 'CLOSED'").fetchone()[0] == 2


def test_human_closure_through_the_owner_login(dbh):
    a = _mk(dbh, "IN_PROGRESS")
    tr(dbh, a, "READY_FOR_DEPLOY", release_version="R2026.10-ID-02")
    as_login(dbh, "backlog_human", lambda c: c.execute("CALL backlog.sp_close_release('R2026.10-ID-02', 'jacor')"))
    assert dbh.execute("SELECT actor FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (a,)).fetchone()[0] == HUMAN


# --- the role (step 32) -----------------------------------------------------------------------------------------------------------------------

def test_the_role_is_created_without_privileges_of_its_own_and_inherits_the_writers(dbh):
    assert dbh.execute("SELECT rolsuper, rolcanlogin, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles WHERE rolname = 'backlog_human'").fetchone() == (False, True, False, False, False)
    assert dbh.execute("SELECT pg_has_role('backlog_human', 'backlog_writer', 'MEMBER')").fetchone()[0] is True
    assert dbh.execute("SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'backlog_human'").fetchone()[0] is True       # no password is ever set by the script: the owner sets it
    assert dbh.execute("SELECT has_function_privilege('backlog_human', 'backlog.sp_transition(text,text,text,text,text,text,text,text,text,date,text,text,text,boolean)', 'EXECUTE')").fetchone()[0] is True


def test_step_32_is_idempotent_and_its_rollback_restores_the_previous_state(dbh):
    mig.migrate(dbh, "human")
    assert mig.existing(dbh, "human")["roles"] == ["backlog_human"]
    mig.migrate(dbh, "human", down=True)
    assert mig.existing(dbh, "human")["roles"] == []
    assert authorized(dbh, "backlog_writer", HUMAN) is True                                     # back to not enforced


def test_step_32_refuses_to_run_without_the_updated_routines(pg_conn):
    _base(pg_conn)                                                                              # no step A-1 at all
    with pytest.raises(RuntimeError, match="fn_actor_authorized"):
        mig.migrate(pg_conn, "human")


# --- step A-2: the same rule on direct log and link-event writes (closes the INSERT loophole) -----------------------------------------------------

def test_the_author_trigger_applies_the_same_rule_after_a2(dbh):
    mig.migrate(dbh, "revoke")
    ap = _mk(dbh, "OPEN")
    ins = "INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, 'x')"
    for who in ("jacor", HUMAN):                                                                # the owner's raw name and the canonical one
        denied(dbh, "backlog_writer", lambda c, w=who: c.execute(ins, (ap, w)), match="may not write")
        as_login(dbh, "backlog_human", lambda c, w=who: c.execute(ins, (ap, w)))
    for who in ("claude", "claude-code", "agent:claude"):                                       # the generic agent, whatever the spelling
        denied(dbh, "backlog_writer", lambda c, w=who: c.execute(ins, (ap, w)), match="may not write")
        denied(dbh, "backlog_human", lambda c, w=who: c.execute(ins, (ap, w)), match="may not write")
    for who in (ME, "SYSTEM_RELEASE", "system:autodetect"):
        as_login(dbh, "backlog_writer", lambda c, w=who: c.execute(ins, (ap, w)))


def test_the_same_rule_covers_link_events_after_a2(dbh):
    mig.migrate(dbh, "revoke")
    a, b = _mk(dbh, "OPEN"), _mk(dbh, "OPEN")
    ins = ("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'PROPOSED', 'e', %s)")
    denied(dbh, "backlog_writer", lambda c: c.execute(ins, (a, b, HUMAN)), match="may not write")
    as_login(dbh, "backlog_human", lambda c: c.execute(ins, (a, b, HUMAN)))
    as_login(dbh, "backlog_writer", lambda c: c.execute(ins, (a, b, ME)))


# --- the client ---------------------------------------------------------------------------------------------------------------------------------

def test_the_client_uses_the_owner_connection_only_for_human_actors(monkeypatch):
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://writer@h/gestion")
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN_HUMAN", raising=False)
    assert bc.dsn_for_actor(ME) == "postgresql://writer@h/gestion" and bc.dsn_for_actor("system:release") == "postgresql://writer@h/gestion"
    with pytest.raises(bc.HumanDsnMissing, match="FONDOS_BACKLOG_PG_DSN_HUMAN"):
        bc.dsn_for_actor(HUMAN)                                                                 # never falls back to the shared connection
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN_HUMAN", "postgresql://human@h/gestion")
    assert bc.dsn_for_actor(HUMAN) == "postgresql://human@h/gestion" and bc.dsn_for_actor(ME) == "postgresql://writer@h/gestion"
    assert issubclass(bc.HumanDsnMissing, bc.BacklogUnavailable)


def test_a_human_write_without_the_owner_dsn_fails_loudly_before_touching_anything(monkeypatch):
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://writer@h/gestion")
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN_HUMAN", raising=False)
    with pytest.raises(bc.HumanDsnMissing):
        bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id=SESSION, description="d", first_log="first log", actor=HUMAN)
    with pytest.raises(bc.HumanDsnMissing):
        bc.append_log("FND-0001", HUMAN, "a note")
    with pytest.raises(bc.HumanDsnMissing):
        bc.record_link_event(from_id="FND-0001", to_id="FND-0002", link_type="RELATED", status="PROPOSED", evidence="e", username=HUMAN)


def test_a_borrowed_connection_is_used_as_given_whatever_the_actor(db):
    ap = bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id=SESSION, description="d", first_log="first log", actor=HUMAN, conn=db)
    assert db.execute("SELECT reporter FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0] == HUMAN   # the caller owns the connection and its authority
