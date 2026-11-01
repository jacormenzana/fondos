# tests/test_backlog_modules_pg.py
# -*- coding: utf-8 -*-
"""Backlog v3 W4b (FND-0360), phase `modules` (db/pg_backlog/99_modules.sql): the append-only table of which module each ticket targets, touched or cites, its current-set view, its one door
(sp_record_module) and the MODULE group kind. Hermetic PostgreSQL; the privileges are the real ones (SET LOCAL SESSION AUTHORIZATION). Run with python scripts/ops/run_pg_tests.py."""
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
from test_backlog_transitions_pg import ME, OWNER, _base, _mk, refused  # noqa: E402

PHASES = ("relations", "groups", "sync", "hook", "collision", "autonomy", "events", "modules")
SYS = "system:autopilot"
M = "shared/backlog_client.py"


@pytest.fixture
def db(pg_conn):
    _base(pg_conn)
    for phase in PHASES:
        mig.migrate(pg_conn, phase)
    return pg_conn


def as_role(conn, role):
    conn.execute(f"SET LOCAL SESSION AUTHORIZATION {role}")


def as_owner(conn):
    conn.execute("RESET SESSION AUTHORIZATION")


def rec(conn, ap, path=M, symbol=None, role="TOUCHED", source="COMMIT", evidence="commit abc123", actor=SYS, retracted=False):
    return conn.execute("SELECT backlog.sp_record_module(%s, %s, %s, %s, %s, %s, %s, %s)", (ap, path, symbol, role, source, evidence, actor, retracted)).fetchone()[0]


def current(conn, ap=None):
    sql = "SELECT action_point_id, module_path, symbol, role, source FROM backlog.v_ticket_module" + (" WHERE action_point_id = %s" if ap else "") + " ORDER BY 1, 2, 3, 4, 5"
    return conn.execute(sql, (ap,) if ap else ()).fetchall()


# ---------------------------------------------------------------- the phase

def test_the_phase_creates_its_objects_is_idempotent_and_widens_the_group_kind(db):
    assert mig.existing(db, "modules")["tables"] == ["ticket_module_event"] and mig.existing(db, "modules")["views"] == ["v_ticket_module"]
    assert mig.existing(db, "modules")["routines"] == ["sp_record_module"]
    mig.migrate(db, "modules")                                                                  # applying it again changes nothing
    assert db.execute("SELECT count(*) FROM pg_constraint WHERE conrelid = 'backlog.group_tag'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%MODULE%'").fetchone()[0] == 1
    db.execute("SELECT backlog.sp_create_group('MOD.SHARED.BACKLOG_CLIENT', 'MODULE', 'a module cluster', %s)", (OWNER,))
    refused(db, "group_tag_kind_check", lambda c: c.execute("SELECT backlog.sp_create_group('X', 'BOGUS', 'no such kind', %s)", (OWNER,)))


def test_the_table_is_append_only_and_checks_the_shape_of_every_row(db):
    ap = _mk(db)
    assert rec(db, ap) > 0
    refused(db, "append-only|append_only", lambda c: c.execute("UPDATE backlog.ticket_module_event SET evidence = 'x'"))
    refused(db, "append-only|append_only", lambda c: c.execute("DELETE FROM backlog.ticket_module_event"))
    for bad in ("../etc/passwd", "/abs/path.py", "a b.py", "a\\b.py", "a//b.py", "x" * 301):
        refused(db, "ticket_module_event|check", lambda c, b=bad: rec(c, ap, path=b))
    refused(db, "check", lambda c: rec(c, ap, symbol="not a symbol"))
    refused(db, "check", lambda c: rec(c, ap, role="TARGET", source="COMMIT"))                  # a commit makes TOUCHED, nothing else
    refused(db, "check", lambda c: rec(c, ap, role="TOUCHED", source="TEXT"))
    refused(db, "check", lambda c: rec(c, ap, role="TARGET", source="TEXT", actor=SYS))


# ---------------------------------------------------------------- the door

