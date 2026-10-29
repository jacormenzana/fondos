# tests/test_backlog_iam_pg.py
# -*- coding: utf-8 -*-
"""IAM of Backlog v2 (FND-0354; owner decisions of 2026-10-29): db/pg_backlog/50_iam.sql, applied by scripts/ops/migrate_backlog_v2.py --phase iam.

Three per-identity roles (reader, logger, incident), the scoped routines they may run (sp_append_log: a note on an EXISTING ticket and nothing else; sp_report_incident: insert-only),
and the binding of database logins to identities (a bound login writes ONLY as its identity; a human identity only from a login bound to it). The rule looks at session_user, so the tests
switch the SESSION authorization, which is how a real login differs from SET ROLE. Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_iam_pg.py
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
from test_backlog_identity_pg import as_login, denied  # noqa: E402
from test_backlog_transitions_pg import ME, SESSION, _base, _mk, tr  # noqa: E402

OWNER_LOGIN, DATA_LOGIN, DEVOPS_LOGIN = "iam_owner", "iam_data", "iam_devops"
OWNER, DATA, DEVOPS = "human:j.cormenzana_owner", "human:j.cormenzana_data", "human:a.gomez_devops"
REASON = "bound by the test, with a reason"


@pytest.fixture
def db(pg_conn):
    """Step A-1 (with the FND-0354 hook) and then step iam applied; the owner-only role exists too."""
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    mig.migrate(pg_conn, "human")
    mig.migrate(pg_conn, "iam")
    return pg_conn


def bind(conn, login, actor, person, kind, active=True, declared=OWNER):
    return conn.execute("SELECT backlog.sp_bind_login(%s, %s, %s, %s, %s, %s, %s)", (login, actor, person, kind, active, REASON, declared)).fetchone()[0]


def invalid(conn, role, fn, *a, match, **kw):
    """The routine ran as `role` and refused the arguments (a data error, not a privilege one)."""
    with pytest.raises(psycopg.errors.Error, match=match):
        with conn.transaction():
            as_login(conn, role, fn, *a, **kw)


def mk_login(conn, name, member_of=()):
    conn.execute(f"CREATE ROLE {name} LOGIN")
    for r in member_of:
        conn.execute(f"GRANT {r} TO {name}")


# --- the phase -------------------------------------------------------------------------------------------------------------------------------

def test_the_phase_creates_the_roles_the_binding_store_and_the_routines_and_is_idempotent(db):
    have = mig.existing(db, "iam")
    assert have["roles"] == ["backlog_incident", "backlog_logger", "backlog_reader"] and have["tables"] == ["actor_login_event"] and have["views"] == ["v_actor_login"]
    assert set(have["routines"]) == {"fn_actor_bound", "sp_bind_login", "sp_append_log", "sp_report_incident"} and not have["missing_requires"]
    mig.migrate(db, "iam")                                                                    # a second apply changes nothing and does not fail
    assert db.execute("SELECT count(*) FROM backlog.actor_login_event").fetchone()[0] == 0


def test_the_phase_refuses_to_run_before_step_a1_has_the_hook(pg_conn):
    _base(pg_conn)
    with pytest.raises(RuntimeError, match="needs"):
        mig.migrate(pg_conn, "iam")


def test_the_rollback_removes_everything_and_puts_the_hook_back_to_the_stub(db):
    ap = _mk(db)
    bind(db, "backlog_logger", "agent:%", "non-human", "AGENT")
    mig.migrate(db, "iam", down=True)
    have = mig.existing(db, "iam")
    assert have["roles"] == [] and have["tables"] == [] and have["views"] == [] and "sp_append_log" not in have["routines"] and "fn_actor_bound" in have["routines"]
    assert db.execute("SELECT backlog.fn_actor_bound('human:anyone')").fetchone()[0] is True        # the stub allows everything again
    assert db.execute("SELECT status FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0] == "OPEN"     # tickets untouched
    mig.migrate(db, "iam")                                                                    # and it can be applied again


def test_reapplying_step_a1_after_iam_does_not_overwrite_the_real_hook(db):
    mig.migrate(db, "relations")                                                              # the phase that re-applies the A-1 block
    assert "v_actor_login" in db.execute("SELECT pg_get_functiondef('backlog.fn_actor_bound(text)'::regprocedure)").fetchone()[0]


# --- what each role can do -------------------------------------------------------------------------------------------------------------------

def test_the_privilege_matrix_of_the_three_roles(db):
    cat = bc.iam_catalog(conn=db)
    r = cat["roles"]
    for name in ("backlog_reader", "backlog_logger", "backlog_incident"):
        assert r[name]["select_all"] is True and r[name]["write_tables"] == [] and r[name]["superuser"] is False, name        # nobody writes a table directly
        assert "sp_open_ticket" not in r[name]["executes"] and "sp_transition" not in r[name]["executes"] and "sp_bind_login" not in r[name]["executes"], name
    assert r["backlog_reader"]["executes"] == []
    assert r["backlog_logger"]["executes"] == ["sp_append_log"]
    assert r["backlog_incident"]["executes"] == ["sp_report_incident"]
    db.execute("CREATE ROLE somebody_else NOLOGIN")
    for sig in ("backlog.sp_append_log(text,text,text)", "backlog.sp_report_incident(text,text,text,text,text,text,interval,text,text,text,text)", "backlog.fn_actor_bound(text)"):
        assert db.execute("SELECT has_function_privilege('somebody_else', %s::regprocedure, 'EXECUTE')", (sig,)).fetchone()[0] is False     # PUBLIC has no execute


def test_the_reader_reads_and_can_do_nothing_else(db):
    ap = _mk(db)
    assert as_login(db, "backlog_reader", lambda c: c.execute("SELECT count(*) FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0]) == 1
    assert as_login(db, "backlog_reader", lambda c: c.execute("SELECT count(*) FROM backlog.v_actor_login").fetchone()[0]) == 0
    denied(db, "backlog_reader", lambda c: c.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, 'x')", (ap, ME)), match="permission denied")
    denied(db, "backlog_reader", lambda c: c.execute("SELECT backlog.sp_append_log(%s, %s, 'a note')", (ap, ME)), match="permission denied")
    denied(db, "backlog_reader", lambda c: tr(c, ap, "TODO"), match="permission denied")


# --- the agent bookkeeping path --------------------------------------------------------------------------------------------------------------

def test_the_logger_appends_a_note_to_an_existing_ticket(db):
    ap = _mk(db)
    n0 = db.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (ap,)).fetchone()[0]
    log_id = as_login(db, "backlog_logger", lambda c: c.execute("SELECT backlog.sp_append_log(%s, %s, 'progress note from the agent')", (ap, ME)).fetchone()[0])
    assert db.execute("SELECT username, comment FROM backlog.BACKLOG_LOGS WHERE log_id = %s", (log_id,)).fetchone() == (ME, "progress note from the agent")
    assert db.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (ap,)).fetchone()[0] == n0 + 1
    assert as_login(db, "backlog_logger", lambda c: c.execute("SELECT backlog.sp_append_log(%s, 'system:release', 'a system note')", (ap,)).fetchone()[0]) > log_id


def test_the_logger_cannot_create_move_link_or_edit_anything(db):
    ap = _mk(db)
    denied(db, "backlog_logger", lambda c: c.execute("SELECT backlog.sp_open_ticket('t','BUG','N/A',NULL,'LOW',%s,%s,'d','first log')", (ME, SESSION)), match="permission denied")
    denied(db, "backlog_logger", lambda c: tr(c, ap, "TODO"), match="permission denied")
    denied(db, "backlog_logger", lambda c: c.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'PROPOSED', 'e', %s)",
                                                     (ap, ap, ME)), match="permission denied")
    denied(db, "backlog_logger", lambda c: c.execute("UPDATE backlog.backlog SET title = 'x' WHERE action_point_id = %s", (ap,)), match="permission denied")
    denied(db, "backlog_logger", lambda c: c.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, 'raw')", (ap, ME)), match="permission denied")
    denied(db, "backlog_logger", lambda c: c.execute("SELECT backlog.sp_report_incident('obj', 'desc')"), match="permission denied")


def test_sp_append_log_refuses_a_missing_ticket_a_human_the_generic_agent_and_an_empty_note(db):
    ap = _mk(db)
    for args, match in (((("FND-0999999"), ME, "note"), "does not exist"), ((ap, OWNER, "note"), "must be agent"), ((ap, "agent:claude", "note"), "not authorized"),
                        ((ap, ME, "   "), "required"), ((ap, "claude", "note"), "must be agent")):
        invalid(db, "backlog_logger", lambda c, a=args: c.execute("SELECT backlog.sp_append_log(%s, %s, %s)", a), match=match)


# --- the automatic incident capture ----------------------------------------------------------------------------------------------------------

def report(c, obj="pipeline.step", desc="the step crashed with a stack trace", **kw):
    args = dict(p_object_name=obj, p_description=desc)
    args.update(kw)
    return c.execute("SELECT backlog.sp_report_incident(" + ", ".join(f"{k} => %s" for k in args) + ")", tuple(args.values())).fetchone()[0]


def test_the_incident_role_opens_a_ticket_authored_by_the_system_and_dedups_the_next_report(db):
    ap = as_login(db, "backlog_incident", report)
    assert db.execute("SELECT reporter, status, nature, priority, object_name FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone() == ("system:autodetect", "OPEN", "BUG", "HIGH", "pipeline.step")
    again = as_login(db, "backlog_incident", lambda c: report(c, desc="and again"))
    assert again == ap
    assert db.execute("SELECT count(*) FROM backlog.backlog WHERE object_name = 'pipeline.step'").fetchone()[0] == 1
    assert db.execute("SELECT username, comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id DESC LIMIT 1", (ap,)).fetchone() == ("system:autodetect", "Reincidence detected: and again")


def test_the_overlap_the_client_found_is_written_as_a_note_or_a_suggestion(db):
    target = _mk(db, "IN_PROGRESS")
    assert as_login(db, "backlog_incident", lambda c: report(c, obj="other.object", p_overlap_ticket=target, p_overlap_reason="same crash")) == target
    assert "overlap: same crash" in db.execute("SELECT comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id DESC LIMIT 1", (target,)).fetchone()[0]
    ap = as_login(db, "backlog_incident", lambda c: report(c, obj="third.object", p_suggest_ticket=target, p_suggest_reason="similar text"))
    assert ap != target and f"Possibly related (NOT merged): {target} -- similar text" in db.execute("SELECT comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id LIMIT 1", (ap,)).fetchone()[0]


def test_the_incident_role_can_only_report(db):
    ap = _mk(db)
    denied(db, "backlog_incident", lambda c: tr(c, ap, "TODO"), match="permission denied")
    denied(db, "backlog_incident", lambda c: c.execute("SELECT backlog.sp_append_log(%s, %s, 'note')", (ap, ME)), match="permission denied")
    denied(db, "backlog_incident", lambda c: c.execute("SELECT backlog.sp_open_ticket('t','BUG','N/A',NULL,'LOW','system:autodetect',%s,'d','l')", (SESSION,)), match="permission denied")
    invalid(db, "backlog_incident", lambda c: report(c, desc=""), match="required")
    invalid(db, "backlog_incident", lambda c: report(c, p_dedup="30 days"), match="dedup window")


# --- the binding of logins to identities -----------------------------------------------------------------------------------------------------

def test_nothing_is_enforced_while_no_login_is_bound(db):
    ap = _mk(db)
    note = lambda c, who: c.execute("SELECT backlog.sp_append_log(%s, %s, 'a note')", (ap, who)).fetchone()[0]              # noqa: E731
    assert as_login(db, "backlog_logger", note, ME) > 0 and as_login(db, "backlog_logger", note, "system:anything") > 0


def test_a_bound_login_writes_only_as_its_identity(db):
    ap = _mk(db)
    bind(db, "backlog_logger", "agent:claude-sonnet-5-5", "non-human", "AGENT")
    appended = lambda c, who: c.execute("SELECT backlog.sp_append_log(%s, %s, 'a note')", (ap, who)).fetchone()[0]       # noqa: E731
    assert as_login(db, "backlog_logger", appended, "agent:claude-sonnet-5-5") > 0
    denied(db, "backlog_logger", appended, "agent:claude-opus-5-5", match="not authorized")                          # another model: refused
    denied(db, "backlog_logger", appended, "system:release", match="not authorized")                                # another kind: refused
    assert db.execute("SELECT backlog.fn_actor_authorized(%s)", (ME,)).fetchone()[0] is True                         # the superuser (DDL role) is not bound


def test_an_agent_binding_may_use_a_wildcard_and_a_human_one_may_not(db):
    ap = _mk(db)
    bind(db, "backlog_logger", "agent:%", "non-human", "AGENT")
    note = lambda c, who: c.execute("SELECT backlog.sp_append_log(%s, %s, 'a note')", (ap, who)).fetchone()[0]              # noqa: E731
    assert as_login(db, "backlog_logger", note, "agent:claude-opus-5-5") > 0 and as_login(db, "backlog_logger", note, "agent:claude-sonnet-5-5") > 0
    denied(db, "backlog_logger", note, "system:release", match="not authorized")
    mk_login(db, DEVOPS_LOGIN)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            bind(db, DEVOPS_LOGIN, "human:%", "human:a.gomez", "HUMAN")
    with pytest.raises(psycopg.errors.CheckViolation):                                                                    # the kind must match the identity
        with db.transaction():
            bind(db, DEVOPS_LOGIN, "agent:x", "human:a.gomez", "HUMAN")


def test_a_human_identity_is_accepted_only_from_the_login_bound_to_it(db):
    for login in (OWNER_LOGIN, DATA_LOGIN, DEVOPS_LOGIN):
        mk_login(db, login, member_of=("backlog_human",))
    bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")
    bind(db, DATA_LOGIN, DATA, "human:j.cormenzana", "HUMAN")
    bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN")
    auth = lambda c, who: c.execute("SELECT backlog.fn_actor_authorized(%s)", (who,)).fetchone()[0]                        # noqa: E731
    assert as_login(db, OWNER_LOGIN, auth, OWNER) is True and as_login(db, DATA_LOGIN, auth, DATA) is True and as_login(db, DEVOPS_LOGIN, auth, DEVOPS) is True
    assert as_login(db, OWNER_LOGIN, auth, DATA) is False                                                                   # one login, one identity, even for the same person
    assert as_login(db, DEVOPS_LOGIN, auth, OWNER) is False and as_login(db, DEVOPS_LOGIN, auth, ME) is False              # a bound human login does not act as an agent either
    assert as_login(db, "backlog_human", auth, "human:jacor") is False                                                     # the old shared owner login is unbound: no human identity from it
    ap = as_login(db, OWNER_LOGIN, lambda c: c.execute("SELECT backlog.sp_open_ticket('t','BUG','N/A',NULL,'LOW',%s,%s,'d','first log')", (OWNER, SESSION)).fetchone()[0])
    assert db.execute("SELECT reporter FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0] == OWNER


def test_the_old_owner_login_keeps_working_when_it_is_bound_to_its_historical_identity(db):
    bind(db, "backlog_human", "human:jacor", "human:j.cormenzana", "HUMAN")
    assert as_login(db, "backlog_human", lambda c: c.execute("SELECT backlog.fn_actor_authorized('human:jacor')").fetchone()[0]) is True


def test_the_person_is_a_canonical_value_and_a_typo_is_rejected_by_the_database(db):
    mk_login(db, DEVOPS_LOGIN)
    for typo in ("a.gomez", "human:a.gomez_", "human:a.gómez", "human:A.Gomez", "human:j.cormenzana", " human:a.gomez", "jacor", "human:jacor", "", "non-human"):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                bind(db, DEVOPS_LOGIN, DEVOPS, typo, "HUMAN")                              # the last two belong to nobody / to the wrong kind; j.cormenzana does not own a.gomez_devops
    bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN")                                # the exact canonical value works
    for person in ("human:a.gomez", "human:m.ruiz", "claude", ""):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                bind(db, "backlog_logger", "agent:%", person, "AGENT")                    # a non-human login has no person but the literal non-human
    bind(db, "backlog_logger", "agent:%", "non-human", "AGENT")


def test_a_person_can_only_be_bound_to_his_own_identities(db):
    for login in (OWNER_LOGIN, DATA_LOGIN, DEVOPS_LOGIN, "iam_qa"):
        mk_login(db, login)
    bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")
    bind(db, DATA_LOGIN, DATA, "human:j.cormenzana", "HUMAN")
    bind(db, "backlog_human", "human:jacor", "human:j.cormenzana", "HUMAN")                 # the historical alias belongs to the owner
    bind(db, "iam_qa", "human:m.ruiz_qa", "human:m.ruiz", "HUMAN")
    for actor, person in ((DEVOPS, "human:j.cormenzana"), (OWNER, "human:a.gomez"), ("human:jacor", "human:m.ruiz"), ("human:m.ruiz_qa", "human:a.gomez"), ("human:x", "human:m.ruiz")):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                bind(db, DEVOPS_LOGIN, actor, person, "HUMAN")                                # three logins of one person spelled as two persons cannot be built either


def test_only_the_owner_binds_logins(db):
    mk_login(db, DEVOPS_LOGIN)
    for role in ("backlog_logger", "backlog_incident", "backlog_reader", "backlog_writer", DEVOPS_LOGIN):
        denied(db, role, lambda c: bind(c, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN"), match="permission denied|only the owner")
    mk_login(db, OWNER_LOGIN, member_of=("backlog_human",))
    as_login(db, OWNER_LOGIN, lambda c: bind(c, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN"))
    with pytest.raises(psycopg.errors.Error, match="not a login role"):
        with db.transaction():
            bind(db, "no_such_login", DEVOPS, "human:a.gomez", "HUMAN")


def test_a_revocation_is_a_new_event_and_the_history_is_append_only(db):
    ap = _mk(db)
    bind(db, "backlog_logger", "agent:claude-sonnet-5-5", "non-human", "AGENT")
    bind(db, "backlog_logger", "agent:claude-sonnet-5-5", "non-human", "AGENT", active=False)
    assert db.execute("SELECT active FROM backlog.v_actor_login WHERE login_name = 'backlog_logger'").fetchone()[0] is False
    assert db.execute("SELECT count(*) FROM backlog.actor_login_event WHERE login_name = 'backlog_logger'").fetchone()[0] == 2
    assert as_login(db, "backlog_logger", lambda c: c.execute("SELECT backlog.sp_append_log(%s, 'system:release', 'unbound again')", (ap,)).fetchone()[0]) > 0      # nothing active: not enforced
    for sql in ("UPDATE backlog.actor_login_event SET active = true", "DELETE FROM backlog.actor_login_event"):
        with pytest.raises(psycopg.errors.Error, match="append-only"):
            with db.transaction():
                db.execute(sql)


def test_the_shared_writer_can_be_bound_like_any_other_login(db):
    mk_login(db, "shared_writer", member_of=("backlog_writer",))
    bind(db, "shared_writer", "agent:claude-sonnet-5-5", "non-human", "AGENT")
    auth = lambda c, who: c.execute("SELECT backlog.fn_actor_authorized(%s)", (who,)).fetchone()[0]                        # noqa: E731
    assert as_login(db, "shared_writer", auth, "agent:claude-opus-5-5") is False
    assert as_login(db, "shared_writer", auth, "agent:claude-sonnet-5-5") is True


# --- the catalog the acceptance and the pre-flight read --------------------------------------------------------------------------------------

def test_two_logins_of_one_person_count_as_one_person_and_agents_are_not_persons(db):
    for login in (OWNER_LOGIN, DATA_LOGIN, DEVOPS_LOGIN):
        mk_login(db, login, member_of=("backlog_human",))
    bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")
    bind(db, DATA_LOGIN, DATA, "human:j.cormenzana", "HUMAN")
    bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN")
    bind(db, "backlog_logger", "agent:%", "non-human", "AGENT")
    cat = bc.iam_catalog(conn=db)
    assert cat["active_logins"] == 4 and cat["active_persons"] == ["human:a.gomez", "human:j.cormenzana"]


def test_the_catalog_lists_every_login_that_can_still_write_and_whether_it_is_bound(db):
    mk_login(db, "shared_writer", member_of=("backlog_writer",))
    mk_login(db, OWNER_LOGIN, member_of=("backlog_writer", "backlog_human"))
    bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")
    cat = bc.iam_catalog(conn=db)
    writers = {w["login"]: w["bound"] for w in cat["writers"]}
    assert writers["shared_writer"] is False and writers[OWNER_LOGIN] is True
    assert "shared_writer" in cat["unbound_writers"] and OWNER_LOGIN not in cat["unbound_writers"]
    assert "backlog_logger" not in writers and "backlog_reader" not in writers                       # they cannot open or move a ticket
    db.execute("ALTER ROLE shared_writer NOLOGIN")                                                   # the retirement of the shared login
    assert "shared_writer" not in bc.iam_catalog(conn=db)["unbound_writers"]


# --- A-2 pool: transitions made by distinct bound persons -------------------------------------------------------------------------------------

def test_transitions_are_counted_per_person_only_when_made_by_a_bound_human_identity_after_it_was_bound(db):
    for login in (OWNER_LOGIN, DATA_LOGIN, DEVOPS_LOGIN, "iam_qa"):
        mk_login(db, login, member_of=("backlog_human", "backlog_writer"))
    ap1, ap2, ap3, ap4 = _mk(db), _mk(db), _mk(db), _mk(db)
    tr(db, ap4, "TODO", actor=OWNER)                                                      # a human identity BEFORE any binding: never counted for that login
    bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")
    bind(db, DATA_LOGIN, DATA, "human:j.cormenzana", "HUMAN")
    bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN")
    bind(db, "iam_qa", "human:m.ruiz_qa", "human:m.ruiz", "HUMAN")
    assert bc.iam_catalog(conn=db)["transition_persons"] == []
    as_login(db, OWNER_LOGIN, lambda c: tr(c, ap1, "TODO", actor=OWNER))
    as_login(db, DATA_LOGIN, lambda c: tr(c, ap2, "TODO", actor=DATA))                    # the same person through a second login: still one person
    cat = bc.iam_catalog(conn=db)
    assert cat["transitions_by_person"] == {"human:j.cormenzana": 2} and cat["transition_persons"] == ["human:j.cormenzana"]
    as_login(db, DEVOPS_LOGIN, lambda c: tr(c, ap3, "TODO", actor=DEVOPS))
    as_login(db, "iam_qa", lambda c: tr(c, ap3, "IN_PROGRESS", actor="human:m.ruiz_qa"))
    tr(db, ap1, "IN_PROGRESS", actor=ME)                                                  # an agent never counts
    cat = bc.iam_catalog(conn=db)
    assert cat["transitions_by_person"] == {"human:j.cormenzana": 2, "human:a.gomez": 1, "human:m.ruiz": 1} and cat["transition_persons"] == ["human:a.gomez", "human:j.cormenzana", "human:m.ruiz"]
    bind(db, "iam_qa", "human:m.ruiz_qa", "human:m.ruiz", "HUMAN", active=False)               # a revoked binding no longer backs the transitions
    assert "human:m.ruiz" not in bc.iam_catalog(conn=db)["transition_persons"]


# --- the binding audit: the declared human identity (owner order 2026-10-29) ---------------------------------------------------------------------

def test_a_binding_must_name_a_canonical_human_declarer(db):
    mk_login(db, DEVOPS_LOGIN)
    with pytest.raises(psycopg.errors.UndefinedFunction):                                           # the old six-argument form no longer exists
        with db.transaction():
            db.execute("SELECT backlog.sp_bind_login(%s, %s, %s, %s, %s, %s)", (DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", True, REASON))
    for declared in (None, "", "agent:claude-sonnet-5-5", "system:release", "claude", "human:", "human: x", "human:%"):
        with pytest.raises(psycopg.errors.Error, match="canonical human"):
            with db.transaction():
                bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", declared=declared)
    event = bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", declared=OWNER)
    assert db.execute("SELECT declared_by FROM backlog.actor_login_event WHERE event_id = %s", (event,)).fetchone()[0] == OWNER


def test_once_bindings_exist_a_bound_login_declares_only_its_own_identity(db):
    for login in (OWNER_LOGIN, DEVOPS_LOGIN):
        mk_login(db, login, member_of=("backlog_human",))
    bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")                                     # the bootstrap, by the superuser
    as_login(db, OWNER_LOGIN, lambda c: bind(c, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", declared=OWNER))             # its own identity: accepted
    for declared in (DATA, DEVOPS, "human:m.ruiz_qa"):
        denied(db, OWNER_LOGIN, lambda c, d=declared: bind(c, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", active=False, declared=d), match="may not declare")
    assert db.execute("SELECT count(*) FROM backlog.actor_login_event").fetchone()[0] == 2        # the refused ones wrote nothing


def test_the_audit_view_flags_what_needs_attention(db):
    for login in (OWNER_LOGIN, DEVOPS_LOGIN):
        mk_login(db, login, member_of=("backlog_human",))
    first = bind(db, OWNER_LOGIN, OWNER, "human:j.cormenzana", "HUMAN")                              # superuser, declares the identity it binds, nobody bound yet
    second = as_login(db, OWNER_LOGIN, lambda c: bind(c, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", declared=OWNER))
    audit = {r["event_id"]: r for r in bc.binding_audit(conn=db)}
    assert set(audit[first]["flags"]) == {"SELF_BINDING", "DECLARER_NOT_BOUND_TO_SET_BY", "RECENT"}
    assert audit[first]["declared_by"] == OWNER and audit[first]["set_by"] == db.execute("SELECT session_user").fetchone()[0]
    assert audit[second]["flags"] == ["RECENT"] and audit[second]["set_by"] == OWNER_LOGIN and audit[second]["bound_actor"] == DEVOPS


def test_events_written_before_the_audit_stay_as_legacy_and_new_ones_must_name_a_declarer(db):
    mk_login(db, DEVOPS_LOGIN)
    legacy = bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN")
    # an earlier version of this step had no declared_by: simulate the table as it was, then upgrade it by re-applying the step
    db.execute("ALTER TABLE backlog.actor_login_event DROP COLUMN declared_by CASCADE")
    assert mig.existing(db, "iam")["views"] == []                                                      # the dependent views went with the column
    mig.migrate(db, "iam")
    assert db.execute("SELECT declared_by FROM backlog.actor_login_event WHERE event_id = %s", (legacy,)).fetchone()[0] is None      # not rewritten (append-only)
    audit = {r["event_id"]: r for r in bc.binding_audit(conn=db)}
    assert "LEGACY_NO_DECLARER" in audit[legacy]["flags"] and "DECLARER_NOT_BOUND_TO_SET_BY" not in audit[legacy]["flags"]
    with pytest.raises(psycopg.errors.CheckViolation):                                                  # the constraint is NOT VALID: it skips the old row but binds every new one
        with db.transaction():
            db.execute("INSERT INTO backlog.actor_login_event (login_name, actor, person, kind, active, reason) VALUES (%s, %s, 'human:a.gomez', 'HUMAN', true, 'a direct insert without a declarer')", (DEVOPS_LOGIN, DEVOPS))
    new = bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN", active=False)
    assert db.execute("SELECT declared_by FROM backlog.actor_login_event WHERE event_id = %s", (new,)).fetchone()[0] == OWNER
    cat = bc.iam_catalog(conn=db)
    assert cat["binding_store"] == {"declared_by_column": True, "declared_by_constraint": True, "audit_view": True}
    assert [b["login_name"] for b in cat["bindings"] if b["declared_by"] is None] == []             # the latest event of the login names a declarer


def test_the_audit_view_is_readable_by_the_reader_and_the_declarer_travels_in_the_binding_view(db):
    mk_login(db, DEVOPS_LOGIN)
    bind(db, DEVOPS_LOGIN, DEVOPS, "human:a.gomez", "HUMAN")
    assert as_login(db, "backlog_reader", lambda c: c.execute("SELECT count(*) FROM backlog.v_binding_audit").fetchone()[0]) == 1
    assert db.execute("SELECT declared_by FROM backlog.v_actor_login WHERE login_name = %s", (DEVOPS_LOGIN,)).fetchone()[0] == OWNER
