# tests/test_backlog_v31_scenarios_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3.1 end to end on a throwaway PostgreSQL (the staging rehearsal the plan asked for, with the real objects): the rule engine decides, the runner's act() carries the decision out through the
real client, under the real scoped logins (SESSION AUTHORIZATION), against the real DDL of every phase. Also: the deploy watcher's facts recorder through the facts login, and the whole chain of phases
applied, rolled back in reverse and applied again. Run with python scripts/ops/run_pg_tests.py (skipped without FONDOS_TEST_PG_DSN)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_autopilot as run  # noqa: E402
import backlog_drift as bd  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
import owner_deploy as od  # noqa: E402
from shared import backlog_autopilot as ap_mod  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import ME, SESSION, _base, _mk, refused, row, status, tr  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "autonomy", "events")
AUTO = "system:autopilot"
SHA = "a" * 40
T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
RUN_SESSION = "FND_20261101_SYNC_E2E"


@pytest.fixture
def db(pg_conn, monkeypatch):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    monkeypatch.setenv("FONDOS_ACTOR", AUTO)                      # this process IS the autopilot, as in scripts/audit/backlog_autopilot.py
    return pg_conn


def as_role(conn, role):
    conn.execute(f"SET LOCAL SESSION AUTHORIZATION {role}")


def as_owner(conn):
    conn.execute("RESET SESSION AUTHORIZATION")


class Shim:
    """The client as the runner's act() sees it, with every call bound to the test connection."""
    BacklogUnavailable = bc.BacklogUnavailable
    AUTOPILOT_ACTOR = bc.AUTOPILOT_ACTOR

    def __init__(self, conn):
        self.conn = conn

    def auto_transition(self, *a, **kw):
        return bc.auto_transition(*a, conn=self.conn, **kw)

    def reopen(self, *a, **kw):
        return bc.reopen(*a, conn=self.conn, **kw)

    def append_log(self, ap, username, text):
        return bc.append_log(ap, username, text, conn=self.conn)


def tickets_of(conn, ids):
    """The runner's ticket facts, read the way main() reads them."""
    return {t["action_point_id"]: {"status": t["status"], "release_version": t["release_version"], "solution_type": t["solution_type"]} for t in bc.list_tickets(ids=ids, conn=conn)}


def carry_out(conn, facts):
    """decide + act in ON mode, as the runner does after it has gathered the facts."""
    return run.act(ap_mod.decide(facts), "ON", Shim(conn), {}, RUN_SESSION, out=lambda *_: None)


def record_merge(conn, ap, paths, ref=SHA, pr=17):
    as_role(conn, "backlog_autopilot")
    assert bc.record_ops_event("MERGE", ref, ap, "REFS", {"paths": list(paths), "pr": pr}, T0, conn=conn) is True
    return ap_mod.Merge(ref=ref, pr=pr, paths=tuple(paths), year=2026, month=11)


# ---------------------------------------------------------------- delivery -> closure -> a later commit reopens it

