# tests/test_backlog_groups_pg.py
# -*- coding: utf-8 -*-
"""Phase D of Backlog v2 (FND-0335): db/pg_backlog/60_groups_violations.sql applied by scripts/ops/migrate_backlog_v2.py --phase groups.
Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn); never the live backlog.

Covers: prerequisites, idempotence and the rollback that keeps history; the one-time RELEASE_WITH -> TRAIN migration; the group routines and
v_group_status; every violation code of the view (and its negative case); lean metrics on LIVE events only; impact closure (directions, closed
tickets, cycles, the 5-level bound); privileges; the client's violation_summary.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_groups_pg.py
"""
from __future__ import annotations

import datetime as dt
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
from test_backlog_relations_pg import close, link, rfd  # noqa: E402
from test_backlog_transitions_pg import FORCE_REASON, ME, OWNER, _base, _mk, refused, tr  # noqa: E402

REASON = "a membership reason long enough"


@pytest.fixture
def db(pg_conn):
    """Steps A-1 and B as deployed (gate in AUDIT), then Phase D."""
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")
    mig.migrate(pg_conn, "groups")
    return pg_conn


def violations(conn, code=None, ap=None):
    sql, args = "SELECT code, action_point_id FROM backlog.v_backlog_violations WHERE true", []
    if code:
        sql, args = sql + " AND code = %s", args + [code]
    if ap:
        sql, args = sql + " AND action_point_id = %s", args + [ap]
    return conn.execute(sql + " ORDER BY 1, 2", args).fetchall()


def as_proc(conn, sql, args=()):
    """A setup write that must not itself count as a V-00 (the setting the routines use)."""
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    conn.execute(sql, args)
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")


def closed_ticket(conn, solution, minutes=600, nature="BUG"):
    ap = _mk(conn, "OPEN")
    as_proc(conn, "UPDATE backlog.BACKLOG SET status = 'CLOSED', solution_type = %s, nature = %s, creation_ts = now() - make_interval(mins => %s), "
                  "closure_ts = now() WHERE action_point_id = %s", (solution, nature, minutes, ap))
    return ap


# ---------------------------------------------------------------- migration

