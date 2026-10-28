# tests/test_backlog_relations_pg.py
# -*- coding: utf-8 -*-
"""Phase B of Backlog v2 (FND-0333): db/pg_backlog/40_relations_v2.sql applied by scripts/ops/migrate_backlog_v2.py --phase relations, and the gate hooks
that step A-1 (30_transitions.sql) carries in sp_transition. Hermetic: the throwaway container inside a rolled-back savepoint (pg_conn).

Covers: the delivery gate (prerequisite, hold, review flag; scope; the error lists the blockers), link semantics (IMPACTS, SUPERSEDES, OVERLAPS with
`absorbs`, flag raise / acknowledge), DEPENDS_ON cycle detection, the exception path (p_force with a reason, the counted override, the switch that only a
human actor changes), the structured wait annotation, privileges, idempotence, and the rollback.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_relations_pg.py
"""
from __future__ import annotations

import json
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
from test_backlog_transitions_pg import (FORCE_REASON, ME, OWNER, SESSION, _base, _mk, open_, refused, status, tr)  # noqa: E402

REL = "R2026.10-RELTEST"


@pytest.fixture
def db(pg_conn):
    """Step A-1 only (the gate hooks are the stubs)."""
    _base(pg_conn)
    mig.migrate(pg_conn, "transitions")
    return pg_conn


@pytest.fixture
def dba(db):
    """Step A-1 plus Phase B exactly as deployed: the gate is in AUDIT mode (the seed row of the deployment)."""
    mig.migrate(db, "relations")
    return db


def enforce(conn):
    """Put the gate in ENFORCE as the owner connection would after a clear audit window (a direct row: the 48-hour guard belongs to sp_set_gate)."""
    conn.execute("INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'ENFORCE', 'human:jacor', 'enforced by a test of the gate itself')")


@pytest.fixture
def dbb(dba):
    """Phase B with the gate ENFORCED: what the gate tests below exercise."""
    enforce(dba)
    return dba


def link(conn, a, b, typ, st="CONFIRMED", absorbs=None, origin=None):
    """One link event; the column origin exists only once Phase B is applied, so it is named only when a test asks for it."""
    cols, vals = ("from_id, to_id, link_type, status, evidence, absorbs, username", [a, b, typ, st, "test", absorbs, "pytest"])
    if origin:
        cols, vals = cols + ", origin", vals + [origin]
    conn.execute(f"INSERT INTO backlog.backlog_link_event ({cols}) VALUES ({', '.join(['%s'] * len(vals))})", vals)


def rfd(conn, ap, **kw):
    return tr(conn, ap, "READY_FOR_DEPLOY", release_version=REL, **kw)


def close(conn, ap, sol="CODE_FIX", **kw):
    return tr(conn, ap, "CLOSED", solution_type=sol, **kw)


def blockers(conn, ap, to="READY_FOR_DEPLOY", delivery=False):
    return conn.execute("SELECT backlog.fn_gate_blockers(%s, %s, %s)", (ap, to, delivery)).fetchone()[0]


def open_flags(conn, ap=None):
    return conn.execute("SELECT flag_id, action_point_id, source_id, link_type FROM backlog.v_open_review_flag WHERE %s::text IS NULL OR action_point_id = %s ORDER BY 1", (ap, ap)).fetchall()


# --- the phase itself --------------------------------------------------------------------------------------------------------------------

def test_phase_b_is_idempotent_complete_and_leaves_history_identical(db):
    _mk(db)
    before = bc.history_fingerprint(conn=db)
    mig.migrate(db, "relations")
    mig.migrate(db, "relations")
    assert bc.history_fingerprint(conn=db) == before
    have = mig.existing(db, "relations")
    assert have["tables"] == ["gate_audit_event", "gate_switch", "hold_event", "review_flag"] and have["missing_requires"] == []
    assert set(mig.PHASES["relations"]["routines"]) <= set(have["routines"])
    assert db.execute("SELECT count(*) FROM backlog.backlog_link_event WHERE origin <> 'REVIEW'").fetchone()[0] == 0
    owners = {r[0] for r in db.execute("SELECT DISTINCT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace = 'backlog'::regnamespace AND proname = ANY(%s)", (list(mig.PHASES["relations"]["routines"]),)).fetchall()}
    assert owners == {"backlog_owner"}
    assert bc.gate_mode(conn=db) == "AUDIT"                                                              # the deployment seeds AUDIT, never ENFORCE


def test_one_command_applies_a1_and_phase_b_from_a_database_that_had_neither(pg_conn):
    """Owner mitigation 1 (2026-10-28): no separate 're-apply A-1' step. The phase carries the A-1 block, so a database with neither is brought to A-1 + B by ONE migrate call."""
    _base(pg_conn)
    assert mig.PHASES["relations"]["up"][0][0] == "30_transitions.sql" and mig.PHASES["relations"]["up"][1][0] == "40_relations_v2.sql"
    mig.migrate(pg_conn, "relations")
    have = mig.existing(pg_conn, "transitions")
    assert have["tables"] == ["raw_update_event", "status_event", "wait_event"] and set(mig.PHASES["transitions"]["routines"]) <= set(have["routines"])
    a, b = _mk(pg_conn, "IN_PROGRESS"), _mk(pg_conn, "IN_PROGRESS")
    link(pg_conn, a, b, "DEPENDS_ON")
    enforce(pg_conn)
    refused(pg_conn, "delivery gate", rfd, a)


def test_a_failure_in_phase_b_rolls_back_the_a1_block_of_the_same_command(pg_conn):
    """The single transaction is real: if Phase B fails after the A-1 block ran, the A-1 objects are NOT left behind."""
    _base(pg_conn)
    pg_conn.execute("CREATE TABLE backlog.review_flag (unrelated int)")              # a conflicting object: 40 cannot create its table, its indexes then fail
    with pytest.raises(psycopg.Error):
        with pg_conn.transaction():
            mig.migrate(pg_conn, "relations")
    assert pg_conn.execute("SELECT to_regprocedure('backlog.fn_gate_mode(text)') IS NULL AND to_regclass('backlog.status_event') IS NULL").fetchone()[0] is True


