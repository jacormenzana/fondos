# tests/test_backlog_agent_route.py
# -*- coding: utf-8 -*-
"""Backlog v3 W2 (FND-0357), the client half: an agent:<model> identity with its scoped login configured (FONDOS_BACKLOG_PG_DSN_AGENT) writes through the agent routines and needs no terminal;
everything else keeps the console path. No database, no network: the connection is a fake that records the SQL (R-7)."""
from __future__ import annotations

import inspect
import re

import pytest

from shared import backlog_client as bc

AGENT, HUMAN, SYSTEM = "agent:claude-sonnet-5-5", "human:jacor", "system:sync"
AGENT_DSN = "postgresql://backlog_agent@127.0.0.1:1/gestion"


class Cur:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, row=(7,)):
        self.calls, self.row = [], row

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return Cur(self.row)

    def sqls(self):
        return " | ".join(re.sub(r"\s+", " ", s) for s, _ in self.calls)


@pytest.fixture
def agent_env(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", AGENT)
    monkeypatch.setenv(bc._AGENT_DSN_ENV_VAR, AGENT_DSN)
    return monkeypatch


# ---------------------------------------------------------------- the route and its DSN

def test_the_agent_operation_is_allow_listed_and_has_its_own_login_with_no_fallback(monkeypatch):
    assert "agent" in bc.NON_INTERACTIVE_OPERATIONS
    monkeypatch.setenv(bc._DSN_ENV_VAR, "postgresql://backlog_writer@127.0.0.1:1/gestion")
    monkeypatch.delenv(bc._AGENT_DSN_ENV_VAR, raising=False)
    assert bc.dsn_for_operation("agent") is None                       # the general writer is NEVER used for it: without its own login there is no live write
    monkeypatch.setenv(bc._AGENT_DSN_ENV_VAR, AGENT_DSN)
    assert bc.dsn_for_operation("agent") == AGENT_DSN


def test_only_an_agent_identity_with_the_login_configured_and_the_switch_on_takes_the_route(monkeypatch):
    monkeypatch.delenv(bc._AGENT_DSN_ENV_VAR, raising=False)
    assert bc.agent_operation(AGENT) is None                           # no DSN: the console path, exactly as before
    monkeypatch.setenv(bc._AGENT_DSN_ENV_VAR, AGENT_DSN)
    assert bc.agent_operation(AGENT) == "agent"
    assert bc.agent_operation(HUMAN) is None and bc.agent_operation(SYSTEM) is None
    from shared import config
    monkeypatch.setattr(config, "BACKLOG_AGENT_WRITES_ENABLED", False)
    assert bc.agent_operation(AGENT) is None                           # the kill-switch


def test_a_missing_agent_dsn_names_the_variable_that_is_missing(monkeypatch):
    monkeypatch.delenv(bc._AGENT_DSN_ENV_VAR, raising=False)
    monkeypatch.setattr(bc, "confirm_live_write", lambda *a, **k: True)
    with pytest.raises(bc.BacklogUnavailable, match="FONDOS_BACKLOG_PG_DSN_AGENT"):
        with bc._session(None, write=True, operation="agent"):
            pass


# ---------------------------------------------------------------- transitions

def test_an_agent_moves_a_ticket_through_sp_agent_transition_which_has_no_force_argument(agent_env):
    conn = FakeConn((11,))
    assert bc.transition("FND-0001", "IN_PROGRESS", session_id="FND_20261031_DEV_TEST", comment="work started on it", conn=conn) == 11
    sql = conn.sqls()
    assert "sp_agent_transition" in sql and "p_force" not in sql and "sp_transition(" not in sql
    assert conn.calls[0][1][2] == AGENT                                # the actor is the canonical identity of the environment


def test_an_agent_that_asks_to_force_is_refused_before_any_connection(agent_env):
    conn = FakeConn()
    with pytest.raises(ValueError, match="cannot force"):
        bc.transition("FND-0001", "CLOSED", session_id="FND_20261031_DEV_TEST", comment="x" * 50, force=True, conn=conn)
    assert conn.calls == []


def test_without_the_agent_login_the_transition_is_the_old_one_and_a_person_may_still_force(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", HUMAN)
    monkeypatch.setenv(bc._AGENT_DSN_ENV_VAR, AGENT_DSN)               # present, but the identity is human: it does not route
    conn = FakeConn((3,))
    bc.transition("FND-0001", "IN_PROGRESS", session_id="FND_20261031_DEV_TEST", comment="a person forces it " * 3, force=True, conn=conn)
    assert "sp_transition(" in conn.sqls() and "p_force" in conn.sqls() and "sp_agent_transition" not in conn.sqls()


# ---------------------------------------------------------------- links, tickets and the other helpers

def _event(username, status="PROPOSED"):
    return dict(from_id="FND-0001", to_id="FND-0002", link_type="RELATED", status=status, evidence="named in the text of both tickets, read by the agent", username=username)


def test_an_agents_link_events_go_through_sp_record_link_and_a_persons_keep_the_direct_insert(agent_env):
    conn = FakeConn((5,))
    assert bc.record_link_events([_event(AGENT)], conn=conn) == [5]
    assert "sp_record_link" in conn.sqls() and "INSERT INTO" not in conn.sqls() and len(conn.calls[0][1]) == 8
    conn2 = FakeConn((6,))
    assert bc.record_link_events([_event(HUMAN, "CONFIRMED")], conn=conn2) == [6]
    assert "INSERT INTO backlog.backlog_link_event" in conn2.sqls() and "sp_record_link" not in conn2.sqls()


def test_a_mixed_batch_is_not_routed_to_the_agent_login(agent_env):
    conn = FakeConn((8,))
    bc.record_link_events([_event(AGENT), _event(HUMAN, "CONFIRMED")], conn=conn)
    assert "sp_record_link" not in conn.sqls()


def test_an_agent_opens_a_ticket_through_sp_open_ticket(agent_env):
    conn = FakeConn(("FND-0900",))
    out = bc.open_ticket(title="a ticket", nature="TSK", object_type="N/A", priority="LOW", session_id="FND_20261031_DEV_TEST", description="no other ticket named here",
                         first_log="opened by the test", conn=conn)
    assert str(out) == "FND-0900" and "sp_open_ticket" in conn.sqls() and out.mentions == ()


def test_the_four_group_and_close_condition_helpers_are_writes_and_pass_the_live_write_gate():
    """They called _session without write=True, which skipped the live-write gate of the owner's 2026-10-29 order; each is now a write and routes by actor kind."""
    for name in ("create_group", "group_member", "add_close_condition", "retire_close_condition", "annotate_wait", "open_ticket", "transition"):
        src = inspect.getsource(getattr(bc, name))
        assert "write=True" in src and "operation=agent_operation(who)" in src.replace("op,", "agent_operation(who),") or "operation=op" in src, name


# ---------------------------------------------------------------- Backlog v3 W3 (FND-0358): the autopilot's own login and its one move

AUTOPILOT = "system:autopilot"
AUTOPILOT_DSN = "postgresql://backlog_autopilot@127.0.0.1:1/gestion"


def test_the_autopilot_operation_is_allow_listed_with_its_own_dsn_and_no_fallback(monkeypatch):
    assert "autopilot" in bc.NON_INTERACTIVE_OPERATIONS
    monkeypatch.setenv(bc._DSN_ENV_VAR, "postgresql://backlog_writer@127.0.0.1:1/gestion")
    monkeypatch.delenv(bc._AUTOPILOT_DSN_ENV_VAR, raising=False)
    assert bc.dsn_for_operation("autopilot") is None                   # never the general writer
    monkeypatch.setenv(bc._AUTOPILOT_DSN_ENV_VAR, AUTOPILOT_DSN)
    assert bc.dsn_for_operation("autopilot") == AUTOPILOT_DSN


def test_only_system_autopilot_with_its_login_configured_takes_the_autopilot_route(monkeypatch):
    monkeypatch.delenv(bc._AUTOPILOT_DSN_ENV_VAR, raising=False)
    assert bc.autopilot_operation(AUTOPILOT) is None
    monkeypatch.setenv(bc._AUTOPILOT_DSN_ENV_VAR, AUTOPILOT_DSN)
    assert bc.autopilot_operation(AUTOPILOT) == "autopilot"
    assert bc.autopilot_operation(AGENT) is None and bc.autopilot_operation(SYSTEM) is None and bc.autopilot_operation(HUMAN) is None


def test_the_fact_kinds_of_the_autopilot_are_known_to_the_client():
    assert {"MERGE", "DEPLOY", "CYCLE"} <= set(bc.OPS_KINDS)


def test_the_autopilot_cannot_use_the_general_transition(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", AUTOPILOT)
    conn = FakeConn()
    with pytest.raises(ValueError, match="only through auto_transition"):
        bc.transition("FND-0001", "IN_PROGRESS", session_id="FND_20261101_SYNC_TEST", comment="x" * 50, conn=conn)
    assert conn.calls == []


def test_auto_transition_calls_the_routine_with_a_merge_as_evidence_and_only_as_the_autopilot(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", AUTOPILOT)
    conn = FakeConn((21,))
    out = bc.auto_transition("FND-0001", "CLOSED", evidence_ref=SHA40, session_id="FND_20261101_SYNC_TEST", comment="closed on evidence", solution_type="CODE_FIX",
                             release_version="R2026.11-PR17", conn=conn)
    assert out == 21 and "sp_auto_transition" in conn.sqls() and "p_force" not in conn.sqls()
    assert conn.calls[0][1] == ("FND-0001", "CLOSED", "MERGE", SHA40, "FND_20261101_SYNC_TEST", "closed on evidence", "CODE_FIX", "R2026.11-PR17")      # MERGE is the default evidence
    conn2 = FakeConn((22,))
    bc.auto_transition("FND-0002", "IN_PROGRESS", evidence_ref="FND-0001", evidence_kind="BLOCKER", session_id="FND_20261101_SYNC_TEST", comment="blocker closed", conn=conn2)
    assert conn2.calls[0][1][:4] == ("FND-0002", "IN_PROGRESS", "BLOCKER", "FND-0001")
    with pytest.raises(ValueError, match="evidence_kind"):
        bc.auto_transition("FND-0002", "IN_PROGRESS", evidence_ref="x", evidence_kind="HUNCH", session_id="FND_20261101_SYNC_TEST", comment="x", conn=FakeConn())
    monkeypatch.setenv("FONDOS_ACTOR", AGENT)
    with pytest.raises(bc.ActorUnknown, match="auto_transition is the autopilot's"):
        bc.auto_transition("FND-0001", "CLOSED", evidence_ref=SHA40, session_id="FND_20261101_SYNC_TEST", comment="x", conn=FakeConn())


SHA40 = "0123456789abcdef0123456789abcdef01234567"


class _Session:
    """Replaces backlog_client._session to record which operation each write asks for, and hands back a fake connection."""

    def __init__(self, monkeypatch, conn):
        self.calls = []
        from contextlib import contextmanager

        @contextmanager
        def fake(c=None, **kw):
            self.calls.append(kw)
            yield conn
        monkeypatch.setattr(bc, "_session", fake)


def test_the_autopilot_records_facts_and_notes_through_its_own_login(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("FONDOS_ACTOR", AUTOPILOT)
    monkeypatch.setenv(bc._AUTOPILOT_DSN_ENV_VAR, AUTOPILOT_DSN)
    seen = _Session(monkeypatch, FakeConn((True,)))
    assert bc.record_ops_event("MERGE", SHA40, "FND-0001", "REFS", {"paths": ["a.py"]}, datetime(2026, 11, 1, tzinfo=timezone.utc)) is True
    assert seen.calls[-1]["operation"] == "autopilot" and seen.calls[-1]["write"] is True


def test_an_ordinary_actor_recording_a_fact_keeps_the_console_path_but_now_asks_for_a_write_session(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("FONDOS_ACTOR", SYSTEM)
    monkeypatch.setenv(bc._AUTOPILOT_DSN_ENV_VAR, AUTOPILOT_DSN)
    seen = _Session(monkeypatch, FakeConn((True,)))
    bc.record_ops_event("COMMIT", SHA40, "FND-0001", "REFS", {}, datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert seen.calls[-1]["operation"] is None and seen.calls[-1]["write"] is True


# ---------------------------------------------------------------- Backlog v3.1 (FND-0355): the event functions route by actor and check their arguments before any connection

SESSION = "FND_20261101_SYNC_TEST"


def test_reopen_routes_an_agent_through_its_login_a_person_through_the_owner_only_one_and_records_the_cause(agent_env):
    conn = FakeConn((31,))
    seen = _Session(agent_env, conn)
    assert bc.reopen("FND-0001", "COVERAGE_GAP", "a reason long enough to be a reason", session_id=SESSION) == 31
    assert seen.calls[-1]["operation"] == "agent" and seen.calls[-1]["write"] is True and seen.calls[-1]["human"] is False
    assert "sp_reopen" in conn.sqls() and "p_force" not in conn.sqls() and conn.calls[0][1] == ("FND-0001", "COVERAGE_GAP", "a reason long enough to be a reason", AGENT, SESSION, "IN_PROGRESS")
    agent_env.setenv("FONDOS_ACTOR", "human:owner")
    bc.reopen("FND-0001", "SCOPE_CHANGE", "x" * 40, session_id=SESSION)
    assert seen.calls[-1]["human"] is True and seen.calls[-1]["operation"] is None


def test_reopen_refuses_an_unknown_cause_before_any_connection(agent_env):
    conn = FakeConn()
    with pytest.raises(ValueError, match="cause"):
        bc.reopen("FND-0001", "BECAUSE", "x" * 40, session_id=SESSION, conn=conn)
    assert conn.calls == []


def test_the_autopilot_reopens_and_sets_its_mode_through_its_own_login(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", AUTOPILOT)
    monkeypatch.setenv(bc._AUTOPILOT_DSN_ENV_VAR, AUTOPILOT_DSN)
    seen = _Session(monkeypatch, FakeConn((5,)))
    bc.reopen("FND-0001", "INCOMPLETE_FIX", "a later merge changed code", session_id=SESSION)
    assert seen.calls[-1]["operation"] == "autopilot"
    bc.set_autopilot_mode("ON", "20 decisions, none contradicted")
    assert seen.calls[-1]["operation"] == "autopilot" and seen.calls[-1]["write"] is True
    bc.auto_release_holds()
    assert seen.calls[-1]["operation"] == "autopilot"
    with pytest.raises(ValueError, match="mode"):
        bc.set_autopilot_mode("MAYBE", "x")


def test_an_agent_adjudication_needs_an_agent_identity_and_never_a_revert(agent_env):
    conn = FakeConn((9,))
    seen = _Session(agent_env, conn)
    for bad in ("REVERT", "WHATEVER"):
        with pytest.raises(ValueError, match="decision"):
            bc.agent_adjudicate_overlap("FND-0001", "FND-0002", bad, "y" * 40)
    assert conn.calls == [] and seen.calls == []
    assert bc.agent_adjudicate_overlap("FND-0001", "FND-0002", "RELATED", "y" * 40) == 9 and seen.calls[-1]["operation"] == "agent"
    assert "sp_agent_adjudicate_overlap" in conn.sqls() and conn.calls[0][1] == ("FND-0001", "FND-0002", "RELATED", None, "y" * 40, AGENT)
    agent_env.setenv("FONDOS_ACTOR", "human:owner")
    with pytest.raises(ValueError, match="agent"):
        bc.agent_adjudicate_overlap("FND-0001", "FND-0002", "RELATED", "y" * 40)


def test_rescind_and_mode_reads_call_their_routines(agent_env):
    conn = FakeConn((11,))
    assert bc.rescind_reopen("FND-0001", session_id=SESSION, conn=conn) == 11 and "sp_rescind_reopen" in conn.sqls()
    assert conn.calls[0][1] == ("FND-0001", AGENT, SESSION)
    assert bc.autopilot_mode(conn=FakeConn(("SHADOW",))) == "SHADOW"


# ---------------------------------------------------------------- Backlog v3.1: the DEPLOY fact goes through its own login, with no fallback

def test_the_facts_operation_is_allow_listed_and_uses_its_own_string_exclusively(monkeypatch):
    assert "facts" in bc.NON_INTERACTIVE_OPERATIONS and bc._SCOPED_DSN_ENV["facts"] == "FONDOS_BACKLOG_PG_DSN_FACTS"
    monkeypatch.setenv(bc._DSN_ENV_VAR, "postgresql://backlog_writer@127.0.0.1:1/gestion")
    monkeypatch.setenv(bc._AUTOPILOT_DSN_ENV_VAR, AUTOPILOT_DSN)
    monkeypatch.delenv(bc._FACTS_DSN_ENV_VAR, raising=False)
    assert bc.dsn_for_operation("facts") is None                                    # neither the general writer nor the autopilot's login can stand in for it
    monkeypatch.setenv(bc._FACTS_DSN_ENV_VAR, "postgresql://backlog_facts@127.0.0.1:1/gestion")
    assert bc.dsn_for_operation("facts") == "postgresql://backlog_facts@127.0.0.1:1/gestion"


def test_record_deploy_fact_calls_the_fact_routine_for_deploy_only_through_the_facts_operation(monkeypatch):
    from datetime import datetime, timezone
    conn = FakeConn((True,))
    seen = _Session(monkeypatch, conn)
    ts = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert bc.record_deploy_fact("deploy-abc", "FND-0355", {"head": "x"}, ts) is True
    assert seen.calls[-1]["operation"] == "facts" and seen.calls[-1]["write"] is True
    assert "sp_record_fact('DEPLOY'" in conn.sqls() and conn.calls[0][1] == ("deploy-abc", "FND-0355", '{"head": "x"}', ts)
