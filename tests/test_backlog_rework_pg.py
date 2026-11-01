# tests/test_backlog_rework_pg.py
# -*- coding: utf-8 -*-
"""FND-0368: a rejected delivery stays rejected. The autopilot reads the time of a ticket's latest move back to work from READY_FOR_DEPLOY or CLOSED (backlog_client.rework_times, from the live status
events) and the rule engine ignores the delivery merges that are not newer than it. Hermetic PostgreSQL with the real client and the real rule engine. Run with python scripts/ops/run_pg_tests.py."""
from __future__ import annotations

import sys
from datetime import timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_autopilot as engine  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_relations_pg import close, rfd  # noqa: E402
from test_backlog_transitions_pg import ME, _base, _mk, tr  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "autonomy", "events")


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    return pg_conn


def merge_at(conn, offset_seconds):
    """A delivery merge whose author time is `offset_seconds` from the database's now."""
    now = conn.execute("SELECT clock_timestamp()").fetchone()[0].astimezone(timezone.utc)
    from datetime import timedelta
    return engine.Merge(ref="a" * 40, pr=1, paths=("shared/x.py",), year=2026, month=11, ts=now + timedelta(seconds=offset_seconds))


def decide(ap, status, merge, reworked):
    facts = engine.Facts(tickets={ap: {"status": status, "release_version": None}}, merges={ap: [merge]}, reworked=reworked)
    return engine.decide_one(ap, facts)


def test_rework_times_is_the_last_move_back_to_work_from_ready_or_closed_and_nothing_else(db):
    a = _mk(db)
    tr(db, a, "IN_PROGRESS")
    rfd(db, a)
    assert a not in bc.rework_times(conn=db)                                                        # going forward is not a rejection
    tr(db, a, "IN_PROGRESS", comment="the owner rejected the delivery and put it back to work")
    first = bc.rework_times(conn=db)[a]
    rfd(db, a)
    close(db, a)
    bc.reopen(a, "INCOMPLETE_FIX", "reopened: the delivery lacked the enforcement", session_id="FND_20261101_DEV_TEST", actor=ME, conn=db)
    assert bc.rework_times(conn=db)[a] > first                                                       # the latest one wins: a reopening of a closed ticket counts too
    b = _mk(db)
    tr(db, b, "TODO")
    assert b not in bc.rework_times(conn=db)                                                        # a move from OPEN is not a rejection


def test_a_reopening_to_todo_or_open_is_a_rejection_too_and_a_backfilled_event_is_history_not_a_rejection(db):
    a = _mk(db)
    tr(db, a, "IN_PROGRESS")
    rfd(db, a)
    close(db, a)
    bc.reopen(a, "SCOPE_CHANGE", "reopened to be planned again after the owner's review", session_id="FND_20261101_DEV_TEST", actor=ME, to_status="TODO", conn=db)
    assert a in bc.rework_times(conn=db)                                                            # CLOSED -> TODO counts: the delivery is rejected whichever working status it returns to
    b = _mk(db, "READY_FOR_DEPLOY")
    db.execute("INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, origin) VALUES (%s, 'READY_FOR_DEPLOY', 'IN_PROGRESS', 'system:backfill', 'BACKFILL')", (b,))
    assert b not in bc.rework_times(conn=db)                                                        # the history loaded from before the state machine is not a rejection of anything


def test_a_ticket_put_back_to_work_is_not_delivered_again_by_its_old_merge_and_is_by_a_newer_one(db):
    a = _mk(db)
    tr(db, a, "IN_PROGRESS")
    old = merge_at(db, -3600)                                                                       # the delivery merged an hour ago ...
    rfd(db, a)
    assert decide(a, "READY_FOR_DEPLOY", old, bc.rework_times(conn=db)).action == engine.CLOSE      # ... and, as long as nobody rejected it, it is the delivery that counts
    tr(db, a, "IN_PROGRESS", comment="the owner rejected the delivery and put it back to work")
    reworked = bc.rework_times(conn=db)
    assert decide(a, "IN_PROGRESS", old, reworked) is None                                          # the old merge no longer moves it to READY_FOR_DEPLOY ...
    new = merge_at(db, 3600)
    d = decide(a, "IN_PROGRESS", new, reworked)
    assert d.action == engine.MOVE and d.evidence_ref == new.ref                                    # ... a merge after the rejection is a new delivery
