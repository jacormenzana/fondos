# tests/test_backlog_links_ddl_pg.py
# -*- coding: utf-8 -*-
"""Relationship store of the action-point backlog (db/pg_backlog/20_links.sql, applied by scripts/ops/migrate_backlog_links.py).

Hermetic: loads db/pg_backlog/00_schema.sql + the migration's DDL block into the throwaway container inside a savepoint that is
always rolled back (pg_conn). Covers the CHECK vocabulary, the FK, the self-link guard, idempotence, the latest-event view with a
deterministic tie-break on event_ts (owner safeguard S-3) and blocked_by_open.

    python scripts/ops/run_pg_tests.py -- tests/test_backlog_links_ddl_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_backlog_links as mig  # noqa: E402

SCHEMA_SQL = (_ROOT / "db" / "pg_backlog" / "00_schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def db(pg_conn):
    pg_conn.execute(SCHEMA_SQL)
    mig.migrate(pg_conn)
    for i, status in ((1, "OPEN"), (2, "IN_PROGRESS"), (3, "CLOSED"), (4, "OPEN")):
        pg_conn.execute("INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, object_type, status, priority) "
                        "VALUES (%s, %s, 'FND', 'BUG', 'N/A', %s, 'LOW')", (f"FND-900{i}", f"t{i}", status))
    return pg_conn


def _event(conn, f, t, typ, status, ts=None):
    if ts is None:
        conn.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
                     "VALUES (%s, %s, %s, %s, 'test', 'pytest')", (f, t, typ, status))
    else:
        conn.execute("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, event_ts) "
                     "VALUES (%s, %s, %s, %s, 'test', 'pytest', %s)", (f, t, typ, status, ts))


def _rejects(conn, sql, params=()):
    with pytest.raises(psycopg.errors.Error):
        with conn.transaction():
            conn.execute(sql, params)


def test_migration_is_idempotent(db):
    mig.migrate(db)                                      # second run: no error, nothing duplicated
    assert mig.existing(db) == {"table": True, "views": ["v_backlog_link", "v_open_link_graph"]}


def test_vocabulary_self_link_and_fk_are_enforced(db):
    ins = ("INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username) "
           "VALUES (%s, %s, %s, %s, 'e', 'u')")
    _rejects(db, ins, ("FND-9001", "FND-9002", "BLOCKS", "CONFIRMED"))          # not in the vocabulary
    _rejects(db, ins, ("FND-9001", "FND-9002", "OVERLAPS", "DONE"))             # status not in the vocabulary
    _rejects(db, ins, ("FND-9001", "FND-9001", "RELATED", "PROPOSED"))          # self link
    _rejects(db, ins, ("FND-9001", "FND-0000", "RELATED", "PROPOSED"))          # unknown ticket


def test_latest_event_wins_with_a_deterministic_tie_break(db):
    _event(db, "FND-9001", "FND-9002", "OVERLAPS", "PROPOSED")
    _event(db, "FND-9001", "FND-9002", "OVERLAPS", "CONFIRMED")
    # same timestamp twice: the higher event_id decides (S-3)
    _event(db, "FND-9001", "FND-9004", "RELATED", "CONFIRMED", ts="2026-10-10 10:00:00+00")
    _event(db, "FND-9001", "FND-9004", "RELATED", "REJECTED", ts="2026-10-10 10:00:00+00")
    rows = dict(((r[0], r[1]), r[2]) for r in db.execute(
        "SELECT from_id, to_id, status FROM backlog.v_backlog_link").fetchall())
    assert rows[("FND-9001", "FND-9002")] == "CONFIRMED"
    assert rows[("FND-9001", "FND-9004")] == "REJECTED"
    assert db.execute("SELECT count(*) FROM backlog.backlog_link_event").fetchone()[0] == 4     # nothing overwritten


def test_open_graph_exposes_blocked_by_open_and_hides_rejected_and_closed_pairs(db):
    _event(db, "FND-9002", "FND-9001", "DEPENDS_ON", "CONFIRMED")     # prerequisite FND-9001 still OPEN -> blocked
    _event(db, "FND-9004", "FND-9003", "DEPENDS_ON", "CONFIRMED")     # prerequisite FND-9003 CLOSED -> not blocked
    _event(db, "FND-9001", "FND-9004", "OVERLAPS", "REJECTED")        # rejected: not in the graph
    g = {(r[0], r[1]): r[2] for r in db.execute(
        "SELECT from_id, to_id, blocked_by_open FROM backlog.v_open_link_graph").fetchall()}
    assert g == {("FND-9002", "FND-9001"): True, ("FND-9004", "FND-9003"): False}


def test_gestion_dsn_swaps_only_the_dbname():
    dsn = mig.gestion_dsn("host=127.0.0.1 port=5436 dbname=fondos user=fondos_owner password=secret")
    d = psycopg.conninfo.conninfo_to_dict(dsn)
    assert d["dbname"] == "gestion" and d["user"] == "fondos_owner" and d["port"] == "5436" and d["password"] == "secret"