def test_reapplying_a1_after_phase_b_never_overwrites_the_real_gate(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "OPEN")
    link(dbb, a, b, "DEPENDS_ON")
    assert len(blockers(dbb, a)) == 1
    mig.migrate(dbb, "transitions")                                       # A-1 applied again on its own (as an old runbook would): the stubs are created only when absent
    assert len(blockers(dbb, a)) == 1
    refused(dbb, "delivery gate", rfd, a)


def test_the_gate_is_inert_until_phase_b_is_applied(db):
    a, b = _mk(db, "IN_PROGRESS"), _mk(db, "OPEN")
    link(db, a, b, "DEPENDS_ON")
    rfd(db, a)                                                            # step A-1 alone: the stub blocks nothing
    assert status(db, a) == "READY_FOR_DEPLOY"


# --- the delivery gate -------------------------------------------------------------------------------------------------------------------

def test_an_open_prerequisite_refuses_ready_for_deploy_and_lists_the_blocker(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    with pytest.raises(psycopg.errors.Error, match="delivery gate") as e:
        with dbb.transaction():
            rfd(dbb, a)
    assert b in str(e.value) and "PREREQUISITE" in str(e.value)
    assert status(dbb, a) == "IN_PROGRESS"
    assert blockers(dbb, a) == [{"kind": "PREREQUISITE", "ticket": b, "status": "IN_PROGRESS"}]


@pytest.mark.parametrize("sol", ["CODE_FIX", "DATA_FIX", "WORKAROUND"])
def test_delivery_closures_are_gated_too(dbb, sol):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    rfd(dbb, a)
    link(dbb, a, b, "DEPENDS_ON")                                         # the dependency was recorded after the READY_FOR_DEPLOY move
    refused(dbb, "delivery gate", close, a, sol)
    assert status(dbb, a) == "READY_FOR_DEPLOY"


@pytest.mark.parametrize("sol", ["REJECTED", "DUPLICATED"])
def test_rejected_and_duplicated_closures_are_never_gated(dbb, sol):
    a, b, k = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS"), _mk(dbb, "OPEN")
    link(dbb, a, b, "DEPENDS_ON")
    tr(dbb, a, "CLOSED", solution_type=sol, followup=k)
    assert status(dbb, a) == "CLOSED"


def test_the_gate_clears_when_the_prerequisite_closes_and_ignores_unconfirmed_or_other_links(dbb):
    a, b, c, d, e = (_mk(dbb, "IN_PROGRESS") for _ in range(5))
    link(dbb, a, b, "DEPENDS_ON", "PROPOSED")
    link(dbb, a, c, "DEPENDS_ON", "REJECTED")
    link(dbb, a, d, "RELATED")
    link(dbb, a, e, "DEPENDS_ON")
    assert [x["ticket"] for x in blockers(dbb, a)] == [e]                  # only the CONFIRMED DEPENDS_ON counts
    rfd(dbb, e)
    close(dbb, e)
    assert blockers(dbb, a) == []
    rfd(dbb, a)
    close(dbb, a)
    assert status(dbb, a) == "CLOSED"


def test_the_direction_matters_the_dependent_is_gated_not_the_prerequisite(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    rfd(dbb, b)                                                           # b is the prerequisite: free to move
    assert status(dbb, b) == "READY_FOR_DEPLOY"


def test_a_rejection_event_lifts_a_confirmed_dependency(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    assert len(blockers(dbb, a)) == 1
    link(dbb, a, b, "DEPENDS_ON", "REJECTED")                             # append-only: a NEW event decides (the owner's decision of 2026-10-28 for FND-0333)
    assert blockers(dbb, a) == []


def test_an_open_hold_blocks_and_only_a_human_releases_it(dbb):
    a = _mk(dbb, "IN_PROGRESS")
    h = dbb.execute("SELECT backlog.sp_raise_hold(%s, 'raised on purpose in a test', 'V-HOOK-BYPASS', %s)", (a, ME)).fetchone()[0]
    assert blockers(dbb, a) == [{"kind": "HOLD", "hold_id": h, "code": "V-HOOK-BYPASS"}]
    refused(dbb, "delivery gate", rfd, a)
    refused(dbb, "only a human actor", lambda c: c.execute("SELECT backlog.sp_release_hold(%s, %s, 'an agent tries to release it')", (h, ME)))
    dbb.execute("SELECT backlog.sp_release_hold(%s, %s, 'released by the owner after review')", (h, OWNER))
    refused(dbb, "already released", lambda c: c.execute("SELECT backlog.sp_release_hold(%s, %s, 'released twice by mistake')", (h, OWNER)))
    assert blockers(dbb, a) == []
    rfd(dbb, a)


def test_a_hold_needs_a_reason_a_known_ticket_and_a_valid_code(dbb):
    a = _mk(dbb, "IN_PROGRESS")
    refused(dbb, "reason", lambda c: c.execute("SELECT backlog.sp_raise_hold(%s, 'short', NULL, %s)", (a, ME)))
    refused(dbb, "does not exist", lambda c: c.execute("SELECT backlog.sp_raise_hold('FND-0', 'a long enough reason', NULL, %s)", (ME,)))
    refused(dbb, "violation_code", lambda c: c.execute("SELECT backlog.sp_raise_hold(%s, 'a long enough reason', 'bad code', %s)", (a, ME)))


# --- review flags and link semantics ------------------------------------------------------------------------------------------------------

def test_impacts_raises_a_flag_on_the_far_end_that_blocks_it_until_acknowledged(dbb):
    x, y = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, x, y, "IMPACTS")
    rfd(dbb, x)                                                           # x reaches READY_FOR_DEPLOY: y must review
    flags = open_flags(dbb, y)
    assert len(flags) == 1 and flags[0][2:] == (x, "IMPACTS")
    assert open_flags(dbb, x) == []                                       # the near end is not flagged
    refused(dbb, "delivery gate", rfd, y)
    assert blockers(dbb, y)[0]["kind"] == "REVIEW_FLAG"
    refused(dbb, "10\\+ characters", lambda c: c.execute("SELECT backlog.sp_ack_review(%s, %s, 'short')", (flags[0][0], ME)))
    dbb.execute("SELECT backlog.sp_ack_review(%s, %s, 'reviewed the impact of the change and nothing else is needed')", (flags[0][0], ME))
    refused(dbb, "already acknowledged", lambda c: c.execute("SELECT backlog.sp_ack_review(%s, %s, 'acknowledged a second time by mistake')", (flags[0][0], ME)))
    assert open_flags(dbb, y) == []
    rfd(dbb, y)
    assert status(dbb, y) == "READY_FOR_DEPLOY"


def test_closing_after_ready_for_deploy_does_not_raise_the_same_flag_twice(dbb):
    x, y = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, x, y, "IMPACTS")
    rfd(dbb, x)
    fid = open_flags(dbb, y)[0][0]
    dbb.execute("SELECT backlog.sp_ack_review(%s, %s, 'reviewed after the READY_FOR_DEPLOY move')", (fid, ME))
    close(dbb, x)
    assert open_flags(dbb, y) == [] and dbb.execute("SELECT count(*) FROM backlog.review_flag WHERE kind = 'RAISE'").fetchone()[0] == 1


def test_a_ticket_closed_straight_from_in_progress_by_a_non_delivery_closure_also_raises_flags(dbb):
    x, y, k = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS"), _mk(dbb, "OPEN")
    link(dbb, x, y, "IMPACTS")
    tr(dbb, x, "CLOSED", solution_type="REJECTED", followup=k)
    assert [f[1] for f in open_flags(dbb)] == [y]


def test_overlaps_flags_only_when_it_carries_absorbs_and_in_both_directions(dbb):
    a, b, c, d = (_mk(dbb, "IN_PROGRESS") for _ in range(4))
    link(dbb, a, b, "OVERLAPS")                                           # no absorbs: nothing propagates
    link(dbb, c, d, "OVERLAPS", absorbs="criteria of the second ticket")
    rfd(dbb, a)
    assert open_flags(dbb) == []
    rfd(dbb, d)                                                           # the link points c -> d, the move is on d: still flags c
    assert [(f[1], f[2], f[3]) for f in open_flags(dbb)] == [(c, d, "OVERLAPS")]


def test_other_link_types_and_unconfirmed_links_raise_nothing(dbb):
    x = _mk(dbb, "IN_PROGRESS")
    others = [_mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS"), _mk(dbb, "CLOSED"), _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")]   # the DEPENDS_ON target is closed
    for typ, o in zip(["RELATED", "RELEASE_WITH", "DEPENDS_ON", "SUPERSEDES"], others):
        link(dbb, x, o, typ)
    link(dbb, x, others[4], "IMPACTS", "PROPOSED")
    rfd(dbb, x)
    assert open_flags(dbb) == []


