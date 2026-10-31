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
