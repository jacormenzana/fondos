# tests/test_backlog_sync_pg.py
# -*- coding: utf-8 -*-
"""Phase C slice 1 of Backlog v2 (FND-0334): db/pg_backlog/70_sync.sql applied by scripts/ops/migrate_backlog_v2.py --phase sync, the client functions
and scripts/audit/backlog_sync.py writing a plan. Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn); never the live backlog.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_sync_pg.py
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_sync as bs  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_groups_pg import as_proc, violations  # noqa: E402
from test_backlog_transitions_pg import FORCE_REASON, ME, _base, _mk, refused, tr  # noqa: E402

SYNC = "system:sync"


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in ("relations", "groups", "sync"):
        mig.migrate(pg_conn, phase)
    return pg_conn


def now(conn):
    return conn.execute("SELECT clock_timestamp()").fetchone()[0]


def commit(conn, ap, sha, minutes_from_now=0, source="SUBJECT"):
    return bc.record_ops_event("COMMIT", sha, ap, source, {"subject": "s"}, now(conn) + timedelta(minutes=minutes_from_now), actor=SYNC, conn=conn)


# ---------------------------------------------------------------- migration

def test_sync_needs_phase_d(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")
    with pytest.raises(RuntimeError, match="v_violations_d"):
        mig.migrate(pg_conn, "sync")


def test_sync_is_idempotent_and_joins_the_catalogue_view(db):
    mig.migrate(db, "sync")
    have = mig.existing(db, "sync")
    assert have["tables"] == sorted(mig.PHASES["sync"]["tables"]) and have["views"] == sorted(mig.PHASES["sync"]["views"])
    assert have["routines"] == sorted(mig.PHASES["sync"]["routines"])
    a = _mk(db)
    commit(db, a, "a" * 40, minutes_from_now=-600)                      # older than the ticket: V-RETRO
    assert violations(db, "V-RETRO", a) == [("V-RETRO", a)]
    mig.migrate(db, "groups")                                           # re-applying Phase D keeps the Phase C codes in the union
    assert violations(db, "V-RETRO", a) == [("V-RETRO", a)]


def test_rollback_keeps_history_and_removes_the_phase_c_codes(db):
    a = _mk(db)
    commit(db, a, "b" * 40, minutes_from_now=-600)
    mig.migrate(db, "sync", down=True)
    have = mig.existing(db, "sync")
    assert have["views"] == [] and have["routines"] == [] and have["tables"] == ["close_condition", "ops_event"]
    assert db.execute("SELECT count(*) FROM backlog.v_backlog_violations WHERE code = 'V-RETRO'").fetchone()[0] == 0
    mig.migrate(db, "sync")
    assert violations(db, "V-RETRO", a) == [("V-RETRO", a)]


# ---------------------------------------------------------------- ops_event

def test_recording_is_idempotent_and_append_only(db):
    a = _mk(db)
    assert commit(db, a, "c" * 40) is True
    assert commit(db, a, "c" * 40) is False
    assert bc.ops_event_keys(conn=db) == {("c" * 40, a)}
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.ops_event SET source = 'REFS'"))
    refused(db, "does not exist", lambda c: commit(c, "FND-0001", "d" * 40))
    refused(db, "not canonical", lambda c: c.execute("SELECT backlog.sp_record_ops_event('COMMIT', 'x', %s, 'REFS', '{}', now(), 'sync')", (a,)))
    with pytest.raises(ValueError):
        bc.record_ops_event("PUSH", "x", a, "REFS", {}, now(db), actor=SYNC, conn=db)


def test_retro_and_work_on_closed(db):
    early, on_time, closed = _mk(db), _mk(db), _mk(db)
    commit(db, early, "e1" * 20, minutes_from_now=-90)
    commit(db, early, "e2" * 20, minutes_from_now=+5)                   # only the FIRST commit counts for V-RETRO
    commit(db, on_time, "o1" * 20, minutes_from_now=+5)
    tr(db, closed, "CLOSED", solution_type="REJECTED", force=True, comment=FORCE_REASON)
    commit(db, closed, "w0" * 20, minutes_from_now=-1)                  # before the closure: not work-on-closed
    commit(db, closed, "w1" * 20, minutes_from_now=+60)
    commit(db, closed, "w2" * 20, minutes_from_now=+120)
    assert violations(db, "V-RETRO", early) == [("V-RETRO", early)] and not violations(db, "V-RETRO", on_time)
    assert violations(db, "V-WORK-ON-CLOSED") == [("V-WORK-ON-CLOSED", closed)] * 2
    tr(db, closed, "IN_PROGRESS", force=True, comment=FORCE_REASON)     # reopened: the later commits are legitimate work again
    assert violations(db, "V-WORK-ON-CLOSED") == []


# ---------------------------------------------------------------- close_condition

def test_close_conditions_add_refuse_duplicate_retire(db):
    a, b = _mk(db), _mk(db)
    assert bc.close_conditions(conn=db) == {}
    i1 = bc.add_close_condition(a, "pushed", ["abc123"], "the fix is on origin/master", actor=ME, conn=db)
    bc.add_close_condition(a, "flag", ["X_ENABLED", True], "the switch is on in production", actor=ME, conn=db)
    bc.add_close_condition(b, "manual", ["owner checks the dashboard"], "owner action no script sees", actor=ME, conn=db)
    assert bc.close_conditions(conn=db) == {a: [("pushed", "abc123"), ("flag", "X_ENABLED", True)], b: [("manual", "owner checks the dashboard")]}
    refused(db, "already has the condition", lambda c: bc.add_close_condition(a, "pushed", ["abc123"], "the same one again", actor=ME, conn=c))
    refused(db, "probe_kind_check", lambda c: bc.add_close_condition(a, "guess", [], "an unknown probe kind", actor=ME, conn=c))
    bc.retire_close_condition(i1, "the commit was superseded", actor=ME, conn=db)
    refused(db, "not an active condition", lambda c: bc.retire_close_condition(i1, "retired twice here", actor=ME, conn=c))
    assert bc.close_conditions(conn=db)[a] == [("flag", "X_ENABLED", True)]
    bc.add_close_condition(a, "pushed", ["abc123"], "added back after the retire", actor=ME, conn=db)   # allowed once retired


def test_close_conditions_is_none_before_the_store(pg_conn):
    _base(pg_conn)
    assert bc.close_conditions(conn=pg_conn) is None


# ---------------------------------------------------------------- status feed, privileges

def test_status_feed_lists_every_event_in_order(db):
    a = _mk(db)
    tr(db, a, "TODO")
    tr(db, a, "IN_PROGRESS")
    rows = db.execute("SELECT to_status, origin FROM backlog.v_status_feed WHERE action_point_id = %s ORDER BY event_id", (a,)).fetchall()
    assert rows == [("TODO", "LIVE"), ("IN_PROGRESS", "LIVE")]


def test_writer_executes_the_routines_and_reads_but_cannot_write_directly(db):
    for t in mig.PHASES["sync"]["tables"]:
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'INSERT')", (f"backlog.{t}",)).fetchone()[0] is False
    for v in mig.PHASES["sync"]["tables"] + mig.PHASES["sync"]["views"] + ("v_backlog_violations",):
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'SELECT')", (f"backlog.{v}",)).fetchone()[0] is True
    for sig in ("backlog.sp_record_ops_event(text,text,text,text,jsonb,timestamptz,text)", "backlog.sp_add_close_condition(text,text,jsonb,text,text)",
                "backlog.sp_retire_close_condition(bigint,text,text)"):
        assert db.execute("SELECT has_function_privilege('backlog_writer', %s, 'EXECUTE')", (sig,)).fetchone()[0] is True
        assert db.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (sig,)).fetchone()[0] is False


# ---------------------------------------------------------------- backlog_sync writing a plan

def test_sync_plan_records_and_forwards_through_the_routines(db):
    todo, blocked, mention = _mk(db, "TODO"), _mk(db), _mk(db)
    tr(db, blocked, "IN_PROGRESS")
    tr(db, blocked, "BLOCKED", blocker=todo)
    ts = now(db).isoformat()
    log = (f"{'f1' * 20}\x1f{ts}\x1fwork: {mention}\x1f{todo},{blocked}\x1e\n")
    statuses = {t["action_point_id"]: t["status"] for t in bc.list_tickets(conn=db)}
    p = bs.plan(bs.parse_log(log), statuses, bc.ops_event_keys(conn=db))
    done = bs.apply_plan(p, {"written": 0, "moved": 0}, conn=db)
    assert done == {"written": 3, "moved": 1}
    st = {t["action_point_id"]: t["status"] for t in bc.list_tickets(ids=[todo, blocked, mention], conn=db)}
    assert st == {todo: "IN_PROGRESS", blocked: "BLOCKED", mention: "OPEN"}
    ev = db.execute("SELECT actor, reason FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (todo,)).fetchone()
    assert ev[0] == SYNC and "f1f1f1f1f1f1" in ev[1]
    # a rerun records nothing and moves nothing, even after a person moves the ticket back
    tr(db, todo, "TODO")
    p2 = bs.plan(bs.parse_log(log), {t["action_point_id"]: t["status"] for t in bc.list_tickets(conn=db)}, bc.ops_event_keys(conn=db))
    assert p2["new"] == [] and p2["forward"] == [] and p2["already"] == 3