def test_a_closed_far_end_is_not_flagged(dbb):
    x, y = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "CLOSED")
    link(dbb, x, y, "IMPACTS")
    rfd(dbb, x)
    assert open_flags(dbb) == []


def test_supersedes_only_suggests_and_never_blocks(dbb):
    new, old = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, new, old, "SUPERSEDES")
    assert dbb.execute("SELECT count(*) FROM backlog.v_supersede_suggestion").fetchone()[0] == 0     # not before the superseding ticket moves
    rfd(dbb, new)
    rows = dbb.execute("SELECT superseding_id, superseded_id, suggestion FROM backlog.v_supersede_suggestion").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(new, old)] and "DUPLICATED or CODE_FIX" in rows[0][2]
    assert open_flags(dbb) == [] and blockers(dbb, old) == []
    rfd(dbb, old)


# --- DEPENDS_ON cycle detection ----------------------------------------------------------------------------------------------------------

def test_a_depends_on_cycle_is_refused_when_recorded(dbb):
    a, b, c = (_mk(dbb) for _ in range(3))
    link(dbb, a, b, "DEPENDS_ON")
    link(dbb, b, c, "DEPENDS_ON", "PROPOSED")
    with pytest.raises(psycopg.errors.CheckViolation, match="cycle"):
        with dbb.transaction():
            link(dbb, c, a, "DEPENDS_ON")
    with pytest.raises(psycopg.errors.CheckViolation, match="cycle"):
        with dbb.transaction():
            link(dbb, b, a, "DEPENDS_ON", "PROPOSED")                     # a direct two-ticket cycle
    link(dbb, c, a, "RELATED")                                            # other types never form a dependency cycle
    link(dbb, c, a, "DEPENDS_ON", "REJECTED")                             # a rejection of the cycle edge is just history
    link(dbb, a, c, "DEPENDS_ON")                                         # a forward edge is fine (no cycle)


def test_a_cycle_through_a_rejected_edge_is_not_a_cycle(dbb):
    a, b, c = (_mk(dbb) for _ in range(3))
    link(dbb, a, b, "DEPENDS_ON")
    link(dbb, b, c, "DEPENDS_ON")
    link(dbb, a, b, "DEPENDS_ON", "REJECTED")                             # the edge a -> b is no longer current
    link(dbb, c, a, "DEPENDS_ON")                                         # c -> a -> (nothing): no cycle any more


def test_a_long_chain_is_walked_to_the_end(dbb):
    ids = [_mk(dbb) for _ in range(12)]
    for x, y in zip(ids, ids[1:]):
        link(dbb, x, y, "DEPENDS_ON")
    with pytest.raises(psycopg.errors.CheckViolation, match="cycle"):
        with dbb.transaction():
            link(dbb, ids[-1], ids[0], "DEPENDS_ON")


# --- exceptions: p_force, the counted override, the switch -------------------------------------------------------------------------------