def test_groups_refuses_without_phase_b(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    with pytest.raises(RuntimeError, match="hold_event"):
        mig.migrate(pg_conn, "groups")


def test_groups_is_idempotent_and_complete(db):
    mig.migrate(db, "groups")
    have = mig.existing(db, "groups")
    assert have["tables"] == sorted(mig.PHASES["groups"]["tables"])
    assert have["views"] == sorted(mig.PHASES["groups"]["views"])
    assert have["routines"] == sorted(mig.PHASES["groups"]["routines"])
    assert have["missing_requires"] == []


def test_rollback_drops_views_and_routines_and_keeps_history(db):
    bc.create_group("theme-x", "THEME", "a theme for the rollback test", actor=ME, conn=db)
    mig.migrate(db, "groups", down=True)
    have = mig.existing(db, "groups")
    assert have["views"] == [] and have["routines"] == []
    assert have["tables"] == ["group_member", "group_tag"]
    mig.migrate(db, "groups")                                     # re-apply resumes with the same rows
    assert db.execute("SELECT name FROM backlog.v_group_status").fetchall() == [("THEME-X",)]


def test_release_with_links_become_one_train_per_component_once(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")
    a, b, c, d, e = (_mk(pg_conn) for _ in range(5))
    link(pg_conn, a, b, "RELEASE_WITH")
    link(pg_conn, c, b, "RELEASE_WITH")
    link(pg_conn, d, e, "RELEASE_WITH")
    link(pg_conn, a, d, "RELEASE_WITH", st="REJECTED")             # a rejected link joins nothing
    mig.migrate(pg_conn, "groups")
    got = pg_conn.execute("SELECT g.name, g.kind, array_agg(m.action_point_id ORDER BY m.action_point_id) FROM backlog.group_tag g "
                          "JOIN backlog.v_group_member m USING (group_id) GROUP BY g.name, g.kind ORDER BY 1").fetchall()
    assert got == [(f"TRAIN-{a}", "TRAIN", [a, b, c]), (f"TRAIN-{d}", "TRAIN", [d, e])]
    bc.group_member(f"TRAIN-{a}", c, "REMOVE", REASON, actor=ME, conn=pg_conn)
    mig.migrate(pg_conn, "groups")                                # the owner's removal survives a re-apply
    assert pg_conn.execute("SELECT count(*) FROM backlog.group_tag").fetchone()[0] == 2
    assert pg_conn.execute("SELECT count(*) FROM backlog.v_group_member WHERE action_point_id = %s", (c,)).fetchone()[0] == 0


# ---------------------------------------------------------------- groups

def test_group_lifecycle_and_status(db):
    a, b = _mk(db), _mk(db)
    gid = bc.create_group("decision-fx", "DECISION", "owner decision pending on the FX policy", actor=ME, conn=db)
    assert isinstance(gid, int)
    st = lambda: db.execute("SELECT group_status, members, closed_members, open_member_ids FROM backlog.v_group_status WHERE group_id = %s", (gid,)).fetchone()
    assert st() == ("EMPTY", 0, 0, [])
    bc.group_member("DECISION-FX", a, "ADD", REASON, actor=ME, conn=db)
    bc.group_member("decision-fx", b, "ADD", REASON, actor=ME, conn=db)
    assert st() == ("OPEN", 2, 0, [a, b])
    for ap in (a, b):
        tr(db, ap, "CLOSED", solution_type="REJECTED", force=True, comment=FORCE_REASON)
    assert st() == ("READY_TO_CLOSE", 2, 2, [])


def test_group_routines_refuse_what_changes_nothing_or_is_invalid(db):
    a = _mk(db)
    bc.create_group("theme-a", "THEME", "a theme for the refusal test", actor=ME, conn=db)
    refused(db, "already exists", lambda c: bc.create_group("THEME-A", "THEME", "the same name again", actor=ME, conn=c))
    refused(db, "group_tag_kind_check", lambda c: c.execute("SELECT backlog.sp_create_group('x-kind', 'CLUSTER', 'unknown kind here', %s)", (ME,)))
    refused(db, "not canonical", lambda c: c.execute("SELECT backlog.sp_create_group('x-actor', 'THEME', 'bad actor here', 'claude')"))
    refused(db, "not authorized", lambda c: c.execute("SELECT backlog.sp_create_group('x-generic', 'THEME', 'generic agent here', 'agent:claude')"))
    refused(db, "does not exist", lambda c: bc.group_member("no-such-group", a, "ADD", REASON, actor=ME, conn=c))
    refused(db, "does not exist", lambda c: bc.group_member("theme-a", "FND-0001", "ADD", REASON, actor=ME, conn=c))
    refused(db, "is not a member", lambda c: bc.group_member("theme-a", a, "REMOVE", REASON, actor=ME, conn=c))
    bc.group_member("theme-a", a, "ADD", REASON, actor=ME, conn=db)
    refused(db, "already a member", lambda c: bc.group_member("theme-a", a, "ADD", REASON, actor=ME, conn=c))
    refused(db, "append-only", lambda c: c.execute("UPDATE backlog.group_tag SET description = 'rewritten description'"))
    refused(db, "append-only", lambda c: c.execute("DELETE FROM backlog.group_member"))
    with pytest.raises(ValueError):
        bc.create_group("x", "CLUSTER", "unknown kind", actor=ME, conn=db)


def test_writer_executes_the_routines_and_reads_but_cannot_write_directly(db):
    for obj in ("group_tag", "group_member"):
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'INSERT')", (f"backlog.{obj}",)).fetchone()[0] is False
    for obj in mig.PHASES["groups"]["tables"] + mig.PHASES["groups"]["views"]:
        assert db.execute("SELECT has_table_privilege('backlog_writer', %s, 'SELECT')", (f"backlog.{obj}",)).fetchone()[0] is True
    for sig in ("backlog.sp_create_group(text,text,text,text)", "backlog.sp_group_member(text,text,text,text,text)"):
        assert db.execute("SELECT has_function_privilege('backlog_writer', %s, 'EXECUTE')", (sig,)).fetchone()[0] is True
        assert db.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (sig,)).fetchone()[0] is False


# ---------------------------------------------------------------- violations

def test_v00_and_forced(db):
    a = _mk(db)
    db.execute("UPDATE backlog.BACKLOG SET status = 'TODO' WHERE action_point_id = %s", (a,))          # a raw write: V-00
    assert violations(db, "V-00", a) == [("V-00", a)]
    b = _mk(db)
    tr(db, b, "CLOSED", solution_type="REJECTED", force=True, comment=FORCE_REASON)
    assert violations(db, "V-FORCED", b) == [("V-FORCED", b)]
    c = _mk(db)
    tr(db, c, "IN_PROGRESS")
    assert violations(db, None, c) == []


