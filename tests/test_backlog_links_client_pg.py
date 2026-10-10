# tests/test_backlog_links_client_pg.py
# -*- coding: utf-8 -*-
"""Relationship API of shared/backlog_client.py against the real DDL (db/pg_backlog/00_schema.sql + 20_links.sql).

Hermetic: pg_conn is a throwaway container inside a savepoint that is always rolled back; the API is driven through its `conn`
parameter, so nothing is ever committed. Covers the ticket scopes (all / open / open + recently closed / by id), the log reader,
current-state vs history link readers, the round trip of record_link_event(s), whole-batch atomicity and that a database error
(unknown ticket) propagates instead of being swallowed.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_links_client_pg.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_links as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    mig.migrate(pg_conn)
    rows = (
        ("FND-9001", "OPEN", None),
        ("FND-9002", "IN_PROGRESS", None),
        ("FND-9003", "CLOSED", NOW - timedelta(days=5)),     # recently closed
        ("FND-9004", "CLOSED", NOW - timedelta(days=90)),    # long closed
    )
    for apid, status, closed in rows:
        pg_conn.execute(
            "INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority, "
            "closure_ts) VALUES (%s, %s, 'FND', 'BUG', 'N/A', %s, 'LOW', %s)", (apid, f"t {apid}", status, closed))
    pg_conn.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES "
                    "('FND-9001', 'u', 'first'), ('FND-9001', 'u', 'second'), ('FND-9002', 'u', 'other')")
    return pg_conn


def _ids(rows):
    return [r["action_point_id"] for r in rows]


def _ev(f, t, typ, st, **kw):
    return {"from_id": f, "to_id": t, "link_type": typ, "status": st, "evidence": "e", "username": "u", **kw}


def test_links_store_presence_is_detected(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    assert bc.links_store_present(conn=pg_conn) is False        # DDL of 20_links.sql not applied yet
    mig.migrate(pg_conn)
    assert bc.links_store_present(conn=pg_conn) is True


def test_ticket_scopes(db):
    assert _ids(bc.list_tickets(conn=db)) == ["FND-9001", "FND-9002", "FND-9003", "FND-9004"]
    assert _ids(bc.list_tickets(open_only=True, conn=db)) == ["FND-9001", "FND-9002"]
    since = NOW - timedelta(days=30)
    assert _ids(bc.list_tickets(open_only=True, closed_since=since, conn=db)) == ["FND-9001", "FND-9002", "FND-9003"]


def test_ticket_ids_filter_and_missing_id_is_absent(db):
    got = bc.list_tickets(ids=["FND-9002", "FND-0000"], conn=db)
    assert _ids(got) == ["FND-9002"]
    assert got[0]["status"] == "IN_PROGRESS" and got[0]["update_ts"] is not None   # what the staleness pre-flight reads
    assert bc.list_tickets(ids=[], conn=db) == []


def test_logs_are_ordered_and_filterable(db):
    assert [r["comment"] for r in bc.list_logs(ids=["FND-9001"], conn=db)] == ["first", "second"]
    assert len(bc.list_logs(conn=db)) == 3


def test_event_roundtrip_current_state_and_history(db):
    e1 = bc.record_link_event(from_id="FND-9001", to_id="FND-9002", link_type="OVERLAPS", status="PROPOSED",
                              evidence="shared column", username="pytest", absorbs="criteria of 9002", conn=db)
    e2 = bc.record_link_event(from_id="FND-9001", to_id="FND-9002", link_type="OVERLAPS", status="CONFIRMED",
                              evidence="owner review", username="pytest", conn=db)
    assert e2 > e1
    cur = bc.list_links(conn=db)
    assert len(cur) == 1 and cur[0]["status"] == "CONFIRMED" and cur[0]["event_id"] == e2      # latest event wins
    hist = bc.list_links(history=True, conn=db)
    assert [h["status"] for h in hist] == ["PROPOSED", "CONFIRMED"] and hist[0]["absorbs"] == "criteria of 9002"
    assert bc.list_links(status="PROPOSED", conn=db) == []                                      # current state, not history


def test_links_ids_filter_matches_either_end(db):
    bc.record_link_events([_ev("FND-9001", "FND-9002", "DEPENDS_ON", "CONFIRMED"),
                           _ev("FND-9003", "FND-9004", "RELATED", "PROPOSED")], conn=db)
    assert {(r["from_id"], r["to_id"]) for r in bc.list_links(ids=["FND-9002"], conn=db)} == {("FND-9001", "FND-9002")}
    assert len(bc.list_links(ids=["FND-9001", "FND-9004"], conn=db)) == 2


def test_every_vocabulary_value_is_accepted_by_the_table(db):
    for typ in bc.LINK_TYPES:
        for st in bc.LINK_STATUSES:
            bc.record_link_event(from_id="FND-9001", to_id="FND-9002", link_type=typ, status=st, evidence="e",
                                 username="u", conn=db)
    assert len(bc.list_links(history=True, conn=db)) == len(bc.LINK_TYPES) * len(bc.LINK_STATUSES)


def test_database_error_propagates_and_the_caller_can_roll_the_batch_back(db):
    good = _ev("FND-9001", "FND-9002", "RELATED", "PROPOSED")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.transaction():                      # a savepoint standing in for the caller's transaction
            bc.record_link_events([good, _ev("FND-9001", "FND-0000", "RELATED", "PROPOSED")], conn=db)
    assert bc.list_links(history=True, conn=db) == []


def test_append_log_inserts_without_touching_the_ticket_status(db):
    before = bc.list_tickets(ids=["FND-9001"], conn=db)[0]
    log_id = bc.append_log("FND-9001", "pytest", "relations note", conn=db)
    assert isinstance(log_id, int)
    assert bc.list_logs(ids=["FND-9001"], conn=db)[-1]["comment"] == "relations note"
    assert bc.list_tickets(ids=["FND-9001"], conn=db)[0]["status"] == before["status"] == "OPEN"
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.transaction():
            bc.append_log("FND-0000", "pytest", "orphan", conn=db)