def test_p_force_overrides_the_gate_with_a_reason_and_the_override_is_recorded(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    refused(dbb, "40\\+ characters", rfd, a, force=True)                  # forced, but the reason is short (COMMENT is 39 characters)
    ev = rfd(dbb, a, force=True, comment=FORCE_REASON)
    forced, detail = dbb.execute("SELECT forced, detail FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()
    assert forced is True and detail["gate_override"] == [{"kind": "PREREQUISITE", "ticket": b, "status": "IN_PROGRESS"}]
    assert status(dbb, a) == "READY_FOR_DEPLOY"
    assert dbb.execute("SELECT count(*) FROM backlog.status_event WHERE forced").fetchone()[0] == 1       # V-FORCED counts these


def test_an_unforced_move_never_carries_a_gate_override(dbb):
    a = _mk(dbb, "IN_PROGRESS")
    ev = rfd(dbb, a)
    forced, detail = dbb.execute("SELECT forced, detail FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()
    assert forced is False and "gate_override" not in detail


def test_only_a_human_actor_changes_the_mode_and_off_stops_the_evaluation(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    refused(dbb, "only a human actor", lambda c: c.execute("SELECT backlog.sp_set_gate('DELIVERY', 'OFF', %s, 'an agent tries to open the gate')", (ME,)))
    assert bc.gate_mode(conn=dbb) == "ENFORCE"
    dbb.execute("SELECT backlog.sp_set_gate('DELIVERY', 'OFF', %s, 'the owner switches the gate off for an emergency')", (OWNER,))
    assert blockers(dbb, a) == [] and bc.gate_mode(conn=dbb) == "OFF"
    n = dbb.execute("SELECT count(*) FROM backlog.gate_audit_event").fetchone()[0]
    rfd(dbb, a)                                                           # nothing is evaluated or recorded while the mode is OFF
    assert dbb.execute("SELECT count(*) FROM backlog.gate_audit_event").fetchone()[0] == n
    dbb.execute("SELECT backlog.sp_set_gate('DELIVERY', 'AUDIT', %s, 'the owner puts the gate back in audit mode')", (OWNER,))
    refused(dbb, "must be OFF, AUDIT or ENFORCE", lambda c: c.execute("SELECT backlog.sp_set_gate('DELIVERY', 'WHATEVER', %s, 'a made up mode for the test')", (OWNER,)))


def test_the_switch_is_append_only_and_has_a_reason(dbb):
    refused(dbb, "reason", lambda c: c.execute("SELECT backlog.sp_set_gate('DELIVERY', 'OFF', %s, 'short')", (OWNER,)))
    dbb.execute("SELECT backlog.sp_set_gate('DELIVERY', 'OFF', %s, 'the owner switches the gate off for an emergency')", (OWNER,))
    x, y = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, x, y, "IMPACTS")
    rfd(dbb, x)                                                           # one flag row ...
    dbb.execute("SELECT backlog.sp_raise_hold(%s, 'raised so there is a row to protect', NULL, %s)", (y, ME))     # ... and one hold row
    refused(dbb, "append-only", lambda c: c.execute("UPDATE backlog.gate_switch SET mode = 'OFF'"))
    refused(dbb, "append-only", lambda c: c.execute("DELETE FROM backlog.review_flag"))
    refused(dbb, "append-only", lambda c: c.execute("UPDATE backlog.hold_event SET reason = 'rewritten history'"))


# --- the structured wait annotation (used by step 0) -------------------------------------------------------------------------------------

def test_annotate_wait_records_a_blocker_without_changing_the_status(dbb):
    a, b = _mk(dbb, "BLOCKED"), _mk(dbb, "IN_PROGRESS")
    n_events = dbb.execute("SELECT count(*) FROM backlog.status_event WHERE action_point_id = %s", (a,)).fetchone()[0]
    wid = dbb.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'structured blocker recorded by step 0', %s)", (a, ME, b)).fetchone()[0]
    row = dbb.execute("SELECT kind, blocker_ref, revisit_basis FROM backlog.wait_event WHERE wait_id = %s", (wid,)).fetchone()
    assert row == ("BLOCKED", b, "DECLARED") and status(dbb, a) == "BLOCKED"
    assert dbb.execute("SELECT count(*) FROM backlog.status_event WHERE action_point_id = %s", (a,)).fetchone()[0] == n_events
    assert dbb.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s AND comment LIKE 'structured blocker%%'", (a,)).fetchone()[0] == 1


def test_annotate_wait_for_a_deferred_ticket_keeps_the_revisit_basis(dbb):
    a = _mk(dbb, "DEFERRED")
    dbb.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'revisit date placeholder from step 0', NULL, NULL, '2027-01-26', NULL, 'DEFAULT_90D')", (a, ME))
    assert dbb.execute("SELECT kind, revisit_by::text, revisit_basis FROM backlog.wait_event WHERE action_point_id = %s", (a,)).fetchone() == ("DEFERRED", "2027-01-26", "DEFAULT_90D")


def test_annotate_wait_refuses_what_is_not_waiting_or_not_structured(dbb):
    a, b, c = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "BLOCKED"), _mk(dbb, "DEFERRED")
    refused(dbb, "not a BLOCKED or DEFERRED", lambda x: x.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'a note of enough length', %s)", (a, ME, b)))
    refused(dbb, "blocker ticket or a named condition", lambda x: x.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'a note of enough length')", (b, ME)))
    refused(dbb, "revisit_by or revisit_on_event", lambda x: x.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'a note of enough length')", (c, ME)))
    refused(dbb, "does not exist", lambda x: x.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'a note of enough length', 'FND-0')", (b, ME)))
    refused(dbb, "basis", lambda x: x.execute("SELECT backlog.sp_annotate_wait(%s, %s, 'a note of enough length', NULL, 'a named condition', NULL, NULL, 'WHATEVER')", (b, ME)))


# --- privileges, ownership, rollback ------------------------------------------------------------------------------------------------------

