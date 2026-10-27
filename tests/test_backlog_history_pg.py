# tests/test_backlog_history_pg.py
# -*- coding: utf-8 -*-
"""Descriptive change history of Backlog v2 (FND-0338; db/pg_backlog/33_descriptive_history.sql, shared/backlog_client.update_attributes).

Owner decision of 2026-10-27: an append-only history, filled by a trigger, of the eight descriptive columns the writer keeps updating directly (exception E3). The owner
requires its COVERAGE (every column, every kind of change) and its EFFECTIVENESS (how many changes carry an actor) to be verified before the limitation is resolved:
this suite covers the first completely and builds the measure of the second. Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_history_pg.py
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
from test_backlog_transitions_pg import ME, _base, _mk, tr  # noqa: E402

HUMAN = "human:jacor"
# column -> (a new value that the CHECKs accept)
NEW = {"title": "a new title", "nature": "REQ", "schema_name": "silver", "object_type": "TAB", "object_name": "fund_metrics",
       "priority": "HIGH", "session_id": "FND_20261027_DEV_OTHER", "scenario_description": "a new description"}


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    mig.migrate(pg_conn, "history")
    return pg_conn


def events(conn, ap=None):
    sql = "SELECT field, old_value, new_value, actor, db_user FROM backlog.field_change_event"
    if ap:
        return conn.execute(sql + " WHERE action_point_id = %s ORDER BY event_id", (ap,)).fetchall()
    return conn.execute(sql + " ORDER BY event_id").fetchall()


def as_login(conn, role, fn):
    conn.execute(f"SET SESSION AUTHORIZATION {role}")
    try:
        return fn(conn)
    finally:
        try:
            conn.execute("RESET SESSION AUTHORIZATION")
        except psycopg.errors.InFailedSqlTransaction:
            pass


# --- coverage: every one of the eight columns --------------------------------------------------------------------------------------------------

def test_the_watched_columns_are_exactly_the_eight_of_exception_e3(db):
    assert tuple(sorted(NEW)) == tuple(sorted(bc.DESCRIPTIVE_FIELDS))                           # the client whitelist, the tests and the trigger agree
    trigdef = db.execute("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = 'field_changes' AND tgrelid = 'backlog.backlog'::regclass").fetchone()[0]
    for col in bc.DESCRIPTIVE_FIELDS:
        assert col in trigdef, col
    assert "AFTER UPDATE OF" in trigdef


@pytest.mark.parametrize("col", sorted(NEW))
def test_every_descriptive_column_is_recorded_with_old_and_new_value(db, col):
    ap = _mk(db, "OPEN")
    old = db.execute(f"SELECT {col} FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0]
    db.execute(f"UPDATE backlog.backlog SET {col} = %s WHERE action_point_id = %s", (NEW[col], ap))
    assert events(db, ap) == [(col, old, NEW[col], None, db.execute("SELECT session_user").fetchone()[0])]


def test_no_event_when_nothing_changes_or_when_the_column_is_not_descriptive(db):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.backlog SET title = title, priority = priority WHERE action_point_id = %s", (ap,))     # written, not changed
    db.execute("UPDATE backlog.backlog SET assignee = 'agent:x-y' WHERE action_point_id = %s", (ap,))                  # governed column: the V-00 trigger's business, not this one
    assert events(db, ap) == []


def test_several_columns_in_one_update_give_one_event_each_and_only_for_those_that_changed(db):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.backlog SET title = %s, priority = 'LOW', nature = %s, object_name = %s WHERE action_point_id = %s", ("two", "REQ", "x", ap))   # priority unchanged (LOW)
    assert sorted(e[0] for e in events(db, ap)) == ["nature", "object_name", "title"]


def test_nulls_are_recorded_as_null_not_as_text(db):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.backlog SET object_name = 'a' WHERE action_point_id = %s", (ap,))
    db.execute("UPDATE backlog.backlog SET object_name = NULL WHERE action_point_id = %s", (ap,))
    assert [(e[1], e[2]) for e in events(db, ap)] == [(None, "a"), ("a", None)]


def test_the_routines_do_not_write_descriptive_history(db):
    ap = _mk(db, "OPEN")
    tr(db, ap, "TODO")
    tr(db, ap, "IN_PROGRESS", assignee=ME)
    assert events(db, ap) == []                                                                 # status and assignee are the routines' business, recorded in status_event


# --- the actor: declared, validated, measured -------------------------------------------------------------------------------------------------

def test_the_declared_actor_is_recorded(db):
    ap = _mk(db, "OPEN")
    db.execute("SELECT set_config('backlog.actor', %s, true)", (ME,))
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))
    assert events(db, ap)[0][3] == ME


@pytest.mark.parametrize("claim", ["claude", "agent:claude", "robot:x", "  ", "human:"])
def test_a_claim_that_is_not_valid_is_not_recorded_as_an_actor(db, claim):
    ap = _mk(db, "OPEN")
    db.execute("SELECT set_config('backlog.actor', %s, true)", (claim,))
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))
    assert events(db, ap)[0][3] is None                                                         # the change is still recorded, with the database user, but unattributed


def test_a_human_claim_from_the_shared_connection_is_not_recorded_once_the_owner_role_exists(db):
    mig.migrate(db, "human")
    ap = _mk(db, "OPEN")

    def edit(c, actor):
        c.execute("SELECT set_config('backlog.actor', %s, true)", (actor,))
        c.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))

    as_login(db, "backlog_writer", lambda c: edit(c, HUMAN))
    ev = events(db, ap)
    assert ev[0][3] is None and ev[0][4] == "backlog_writer"                                    # the login was the shared one: the claim is dropped, the login is recorded
    ap2 = _mk(db, "OPEN")

    def edit_as_owner(c):
        c.execute("SELECT set_config('backlog.actor', %s, true)", (HUMAN,))
        c.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap2,))

    as_login(db, "backlog_human", edit_as_owner)
    assert events(db, ap2)[0][3] == HUMAN                                                       # through the owner-only login the claim is recorded


def test_the_attribution_view_measures_the_share_of_changes_with_an_actor(db):
    assert [tuple(r[:3]) for r in db.execute("SELECT field, n_changes, n_with_actor FROM backlog.v_field_change_attribution").fetchall()] == [("ALL", 0, 0)]    # empty history, one row
    a, b, c3 = _mk(db, "OPEN"), _mk(db, "OPEN"), _mk(db, "OPEN")
    db.execute("SELECT set_config('backlog.actor', %s, true)", (ME,))
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (a,))
    db.execute("UPDATE backlog.backlog SET title = 'x' WHERE action_point_id = %s", (b,))
    db.execute("SELECT set_config('backlog.actor', '', true)")
    db.execute("UPDATE backlog.backlog SET title = 'y' WHERE action_point_id = %s", (c3,))
    rows = {r["field"]: r for r in bc.field_change_attribution(conn=db)}
    assert (rows["ALL"]["n_changes"], rows["ALL"]["n_with_actor"], float(rows["ALL"]["pct_with_actor"])) == (3, 2, 66.7)
    assert (rows["title"]["n_changes"], rows["title"]["n_with_actor"]) == (2, 1) and (rows["priority"]["n_changes"], rows["priority"]["n_with_actor"]) == (1, 1)
    assert list(rows)[-1] == "ALL"                                                              # the summary row comes last


# --- the client function -----------------------------------------------------------------------------------------------------------------------

def test_update_attributes_records_the_actor_and_the_old_value(db):
    ap = _mk(db, "OPEN")
    assert bc.update_attributes(ap, actor=ME, conn=db, priority="HIGH", title="renamed") == 1
    ev = {e[0]: e for e in events(db, ap)}
    assert ev["priority"][1:4] == ("LOW", "HIGH", ME) and ev["title"][1] == f"title of {ap}" and ev["title"][3] == ME
    assert bc.update_attributes("FND-0000", actor=ME, conn=db, priority="HIGH") == 0           # an unknown ticket changes nothing


def test_the_declared_actor_covers_only_the_change_it_was_declared_for(db):
    """Found by the live rehearsal: on a borrowed transaction a later raw edit used to inherit the earlier declaration."""
    a, b = _mk(db, "OPEN"), _mk(db, "OPEN")
    bc.update_attributes(a, actor=ME, conn=db, priority="HIGH")
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (b,))                     # same transaction, no declaration of its own
    assert events(db, a)[0][3] == ME and events(db, b)[0][3] is None


def test_update_attributes_changes_only_the_whitelisted_columns_and_needs_an_actor(db, monkeypatch):
    ap = _mk(db, "OPEN")
    for bad in ({"status": "CLOSED"}, {"assignee": "agent:x-y"}, {"reporter": "agent:x-y"}, {"release_version": "R2026.10-X"}, {"priority": "HIGH", "closure_ts": "now()"}):
        with pytest.raises(ValueError, match="changes only"):
            bc.update_attributes(ap, actor=ME, conn=db, **bad)
    with pytest.raises(ValueError, match="no field"):
        bc.update_attributes(ap, actor=ME, conn=db)
    monkeypatch.delenv("FONDOS_ACTOR", raising=False)
    with pytest.raises(bc.ActorUnknown):
        bc.update_attributes(ap, conn=db, priority="HIGH")
    assert db.execute("SELECT status, priority FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone() == ("OPEN", "LOW")


def test_update_attributes_refuses_a_value_the_checks_refuse_and_changes_nothing(db):
    ap = _mk(db, "OPEN")
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            bc.update_attributes(ap, actor=ME, conn=db, priority="URGENT")
    assert events(db, ap) == []


def test_update_attributes_works_before_the_history_step_is_applied(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")                                                         # no history table
    ap = _mk(pg_conn, "OPEN")
    assert bc.update_attributes(ap, actor=ME, conn=pg_conn, priority="HIGH") == 1
    assert pg_conn.execute("SELECT priority FROM backlog.backlog WHERE action_point_id = %s", (ap,)).fetchone()[0] == "HIGH"


# --- the table: append-only, owned by the non-superuser, closed to the writer ---------------------------------------------------------------------

@pytest.mark.parametrize("sql", ["UPDATE backlog.field_change_event SET new_value = 'x'", "DELETE FROM backlog.field_change_event"])
def test_the_history_is_append_only(db, sql):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))
    with pytest.raises(psycopg.errors.Error, match="append-only"):
        with db.transaction():
            db.execute(sql)


def test_the_writer_can_read_the_history_but_cannot_write_it(db):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))
    assert as_login(db, "backlog_writer", lambda c: c.execute("SELECT count(*) FROM backlog.field_change_event").fetchone()[0]) == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            as_login(db, "backlog_writer", lambda c: c.execute("INSERT INTO backlog.field_change_event (action_point_id, field, db_user) VALUES (%s, 'title', 'x')", (ap,)))


def test_a_descriptive_update_by_the_writer_still_works_and_is_recorded_with_its_login(db):
    ap = _mk(db, "OPEN")
    as_login(db, "backlog_writer", lambda c: c.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,)))     # the trigger is a definer routine
    assert events(db, ap) == [("priority", "LOW", "HIGH", None, "backlog_writer")]


def test_it_composes_with_step_a2_the_writer_keeps_exactly_these_columns_and_they_are_recorded(db):
    mig.migrate(db, "revoke")
    ap = _mk(db, "OPEN")
    for col, val in NEW.items():
        as_login(db, "backlog_writer", lambda c, col=col, val=val: c.execute(f"UPDATE backlog.backlog SET {col} = %s WHERE action_point_id = %s", (val, ap)))
    assert sorted(e[0] for e in events(db, ap)) == sorted(NEW)                                  # all eight, through the privileges A-2 leaves
    with pytest.raises(psycopg.errors.InsufficientPrivilege):                                   # and nothing else
        with db.transaction():
            as_login(db, "backlog_writer", lambda c: c.execute("UPDATE backlog.backlog SET status = 'CLOSED' WHERE action_point_id = %s", (ap,)))


# --- the step ---------------------------------------------------------------------------------------------------------------------------------

def test_step_33_is_idempotent_and_reports_what_it_created(db):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))
    mig.migrate(db, "history")
    assert len(events(db, ap)) == 1                                                             # re-applying keeps the recorded history
    have = mig.existing(db, "history")
    assert have["tables"] == ["field_change_event"] and have["views"] == ["v_field_change_attribution"] and have["routines"] == ["trg_field_changes"] and have["missing_requires"] == []
    assert db.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'backlog.field_change_event'::regclass").fetchone()[0] == "backlog_owner"


def test_step_33_refuses_to_run_without_the_updated_routines(pg_conn):
    _base(pg_conn)
    with pytest.raises(RuntimeError, match="trg_append_only|fn_actor_authorized"):
        mig.migrate(pg_conn, "history")


def test_step_33_rollback_removes_it_and_leaves_every_other_record_untouched(db):
    ap = _mk(db, "OPEN")
    tr(db, ap, "TODO")
    db.execute("UPDATE backlog.backlog SET priority = 'HIGH' WHERE action_point_id = %s", (ap,))
    before = bc.history_fingerprint(conn=db)
    status_events = db.execute("SELECT count(*) FROM backlog.status_event").fetchone()[0]
    mig.migrate(db, "history", down=True)
    have = mig.existing(db, "history")
    assert have["tables"] == [] and have["views"] == [] and have["routines"] == []
    assert db.execute("SELECT count(*) FROM pg_trigger WHERE tgname = 'field_changes'").fetchone()[0] == 0
    assert bc.history_fingerprint(conn=db) == before and db.execute("SELECT count(*) FROM backlog.status_event").fetchone()[0] == status_events
    db.execute("UPDATE backlog.backlog SET priority = 'MEDIUM' WHERE action_point_id = %s", (ap,))                  # descriptive edits still work, just unrecorded
    mig.migrate(db, "history")                                                                  # and it can be applied again
    assert events(db, ap) == []                                                                 # the rolled-back history was not kept: the owner is warned in the down script