def test_a_merge_moves_then_closes_a_ticket_and_a_later_code_commit_reopens_it_with_the_autopilots_record_updated(db):
    ap = _mk(db, "IN_PROGRESS")
    merge = record_merge(db, ap, ["shared/x.py"])
    res = carry_out(db, ap_mod.Facts(tickets=tickets_of(db, [ap]), merges={ap: [merge]}))
    assert res["moved"] == 1
    as_owner(db)
    assert status(db, ap) == "READY_FOR_DEPLOY"

    as_role(db, "backlog_autopilot")
    res = carry_out(db, ap_mod.Facts(tickets=tickets_of(db, [ap]), merges={ap: [merge]}))
    assert res["closed"] == 1
    as_owner(db)
    state, closure_ts, solution, _dep, release, _assignee = row(db, ap)
    assert (state, solution, release) == ("CLOSED", "CODE_FIX", "R2026.11-PR17")
    assert db.execute("SELECT actor, forced FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (ap,)).fetchone() == (AUTO, False)

    cite = ap_mod.Citation(ref="b" * 40, ts=closure_ts + timedelta(hours=1), paths=("shared/x.py", "tests/test_x.py"))
    as_role(db, "backlog_autopilot")
    res = carry_out(db, ap_mod.Facts(tickets=tickets_of(db, [ap]), citations={ap: [cite]}, closed_at={ap: closure_ts}))
    assert res["reopened"] == 1
    as_owner(db)
    assert status(db, ap) == "IN_PROGRESS"
    assert db.execute("SELECT actor, cause, kind FROM backlog.reopen_event WHERE action_point_id = %s AND kind = 'REOPEN' ORDER BY reopen_id DESC LIMIT 1", (ap,)).fetchone() == (AUTO, "INCOMPLETE_FIX", "REOPEN")
    assert db.execute("SELECT forced FROM backlog.status_event WHERE action_point_id = %s ORDER BY event_id DESC LIMIT 1", (ap,)).fetchone()[0] is False      # an event, not a forced exception

    rec = bc.autopilot_track_record(conn=db)
    assert rec["reopened_closures"] == 1                                                    # the reopening of ITS closure is on its record
    assert ap_mod.mode_change("ON", 0, 0, rec["reopened_closures"]) is None and ap_mod.mode_change("ON", 0, 0, 2)[0] == "SHADOW"


def test_a_documentation_only_commit_after_the_closure_does_not_reopen_and_a_rejected_ticket_never_does(db):
    ap = _mk(db, "READY_FOR_DEPLOY")
    tr(db, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.11-PR1")
    closure_ts = row(db, ap)[1]
    docs = ap_mod.Citation(ref="c" * 40, ts=closure_ts + timedelta(hours=1), paths=("doc/reglas/NORMAS_BACKLOG.md", "README.md"))
    as_role(db, "backlog_autopilot")
    assert carry_out(db, ap_mod.Facts(tickets=tickets_of(db, [ap]), citations={ap: [docs]}, closed_at={ap: closure_ts}))["reopened"] == 0
    as_owner(db)
    assert status(db, ap) == "CLOSED"


# ---------------------------------------------------------------- a merge that changes db/** closes only after the DEPLOY fact, and only the facts login can make it

def test_a_db_merge_waits_for_the_deploy_fact_which_only_the_facts_login_can_record(db):
    ap = _mk(db, "IN_PROGRESS")
    merge = record_merge(db, ap, ["db/pg_backlog/99_x.sql", "shared/y.py"])
    carry_out(db, ap_mod.Facts(tickets=tickets_of(db, [ap]), merges={ap: [merge]}))                       # IN_PROGRESS -> READY_FOR_DEPLOY
    facts = lambda: ap_mod.Facts(tickets=tickets_of(db, [ap]), merges={ap: [merge]}, deploys={r for _x, r in bc.ops_event_keys("DEPLOY", conn=db)})   # noqa: E731
    assert [d.action for d in ap_mod.decide(facts())] == [ap_mod.WAIT] and "DEPLOY" in " ".join(ap_mod.decide(facts())[0].reasons)           # the engine waits...
    refused(db, "no DEPLOY event", lambda c: bc.auto_transition(ap, "CLOSED", evidence_ref=SHA, session_id=RUN_SESSION, comment="forcing the closure", solution_type="CODE_FIX",
                                                               release_version="R2026.11-PR17", conn=c))                                       # ...and the database would refuse anyway
    refused(db, "DEPLOY", lambda c: bc.record_ops_event("DEPLOY", "forged", ap, "REFS", {}, T0, conn=c))                                         # the autopilot cannot declare its own DDL live
    as_owner(db)
    as_role(db, "backlog_facts")
    assert bc.record_deploy_fact("deploy-bbbbbbbbbbbb", ap, {"head": "b" * 40}, T0, conn=db) is True
    assert bc.record_deploy_fact("deploy-bbbbbbbbbbbb", ap, {"head": "b" * 40}, T0, conn=db) is False      # idempotent
    as_owner(db)
    as_role(db, "backlog_autopilot")
    assert [d.action for d in ap_mod.decide(facts())] == [ap_mod.CLOSE]
    assert carry_out(db, facts())["closed"] == 1
    as_owner(db)
    assert status(db, ap) == "CLOSED"


def test_the_watchers_facts_recorder_writes_the_deploy_fact_of_every_ticket_the_ddl_commits_cite(db, monkeypatch):
    a, b = _mk(db, "IN_PROGRESS"), _mk(db, "IN_PROGRESS")
    log = f"feat(backlog): a\n\nRefs: {a}\nCo-Authored-By: t <t@t>\n\x1e" f"feat(backlog): b\n\nCloses: {b}\nCo-Authored-By: t <t@t>\n\x1e"

    class Vault:
        def get(self, name):
            return "postgresql://backlog_facts@127.0.0.1:1/gestion"

    as_role(db, "backlog_facts")
    line = od.record_facts("c" * 40, "d" * 40, ["events"], vault=Vault(), ddl_log=lambda x, y: log, environ={},
                           record=lambda ref, ap, payload, ts: bc.record_deploy_fact(ref, ap, payload, ts, conn=db))
    as_owner(db)
    assert "2 new for 2 ticket(s)" in line and "FAILED" not in line
    rows = db.execute("SELECT action_point_id, actor, ref FROM backlog.ops_event WHERE kind = 'DEPLOY' ORDER BY action_point_id").fetchall()
    assert rows == sorted([(a, "system:deploy", "deploy-" + "c" * 12), (b, "system:deploy", "deploy-" + "c" * 12)])
    assert {r for _x, r in bc.ops_event_keys("DEPLOY", conn=db)} >= {a, b}                      # what the autopilot reads as "DDL is live"


# ---------------------------------------------------------------- unblock and undefer, from the facts the runner itself reads

def test_unblock_and_undefer_are_decided_from_the_runners_own_reads_and_made_by_the_autopilot(db):
    blocker, blocked = _mk(db, "IN_PROGRESS"), _mk(db, "IN_PROGRESS")
    db.execute("SELECT backlog.sp_transition(p_ap_id => %s, p_to => 'BLOCKED', p_actor => %s, p_session_id => %s, p_comment => 'waiting for the blocker to close', p_blocker => %s)", (blocked, ME, SESSION, blocker))
    successor = _mk(db, "OPEN")
    db.execute("SELECT backlog.sp_transition(p_ap_id => %s, p_to => 'CLOSED', p_actor => %s, p_session_id => %s, p_comment => 'closed as not needed, for a test', p_solution_type => 'REJECTED', p_followup => %s)",
               (blocker, ME, SESSION, successor))
    deferred = _mk(db, "DEFERRED")
    db.execute("INSERT INTO backlog.wait_event (action_point_id, kind, condition_text, revisit_by, actor) VALUES (%s, 'DEFERRED', 'revisit later', current_date - 1, %s)", (deferred, ME))
    still_blocked = _mk(db, "IN_PROGRESS")
    other_open = _mk(db, "IN_PROGRESS")
    db.execute("SELECT backlog.sp_transition(p_ap_id => %s, p_to => 'BLOCKED', p_actor => %s, p_session_id => %s, p_comment => 'waiting for an open blocker', p_blocker => %s)", (still_blocked, ME, SESSION, other_open))

    ids = [blocker, blocked, deferred, still_blocked, other_open]
    tickets = tickets_of(db, ids)
    blockers, revisits = run.wait_facts(bc.list_wait_events(ids, conn=db), tickets)
    assert blockers == {blocked: blocker, still_blocked: other_open} and revisits == {deferred: date.today() - timedelta(days=1)}
    as_role(db, "backlog_autopilot")
    res = carry_out(db, ap_mod.Facts(tickets=tickets, blockers=blockers, revisits=revisits, today=date.today()))
    as_owner(db)
    assert (res["unblocked"], res["undeferred"], res["refused"]) == (1, 1, 0)
    assert (status(db, blocked), status(db, deferred), status(db, still_blocked)) == ("IN_PROGRESS", "TODO", "BLOCKED")        # the open blocker keeps its ticket blocked
    assert db.execute("SELECT count(*) FROM backlog.status_event WHERE actor = %s AND forced", (AUTO,)).fetchone()[0] == 0


# ---------------------------------------------------------------- the whole chain rolled back in reverse and applied again (what AUTO does on a failure)

def test_every_phase_above_the_foundation_rolls_back_in_reverse_order_and_applies_again_keeping_the_history(pg_conn):
    _base(pg_conn)
    order = list(bd.PHASES_IN_ORDER)
    for ph in order:
        mig.migrate(pg_conn, ph)
    above = [ph for ph in order if ph != "transitions"]                                  # `transitions` is the foundation: its rollback restores the pre-v2 base (next test)
    before = {ph: mig.existing(pg_conn, ph) for ph in order}
    ap = _mk(pg_conn, "READY_FOR_DEPLOY")
    tr(pg_conn, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.11-PR2")
    pg_conn.execute("SELECT backlog.sp_reopen(%s, 'COVERAGE_GAP', 'a reopening that must survive the rollback of the chain', %s, %s, 'IN_PROGRESS')", (ap, ME, SESSION))
    events_before = pg_conn.execute("SELECT count(*) FROM backlog.reopen_event").fetchone()[0]
    for ph in reversed(above):
        mig.migrate(pg_conn, ph, down=True)
    assert pg_conn.execute("SELECT count(*) FROM backlog.reopen_event").fetchone()[0] == events_before >= 1               # the history is kept by the rollback
    for ph in above:
        mig.migrate(pg_conn, ph)
    assert {ph: mig.existing(pg_conn, ph) for ph in order} == before
    assert pg_conn.execute("SELECT count(*) FROM backlog.reopen_event").fetchone()[0] == events_before


def test_the_rollback_of_the_foundation_phase_refuses_safely_while_later_history_tables_exist(pg_conn):
    """Found by the chain test: `transitions --down` drops objects (raw_update_event, trg_append_only()) that the views and the history tables of later phases, kept by their rollbacks, still depend on.
    The statement aborts as a whole, so nothing is half-done; the automatic rollback never reaches it (it undoes only the phases of its own run, newest first, and stops at the first failure)."""
    _base(pg_conn)
    for ph in bd.PHASES_IN_ORDER:
        mig.migrate(pg_conn, ph)
    before = {ph: mig.existing(pg_conn, ph) for ph in bd.PHASES_IN_ORDER}
    refused(pg_conn, "cannot drop .* because other objects depend on it", lambda c: mig.migrate(c, "transitions", down=True))
    assert {ph: mig.existing(pg_conn, ph) for ph in bd.PHASES_IN_ORDER} == before


# ---------------------------------------------------------------- the weekly digest reads the real objects, and degrades block by block when a phase is not applied

def test_digest_facts_counts_the_weeks_moves_reopenings_and_waits_from_the_real_objects(db):
    ap = _mk(db, "READY_FOR_DEPLOY")
    tr(db, ap, "CLOSED", solution_type="CODE_FIX", release_version="R2026.11-PR3")
    db.execute("SELECT backlog.sp_reopen(%s, 'COVERAGE_GAP', 'the digest must count this reopening by its cause', %s, %s, 'IN_PROGRESS')", (ap, ME, SESSION))
    f = bc.digest_facts(conn=db)
    assert f["reopens_by_cause"] == {"COVERAGE_GAP": 1}
    assert f["by_status"]["IN_PROGRESS"] >= 1 and f["moves_by_actor_kind"]["agent"]["n"] >= 2 and f["moves_by_actor_kind"]["agent"]["forced"] == 0     # the reopening is not a forced move
    assert (f["open_holds"], f["deploy_facts"], f["undecided_pairs"]) == (0, 0, 0) and f["idle_in_progress"] == 0


def test_digest_facts_marks_a_block_whose_phase_is_not_applied_as_unavailable_and_still_answers_the_rest(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")                                                       # A-1 and Phase B only: no reopen_event, no gate queue, no ops_event
    f = bc.digest_facts(conn=pg_conn)
    assert f["reopens_by_cause"] is None and f["undecided_pairs"] is None and f["deploy_facts"] is None
    assert f["by_status"] is not None and f["moves_by_actor_kind"] is not None             # what exists is still reported


# ---------------------------------------------------------------- the watcher's read of "is A-2 applied" follows the real revoke phase

def test_the_direct_write_privilege_is_reported_live_until_the_real_revoke_phase_is_applied(pg_conn):
    _base(pg_conn)
    mig.migrate(pg_conn, "relations")                                                       # carries the A-1 block (v_shadow_stage) the revoke phase requires
    assert bc.raw_update_privilege_live(conn=pg_conn) is True
    mig.migrate(pg_conn, "revoke")
    assert bc.raw_update_privilege_live(conn=pg_conn) is False
    mig.migrate(pg_conn, "revoke", down=True)
    assert bc.raw_update_privilege_live(conn=pg_conn) is True                               # and the rollback the watcher would run gives it back