def test_the_shared_writer_can_run_the_routines_but_cannot_write_the_new_tables(dbb):
    a = _mk(dbb, "IN_PROGRESS")
    dbb.execute("SET LOCAL ROLE backlog_writer")
    try:
        assert blockers(dbb, a) == []
        dbb.execute("SELECT count(*) FROM backlog.v_open_hold").fetchone()
        for table in ("review_flag", "hold_event", "gate_switch"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with dbb.transaction():
                    dbb.execute(f"INSERT INTO backlog.{table} (kind) VALUES ('RAISE')" if table != "gate_switch" else "INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES ('DELIVERY', 'OFF', 'human:x', 'a reason of ten')")
    finally:
        dbb.execute("RESET ROLE")


def test_the_cycle_guard_also_runs_for_the_shared_writer(dbb):
    a, b = _mk(dbb), _mk(dbb)
    dbb.execute("SET LOCAL ROLE backlog_writer")
    try:
        link(dbb, a, b, "DEPENDS_ON")
        with pytest.raises(psycopg.errors.CheckViolation, match="cycle"):
            with dbb.transaction():
                link(dbb, b, a, "DEPENDS_ON")
    finally:
        dbb.execute("RESET ROLE")


def test_the_rollback_restores_the_stubs_and_keeps_used_vocabulary(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    link(dbb, a, b, "IMPACTS")                                            # the history now uses a Phase B type: it must survive the rollback
    before = bc.history_fingerprint(conn=dbb)
    mig.migrate(dbb, "relations", down=True)
    assert mig.existing(dbb, "relations")["tables"] == []
    assert bc.history_fingerprint(max_log_id=before["max_log_id"], max_event_id=before["max_event_id"], conn=dbb) == before     # the rollback rewrote no history
    rfd(dbb, a)                                                           # the gate no longer binds; sp_transition still works
    assert status(dbb, a) == "READY_FOR_DEPLOY"
    assert dbb.execute("SELECT count(*) FROM backlog.backlog_link_event WHERE link_type = 'IMPACTS'").fetchone()[0] == 1
    mig.migrate(dbb, "relations")                                         # and it can be applied again
    assert len(blockers(dbb, a, to="CLOSED", delivery=True)) == 1


def test_the_rollback_of_an_unused_vocabulary_restores_the_old_shape(dbb):
    mig.migrate(dbb, "relations", down=True)
    cols = {r[0] for r in dbb.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'backlog_link_event'").fetchall()}
    assert "origin" not in cols
    a, b = _mk(dbb), _mk(dbb)
    with pytest.raises(psycopg.errors.CheckViolation):
        with dbb.transaction():
            dbb.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'IMPACTS', 'PROPOSED', 'e', 'u')", (a, b))


def test_origin_is_constrained_and_defaults_to_review(dbb):
    a, b = _mk(dbb), _mk(dbb)
    dbb.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) VALUES (%s, %s, 'RELATED', 'PROPOSED', 'e', 'u')", (a, b))
    assert dbb.execute("SELECT origin FROM backlog.backlog_link_event").fetchone()[0] == "REVIEW"
    with pytest.raises(psycopg.errors.CheckViolation):
        with dbb.transaction():
            link(dbb, a, b, "RELATED", origin="MADE_UP")
    for origin in ("GATE", "STEP0", "SOURCE"):
        link(dbb, a, b, "RELATED", "PROPOSED", origin=origin)


def test_a_new_ticket_opens_through_the_routine_with_phase_b_applied(dbb):
    ap = open_(dbb)
    assert status(dbb, ap) == "OPEN"
    tr(dbb, ap, "IN_PROGRESS")
    rfd(dbb, ap)
    close(dbb, ap)
    assert status(dbb, ap) == "CLOSED"
    assert json.loads(json.dumps(blockers(dbb, ap))) == []


# --- the client (shared/backlog_client.py) -----------------------------------------------------------------------------------------------

def _open(conn, **kw):
    a = dict(title="client opened ticket", nature="BUG", object_type="N/A", priority="LOW", session_id=SESSION, description="description", first_log="first log of the ticket", actor=ME, conn=conn)
    a.update(kw)
    return bc.open_ticket(**a)


def test_mentioned_ids_are_distinct_ordered_and_pure():
    assert bc.mentioned_ids("see FND-0102 and FND-0034", None, "again FND-0102, also xFND-0200 and FND-12345") == ["FND-0102", "FND-0034"]
    assert bc.mentioned_ids() == [] and bc.mentioned_ids("no ticket here", "fnd-0102") == []


def test_open_ticket_proposes_a_related_link_for_every_existing_mention(dbb):
    a, b = _mk(dbb), _mk(dbb)
    got = _open(dbb, description=f"follows {a}; unknown FND-0777 is ignored", first_log=f"see also {b} and {a} again")
    assert isinstance(got, str) and got.startswith("FND-") and got.mentions == (a, "FND-0777", b) and sorted(got.linked) == sorted([a, b])
    rows = dbb.execute("SELECT to_id, link_type, status, origin, username FROM backlog.backlog_link_event WHERE from_id = %s ORDER BY to_id", (str(got),)).fetchall()
    assert rows == [(a, "RELATED", "PROPOSED", "SOURCE", ME), (b, "RELATED", "PROPOSED", "SOURCE", ME)]


def test_open_ticket_never_repeats_or_overrides_a_decided_pair_and_can_opt_out(dbb):
    a, b = _mk(dbb), _mk(dbb)
    first = _open(dbb, description=f"mentions {a}", link_mentions=False)
    assert first.linked == () and first.mentions == (a,)
    assert dbb.execute("SELECT count(*) FROM backlog.backlog_link_event").fetchone()[0] == 0
    n = dbb.execute("SELECT count(*) FROM backlog.backlog_link_event").fetchone()[0]
    dbb.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    got = _open(dbb, description=f"mentions {b}")
    assert got.linked == (b,) and dbb.execute("SELECT count(*) FROM backlog.backlog_link_event").fetchone()[0] == n + 1


def test_open_ticket_records_no_link_before_phase_b(db):
    a = _mk(db)
    got = _open(db, description=f"mentions {a}")
    assert got.mentions == (a,) and got.linked == ()
    assert db.execute("SELECT count(*) FROM backlog.backlog_link_event").fetchone()[0] == 0 and bc.relations_store_present(conn=db) is False


def test_record_link_events_accepts_an_origin_only_when_it_is_valid(dbb):
    a, b = _mk(dbb), _mk(dbb)
    ids = bc.record_link_events([dict(from_id=a, to_id=b, link_type="IMPACTS", status="PROPOSED", evidence="e", username=ME, origin="STEP0"),
                                 dict(from_id=a, to_id=b, link_type="SUPERSEDES", status="PROPOSED", evidence="e", username=ME)], conn=dbb)
    assert dbb.execute("SELECT origin FROM backlog.backlog_link_event WHERE event_id = ANY(%s) ORDER BY event_id", (ids,)).fetchall() == [("STEP0",), ("REVIEW",)]
    with pytest.raises(ValueError, match="origin"):
        bc.record_link_event(from_id=a, to_id=b, link_type="RELATED", status="PROPOSED", evidence="e", username=ME, origin="MADE_UP", conn=dbb)


def test_the_client_helpers_drive_the_gate_flags_and_holds(dbb):
    x, y = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, x, y, "IMPACTS")
    bc.transition(x, "READY_FOR_DEPLOY", session_id=SESSION, comment="progress note long enough for the log", actor=ME, release_version=REL, conn=dbb)
    assert [f["source_id"] for f in bc.open_review_flags(y, conn=dbb)] == [x]
    assert bc.gate_blockers(y, conn=dbb)[0]["kind"] == "REVIEW_FLAG"
    bc.ack_review(bc.open_review_flags(y, conn=dbb)[0]["flag_id"], "reviewed the impact of the change", actor=ME, conn=dbb)
    assert bc.open_review_flags(conn=dbb) == [] and bc.gate_blockers(y, conn=dbb) == []
    h = bc.raise_hold(y, "raised by a test of the client helpers", code="V-HOOK-BYPASS", actor=ME, conn=dbb)
    assert bc.gate_blockers(y, conn=dbb)[0]["kind"] == "HOLD"
    bc.release_hold(h, "released by the owner in a test", actor=OWNER, conn=dbb)
    assert bc.gate_blockers(y, conn=dbb) == []
    bc.set_gate("OFF", "switched off by a test of the client", actor=OWNER, conn=dbb)
    assert bc.phase_b_catalog(conn=dbb)["gate_mode"] == "OFF" and bc.gate_mode(conn=dbb) == "OFF"
    with pytest.raises(ValueError, match="mode"):
        bc.set_gate("MAYBE", "a made up mode for the test", actor=OWNER, conn=dbb)