def test_fast_delivery_closure(db):
    fast, slow, rej = closed_ticket(db, "CODE_FIX", 30), closed_ticket(db, "CODE_FIX", 120), closed_ticket(db, "REJECTED", 5)
    data = closed_ticket(db, "DATA_FIX", 59)
    assert violations(db, "V-FAST") == sorted([("V-FAST", fast), ("V-FAST", data)])
    assert not violations(db, "V-FAST", slow) and not violations(db, "V-FAST", rej)


def test_blocked_and_deferred_need_their_structured_record(db):
    bare_blocked, bare_deferred = _mk(db, "BLOCKED"), _mk(db, "DEFERRED")
    assert violations(db, "V-BLOCKED-NO-BLOCKER") == [("V-BLOCKED-NO-BLOCKER", bare_blocked)]
    assert violations(db, "V-DEFERRED-NO-REVISIT") == [("V-DEFERRED-NO-REVISIT", bare_deferred)]
    good_b, good_d, prereq = _mk(db), _mk(db), _mk(db)
    tr(db, good_b, "IN_PROGRESS")
    tr(db, good_b, "BLOCKED", blocker=prereq)
    tr(db, good_d, "DEFERRED", revisit_by=dt.date.today() + dt.timedelta(days=30))
    assert not violations(db, None, good_b) and not violations(db, None, good_d)
    # step 0's annotation of an already-blocked ticket clears it
    db.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'structured blocker recorded later', %s)", (bare_blocked, ME, prereq))
    assert not violations(db, "V-BLOCKED-NO-BLOCKER")
    # leaving BLOCKED and coming back without a new blocker is caught: the old wait record belongs to the previous period
    tr(db, good_b, "IN_PROGRESS")
    as_proc(db, "UPDATE backlog.BACKLOG SET status = 'BLOCKED' WHERE action_point_id = %s", (good_b,))
    assert violations(db, "V-BLOCKED-NO-BLOCKER", good_b) == [("V-BLOCKED-NO-BLOCKER", good_b)]


def test_expired_revisit_is_listed(db):
    old, fresh = _mk(db), _mk(db)
    for ap in (old, fresh):
        tr(db, ap, "DEFERRED", revisit_by=dt.date.today() + dt.timedelta(days=1))
    # time passes: the routine refuses a past date, so the expiry is simulated by a later wait record dated yesterday
    db.execute("INSERT INTO backlog.wait_event (action_point_id, kind, condition_text, revisit_by, actor) VALUES (%s, 'DEFERRED', 'revisit date', %s, %s)",
               (old, dt.date.today() - dt.timedelta(days=1), ME))
    assert violations(db, "V-REVISIT-EXPIRED") == [("V-REVISIT-EXPIRED", old)]
    assert db.execute("SELECT status FROM backlog.BACKLOG WHERE action_point_id = %s", (old,)).fetchone()[0] == "DEFERRED"   # nothing moves


def test_workaround_needs_an_optimisation_follow_up(db):
    bare, linked_opt, linked_bug = closed_ticket(db, "WORKAROUND"), closed_ticket(db, "WORKAROUND"), closed_ticket(db, "WORKAROUND")
    opt, bug = _mk(db), _mk(db)
    as_proc(db, "UPDATE backlog.BACKLOG SET nature = 'OPT' WHERE action_point_id = %s", (opt,))
    link(db, opt, linked_opt, "SPLIT_FROM")
    link(db, linked_bug, bug, "RELATED")                                   # a link, but not to an OPT ticket
    assert violations(db, "V-WORKAROUND-NO-OPT") == sorted([("V-WORKAROUND-NO-OPT", bare), ("V-WORKAROUND-NO-OPT", linked_bug)])
    # the routine path: a WORKAROUND closure through sp_transition records its follow-up
    via = _mk(db)
    tr(db, via, "IN_PROGRESS")
    rfd(db, via)
    close(db, via, "WORKAROUND", followup=opt)
    assert not violations(db, "V-WORKAROUND-NO-OPT", via)


def test_duplicated_needs_its_link(db):
    bare, kept = closed_ticket(db, "DUPLICATED"), _mk(db)
    assert violations(db, "V-DUP-NO-LINK") == [("V-DUP-NO-LINK", bare)]
    link(db, bare, kept, "DUPLICATES")
    assert not violations(db, "V-DUP-NO-LINK")


