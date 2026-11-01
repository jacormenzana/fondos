# tests/test_backlog_clusters_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4b slice 2 (FND-0360): the MODULE clusters kept by the real runner and the real client on a real database, under the autopilot's real privileges (SET LOCAL SESSION AUTHORIZATION).
Hermetic PostgreSQL. Run with python scripts/ops/run_pg_tests.py."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_modules as run  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from test_backlog_transitions_pg import OWNER, _base, _mk  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "autonomy", "events", "modules")
SYS = "system:autopilot"
GROUP = "MOD.SHARED.BACKLOG_CLIENT"
SHA_A, SHA_B = "a" * 40, "b" * 40
FILES = ["shared/backlog_client.py", "AGENTS.md"]


def fake_git(*args):
    return "\n".join(FILES) if args[0] == "ls-files" else "shared/backlog_client.py\nAGENTS.md\n"


@pytest.fixture
def db(pg_conn, monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", SYS)
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    return pg_conn


class Shim:
    """The client as the runner calls it, bound to the test connection, so the privileges of the session are the real ones."""

    def __init__(self, conn, only):
        self.conn, self.only = conn, only

    def modules_store_present(self):
        return bc.modules_store_present(conn=self.conn)

    def list_tickets(self, open_only=False):
        return [t for t in bc.list_tickets(open_only=open_only, conn=self.conn) if t["action_point_id"] in self.only]

    def list_logs(self, ids=None):
        return bc.list_logs(ids=ids, conn=self.conn)

    def ops_event_keys(self, kind):
        return bc.ops_event_keys(kind, conn=self.conn)

    def list_ticket_modules(self):
        return bc.list_ticket_modules(conn=self.conn)

    def record_modules(self, facts, actor=None):
        return bc.record_modules(facts, actor=actor, conn=self.conn)

    def list_module_group_events(self):
        return bc.list_module_group_events(conn=self.conn)

    def create_group(self, name, kind, description, actor=None):
        return bc.create_group(name, kind, description, actor=actor, conn=self.conn)

    def group_member(self, group, ap_id, op, reason, actor=None):
        return bc.group_member(group, ap_id, op, reason, actor=actor, conn=self.conn)

    def list_links(self):
        return bc.list_links(conn=self.conn)


def commit_fact(conn, ap, sha):
    conn.execute("SELECT backlog.sp_record_ops_event('COMMIT', %s, %s, 'REFS', %s::jsonb, %s, 'system:sync')", (sha, ap, json.dumps({"subject": "work"}), datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)))


def members(conn):
    return sorted(conn.execute("SELECT m.action_point_id FROM backlog.v_group_member m JOIN backlog.group_tag g USING (group_id) WHERE g.name = %s", (GROUP,)).fetchall())


def two_tickets(db):
    a, b = _mk(db, "IN_PROGRESS"), _mk(db, "IN_PROGRESS")
    commit_fact(db, a, SHA_A)
    commit_fact(db, b, SHA_B)
    return a, b, Shim(db, {a, b})


def keep(db, shim, mode="ON", apply=True):
    db.execute("SET LOCAL SESSION AUTHORIZATION backlog_autopilot")
    try:
        return run.run(shim, apply=apply, git=fake_git, out=lambda *_: None, clusters=True, mode=mode)
    finally:
        db.execute("RESET SESSION AUTHORIZATION")


def test_the_autopilot_derives_the_facts_and_keeps_the_group_through_its_own_privileges_and_a_rerun_writes_nothing(db):
    a, b, shim = two_tickets(db)
    first = keep(db, shim)
    assert first["recorded"] == 2 and first["clusters"]["active"] and first["clusters"]["written"] == 3 and first["clusters"]["refused"] == 0        # 2 TOUCHED facts; 1 group created, 2 members added
    assert members(db) == sorted([(a,), (b,)])
    assert db.execute("SELECT kind FROM backlog.group_tag WHERE name = %s", (GROUP,)).fetchone()[0] == "MODULE"
    assert {r[0] for r in db.execute("SELECT DISTINCT actor FROM backlog.group_member").fetchall()} == {SYS}
    again = keep(db, shim)
    assert again["clusters"]["planned"] == 0 and again["clusters"]["written"] == 0 and len(members(db)) == 2


def test_a_removal_by_a_person_stands_and_a_rerun_does_not_add_the_ticket_again(db):
    a, b, shim = two_tickets(db)
    keep(db, shim)
    db.execute("SELECT backlog.sp_group_member(%s, %s, 'REMOVE', 'not related to this module in practice', %s)", (GROUP, b, OWNER))
    assert members(db) == [(a,)]
    res = keep(db, shim)
    assert res["clusters"]["planned"] == 0 and members(db) == [(a,)]


def test_a_dry_run_and_the_modes_off_and_auto_without_proof_write_no_group(db, monkeypatch):
    a, b, shim = two_tickets(db)
    keep(db, shim, apply=False)
    keep(db, shim, mode="OFF")
    monkeypatch.setattr(run, "proof", lambda *x, **k: {"sample": 5, "precision": 1.0, "recall": 1.0, "recall_text": 0.0, "positives": 5})
    res = keep(db, shim, mode="AUTO")
    assert res["clusters"]["active"] is False and "unproven" in res["clusters"]["why"]
    assert db.execute("SELECT count(*) FROM backlog.group_tag WHERE kind = 'MODULE'").fetchone()[0] == 0


def test_the_cluster_step_reads_the_groups_with_the_autopilots_select_only(db):
    a, b, shim = two_tickets(db)
    keep(db, shim)
    db.execute("SET LOCAL SESSION AUTHORIZATION backlog_autopilot")
    try:
        rows = bc.list_module_group_events(conn=db)
    finally:
        db.execute("RESET SESSION AUTHORIZATION")
    assert [(r["name"], r["op"]) for r in rows] == [(GROUP, "ADD"), (GROUP, "ADD")] and {r["action_point_id"] for r in rows} == {a, b}