def test_a_derived_fact_is_a_system_actors_and_a_declaration_is_an_agents_or_a_persons(db):
    ap = _mk(db)
    assert rec(db, ap, role="CITED", source="TEXT", evidence="named in the title") > 0 and rec(db, ap, role="CITED", source="OBJECT_NAME", path="proyecto1/core/pipeline.py") > 0
    assert rec(db, ap, role="TARGET", source="DECLARED", actor=ME, symbol="parse_rows", evidence="the parser is the cause") > 0
    assert rec(db, ap, path="scripts/audit/backlog_links.py", role="CITED", source="DECLARED", actor=OWNER) > 0
    refused(db, "derived", lambda c: rec(c, ap, role="TOUCHED", source="COMMIT", actor=ME))                    # an agent does not claim what git says
    refused(db, "derived", lambda c: rec(c, ap, role="CITED", source="TEXT", actor=OWNER))
    refused(db, "DECLARED is what", lambda c: rec(c, ap, role="TARGET", source="DECLARED", actor=SYS))        # and the system does not declare intent
    refused(db, "does not exist", lambda c: rec(c, "FND-0", actor=SYS))
    refused(db, "not canonical", lambda c: rec(c, ap, actor="claude"))


def test_the_same_fact_is_not_written_twice_and_a_symbol_is_a_different_fact(db):
    ap = _mk(db)
    assert rec(db, ap) > 0 and rec(db, ap) is None and rec(db, ap, evidence="another commit, same file") is None
    assert rec(db, ap, symbol="record_ops_event", role="TARGET", source="DECLARED", actor=ME) > 0
    assert rec(db, ap, symbol="record_link_event", role="TARGET", source="DECLARED", actor=ME) > 0
    assert db.execute("SELECT count(*) FROM backlog.ticket_module_event WHERE action_point_id = %s", (ap,)).fetchone()[0] == 3


def test_a_retraction_removes_the_fact_from_the_current_set_and_a_rejected_proposal_stays_rejected(db):
    ap = _mk(db)
    rec(db, ap, role="CITED", source="TEXT", evidence="named in a log")
    assert current(db, ap) == [(ap, M, None, "CITED", "TEXT")]
    assert rec(db, ap, role="CITED", source="TEXT", actor=ME, retracted=True, evidence="a tool the ticket only mentions") > 0          # an agent rejects the proposal
    assert current(db, ap) == []
    assert rec(db, ap, role="CITED", source="TEXT") is None                                                                       # the derivation does not propose it again
    assert current(db, ap) == []
    assert rec(db, ap, role="CITED", source="TEXT", retracted=True) is None                                                      # nothing current to retract
    # a retraction by the system itself (the file moved) can be undone by the system
    rec(db, ap, path="scripts/ops/x.py")
    assert rec(db, ap, path="scripts/ops/x.py", retracted=True) > 0 and current(db, ap) == []
    assert rec(db, ap, path="scripts/ops/x.py") > 0 and current(db, ap) == [(ap, "scripts/ops/x.py", None, "TOUCHED", "COMMIT")]


def test_the_current_set_is_the_latest_state_of_each_ticket_module_symbol_role_and_source(db):
    a, b = _mk(db), _mk(db)
    rec(db, a)
    rec(db, b)
    rec(db, b, path="proyecto1/core/pipeline.py")
    rec(db, a, role="TARGET", source="DECLARED", actor=ME)
    assert current(db) == sorted(current(db)) and len(current(db)) == 4
    assert {(r[0], r[3]) for r in current(db) if r[1] == M} == {(a, "TOUCHED"), (a, "TARGET"), (b, "TOUCHED")}
    assert db.execute("SELECT count(DISTINCT action_point_id) FROM backlog.v_ticket_module WHERE module_path = %s", (M,)).fetchone()[0] == 2


# ---------------------------------------------------------------- privileges are the real ones

