# tests/test_backlog_actor_env.py
# -*- coding: utf-8 -*-
"""The identity of every backlog write comes from the environment (owner order 2026-10-28) and the test suite leaves no footprint (the sandbox plugin). Pure: no database.

    python -m pytest tests/test_backlog_actor_env.py
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import backlog_client as bc
from shared.testing import sandbox as sb

BAD = ["", "   ", "claude", "agent:", "agent:has space", "human", "robot:x", "AGENT:upper-prefix"]


@pytest.mark.parametrize("value", BAD)
def test_a_missing_or_malformed_identity_is_a_hard_fail(monkeypatch, value):
    monkeypatch.setenv("FONDOS_ACTOR", value)
    with pytest.raises(bc.ActorUnknown):
        bc.resolve_actor()
    with pytest.raises(bc.ActorUnknown):
        bc.resolve_actor("agent:claude-sonnet-5-5")                      # an explicit identity does not replace a broken environment


def test_an_unset_identity_is_a_hard_fail_and_the_os_user_is_never_a_fallback(monkeypatch):
    monkeypatch.delenv("FONDOS_ACTOR", raising=False)
    monkeypatch.setenv("USERNAME", "jacor")
    monkeypatch.setenv("USER", "jacor")
    with pytest.raises(bc.ActorUnknown, match="not set"):
        bc.resolve_actor()


def test_the_environment_identity_is_used_and_an_explicit_one_must_equal_it(monkeypatch):
    monkeypatch.setenv("FONDOS_ACTOR", "agent:claude-sonnet-5-5")
    assert bc.resolve_actor() == "agent:claude-sonnet-5-5" == bc.resolve_actor("agent:claude-sonnet-5-5")
    with pytest.raises(bc.ActorUnknown, match="differs from FONDOS_ACTOR"):
        bc.resolve_actor("agent:claude-opus-5-5")                        # the misattribution of 2026-10-28, now impossible to type
    with pytest.raises(bc.ActorUnknown, match="not canonical"):
        bc.resolve_actor("claude")
    assert bc.resolve_actor(environ={"FONDOS_ACTOR": " human:jacor "}) == "human:jacor"      # a given mapping is read, never os.environ


CALLS = {
    "open_ticket": lambda: bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id="FND_20261029_DEV_X", description="d", first_log="l"),
    "transition": lambda: bc.transition("FND-0001", "IN_PROGRESS", session_id="FND_20261029_DEV_X", comment="a progress note"),
    "raise_hold": lambda: bc.raise_hold("FND-0001", "a long enough reason for the hold"),
    "ack_review": lambda: bc.ack_review(1, "reviewed the impact of the change"),
    "annotate_wait": lambda: bc.annotate_wait("FND-0001", "a long enough structured comment", condition="a named condition for the wait"),
    "update_attributes": lambda: bc.update_attributes("FND-0001", title="a new title"),
    "release_hold": lambda: bc.release_hold(1, "released after review"),
    "set_gate": lambda: bc.set_gate("AUDIT", "back to the audit mode in a test"),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_every_state_changing_helper_refuses_before_it_touches_the_database(monkeypatch, name):
    """No DSN is set: reaching the database would raise BacklogUnavailable. The identity check comes first and is the error."""
    monkeypatch.delenv("FONDOS_ACTOR", raising=False)
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN_HUMAN", raising=False)
    with pytest.raises(bc.ActorUnknown):
        CALLS[name]()
    monkeypatch.setenv("FONDOS_ACTOR", "agent:has space")
    with pytest.raises(bc.ActorUnknown):
        CALLS[name]()


@pytest.mark.parametrize("name", ["release_hold", "set_gate"])
def test_the_human_only_helpers_refuse_an_agent_identity(monkeypatch, name):
    monkeypatch.setenv("FONDOS_ACTOR", "agent:claude-sonnet-5-5")
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    with pytest.raises(bc.ActorUnknown, match="only a human"):
        CALLS[name]()


# --- the sandbox plugin ---------------------------------------------------------------------------------------------------------------------------------------------

def test_the_fallback_log_of_every_test_is_a_temporary_file_never_the_repository_log():
    path = bc._fallback_log_path()
    assert Path(path).resolve() != sb.REPO_FALLBACK_LOG.resolve()
    assert "fondos-tests-" in str(path) and not str(Path(path).resolve()).startswith(str(sb._REPO_ROOT))


def test_every_test_runs_with_the_test_identity_and_acting_as_restores_it():
    assert os.environ["FONDOS_ACTOR"] == sb.TEST_ACTOR
    with sb.acting_as("human:jacor"):
        assert bc.resolve_actor() == "human:jacor"
    assert os.environ["FONDOS_ACTOR"] == sb.TEST_ACTOR
    with sb.acting_as("system:release"):
        pass
    assert bc.current_actor() == sb.TEST_ACTOR


def test_the_footprint_check_fails_the_session_when_the_repository_log_is_touched(tmp_path, monkeypatch):
    fake = tmp_path / "backlog_autolog_fallback.jsonl"
    monkeypatch.setattr(sb, "REPO_FALLBACK_LOG", fake)
    monkeypatch.delenv("FONDOS_TESTS_SKIP_FOOTPRINT_CHECK", raising=False)
    clean, dirty = SimpleNamespace(exitstatus=0), SimpleNamespace(exitstatus=0)
    sb.pytest_sessionstart(clean)
    sb.pytest_sessionfinish(clean, 0)
    assert clean.exitstatus == 0                                          # nothing was written: the run stays as it was
    sb.pytest_sessionstart(dirty)
    fake.write_text("{}\n", encoding="utf-8")                              # a test escaped the sandbox
    sb.pytest_sessionfinish(dirty, 0)
    assert dirty.exitstatus == 1
    skipped = SimpleNamespace(exitstatus=0)
    monkeypatch.setenv("FONDOS_TESTS_SKIP_FOOTPRINT_CHECK", "1")
    sb.pytest_sessionfinish(skipped, 0)
    assert skipped.exitstatus == 0                                         # the documented switch
