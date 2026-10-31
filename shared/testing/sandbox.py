# -*- coding: utf-8 -*-
"""
shared/testing/sandbox.py -- pytest plugin registered by every conftest.py of the repository (owner order 2026-10-28: the test suite must leave no footprint).

* The backlog's local fallback log (shared/log/backlog_autolog_fallback.jsonl, written when an incident cannot reach the database) is redirected, for the whole session, to a file in a
  fresh temporary directory that is deleted at the end (FONDOS_BACKLOG_FALLBACK_LOG), unless a test sets its own. A test that fails on the way to the database can no longer leave
  that file in the working tree.
* FONDOS_ACTOR is set for every test to TEST_ACTOR, because the backlog client now refuses to write without a valid identity from the environment (shared.backlog_client.resolve_actor).
  TEST_ACTOR is a canonical agent identity used by the tests; it is NOT a statement about the model that runs them. A test that needs another identity uses acting_as().
* The connection strings of the LIVE backlog (FONDOS_BACKLOG_PG_DSN, ..._HUMAN, ..._LOGGER, ..._INCIDENT) are removed from the process for the whole session, so no test can reach it even by accident.
* At the end of the session the plugin checks that the repository's real fallback log was not modified during it; if it was (a test escaped the sandbox, or a real pipeline wrote
  to it meanwhile) the run is reported as failed with the reason, so a footprint is never silent.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

TEST_ACTOR = "agent:claude-opus-5-5"
_ENV_FALLBACK = "FONDOS_BACKLOG_FALLBACK_LOG"
_ENV_ACTOR = "FONDOS_ACTOR"
_REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_FALLBACK_LOG = _REPO_ROOT / "shared" / "log" / "backlog_autolog_fallback.jsonl"
_state: dict = {}


@contextlib.contextmanager
def acting_as(actor: str):
    """Run a block with FONDOS_ACTOR set to `actor` (restored afterwards): for the tests that exercise another identity (the owner, a system actor)."""
    old = os.environ.get(_ENV_ACTOR)
    os.environ[_ENV_ACTOR] = actor
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_ENV_ACTOR, None)
        else:
            os.environ[_ENV_ACTOR] = old


def pytest_sessionstart(session):
    _state["start"] = time.time()
    _state["before"] = REPO_FALLBACK_LOG.stat().st_mtime if REPO_FALLBACK_LOG.exists() else None


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("FONDOS_TESTS_SKIP_FOOTPRINT_CHECK"):
        return
    after = REPO_FALLBACK_LOG.stat().st_mtime if REPO_FALLBACK_LOG.exists() else None
    if after is not None and after != _state.get("before"):             # created or modified since the session started (file clocks are coarser than time.time(): no time comparison)
        print(f"\n[sandbox] FOOTPRINT: {REPO_FALLBACK_LOG} was modified during this test session (a test escaped the sandbox, or a real pipeline wrote to it meanwhile)")
        session.exitstatus = int(pytest.ExitCode.TESTS_FAILED)


_LIVE_BACKLOG_DSNS = ("FONDOS_BACKLOG_PG_DSN", "FONDOS_BACKLOG_PG_DSN_HUMAN", "FONDOS_BACKLOG_PG_DSN_LOGGER", "FONDOS_BACKLOG_PG_DSN_INCIDENT", "FONDOS_BACKLOG_PG_DSN_GATE", "FONDOS_BACKLOG_PG_DSN_ADJUDICATOR")


@pytest.fixture(scope="session", autouse=True)
def _fondos_test_sandbox():
    tmp = tempfile.mkdtemp(prefix="fondos-tests-")
    previous = os.environ.get(_ENV_FALLBACK)
    if previous is None:
        os.environ[_ENV_FALLBACK] = str(Path(tmp) / "backlog_autolog_fallback.jsonl")
    # A test run must never SEE the live backlog: its connection strings are removed from the process for the whole session (a test that needs one sets its own with monkeypatch). The documented
    # switch FONDOS_TESTS_ALLOW_LIVE_BACKLOG=1 exists for a deliberate, supervised read-only check and is never set by CI.
    hidden = {}
    if not os.environ.get("FONDOS_TESTS_ALLOW_LIVE_BACKLOG"):
        for name in _LIVE_BACKLOG_DSNS:
            if name in os.environ:
                hidden[name] = os.environ.pop(name)
    try:
        yield tmp
    finally:
        os.environ.update(hidden)
        if previous is None:
            os.environ.pop(_ENV_FALLBACK, None)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fondos_test_actor(monkeypatch):
    monkeypatch.setenv(_ENV_ACTOR, TEST_ACTOR)
    monkeypatch.delenv("FONDOS_AGENT_ACTOR", raising=False)         # the agent default of an interactive session must not leak into a test that expects a missing identity
    yield