def test_the_autopilot_derives_and_keeps_the_clusters_the_agent_declares_and_neither_writes_a_table(db):
    ap = _mk(db)
    as_role(db, "backlog_autopilot")
    assert rec(db, ap, actor=SYS) > 0
    db.execute("SELECT backlog.sp_create_group('MOD.SHARED.BACKLOG_CLIENT', 'MODULE', 'tickets that touch shared/backlog_client.py', %s)", (SYS,))
    assert db.execute("SELECT backlog.sp_group_member('MOD.SHARED.BACKLOG_CLIENT', %s, 'ADD', 'touches the module in commit abc123', %s)", (ap, SYS)).fetchone()[0] > 0
    refused(db, "permission denied", lambda c: c.execute("INSERT INTO backlog.ticket_module_event (action_point_id, module_path, role, source, actor) VALUES (%s, 'a.py', 'TOUCHED', 'COMMIT', 'system:x')", (ap,)))
    as_owner(db)
    as_role(db, "backlog_agent")
    assert rec(db, ap, path="proyecto1/core/pipeline.py", role="TARGET", source="DECLARED", actor=ME) > 0
    assert db.execute("SELECT count(*) FROM backlog.v_ticket_module WHERE action_point_id = %s", (ap,)).fetchone()[0] == 2
    refused(db, "permission denied", lambda c: c.execute("DELETE FROM backlog.ticket_module_event"))
    as_owner(db)


def test_nobody_else_can_call_the_door(db):
    ap = _mk(db)
    for role in ("backlog_facts",):
        as_role(db, role)
        refused(db, "permission denied", lambda c: rec(c, ap))
        as_owner(db)
    assert db.execute("SELECT has_function_privilege('backlog_facts', 'backlog.sp_record_module(text,text,text,text,text,text,text,boolean)', 'EXECUTE')").fetchone()[0] is False
    assert db.execute("SELECT has_function_privilege('public', 'backlog.sp_record_module(text,text,text,text,text,text,text,boolean)', 'EXECUTE')").fetchone()[0] is False


# ---------------------------------------------------------------- rollback

def test_the_rollback_drops_the_door_and_the_view_keeps_the_history_and_the_phase_applies_again(db):
    ap = _mk(db)
    rec(db, ap)
    mig.migrate(db, "modules", down=True)
    assert mig.existing(db, "modules")["routines"] == [] and mig.existing(db, "modules")["views"] == []
    assert db.execute("SELECT count(*) FROM backlog.ticket_module_event").fetchone()[0] == 1                 # the history stays
    assert db.execute("SELECT count(*) FROM pg_constraint WHERE conrelid = 'backlog.group_tag'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%MODULE%'").fetchone()[0] == 1
    mig.migrate(db, "modules")
    assert current(db, ap) == [(ap, M, None, "TOUCHED", "COMMIT")] and rec(db, ap) is None


# ---------------------------------------------------------------- the real runner, the real client and the real database, as the autopilot

import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

for _q in (str(_ROOT / "scripts" / "audit"),):
    if _q not in sys.path:
        sys.path.insert(0, _q)
import backlog_modules as run  # noqa: E402
from shared import backlog_client as bc  # noqa: E402

FILES = ["shared/backlog_client.py", "scripts/audit/audit_incident.py", "proyecto1/core/pipeline.py", "tests/test_backlog_client.py", "AGENTS.md", "doc/reglas/NORMAS_BACKLOG.md"]
SHA_A, SHA_B = "a" * 40, "b" * 40
COMMIT_FILES = {SHA_A: "shared/backlog_client.py\nAGENTS.md\ntests/test_backlog_client.py\n", SHA_B: "shared/backlog_client.py\nproyecto1/core/pipeline.py\ndoc/reglas/NORMAS_BACKLOG.md\n"}


def fake_git(*args):
    return "\n".join(FILES) if args[0] == "ls-files" else COMMIT_FILES[args[-1]]


class Shim:
    """The client as the runner calls it, bound to the test connection (so the privileges of SET LOCAL SESSION AUTHORIZATION are the real ones)."""

    def __init__(self, conn):
        self.conn = conn

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


def commit_fact(conn, ap, sha):
    conn.execute("SELECT backlog.sp_record_ops_event('COMMIT', %s, %s, 'REFS', %s::jsonb, %s, 'system:sync')", (sha, ap, json.dumps({"subject": "work"}), datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)))


