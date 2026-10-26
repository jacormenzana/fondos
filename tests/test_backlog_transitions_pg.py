# tests/test_backlog_transitions_pg.py
# -*- coding: utf-8 -*-
"""Phase A of Backlog v2 (FND-0331): db/pg_backlog/30_transitions.sql (step A-1) and 31_revoke_raw_update.sql (step A-2), applied by
scripts/ops/migrate_backlog_v2.py. Hermetic: the throwaway container, inside a savepoint that is always rolled back (pg_conn).

The fixture reproduces production before A-1: schema, links store, actor alias, the LEGACY sp_close_release, and a `backlog_writer` role with the
privileges it has today. Covers the transition matrix and closure rules, the waiting states, reopening, the V-00 shadow trigger and its counter,
append-only audit tables, the non-superuser owner and hardened routines (owner decisions D1, D3), the rebuilt sp_close_release, the A-2 revoke and its
exceptions, and both rollbacks (history fingerprint identical throughout).

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_transitions_pg.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_links as links  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")
LEGACY_RELEASE = links.ddl_block(_ROOT / "db" / "pg_backlog" / "10_kpis_and_release.sql", "backlog_sp_close_release")
ME, OWNER, SYS = "agent:claude-opus-5-5", "human:jacor", "system:release"
SESSION = "FND_20261026_DEV_TEST"
COMMENT = "a progress note long enough for the log"
FORCE_REASON = "forced on purpose in a test, with a reason of well over forty characters"


def _base(pg_conn, writer: bool = True):
    pg_conn.execute(SCHEMA_SQL)
    links.migrate(pg_conn)
    mig.migrate(pg_conn, "alias")
    pg_conn.execute(LEGACY_RELEASE)
    if writer:                                           # the privileges backlog_writer holds in production today
        pg_conn.execute("CREATE ROLE backlog_writer NOLOGIN")
        pg_conn.execute("GRANT USAGE ON SCHEMA backlog TO backlog_writer")
        pg_conn.execute("GRANT SELECT, INSERT, UPDATE ON backlog.BACKLOG TO backlog_writer")
        pg_conn.execute("GRANT SELECT, INSERT ON backlog.BACKLOG_LOGS, backlog.backlog_link_event TO backlog_writer")
        pg_conn.execute("GRANT SELECT ON backlog.v_backlog_link, backlog.actor_alias TO backlog_writer")
        pg_conn.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA backlog TO backlog_writer")
    return pg_conn


@pytest.fixture
def pre(pg_conn):
    """Production state before step A-1."""
    return _base(pg_conn)


@pytest.fixture
def db(pg_conn):
    """Step A-1 applied."""
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    return pg_conn


def _mk(conn, status="OPEN", n=[9000]):
    """A ticket created by the owner connection WITHOUT counting as a V-00 (the setting is what the routines use)."""
    n[0] += 1
    ap = f"FND-{n[0]}"
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, session_id) "
                 "VALUES (%s, %s, 'FND', 'BUG', 'N/A', %s, 'LOW', %s)", (ap, f"title of {ap}", status, SESSION))
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")
    return ap


def tr(conn, ap, to, actor=ME, session=SESSION, comment=COMMENT, **kw):
    cols = dict(solution_type=None, release_version=None, blocker=None, condition=None, revisit_by=None, revisit_on_event=None,
                followup=None, assignee=None, force=False)
    cols.update(kw)
    return conn.execute(
        "SELECT backlog.sp_transition(p_ap_id => %s, p_to => %s, p_actor => %s, p_session_id => %s, p_comment => %s, p_solution_type => %s, "
        "p_release_version => %s, p_blocker => %s, p_condition => %s, p_revisit_by => %s, p_revisit_on_event => %s, p_followup => %s, "
        "p_assignee => %s, p_force => %s)",
        (ap, to, actor, session, comment, cols["solution_type"], cols["release_version"], cols["blocker"], cols["condition"],
         cols["revisit_by"], cols["revisit_on_event"], cols["followup"], cols["assignee"], cols["force"])).fetchone()[0]


def refused(conn, match, fn, *a, **kw):
    with pytest.raises(psycopg.errors.Error, match=match):
        with conn.transaction():
            fn(conn, *a, **kw)


def status(conn, ap):
    return conn.execute("SELECT status FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()[0]


def row(conn, ap):
    return conn.execute("SELECT status, closure_ts, solution_type, deployment_ts, release_version, assignee FROM backlog.BACKLOG "
                        "WHERE action_point_id = %s", (ap,)).fetchone()


def nlogs(conn, ap):
    return conn.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (ap,)).fetchone()[0]


def v00(conn):
    return conn.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0]


def open_(conn, **kw):
    a = dict(title="a new ticket", nature="BUG", object_type="N/A", object_name=None, priority="LOW", actor=ME, session=SESSION,
             description="description", first_log="first log of the ticket", schema_name=None, project="FND")
    a.update(kw)
    return conn.execute("SELECT backlog.sp_open_ticket(p_title => %s, p_nature => %s, p_object_type => %s, p_object_name => %s, p_priority => %s, "
                        "p_actor => %s, p_session_id => %s, p_description => %s, p_first_log => %s, p_schema_name => %s, p_project_code => %s)",
                        (a["title"], a["nature"], a["object_type"], a["object_name"], a["priority"], a["actor"], a["session"],
                         a["description"], a["first_log"], a["schema_name"], a["project"])).fetchone()[0]


# --- A-1: structure, ownership, hardening, backfill, history ---------------------------------------------------------------------------------

def test_a1_is_idempotent_complete_and_leaves_history_identical(pre):
    ap = _mk(pre)
    pre.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, 'claude', 'old log')", (ap,))
    before = bc.history_fingerprint(conn=pre)
    mig.migrate(pre, "transitions")
    mig.migrate(pre, "transitions")                                   # twice: nothing duplicated, nothing breaks
    assert bc.history_fingerprint(conn=pre) == before
    have = mig.existing(pre, "transitions")
    assert have["tables"] == ["raw_update_event", "status_event", "wait_event"] and have["views"] == ["v_shadow_stage"]
    assert have["missing_requires"] == [] and "sp_transition" in have["routines"] and "sp_open_ticket" in have["routines"]


def test_a1_refuses_without_its_prerequisites(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    with pytest.raises(RuntimeError, match="actor_alias"):
        mig.migrate(pg_conn, "transitions")


def test_owner_role_is_not_a_superuser_and_routines_are_hardened(db):
    r = db.execute("SELECT rolsuper, rolcanlogin, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles WHERE rolname = 'backlog_owner'").fetchone()
    assert r == (False, False, False, False, False)
    routines = db.execute("SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner), p.proconfig FROM pg_proc p "
                          "WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_open_ticket','sp_transition','sp_close_release','trg_governed_writes')").fetchall()
    assert {x[0] for x in routines} == {"sp_open_ticket", "sp_transition", "sp_close_release", "trg_governed_writes"}
    for name, secdef, owner, config in routines:
        assert secdef is True and owner == "backlog_owner", name
        assert config and any(c.replace(" ", "") == "search_path=pg_catalog,backlog" for c in config), (name, config)
    for t in ("status_event", "wait_event", "raw_update_event"):
        assert db.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = ('backlog.' || %s)::regclass", (t,)).fetchone()[0] == "backlog_owner"


def test_only_the_writer_can_execute_the_routines(db):
    db.execute("CREATE ROLE stranger NOLOGIN")
    sig = "backlog.sp_transition(text,text,text,text,text,text,text,text,text,date,text,text,text,boolean)"
    assert db.execute("SELECT has_function_privilege('backlog_writer', %s, 'EXECUTE')", (sig,)).fetchone()[0] is True
    assert db.execute("SELECT has_function_privilege('stranger', %s, 'EXECUTE')", (sig,)).fetchone()[0] is False
    assert db.execute("SELECT has_table_privilege('backlog_writer', 'backlog.status_event', 'INSERT')").fetchone()[0] is False
    assert db.execute("SELECT has_table_privilege('backlog_writer', 'backlog.status_event', 'SELECT')").fetchone()[0] is True


def test_a1_revokes_nothing_from_the_writer(db):
    assert db.execute("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'status', 'UPDATE')").fetchone()[0] is True
    assert db.execute("SELECT has_table_privilege('backlog_writer', 'backlog.backlog', 'INSERT')").fetchone()[0] is True


def test_backfill_gives_one_event_per_ticket_and_is_idempotent(pre):
    t1, t2 = _mk(pre, "OPEN"), _mk(pre, "CLOSED")
    mig.migrate(pre, "transitions")
    evs = pre.execute("SELECT action_point_id, from_status, to_status, actor, origin FROM backlog.status_event ORDER BY action_point_id").fetchall()
    assert evs == [(t1, None, "OPEN", "system:backfill", "BACKFILL"), (t2, None, "CLOSED", "system:backfill", "BACKFILL")]
    mig.migrate(pre, "transitions")
    assert pre.execute("SELECT count(*) FROM backlog.status_event").fetchone()[0] == 2


# --- sp_open_ticket -------------------------------------------------------------------------------------------------------------------------

def test_open_ticket_creates_ticket_log_and_event_with_a_canonical_reporter(db):
    ap = open_(db, title="  incident  ", nature="BUG")
    assert ap.startswith("FND-") and len(ap) == 8
    assert db.execute("SELECT status, reporter, session_id FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone() == ("OPEN", ME, SESSION)
    assert db.execute("SELECT username, comment FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (ap,)).fetchone() == (ME, "first log of the ticket")
    assert db.execute("SELECT from_status, to_status, actor, origin FROM backlog.status_event WHERE action_point_id = %s", (ap,)).fetchone() == (None, "OPEN", ME, "LIVE")
    assert open_(db) != ap                                                      # the sequence advances
    assert v00(db) == 0                                                         # opened through the routine: no V-00


@pytest.mark.parametrize("kw,match", [
    ({"actor": "claude"}, "not canonical"), ({"actor": "agent:"}, "not canonical"), ({"actor": None}, "not canonical"),
    ({"session": "FND_20261026_XXX_TEST"}, "session id"), ({"session": None}, "session id"), ({"session": "free text"}, "session id"),
    ({"title": "  "}, "title and first log"), ({"first_log": ""}, "title and first log"),
    ({"nature": "FEATURE"}, "check"), ({"priority": "URGENT"}, "check"), ({"object_type": "TABLE"}, "check"),
])
def test_open_ticket_refuses_bad_input(db, kw, match):
    refused(db, match, lambda c, **k: open_(c, **k), **kw)


# --- sp_transition: legality, closure, waiting states ----------------------------------------------------------------------------------------

LEGAL = [("OPEN", "TODO"), ("OPEN", "IN_PROGRESS"), ("OPEN", "DEFERRED"), ("TODO", "OPEN"), ("TODO", "IN_PROGRESS"), ("IN_PROGRESS", "TODO"),
         ("IN_PROGRESS", "BLOCKED"), ("IN_PROGRESS", "READY_FOR_DEPLOY"), ("BLOCKED", "IN_PROGRESS"), ("READY_FOR_DEPLOY", "IN_PROGRESS"),
         ("DEFERRED", "OPEN"), ("DEFERRED", "IN_PROGRESS")]
ILLEGAL = [("OPEN", "READY_FOR_DEPLOY"), ("OPEN", "BLOCKED"), ("TODO", "READY_FOR_DEPLOY"), ("BLOCKED", "READY_FOR_DEPLOY"),
           ("BLOCKED", "TODO"), ("READY_FOR_DEPLOY", "BLOCKED"), ("READY_FOR_DEPLOY", "DEFERRED"), ("DEFERRED", "READY_FOR_DEPLOY"),
           ("IN_PROGRESS", "OPEN")]


def _waiting_args(to):
    return {"BLOCKED": dict(condition="waiting for the vendor to answer"),
            "DEFERRED": dict(revisit_on_event="the next harvest")}.get(to, {})


@pytest.mark.parametrize("src,to", LEGAL)
def test_legal_moves_are_accepted_and_recorded(db, src, to):
    ap = _mk(db, src)
    ev = tr(db, ap, to, **_waiting_args(to))
    assert status(db, ap) == to
    assert db.execute("SELECT from_status, to_status, actor, forced, origin FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone() == (src, to, ME, False, "LIVE")
    assert db.execute("SELECT comment, username FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s", (ap,)).fetchone() == (COMMENT, ME)


@pytest.mark.parametrize("src,to", ILLEGAL)
def test_illegal_moves_are_refused_and_only_forced_with_a_reason(db, src, to):
    ap = _mk(db, src)
    refused(db, "not a legal move", tr, ap, to, **_waiting_args(to))
    assert status(db, ap) == src and nlogs(db, ap) == 0
    refused(db, "40\\+ characters", tr, ap, to, force=True, **_waiting_args(to))        # forced needs a real reason
    ev = tr(db, ap, to, force=True, comment=FORCE_REASON, **_waiting_args(to))
    assert status(db, ap) == to and db.execute("SELECT forced FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] is True


@pytest.mark.parametrize("kw,match", [
    ({"actor": "jacor"}, "not canonical"), ({"session": "x"}, "session id"), ({"comment": "short"}, "comment of 10"),
    ({"assignee": "claude"}, "assignee"), ({"to": "DONE"}, "unknown status"), ({"release_version": "v23"}, "release label"),
    ({"followup": "FND-0000"}, "follow-up"),
])
def test_transition_refuses_bad_input_without_touching_the_ticket(db, kw, match):
    ap = _mk(db, "OPEN")
    kw = dict(kw)
    to = kw.pop("to", "TODO")
    refused(db, match, tr, ap, to, **kw)
    assert status(db, ap) == "OPEN" and nlogs(db, ap) == 0


def test_an_unknown_ticket_and_a_no_op_are_refused(db):
    refused(db, "does not exist", tr, "FND-0000", "TODO")
    ap = _mk(db, "OPEN")
    refused(db, "changes nothing", tr, ap, "OPEN")


def test_delivery_closure_needs_ready_for_deploy_a_release_label_and_sets_the_closure_fields(db):
    ap = _mk(db, "IN_PROGRESS")
    refused(db, "comes from READY_FOR_DEPLOY", tr, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.10-TEST-01")
    tr(db, ap, "READY_FOR_DEPLOY")
    refused(db, "release label", tr, ap, "CLOSED", solution_type="CODE_FIX")                              # no label anywhere
    refused(db, "solution_type", tr, ap, "CLOSED")
    refused(db, "solution_type", tr, ap, "CLOSED", solution_type="FIXED")
    ev = tr(db, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.10-TEST-01")
    st, closed, sol, deployed, rel, _ = row(db, ap)
    assert (st, sol, rel) == ("CLOSED", "CODE_FIX", "R2026.10-TEST-01") and closed is not None and deployed is not None
    assert db.execute("SELECT detail->>'solution_type' FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] == "CODE_FIX"


def test_the_release_label_can_be_set_at_ready_for_deploy_and_reused_at_closing(db):
    ap = _mk(db, "IN_PROGRESS")
    tr(db, ap, "READY_FOR_DEPLOY", release_version="R2026.10-TEST-02")
    assert row(db, ap)[4] == "R2026.10-TEST-02"
    tr(db, ap, "CLOSED", solution_type="DATA_FIX")                                                           # label already on the ticket
    assert row(db, ap)[:3][0] == "CLOSED" and row(db, ap)[4] == "R2026.10-TEST-02"


def test_a_forced_delivery_closure_from_another_status_is_recorded_as_forced(db):
    ap = _mk(db, "IN_PROGRESS")
    ev = tr(db, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.10-TEST-03", force=True, comment=FORCE_REASON)
    assert status(db, ap) == "CLOSED" and db.execute("SELECT forced FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] is True


def test_rejected_cites_the_superseding_ticket_or_is_forced(db):
    ap, other = _mk(db, "OPEN"), _mk(db, "OPEN")
    refused(db, "superseded|supersedes", tr, ap, "CLOSED", solution_type="REJECTED")
    tr(db, ap, "CLOSED", solution_type="REJECTED", followup=other)
    st, closed, sol, deployed, rel, _ = row(db, ap)
    assert (st, sol, deployed, rel) == ("CLOSED", "REJECTED", None, None) and closed is not None          # never a deployment
    third = _mk(db, "OPEN")
    ev = tr(db, third, "CLOSED", solution_type="REJECTED", force=True, comment=FORCE_REASON)
    assert db.execute("SELECT forced FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] is True


def test_duplicated_needs_the_kept_ticket_and_records_the_link(db):
    ap, kept = _mk(db, "TODO"), _mk(db, "IN_PROGRESS")
    refused(db, "ticket that is kept", tr, ap, "CLOSED", solution_type="DUPLICATED")
    refused(db, "follow-up", tr, ap, "CLOSED", solution_type="DUPLICATED", followup=ap)
    tr(db, ap, "CLOSED", solution_type="DUPLICATED", followup=kept)
    assert db.execute("SELECT link_type, status, to_id FROM backlog.v_backlog_link WHERE from_id = %s", (ap,)).fetchone() == ("DUPLICATES", "CONFIRMED", kept)


def test_workaround_uses_its_followup_or_opens_an_opt_ticket(db):
    ap, mine = _mk(db, "READY_FOR_DEPLOY"), _mk(db, "READY_FOR_DEPLOY")
    follow = _mk(db, "OPEN")
    tr(db, ap, "CLOSED", solution_type="WORKAROUND", release_version="R2026.10-TEST-04", followup=follow)
    assert db.execute("SELECT count(*) FROM backlog.BACKLOG WHERE nature = 'OPT'").fetchone()[0] == 0           # an existing follow-up: nothing opened
    n_before = db.execute("SELECT count(*) FROM backlog.BACKLOG").fetchone()[0]
    ev = tr(db, mine, "CLOSED", solution_type="WORKAROUND", release_version="R2026.10-TEST-04")
    opt = db.execute("SELECT action_point_id, nature, priority, reporter, status FROM backlog.BACKLOG WHERE nature = 'OPT'").fetchone()
    assert opt[1:] == ("OPT", "LOW", ME, "OPEN") and db.execute("SELECT count(*) FROM backlog.BACKLOG").fetchone()[0] == n_before + 1
    assert db.execute("SELECT link_type, to_id, status FROM backlog.v_backlog_link WHERE from_id = %s", (opt[0],)).fetchone() == ("SPLIT_FROM", mine, "CONFIRMED")
    assert db.execute("SELECT detail->>'followup' FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] == opt[0]
    assert v00(db) == 0


def test_solution_type_only_applies_when_closing(db):
    ap = _mk(db, "OPEN")
    refused(db, "only applies when closing", tr, ap, "TODO", solution_type="CODE_FIX")


def test_blocked_needs_a_blocker_or_a_named_condition_and_guarantees_the_link(db):
    ap, dep = _mk(db, "IN_PROGRESS"), _mk(db, "OPEN")
    refused(db, "blocker ticket", tr, ap, "BLOCKED")
    refused(db, "blocker ticket", tr, ap, "BLOCKED", condition="short")
    refused(db, "does not exist", tr, ap, "BLOCKED", blocker="FND-0000")
    refused(db, "does not exist or is the ticket itself", tr, ap, "BLOCKED", blocker=ap)
    tr(db, ap, "BLOCKED", blocker=dep)
    assert db.execute("SELECT kind, blocker_ref, condition_text, actor FROM backlog.wait_event WHERE action_point_id = %s", (ap,)).fetchone() == ("BLOCKED", dep, None, ME)
    assert db.execute("SELECT link_type, status, to_id FROM backlog.v_backlog_link WHERE from_id = %s", (ap,)).fetchone() == ("DEPENDS_ON", "CONFIRMED", dep)
    tr(db, ap, "IN_PROGRESS")
    tr(db, ap, "BLOCKED", blocker=dep)                                                                        # the link already exists: not duplicated
    assert db.execute("SELECT count(*) FROM backlog.backlog_link_event WHERE from_id = %s", (ap,)).fetchone()[0] == 1
    other = _mk(db, "IN_PROGRESS")
    tr(db, other, "BLOCKED", condition="waiting for the vendor to answer")
    assert db.execute("SELECT blocker_ref, condition_text FROM backlog.wait_event WHERE action_point_id = %s", (other,)).fetchone() == (None, "waiting for the vendor to answer")


def test_deferred_needs_a_revisit_date_in_the_future_or_an_event(db):
    ap = _mk(db, "OPEN")
    refused(db, "revisit_by", tr, ap, "DEFERRED")
    refused(db, "revisit_by", tr, ap, "DEFERRED", revisit_on_event="no")
    refused(db, "future", tr, ap, "DEFERRED", revisit_by=dt.date.today())
    tr(db, ap, "DEFERRED", revisit_by=dt.date.today() + dt.timedelta(days=30))
    assert db.execute("SELECT kind, revisit_by, revisit_basis FROM backlog.wait_event WHERE action_point_id = %s", (ap,)).fetchone() == (
        "DEFERRED", dt.date.today() + dt.timedelta(days=30), "DECLARED")


def test_reopening_a_closed_ticket_needs_force_and_clears_the_closure_but_keeps_the_history(db):
    ap = _mk(db, "READY_FOR_DEPLOY")
    tr(db, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.10-TEST-05")
    refused(db, "reopening needs p_force", tr, ap, "IN_PROGRESS")
    refused(db, "reopens to", tr, ap, "BLOCKED", force=True, comment=FORCE_REASON, condition="waiting for the vendor to answer")
    ev = tr(db, ap, "IN_PROGRESS", force=True, comment=FORCE_REASON)
    assert row(db, ap)[:5] == ("IN_PROGRESS", None, None, None, None)
    d = db.execute("SELECT forced, detail->'before'->>'solution_type', detail->'before'->>'release_version' FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()
    assert d == (True, "CODE_FIX", "R2026.10-TEST-05")                                                        # what the closure said is in the event
    assert db.execute("SELECT count(*) FROM backlog.status_event WHERE action_point_id = %s", (ap,)).fetchone()[0] == 2     # the closure and the reopening (_mk is a raw ticket: no 'opened' event)


def test_same_status_moves_only_assign_or_label(db):
    ap = _mk(db, "IN_PROGRESS")
    ev = tr(db, ap, "IN_PROGRESS", assignee="agent:claude-sonnet-5", comment="reassigned to the other model")
    assert row(db, ap)[5] == "agent:claude-sonnet-5" and status(db, ap) == "IN_PROGRESS"
    assert db.execute("SELECT from_status, to_status FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone() == ("IN_PROGRESS", "IN_PROGRESS")
    rfd = _mk(db, "READY_FOR_DEPLOY")
    tr(db, rfd, "READY_FOR_DEPLOY", release_version="R2026.10-TEST-06", comment="label attached to the ready ticket")
    assert row(db, rfd)[4] == "R2026.10-TEST-06" and status(db, rfd) == "READY_FOR_DEPLOY"
    closed = _mk(db, "READY_FOR_DEPLOY")
    tr(db, closed, "CLOSED", solution_type="CODE_FIX", release_version="R2026.10-TEST-07")
    before = row(db, closed)
    tr(db, closed, "CLOSED", assignee=OWNER, comment="assigned after the closure for the record")
    after = row(db, closed)
    assert after[:5] == before[:5] and after[5] == OWNER                                                       # the closure fields are untouched


def test_assignment_and_status_in_one_call(db):
    ap = _mk(db, "OPEN")
    tr(db, ap, "IN_PROGRESS", assignee=ME)
    assert row(db, ap)[0::5] == ("IN_PROGRESS", ME)


# --- audit tables: append-only; shadow stage (V-00) ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE backlog.status_event SET reason = 'x'", "DELETE FROM backlog.status_event",
    "UPDATE backlog.wait_event SET actor = 'human:x'", "DELETE FROM backlog.wait_event",
    "UPDATE backlog.raw_update_event SET op = 'INSERT'", "DELETE FROM backlog.raw_update_event",
])
def test_the_audit_tables_are_append_only(db, sql):
    ap = _mk(db, "IN_PROGRESS")
    tr(db, ap, "BLOCKED", condition="waiting for the vendor to answer")
    db.execute("UPDATE backlog.BACKLOG SET status = 'OPEN' WHERE action_point_id = %s", (ap,))        # one V-00 so every table has a row
    refused(db, "append-only", lambda c: c.execute(sql))


def test_a_direct_write_to_a_governed_column_is_recorded_not_blocked(db):
    ap = _mk(db, "IN_PROGRESS")
    db.execute("UPDATE backlog.BACKLOG SET status = 'BLOCKED' WHERE action_point_id = %s", (ap,))       # the old habit
    assert status(db, ap) == "BLOCKED"                                                                   # NOT blocked
    ev = db.execute("SELECT op, old_row->>'status', new_row->>'status', db_user FROM backlog.raw_update_event").fetchall()
    assert ev == [("UPDATE", "IN_PROGRESS", "BLOCKED", db.execute("SELECT session_user").fetchone()[0])]
    db.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority) "
               "VALUES ('FND-9999', 't', 'FND', 'BUG', 'N/A', 'OPEN', 'LOW')")
    assert [e[0] for e in db.execute("SELECT op FROM backlog.raw_update_event ORDER BY event_id").fetchall()] == ["UPDATE", "INSERT"]


@pytest.mark.parametrize("col,val", [("closure_ts", "now()"), ("solution_type", "'CODE_FIX'"), ("deployment_ts", "now()"), ("release_version", "'x'"),
                                     ("reporter", "'someone'"), ("assignee", "'someone'")])
def test_every_governed_column_is_watched(db, col, val):
    ap = _mk(db, "OPEN")
    db.execute(f"UPDATE backlog.BACKLOG SET {col} = {val} WHERE action_point_id = %s", (ap,))
    assert v00(db) == 1


def test_descriptive_columns_and_unchanged_governed_values_are_not_a_v00(db):
    ap = _mk(db, "OPEN")
    db.execute("UPDATE backlog.BACKLOG SET title = 'new title', priority = 'HIGH', scenario_description = 'd', nature = 'REQ' WHERE action_point_id = %s", (ap,))
    db.execute("UPDATE backlog.BACKLOG SET status = 'OPEN' WHERE action_point_id = %s", (ap,))          # same value written: nothing changed
    assert v00(db) == 0


def test_a_raw_write_after_a_routine_call_in_the_same_transaction_is_still_recorded(db):
    """The routine marker is scoped to the routine: it must not stay on for the rest of the transaction (found by this suite in the first draft)."""
    ap = open_(db)
    tr(db, ap, "IN_PROGRESS")
    assert db.execute("SELECT current_setting('backlog.via_proc', true)").fetchone()[0] in (None, "", "off")
    db.execute("UPDATE backlog.BACKLOG SET status = 'BLOCKED' WHERE action_point_id = %s", (ap,))
    assert v00(db) == 1
    w, f = _mk(db, "READY_FOR_DEPLOY"), _mk(db, "READY_FOR_DEPLOY")                                         # nested: sp_transition opens an OPT ticket inside
    tr(db, w, "CLOSED", solution_type="WORKAROUND", release_version="R2026.10-TEST-09")
    assert v00(db) == 1                                                                                    # the nested call raised no false V-00
    del f


def test_the_routines_never_produce_a_v00(db):
    ap = open_(db)
    tr(db, ap, "IN_PROGRESS")
    tr(db, ap, "READY_FOR_DEPLOY", release_version="R2026.10-TEST-08")
    tr(db, ap, "CLOSED", solution_type="CODE_FIX")
    tr(db, ap, "IN_PROGRESS", force=True, comment=FORCE_REASON)
    assert v00(db) == 0


def test_shadow_stage_counts_non_system_transitions_since_the_last_v00(db):
    def stage():
        return db.execute("SELECT live_non_system_since_last_v00, v00_total, last_v00_ts IS NOT NULL FROM backlog.v_shadow_stage").fetchone()
    assert stage() == (0, 0, False)
    a, b = _mk(db, "OPEN"), _mk(db, "OPEN")
    tr(db, a, "TODO"); tr(db, a, "IN_PROGRESS"); tr(db, b, "TODO")                                       # 3 real changes by an agent
    tr(db, b, "IN_PROGRESS", actor="system:gate")                                                         # a system actor does not count
    tr(db, b, "IN_PROGRESS", assignee=OWNER, comment="assignment only, same status, does not count")     # not a change of status
    open_(db)                                                                                             # opening is not a transition
    assert stage() == (3, 0, False)
    db.execute("SELECT pg_sleep(0.01)")
    db.execute("UPDATE backlog.BACKLOG SET status = 'BLOCKED' WHERE action_point_id = %s", (a,))          # a direct write resets the counter
    assert stage() == (0, 1, True)
    tr(db, a, "IN_PROGRESS", comment="back to work after the direct write")
    assert stage()[0] == 1


def test_the_a2_gate_is_a_pure_function_of_the_stage():
    ok, msg = mig.revoke_gate({"live_non_system_since_last_v00": 49, "v00_total": 3, "last_v00_ts": None})
    assert ok is False and "49 of 50" in msg
    assert mig.revoke_gate({"live_non_system_since_last_v00": 50, "v00_total": 3, "last_v00_ts": None})[0] is True


# --- sp_close_release rebuilt ---------------------------------------------------------------------------------------------------------------------

def test_close_release_dry_run_changes_nothing_and_closing_keeps_its_log_text(db):
    a, b = _mk(db, "IN_PROGRESS"), _mk(db, "IN_PROGRESS")
    tr(db, a, "READY_FOR_DEPLOY", release_version="R2026.10-REL-01")
    tr(db, b, "READY_FOR_DEPLOY", release_version="R2026.10-REL-01")
    other = _mk(db, "READY_FOR_DEPLOY")
    tr(db, other, "READY_FOR_DEPLOY", release_version="R2026.10-REL-02", comment="another release, untouched by the first")
    snap = [row(db, x) for x in (a, b, other)]
    db.execute("CALL backlog.sp_close_release('R2026.10-REL-01', 'human:jacor', true)")
    assert [row(db, x) for x in (a, b, other)] == snap
    db.execute("CALL backlog.sp_close_release('R2026.10-REL-01')")                                         # default user SYSTEM_RELEASE
    for x in (a, b):
        st, closed, sol, deployed, rel, _ = row(db, x)
        assert (st, sol, rel) == ("CLOSED", "CODE_FIX", "R2026.10-REL-01") and closed is not None and deployed is not None
        assert db.execute("SELECT comment, username FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s ORDER BY log_id DESC LIMIT 1", (x,)).fetchone() == (
            "Closed by sp_close_release('R2026.10-REL-01') -- release deployed", "system:release")
        assert db.execute("SELECT actor, from_status, to_status FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (x,)).fetchone() == (
            "system:release", "READY_FOR_DEPLOY", "CLOSED")
    assert status(db, other) == "READY_FOR_DEPLOY"
    assert v00(db) == 0


def test_close_release_resolves_a_raw_login_through_the_alias_and_refuses_an_unknown_one(db):
    a = _mk(db, "READY_FOR_DEPLOY")
    tr(db, a, "READY_FOR_DEPLOY", release_version="R2026.10-REL-03", comment="label attached to the ready ticket")
    refused(db, "no canonical identity", lambda c: c.execute("CALL backlog.sp_close_release('R2026.10-REL-03', 'stranger')"))
    assert status(db, a) == "READY_FOR_DEPLOY"
    db.execute("CALL backlog.sp_close_release('R2026.10-REL-03', 'jacor')")
    assert db.execute("SELECT actor FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (a,)).fetchone()[0] == "human:jacor"
    refused(db, "must not be empty", lambda c: c.execute("CALL backlog.sp_close_release('')"))


def test_the_writer_role_can_use_every_routine_through_execute_only(db):
    db.execute("SET ROLE backlog_writer")
    try:
        ap = open_(db)
        tr(db, ap, "IN_PROGRESS")
        tr(db, ap, "READY_FOR_DEPLOY", release_version="R2026.10-REL-04")
        db.execute("CALL backlog.sp_close_release('R2026.10-REL-04', 'agent:claude-opus-5-5')")
        assert status(db, ap) == "CLOSED"
        refused(db, "permission denied", lambda c: c.execute("INSERT INTO backlog.status_event (action_point_id, to_status, actor) VALUES (%s, 'OPEN', 'system:x')", (ap,)))
    finally:
        db.execute("RESET ROLE")


# --- A-2: the revoke and its enumerated exceptions -------------------------------------------------------------------------------------------------

@pytest.fixture
def db2(db):
    mig.migrate(db, "revoke")
    return db


def as_writer(conn, fn, *a, **kw):
    conn.execute("SET ROLE backlog_writer")
    try:
        return fn(conn, *a, **kw)
    finally:
        try:
            conn.execute("RESET ROLE")
        except psycopg.errors.InFailedSqlTransaction:        # the statement failed inside a savepoint: its rollback restores the role
            pass


def test_a2_closes_the_governed_columns_and_creation_to_the_writer(db2):
    ap = _mk(db2, "IN_PROGRESS")
    for sql in ("UPDATE backlog.BACKLOG SET status = 'CLOSED' WHERE action_point_id = %s",
                "UPDATE backlog.BACKLOG SET release_version = 'x' WHERE action_point_id = %s",
                "UPDATE backlog.BACKLOG SET assignee = 'human:x' WHERE action_point_id = %s",
                "UPDATE backlog.BACKLOG SET reporter = 'human:x' WHERE action_point_id = %s",
                "UPDATE backlog.BACKLOG SET creation_ts = now() WHERE action_point_id = %s",
                "UPDATE backlog.BACKLOG SET action_point_id = 'FND-1' WHERE action_point_id = %s"):
        refused(db2, "permission denied", lambda c, s=sql: as_writer(c, lambda cc: cc.execute(s, (ap,))))
    refused(db2, "permission denied", lambda c: as_writer(c, lambda cc: cc.execute(
        "INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, priority) VALUES ('FND-9998', 't', 'FND', 'BUG', 'N/A', 'LOW')")))
    assert status(db2, ap) == "IN_PROGRESS"


def test_a2_keeps_exactly_the_enumerated_exceptions(db2):
    ap = _mk(db2, "IN_PROGRESS")
    as_writer(db2, lambda c: c.execute("UPDATE backlog.BACKLOG SET title = 't2', nature = 'REQ', priority = 'HIGH', schema_name = 's', object_type = 'TAB', "
                                      "object_name = 'o', session_id = %s, scenario_description = 'd' WHERE action_point_id = %s", (SESSION, ap)))   # E3
    as_writer(db2, lambda c: c.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, 'agent:claude-opus-5-5', 'note')", (ap,)))  # E1
    other = _mk(db2, "OPEN")
    as_writer(db2, lambda c: c.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
                                      "VALUES (%s, %s, 'RELATED', 'PROPOSED', 'e', 'jacor')", (ap, other)))                                             # E2, a raw name with an alias
    assert v00(db2) == 0
    t = as_writer(db2, lambda c: open_(c))                                                                       # the routines still work
    as_writer(db2, lambda c: tr(c, t, "IN_PROGRESS"))
    assert status(db2, t) == "IN_PROGRESS"


def test_a2_checks_the_author_of_logs_and_link_events(db2):
    ap, other = _mk(db2, "OPEN"), _mk(db2, "OPEN")
    for who in ("stranger", "claude-someone"):
        refused(db2, "not a known author", lambda c, w=who: c.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, 'x')", (ap, w)))
        refused(db2, "not a known author", lambda c, w=who: c.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
                                                                     "VALUES (%s, %s, 'RELATED', 'PROPOSED', 'e', %s)", (ap, other, w)))
    for who in ("agent:claude-opus-5-5", "claude", "SYSTEM_RELEASE"):                                              # canonical, or aliased raw names
        db2.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, %s, 'x')", (ap, who))


def test_a2_is_idempotent_and_its_rollback_restores_the_privileges(db2):
    mig.migrate(db2, "revoke")
    mig.migrate(db2, "revoke", down=True)
    assert db2.execute("SELECT has_column_privilege('backlog_writer', 'backlog.backlog', 'status', 'UPDATE')").fetchone()[0] is True
    assert db2.execute("SELECT has_table_privilege('backlog_writer', 'backlog.backlog', 'INSERT')").fetchone()[0] is True
    assert db2.execute("SELECT count(*) FROM pg_trigger WHERE tgname IN ('logs_actor', 'link_event_actor')").fetchone()[0] == 0
    db2.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, 'stranger', 'x')", (_mk(db2, "OPEN"),))   # unchecked again


# --- rollbacks: history identical, legacy restored, refused while A-2 is applied -------------------------------------------------------------------

def test_a1_rollback_restores_the_legacy_release_procedure_and_removes_the_role(db):
    ap = _mk(db, "IN_PROGRESS")
    tr(db, ap, "READY_FOR_DEPLOY", release_version="R2026.10-REL-05")
    before = bc.history_fingerprint(conn=db)
    mig.migrate(db, "transitions", down=True)
    assert mig.existing(db, "transitions")["tables"] == [] and mig.existing(db, "transitions")["views"] == []
    assert db.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'backlog_owner'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM pg_proc WHERE proname IN ('sp_transition', 'sp_open_ticket', 'fn_actor_ok', 'trg_governed_writes')").fetchone()[0] == 0
    legacy = db.execute("SELECT prosecdef, pg_get_userbyid(proowner) = current_user FROM pg_proc WHERE proname = 'sp_close_release'").fetchone()
    assert legacy == (False, True)                                                                                       # the legacy procedure, owned by the DDL role
    assert bc.history_fingerprint(conn=db) == before                                                                     # tickets, logs, link events untouched
    db.execute("CALL backlog.sp_close_release('R2026.10-REL-05', 'jacor')")                                                # and it works as before
    assert row(db, ap)[:3][0] == "CLOSED"
    mig.migrate(db, "transitions")                                                                                       # A-1 can be applied again
    assert mig.existing(db, "transitions")["tables"] == ["raw_update_event", "status_event", "wait_event"]


def test_a1_rollback_is_refused_while_a2_is_applied(db2):
    with pytest.raises(psycopg.errors.Error, match="step A-2 is still applied"):
        with db2.transaction():
            mig.migrate(db2, "transitions", down=True)
    mig.migrate(db2, "revoke", down=True)
    mig.migrate(db2, "transitions", down=True)                                                                           # in the right order it works


def test_the_history_is_identical_across_a1_a2_and_both_rollbacks(pre):
    ap = _mk(pre, "OPEN")
    pre.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, 'claude', 'old note')", (ap,))
    pre.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'PROPOSED', 'e', 'jacor')", (ap, _mk(pre, "OPEN")))
    base_fp = bc.history_fingerprint(conn=pre)
    for step in (("transitions", False), ("revoke", False), ("revoke", True), ("transitions", True)):
        mig.migrate(pre, step[0], down=step[1])
        assert bc.history_fingerprint(max_log_id=base_fp["max_log_id"], max_event_id=base_fp["max_event_id"], conn=pre) == base_fp, step