def test_the_catalog_reports_what_the_acceptance_relies_on(dba):
    cat = bc.phase_b_catalog(conn=dba)
    assert cat["cycle_guard"] is True and cat["origin_column"] is True and cat["sp_transition_calls_gate"] is True and cat["existing_depends_on_cycles"] == 0
    assert {r["name"] for r in cat["routines"]} == set(bc.PHASE_B_ROUTINES) and all(r["secdef"] and r["owner"] == "backlog_owner" for r in cat["routines"])
    assert cat["table_owners"] == {"gate_audit_event": "backlog_owner", "gate_switch": "backlog_owner", "hold_event": "backlog_owner", "review_flag": "backlog_owner"}
    assert cat["append_only"] == ["gate_audit_event", "gate_switch", "hold_event", "review_flag"] and cat["writer"] == {"can_write_new_tables": False, "can_execute_gate": True}
    assert "IMPACTS" in cat["link_type_check"] and "SUPERSEDES" in cat["link_type_check"] and cat["gate_mode"] == "AUDIT"
    assert cat["audit_window"]["mode"] == "AUDIT" and cat["audit_window"]["clear"] is False


def test_the_catalog_of_a_store_without_phase_b_is_empty_but_safe(db):
    cat = bc.phase_b_catalog(conn=db)
    assert cat["cycle_guard"] is False and cat["origin_column"] is False and cat["table_owners"] == {}
    assert cat["sp_transition_calls_gate"] is True and {r["name"] for r in cat["routines"]} == {"fn_gate_blockers", "fn_raise_review_flags", "fn_gate_mode", "fn_gate_record"}      # the A-1 stubs
    assert cat["gate_mode"] == "OFF" and cat["audit_window"] is None                                                 # before Phase B the stub mode is OFF


# --- phased rollout: AUDIT mode first, ENFORCE only after a clear 48-hour window (owner mitigation, 2026-10-28) ------------------------------