def test_the_runner_records_touched_and_cited_facts_through_the_autopilots_privileges_and_a_rerun_records_nothing(db, monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", SYS)
    a, b = _mk(db, "IN_PROGRESS"), _mk(db, "IN_PROGRESS")
    commit_fact(db, a, SHA_A)
    commit_fact(db, b, SHA_B)
    shim = Shim(db)
    shim.only = {a, b}
    as_role(db, "backlog_autopilot")
    first = run.run(shim, apply=True, git=fake_git, out=lambda *_: None)
    assert first["present"] and first["new"] == first["recorded"] == 3 and first["unchanged"] == 0
    as_owner(db)
    got = {(r[0], r[1], r[2]) for r in db.execute("SELECT action_point_id, module_path, role FROM backlog.v_ticket_module").fetchall()}
    assert got == {(a, "shared/backlog_client.py", "TOUCHED"), (b, "shared/backlog_client.py", "TOUCHED"), (b, "proyecto1/core/pipeline.py", "TOUCHED")}        # tests fold in, docs and registries are left out
    assert {r[0] for r in db.execute("SELECT DISTINCT actor FROM backlog.ticket_module_event").fetchall()} == {SYS}
    as_role(db, "backlog_autopilot")
    again = run.run(shim, apply=True, git=fake_git, out=lambda *_: None)
    assert again["new"] == 0 and again["recorded"] == 0                                                           # idempotent: nothing is sent twice
    as_owner(db)


def test_an_agents_rejection_of_a_proposal_is_never_undone_by_the_derivation(db, monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", SYS)
    a = _mk(db, "IN_PROGRESS")
    db.execute("INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (%s, 'agent:claude-opus-5-5', 'checked scripts/audit/audit_incident.py: only mentioned, not the cause')", (a,))
    shim = Shim(db)
    shim.only = {a}
    as_role(db, "backlog_autopilot")
    run.run(shim, apply=True, git=fake_git, out=lambda *_: None)
    as_owner(db)
    assert current(db, a) == [(a, "scripts/audit/audit_incident.py", None, "CITED", "TEXT")]
    monkeypatch.setenv("FONDOS_ACTOR", ME)
    as_role(db, "backlog_agent")
    assert bc.record_modules([{"ap_id": a, "module": "scripts/audit/audit_incident.py", "role": "CITED", "source": "TEXT", "evidence": "a tool the log only mentions", "retracted": True}], actor=ME, conn=db)["recorded"] == 1
    as_owner(db)
    assert current(db, a) == []
    monkeypatch.setenv("FONDOS_ACTOR", SYS)
    as_role(db, "backlog_autopilot")
    res = run.run(shim, apply=True, git=fake_git, out=lambda *_: None)
    as_owner(db)
    assert res["new"] == 1 and res["recorded"] == 0 and res["unchanged"] == 1 and current(db, a) == []                # sent again, refused by the database: the rejection stands


def test_record_modules_checks_role_and_source_before_it_connects_and_routes_by_actor(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", SYS)
    with pytest.raises(ValueError, match="role must be"):
        bc.record_modules([{"ap_id": "FND-1", "module": "a.py", "role": "OWNER", "source": "TEXT"}], actor=SYS, conn=object())
    seen = {}

    class Conn:
        def execute(self, sql, params=()):
            seen["params"] = params

            class Cur:
                def fetchone(self):
                    return (7,)
            return Cur()

    from contextlib import contextmanager

    @contextmanager
    def fake_session(conn=None, **kw):
        seen["kw"] = kw
        yield Conn()

    monkeypatch.setattr(bc, "_session", fake_session)
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN_AUTOPILOT", "postgresql://backlog_autopilot@127.0.0.1:1/gestion")
    out = bc.record_modules([{"ap_id": "FND-1", "module": "a.py", "role": "TOUCHED", "source": "COMMIT", "evidence": "commit abc"}], actor=SYS)
    assert out == {"recorded": 1, "unchanged": 0} and seen["kw"]["operation"] == "autopilot" and seen["kw"]["write"] is True and seen["params"][6] == SYS