def test_hold_open_past_the_queue_thresholds(db):
    young, old, older = _mk(db), _mk(db), _mk(db)
    for ap, hours in ((young, 1), (old, 50), (older, 100)):
        db.execute("INSERT INTO backlog.hold_event (kind, action_point_id, violation_code, reason, actor, event_ts) "
                   "VALUES ('RAISE', %s, 'V-HOOK-BYPASS', 'a hold raised by the test', %s, clock_timestamp() - make_interval(hours => %s))", (ap, ME, hours))
    rows = dict(db.execute("SELECT action_point_id, detail FROM backlog.v_backlog_violations WHERE code = 'V-HOLD-OPEN'").fetchall())
    assert set(rows) == {old, older}
    assert rows[old].startswith("ATENCION") and rows[older].startswith("ALERTA")


def test_violation_summary_counts_and_ranks(db, monkeypatch):
    for _ in range(3):
        closed_ticket(db, "CODE_FIX", 10)
    _mk(db, "BLOCKED")
    s = bc.violation_summary(conn=db)
    assert s["applied"] is True and s["total"] == 4
    assert s["top"][0] == {"code": "V-FAST", "n": 3} and s["top"][1] == {"code": "V-BLOCKED-NO-BLOCKER", "n": 1}


def test_violation_summary_before_the_phase_is_applied(pg_conn):
    _base(pg_conn)
    assert bc.violation_summary(conn=pg_conn) == {"applied": False, "total": None, "by_code": [], "top": []}


# ---------------------------------------------------------------- lean metrics

def test_lean_metrics_use_live_events_only(pg_conn):
    _base(pg_conn)
    old = _mk(pg_conn, "CLOSED")
    as_proc(pg_conn, "UPDATE backlog.BACKLOG SET solution_type = 'CODE_FIX', creation_ts = now() - interval '10 hours', closure_ts = now() WHERE action_point_id = %s", (old,))
    mid = _mk(pg_conn, "IN_PROGRESS")                          # in progress before A-1: its start time is unknown
    mig.migrate(pg_conn, "relations")                         # the backfill gives `old` and `mid` a BACKFILL event only
    mig.migrate(pg_conn, "groups")
    live = _mk(pg_conn)
    tr(pg_conn, live, "IN_PROGRESS")
    for ap in (live, mid):
        rfd(pg_conn, ap)
        close(pg_conn, ap)
    m = {r[0]: r[1:] for r in pg_conn.execute("SELECT action_point_id, lead_time_h, cycle_time_h, rfd_wait_h FROM backlog.v_backlog_lean_metrics "
                                              "WHERE action_point_id IN (%s, %s, %s)", (old, live, mid)).fetchall()}
    assert float(m[old][0]) == pytest.approx(10.0, abs=0.05) and m[old][1] is None and m[old][2] is None
    assert m[live][1] is not None and m[live][2] is not None and m[live][1] >= m[live][2] >= 0
    assert m[mid][1] is None and m[mid][2] is not None          # the BACKFILL IN_PROGRESS is not a start time


# ---------------------------------------------------------------- impact closure

def reach(conn, src):
    return conn.execute("SELECT reached_id, depth FROM backlog.v_impact_closure WHERE source_id = %s ORDER BY 1", (src,)).fetchall()


def test_impact_closure_directions_and_closed_tickets(db):
    a, b, c, d, e, f = (_mk(db) for _ in range(6))
    link(db, a, b, "IMPACTS")              # a change in a reaches b
    link(db, c, b, "DEPENDS_ON")           # c depends on b: a change in b reaches c
    link(db, c, d, "OVERLAPS")             # both ways
    link(db, e, a, "RELATED")              # carries no impact
    link(db, d, f, "OVERLAPS")
    tr(db, f, "CLOSED", solution_type="REJECTED", force=True, comment=FORCE_REASON)   # closed tickets are not listed
    assert reach(db, a) == [(b, 1), (c, 2), (d, 3)]
    assert reach(db, b) == [(c, 1), (d, 2)]
    assert reach(db, e) == []
    assert (b, 1) not in reach(db, c)      # a dependent does not reach its prerequisite


def test_impact_closure_stops_at_five_levels_and_on_cycles(db):
    chain = [_mk(db) for _ in range(7)]
    for x, y in zip(chain, chain[1:]):
        link(db, x, y, "IMPACTS")
    link(db, chain[3], chain[1], "IMPACTS")                              # a cycle 1 > 2 > 3 > 1 that does not shorten the chain
    got = dict(reach(db, chain[0]))
    assert got[chain[5]] == 5 and chain[6] not in got
    assert chain[1] not in dict(reach(db, chain[1]))                     # a cycle never makes a ticket reach itself