def test_the_deployment_default_is_audit_a_violation_is_logged_and_nothing_is_refused(dba):
    a, b = _mk(dba, "IN_PROGRESS"), _mk(dba, "IN_PROGRESS")
    link(dba, a, b, "DEPENDS_ON")
    assert bc.gate_mode(conn=dba) == "AUDIT" and dba.execute("SELECT actor FROM backlog.gate_switch").fetchone()[0] == "system:deploy"
    ev = rfd(dba, a)                                                      # the move goes through although a prerequisite is open
    assert status(dba, a) == "READY_FOR_DEPLOY"
    forced, detail = dba.execute("SELECT forced, detail FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()
    assert forced is False and detail["gate_audit"] == [{"kind": "PREREQUISITE", "ticket": b, "status": "IN_PROGRESS"}] and "gate_override" not in detail
    rows = dba.execute("SELECT action_point_id, to_status, delivery, blockers, mode, actor, forced FROM backlog.v_gate_audit_violation").fetchall()
    assert rows == [(a, "READY_FOR_DEPLOY", False, [{"kind": "PREREQUISITE", "ticket": b, "status": "IN_PROGRESS"}], "AUDIT", ME, False)]
    assert [v["action_point_id"] for v in bc.gate_audit_violations(conn=dba)] == [a]


def test_audit_mode_also_logs_the_moves_that_were_clean_so_the_log_has_a_denominator(dba):
    a = _mk(dba, "IN_PROGRESS")
    rfd(dba, a)
    close(dba, a)
    rows = dba.execute("SELECT to_status, delivery, blockers FROM backlog.gate_audit_event ORDER BY event_id").fetchall()
    assert rows == [("READY_FOR_DEPLOY", False, []), ("CLOSED", True, [])]
    assert dba.execute("SELECT count(*) FROM backlog.v_gate_audit_violation").fetchone()[0] == 0 and bc.gate_audit_window(conn=dba)["evaluated"] == 2


def test_moves_that_are_not_gated_leave_no_audit_row(dba):
    a, k = _mk(dba, "IN_PROGRESS"), _mk(dba, "OPEN")
    tr(dba, a, "BLOCKED", condition="waiting for the vendor to answer")
    tr(dba, a, "IN_PROGRESS")
    tr(dba, a, "CLOSED", solution_type="REJECTED", followup=k)
    assert dba.execute("SELECT count(*) FROM backlog.gate_audit_event").fetchone()[0] == 0


def _age(conn, hours, events=False):
    """Make the audit window look `hours` old by moving the switch row back in time (and, with events=True, the audit rows too): a test-only edit as the owner connection, which the
    append-only trigger would refuse, so it is lifted for these statements."""
    tables = ["gate_switch"] + (["gate_audit_event"] if events else [])
    for tb in tables:
        conn.execute(f"ALTER TABLE backlog.{tb} DISABLE TRIGGER append_only")
        conn.execute(f"UPDATE backlog.{tb} SET event_ts = event_ts - make_interval(hours => %s)", (hours,))
        conn.execute(f"ALTER TABLE backlog.{tb} ENABLE TRIGGER append_only")


def _moves_on_different_tickets(conn, n, link_first_to_open=False):
    """n gated moves (READY_FOR_DEPLOY) on n DIFFERENT tickets; with link_first_to_open the first one has an open prerequisite (a violation in AUDIT)."""
    ids = [_mk(conn, "IN_PROGRESS") for _ in range(n)]
    if link_first_to_open:
        link(conn, ids[0], _mk(conn, "IN_PROGRESS"), "DEPENDS_ON")
    for ap in ids:
        rfd(conn, ap)
    return ids


def test_enforce_is_refused_until_the_audit_has_run_for_48_hours_on_five_different_tickets(dba):
    refused(dba, "needs a clear audit window", lambda c: c.execute("SELECT backlog.sp_set_gate('DELIVERY', 'ENFORCE', %s, 'trying to enforce on the first day')", (OWNER,)))
    _moves_on_different_tickets(dba, 1)                                   # one clean move: still only minutes of audit
    w = bc.gate_audit_window(conn=dba)
    assert w["mode"] == "AUDIT" and w["evaluated"] == 1 and w["tickets_48h"] == 1 and w["required_tickets"] == 5 and w["violations_48h"] == 0 and w["clear"] is False
    assert float(w["hours_in_audit"]) < 1
    refused(dba, "needs a clear audit window", lambda c: c.execute("SELECT backlog.sp_set_gate('DELIVERY', 'ENFORCE', %s, 'still the first day of the audit')", (OWNER,)))


def test_one_trivial_move_or_four_tickets_do_not_open_enforce_even_after_48_hours(dba):
    _age(dba, 49)                                                         # the audit has now been running for 49 hours
    assert bc.gate_audit_window(conn=dba)["clear"] is False               # no move at all
    a = _moves_on_different_tickets(dba, 1)[0]
    close(dba, a)                                                         # a second move, on the SAME ticket
    w = bc.gate_audit_window(conn=dba)
    assert w["evaluated"] == 2 and w["tickets_48h"] == 1 and w["clear"] is False
    _moves_on_different_tickets(dba, 3)                                   # four different tickets in all
    w = bc.gate_audit_window(conn=dba)
    assert w["tickets_48h"] == 4 and w["clear"] is False
    refused(dba, "needs a clear audit window", lambda c: c.execute("SELECT backlog.sp_set_gate('DELIVERY', 'ENFORCE', %s, 'four tickets are not enough')", (OWNER,)))


def test_enforce_is_accepted_after_48_clear_hours_on_five_tickets_and_binds_from_then_on(dba):
    _age(dba, 49)
    _moves_on_different_tickets(dba, 5)
    w = bc.gate_audit_window(conn=dba)
    assert w["clear"] is True and float(w["hours_in_audit"]) >= 48 and w["tickets_48h"] == 5 and w["violations_48h"] == 0
    bc.set_gate("ENFORCE", "the owner enforces after a clear 48-hour audit window", actor=OWNER, conn=dba)
    assert bc.gate_mode(conn=dba) == "ENFORCE" and bc.gate_audit_window(conn=dba)["clear"] is False       # no longer in audit
    b, c = _mk(dba, "IN_PROGRESS"), _mk(dba, "IN_PROGRESS")
    link(dba, b, c, "DEPENDS_ON")
    refused(dba, "delivery gate", rfd, b)


def test_a_violation_inside_the_last_48_hours_keeps_enforce_closed_and_an_older_one_does_not(dba):
    _moves_on_different_tickets(dba, 2, link_first_to_open=True)         # a violation and a clean move ...
    _age(dba, 49, events=True)                                            # ... that are now 49 hours old
    _moves_on_different_tickets(dba, 5)                                   # five clean moves inside the last 48 hours
    w = bc.gate_audit_window(conn=dba)
    assert w["violations_since"] == 1 and w["violations_48h"] == 0 and w["tickets_48h"] == 5 and w["clear"] is True      # the old violation no longer counts
    _moves_on_different_tickets(dba, 1, link_first_to_open=True)         # a NEW violation now
    w = bc.gate_audit_window(conn=dba)
    assert w["violations_48h"] == 1 and w["clear"] is False
    refused(dba, "needs a clear audit window", lambda c_: c_.execute("SELECT backlog.sp_set_gate('DELIVERY', 'ENFORCE', %s, 'a violation was logged an hour ago')", (OWNER,)))


def test_moves_older_than_48_hours_do_not_count_toward_the_five_tickets(dba):
    _moves_on_different_tickets(dba, 5)
    _age(dba, 100, events=True)                                           # all five moves are 100 hours old, the audit has run 100 hours
    w = bc.gate_audit_window(conn=dba)
    assert float(w["hours_in_audit"]) >= 48 and w["evaluated"] == 5 and w["tickets_48h"] == 0 and w["clear"] is False


def test_going_back_to_audit_starts_a_new_window(dba):
    _age(dba, 49)
    _moves_on_different_tickets(dba, 5)
    bc.set_gate("ENFORCE", "enforced after a clear window in this test", actor=OWNER, conn=dba)
    bc.set_gate("AUDIT", "back to audit after an incident in this test", actor=OWNER, conn=dba)
    w = bc.gate_audit_window(conn=dba)
    assert w["mode"] == "AUDIT" and float(w["hours_in_audit"]) < 1 and w["evaluated"] == 0 and w["tickets_48h"] == 0 and w["clear"] is False


def test_enforce_logs_a_forced_override_in_the_audit_table_too(dbb):
    a, b = _mk(dbb, "IN_PROGRESS"), _mk(dbb, "IN_PROGRESS")
    link(dbb, a, b, "DEPENDS_ON")
    rfd(dbb, a, force=True, comment=FORCE_REASON)
    rows = dbb.execute("SELECT mode, forced, jsonb_array_length(blockers) FROM backlog.gate_audit_event").fetchall()
    assert rows == [("ENFORCE", True, 1)]


def test_the_transition_matrix_has_blocked_to_todo(dba):
    a = _mk(dba, "BLOCKED")
    ev = tr(dba, a, "TODO")                                               # owner mitigation 2: a legal move, no p_force
    assert status(dba, a) == "TODO" and dba.execute("SELECT forced FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()[0] is False


# --- the audited correction of deployment_ts (owner directive 2026-10-28, case FND-0193) ---------------------------------------------------

def _closed_delivery(conn):
    """A ticket created 10 days ago, delivered through the routines (so deployment_ts and closure_ts are 'now'): the case of a closure that came after the deployment."""
    ap = _mk(conn, "IN_PROGRESS")
    conn.execute("SELECT set_config('backlog.via_proc', 'on', true)")
    conn.execute("UPDATE backlog.BACKLOG SET creation_ts = now() - interval '10 days' WHERE action_point_id = %s", (ap,))
    conn.execute("SELECT set_config('backlog.via_proc', 'off', true)")
    rfd(conn, ap)
    close(conn, ap)
    return ap


WHY = "the deployment happened on 2026-10-19 (published and promoted that day, logs of the sibling tickets of the same release)"


def test_the_deployment_ts_is_corrected_with_a_trace_and_without_a_direct_write_record(dba):
    ap = _closed_delivery(dba)
    before = dba.execute("SELECT deployment_ts, closure_ts FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()
    v00, shadow = dba.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0], bc.shadow_progress(conn=dba)["live_non_system_since_last_v00"]
    when = dba.execute("SELECT now() - interval '9 days'").fetchone()[0]
    ev = bc.correct_deployment_ts(ap, when, WHY, session_id=SESSION, actor=ME, conn=dba)
    dep, clo = dba.execute("SELECT deployment_ts, closure_ts FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()
    assert dep == when and clo == before[1]                                         # the deployment moved, the closure did not
    detail, frm, to = dba.execute("SELECT detail, from_status, to_status FROM backlog.status_event WHERE event_id = %s", (ev,)).fetchone()
    assert (frm, to, detail["kind"]) == ("CLOSED", "CLOSED", "deployment_ts_correction") and detail["after"] is not None and detail["before"] is not None
    assert dba.execute("SELECT count(*) FROM backlog.BACKLOG_LOGS WHERE action_point_id = %s AND comment LIKE 'deployment_ts corrected from%%'", (ap,)).fetchone()[0] == 1
    assert dba.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0] == v00                      # not a V-00 (a raw UPDATE would have been)
    assert bc.shadow_progress(conn=dba)["live_non_system_since_last_v00"] == shadow                                # and the A-2 counter did not move
    assert dba.execute("SELECT status, solution_type FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone() == ("CLOSED", "CODE_FIX")


def test_a_raw_update_of_the_deployment_ts_would_have_been_a_v00_and_the_routine_is_not(dba):
    """Why a one-off SQL patch is the wrong tool: it is recorded as the first direct write and resets the counter of step A-2."""
    ap = _closed_delivery(dba)
    assert dba.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0] == 0
    dba.execute("UPDATE backlog.BACKLOG SET deployment_ts = deployment_ts - interval '1 day' WHERE action_point_id = %s", (ap,))
    assert dba.execute("SELECT count(*) FROM backlog.raw_update_event").fetchone()[0] == 1
    assert bc.shadow_progress(conn=dba)["live_non_system_since_last_v00"] == 0


@pytest.mark.parametrize("what", ["not_closed", "rejected", "after_closure", "before_creation", "future", "same_value", "short_reason", "bad_session", "bad_actor", "unknown_ticket", "null_ts"])
def test_the_correction_refuses_what_is_not_a_plain_delivery_ticket_or_not_credible(dba, what):
    ap = _closed_delivery(dba)
    when = dba.execute("SELECT now() - interval '9 days'").fetchone()[0]
    kw = dict(ap=ap, when=when, reason=WHY, session=SESSION, actor=ME)
    if what == "not_closed":
        kw["ap"] = _mk(dba, "IN_PROGRESS")
    elif what == "rejected":
        k, r_ = _mk(dba, "OPEN"), _mk(dba, "IN_PROGRESS")
        tr(dba, r_, "CLOSED", solution_type="REJECTED", followup=k)
        kw["ap"] = r_
    elif what == "after_closure":
        kw["when"] = dba.execute("SELECT now() + interval '1 hour'").fetchone()[0]
    elif what == "before_creation":
        kw["when"] = dba.execute("SELECT now() - interval '20 days'").fetchone()[0]
    elif what == "future":
        kw["when"] = dba.execute("SELECT clock_timestamp() + interval '1 day'").fetchone()[0]
    elif what == "same_value":
        kw["when"] = dba.execute("SELECT deployment_ts FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()[0]
    elif what == "short_reason":
        kw["reason"] = "too short"
    elif what == "bad_session":
        kw["session"] = "not a session"
    elif what == "bad_actor":
        kw["actor"] = "agent:claude"
    elif what == "unknown_ticket":
        kw["ap"] = "FND-0"
    elif what == "null_ts":
        kw["when"] = None
    with pytest.raises(psycopg.errors.Error):
        with dba.transaction():
            dba.execute("SELECT backlog.sp_correct_deployment_ts(%s, %s, %s, %s, %s)", (kw["ap"], kw["when"], kw["actor"], kw["session"], kw["reason"]))
    assert dba.execute("SELECT count(*) FROM backlog.status_event WHERE detail ->> 'kind' = 'deployment_ts_correction'").fetchone()[0] == 0


def test_the_shared_writer_can_run_the_correction_but_only_through_the_routine(dba):
    ap = _closed_delivery(dba)
    when = dba.execute("SELECT now() - interval '9 days'").fetchone()[0]
    dba.execute("SET LOCAL ROLE backlog_writer")
    try:
        bc.correct_deployment_ts(ap, when, WHY, session_id=SESSION, actor=ME, conn=dba)
    finally:
        dba.execute("RESET ROLE")
    assert dba.execute("SELECT deployment_ts FROM backlog.BACKLOG WHERE action_point_id = %s", (ap,)).fetchone()[0] == when
