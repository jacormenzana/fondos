# tests/test_backlog_live_guard.py
# -*- coding: utf-8 -*-
"""Owner orders of 2026-10-29: (1) friction against a synthetic test reaching the LIVE backlog from a terminal; (2) the agent identity of a Claude Code session comes from the git-ignored .env, and only
inside the agent harness. Pure: no database.

    python -m pytest tests/test_backlog_live_guard.py
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from shared import backlog_client as bc

REAL_ENV = {}      # an explicit environment: PYTEST_CURRENT_TEST absent, so the guard logic is exercised


@pytest.fixture(autouse=True)
def _fresh_confirmation(monkeypatch):
    monkeypatch.setattr(bc, "_live_write_confirmed", False)


def ask_never(_):
    raise AssertionError("a person must not be asked in this case")


def test_a_non_interactive_process_is_never_asked():
    assert bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=False, ask=ask_never, environ=REAL_ENV) is True


def test_under_pytest_nobody_is_ever_asked():
    assert bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=True, ask=ask_never, environ={"PYTEST_CURRENT_TEST": "x::y"}) is True


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes", "i confirm this is not a synthetic test", "I CONFIRM THIS IS NOT A SYNTHETIC TEST!", "I CONFIRM"])
def test_a_person_who_does_not_type_the_exact_phrase_writes_nothing(answer):
    with pytest.raises(bc.LiveWriteNotConfirmed, match="nothing was written"):
        bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=True, ask=lambda _: answer, environ=REAL_ENV)
    assert bc._live_write_confirmed is False


def test_the_exact_phrase_is_asked_once_per_process_and_the_prompt_names_the_target_and_the_actor():
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return "  " + bc.LIVE_WRITE_PHRASE + " \n"                      # surrounding whitespace is tolerated, the words are not

    env = {"FONDOS_ACTOR": "human:jacor"}
    assert bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=True, ask=ask, environ=env) is True
    assert bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=True, ask=ask_never, environ=env) is True       # second time: remembered
    assert len(seen) == 1 and "127.0.0.1:5436/gestion" in seen[0] and "human:jacor" in seen[0] and bc.LIVE_WRITE_PHRASE in seen[0] and "LIVE" in seen[0]
    assert bc.LIVE_WRITE_PHRASE == "I CONFIRM THIS IS NOT A SYNTHETIC TEST"


def test_the_prompt_never_shows_a_password():
    seen = []
    bc.confirm_live_write(bc._target_of("postgresql://writer:s3cr3t@127.0.0.1:5436/gestion"), interactive=True, ask=lambda p: seen.append(p) or bc.LIVE_WRITE_PHRASE, environ=REAL_ENV)
    assert "s3cr3t" not in seen[0] and "127.0.0.1:5436/gestion" in seen[0]


@contextmanager
def _fake_connect():
    yield SimpleNamespace()


def test_only_writes_that_open_their_own_connection_are_confirmed_and_reads_never_are(monkeypatch):
    asked = []
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://w:pw@127.0.0.1:5436/gestion")
    monkeypatch.setattr(bc, "confirm_live_write", lambda target, **k: asked.append(target) or True)
    monkeypatch.setattr(bc, "psycopg", SimpleNamespace(connect=lambda *a, **k: _fake_connect()))
    with bc._session():                                                  # a read
        pass
    assert asked == []
    with bc._session(write=True):                                        # a write on its own connection
        pass
    assert asked == ["127.0.0.1:5436/gestion"]
    with bc._session(object(), write=True):                              # a write on a connection the caller owns (tests, staging): never asked
        pass
    assert asked == ["127.0.0.1:5436/gestion"]


def test_every_state_changing_helper_asks_for_a_write_session():
    import inspect
    names = ["open_ticket", "transition", "raise_hold", "ack_review", "release_hold", "set_gate", "annotate_wait", "update_attributes", "append_log", "record_link_events"]
    for n in names:
        assert "write=True" in inspect.getsource(getattr(bc, n)), f"{n} does not open its connection as a write session"
    for n in ("list_tickets", "list_logs", "list_links", "gate_audit_window", "shadow_progress", "phase_b_catalog", "history_fingerprint"):
        assert "write=True" not in inspect.getsource(getattr(bc, n)), f"{n} is a read and must not be confirmed"


# --- the agent identity of a Claude Code session ------------------------------------------------------------------------------------------------------------------------------

AGENT = "agent:claude-sonnet-5-5"


def test_inside_the_agent_harness_the_identity_comes_from_the_dotenv_key():
    assert bc.current_actor({"CLAUDECODE": "1", "FONDOS_AGENT_ACTOR": AGENT}) == AGENT
    assert bc.resolve_actor(environ={"CLAUDECODE": "1", "FONDOS_AGENT_ACTOR": AGENT}) == AGENT
    assert bc.resolve_actor(AGENT, environ={"CLAUDECODE": "1", "FONDOS_AGENT_ACTOR": AGENT}) == AGENT
    with pytest.raises(bc.ActorUnknown, match="differs"):
        bc.resolve_actor("agent:claude-opus-5-5", environ={"CLAUDECODE": "1", "FONDOS_AGENT_ACTOR": AGENT})


@pytest.mark.parametrize("env", [
    {"FONDOS_AGENT_ACTOR": AGENT},                                       # a person's terminal: no CLAUDECODE, the agent default is NOT used (it could not label his command as the agent's)
    {"CLAUDECODE": "0", "FONDOS_AGENT_ACTOR": AGENT},
    {"CLAUDECODE": "", "FONDOS_AGENT_ACTOR": AGENT},
    {"CLAUDECODE": "1"},                                                 # the harness without a declared identity: hard fail
    {"CLAUDECODE": "1", "FONDOS_AGENT_ACTOR": ""},
])
def test_without_the_harness_or_without_the_key_there_is_no_identity(env):
    with pytest.raises(bc.ActorUnknown, match="not set"):
        bc.current_actor(env)


def test_an_explicit_fondos_actor_always_wins_over_the_agent_default():
    assert bc.current_actor({"CLAUDECODE": "1", "FONDOS_ACTOR": "human:jacor", "FONDOS_AGENT_ACTOR": AGENT}) == "human:jacor"


@pytest.mark.parametrize("bad", ["claude", "agent:", "agent:has space", "robot:x"])
def test_a_malformed_agent_default_is_a_hard_fail(bad):
    with pytest.raises(bc.ActorUnknown, match="not canonical"):
        bc.current_actor({"CLAUDECODE": "1", "FONDOS_AGENT_ACTOR": bad})


def test_the_test_suite_neither_sees_the_live_backlog_nor_the_agent_default():
    assert os.environ.get("FONDOS_BACKLOG_PG_DSN") is None or os.environ.get("FONDOS_TESTS_ALLOW_LIVE_BACKLOG")
    assert os.environ.get("FONDOS_BACKLOG_PG_DSN_HUMAN") is None or os.environ.get("FONDOS_TESTS_ALLOW_LIVE_BACKLOG")
    assert os.environ.get("FONDOS_AGENT_ACTOR") is None
