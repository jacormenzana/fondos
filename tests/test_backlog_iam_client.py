# tests/test_backlog_iam_client.py
# -*- coding: utf-8 -*-
"""Client side of the IAM step (FND-0354; owner decisions 2026-10-29): the two non-interactive writes that survive the live-write ban (an agent's note on an existing ticket, the automatic
incident capture) go through an explicit allow-list in the connection factory, each with its own login; libpq SSL parameters in a connection string reach the driver untouched.
Pure: a fake driver stands in for psycopg, no database.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import backlog_client as bc  # noqa: E402

REAL_ENV = {"FONDOS_ACTOR": "agent:claude-sonnet-5-5"}                     # no PYTEST_CURRENT_TEST: the real rule
SSL = "sslmode=verify-full&sslcert=/c/agent.crt&sslkey=/c/agent.key&sslrootcert=/c/ca.crt"
LOGGER_DSN = f"postgresql://backlog_logger@127.0.0.1:5436/gestion?{SSL}"
INCIDENT_DSN = f"postgresql://backlog_incident@127.0.0.1:5436/gestion?{SSL}"
READER_DSN = "postgresql://backlog_reader@127.0.0.1:5436/gestion?sslmode=verify-full"


class FakeCur:
    def __init__(self, conn):
        self.conn = conn
        self.connection = conn
        self._next = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.conn.executed.append((" ".join(sql.split()), params))
        self._next = self.conn.script.pop(0) if self.conn.script else (1,)

    def fetchone(self):
        return self._next

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, script):
        self.script = list(script)
        self.executed = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return FakeCur(self)

    def commit(self):
        self.committed = True


@pytest.fixture
def driver(monkeypatch):
    """Replace psycopg.connect; the test sets .script (rows returned by successive fetchone calls) and reads .calls / .conn afterwards."""
    d = SimpleNamespace(calls=[], script=[], conn=None)

    def connect(dsn, **kw):
        d.calls.append((dsn, kw))
        d.conn = FakeConn(d.script)
        return d.conn

    monkeypatch.setattr(bc, "psycopg", SimpleNamespace(connect=connect))
    # pytest is the one exempt context and it sets PYTEST_CURRENT_TEST again for the call phase: each test calls d.real() in its body to exercise the real rule
    d.real = lambda: monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(bc.sys.stdin, "isatty", lambda: False, raising=False)
    for var in (bc._DSN_ENV_VAR, bc._LOGGER_DSN_ENV_VAR, bc._INCIDENT_DSN_ENV_VAR, bc._HUMAN_DSN_ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FONDOS_ACTOR", "agent:claude-sonnet-5-5")
    return d


# --- the allow-list --------------------------------------------------------------------------------------------------------------------------

def test_the_allow_list_is_exactly_the_two_operations_the_owner_named():
    assert bc.NON_INTERACTIVE_OPERATIONS == ("agent_log", "incident")


@pytest.mark.parametrize("operation", bc.NON_INTERACTIVE_OPERATIONS)
def test_an_allow_listed_operation_needs_no_terminal(operation):
    assert bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=False, ask=lambda _: pytest.fail("asked"), environ=REAL_ENV, operation=operation) is True


def test_any_other_operation_is_a_programming_error_and_no_operation_is_still_refused_without_a_terminal():
    with pytest.raises(ValueError, match="not allow-listed"):
        bc.confirm_live_write("x:1/y", interactive=False, environ=REAL_ENV, operation="transition")
    with pytest.raises(bc.LiveWriteForbidden):
        bc.confirm_live_write("x:1/y", interactive=False, environ=REAL_ENV)
    with pytest.raises(ValueError):
        bc.dsn_for_operation("open_ticket")
    with pytest.raises(ValueError):
        bc._connect_allowlisted("open_ticket", LOGGER_DSN)


def test_each_operation_uses_its_own_login_and_falls_back_to_the_general_one(monkeypatch):
    monkeypatch.setenv(bc._DSN_ENV_VAR, READER_DSN)
    assert bc.dsn_for_operation("agent_log") == READER_DSN and bc.dsn_for_operation("incident") == READER_DSN         # transition period
    monkeypatch.setenv(bc._LOGGER_DSN_ENV_VAR, LOGGER_DSN)
    monkeypatch.setenv(bc._INCIDENT_DSN_ENV_VAR, INCIDENT_DSN)
    assert bc.dsn_for_operation("agent_log") == LOGGER_DSN and bc.dsn_for_operation("incident") == INCIDENT_DSN


# --- the agent bookkeeping path --------------------------------------------------------------------------------------------------------------

def test_an_agent_note_goes_through_the_scoped_routine_with_the_logger_login_and_no_terminal(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._LOGGER_DSN_ENV_VAR, LOGGER_DSN)
    monkeypatch.setenv(bc._DSN_ENV_VAR, READER_DSN)
    driver.script = [(True,), (77,)]                                       # the routine is present; the new log id
    assert bc.append_log("FND-0354", "agent:claude-sonnet-5-5", "a progress note") == 77
    assert driver.calls[0][0] == LOGGER_DSN                                # the SSL parameters reach the driver exactly as configured
    sql = [s for s, _ in driver.conn.executed]
    assert any("sp_append_log" in s for s in sql) and not any("INSERT" in s.upper() for s in sql)


def test_a_system_identity_also_uses_the_scoped_path(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._LOGGER_DSN_ENV_VAR, LOGGER_DSN)
    driver.script = [(True,), (5,)]
    assert bc.append_log("FND-0354", "system:release", "released") == 5


def test_the_note_waits_with_a_clear_error_while_the_iam_step_is_not_applied(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._LOGGER_DSN_ENV_VAR, LOGGER_DSN)
    driver.script = [(False,)]
    with pytest.raises(bc.BacklogUnavailable, match="--phase iam"):
        bc.append_log("FND-0354", "agent:claude-sonnet-5-5", "a note")
    assert not any("sp_append_log(" in s and "SELECT backlog" in s for s, _ in driver.conn.executed)


def test_a_human_note_is_not_exempt_and_nothing_else_the_agent_does_is(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._HUMAN_DSN_ENV_VAR, "postgresql://backlog_human@127.0.0.1:5436/gestion")
    monkeypatch.setenv(bc._DSN_ENV_VAR, READER_DSN)
    with pytest.raises(bc.LiveWriteForbidden):
        bc.append_log("FND-0354", "human:j.cormenzana_owner", "a note")                                         # a person confirms at a terminal
    with pytest.raises(bc.LiveWriteForbidden):
        bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id="FND_20261029_DEV_X", description="d", first_log="l")
    with pytest.raises(bc.LiveWriteForbidden):
        bc.transition("FND-0354", "TODO", session_id="FND_20261029_DEV_X", comment="c")
    with pytest.raises(bc.LiveWriteForbidden):
        bc.record_link_event(from_id="FND-0001", to_id="FND-0002", link_type="RELATED", status="PROPOSED", evidence="e", username="agent:claude-sonnet-5-5")
    assert driver.calls == []                                                                                    # no connection was ever opened


def test_a_malformed_agent_name_does_not_take_the_exempt_path(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._LOGGER_DSN_ENV_VAR, LOGGER_DSN)
    monkeypatch.setenv(bc._DSN_ENV_VAR, READER_DSN)
    with pytest.raises(bc.LiveWriteForbidden):
        bc.append_log("FND-0354", "claude", "a note")
    assert driver.calls == []


# --- the incident capture --------------------------------------------------------------------------------------------------------------------

def test_the_incident_goes_through_the_insert_only_routine_with_its_own_login(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._INCIDENT_DSN_ENV_VAR, INCIDENT_DSN)
    monkeypatch.setattr(bc, "_overlap_settings", lambda: None)
    driver.script = [(True,), None, ("FND-0400",)]                          # routine present; no open ticket in the window; the routine's answer
    assert bc.report_incident(object_name="pipeline.step", scenario_description="it crashed") == "FND-0400"
    assert driver.calls[0][0] == INCIDENT_DSN and driver.conn.committed
    sql = [s for s, _ in driver.conn.executed]
    assert any("sp_report_incident" in s for s in sql)
    assert not any("sp_open_ticket" in s or "INSERT" in s.upper() for s in sql)                               # the client itself opens and inserts nothing


def test_the_overlap_the_client_found_is_handed_to_the_routine(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._INCIDENT_DSN_ENV_VAR, INCIDENT_DSN)
    monkeypatch.setattr(bc, "_overlap_policy", lambda cur, o, d: {"tier": "HIGH", "ticket": "FND-0100", "reason": "same crash"})
    driver.script = [(True,), None, ("FND-0100",)]
    assert bc.report_incident(object_name="pipeline.step", scenario_description="it crashed") == "FND-0100"
    params = driver.conn.executed[-1][1]
    assert "FND-0100" in params and "same crash" in params


def test_an_incident_with_no_login_configured_is_a_no_op(driver):
    driver.real()
    assert bc.report_incident(object_name="pipeline.step", scenario_description="it crashed") is None
    assert driver.calls == []


def test_an_incident_the_database_refuses_goes_to_the_fallback_log_and_never_raises(driver, monkeypatch, tmp_path):
    driver.real()
    monkeypatch.setenv(bc._INCIDENT_DSN_ENV_VAR, INCIDENT_DSN)
    monkeypatch.setenv(bc._FALLBACK_LOG_ENV_VAR, str(tmp_path / "fallback.jsonl"))

    def boom(*a, **k):
        raise RuntimeError("permission denied")
    monkeypatch.setattr(bc, "_routine_present", boom)
    assert bc.report_incident(object_name="pipeline.step", scenario_description="it crashed") is None
    assert (tmp_path / "fallback.jsonl").read_text(encoding="utf-8").count("pipeline.step") == 1


# --- libpq parameters ------------------------------------------------------------------------------------------------------------------------

def test_the_target_shown_to_a_person_never_contains_credentials_or_certificate_paths():
    shown = bc._target_of(LOGGER_DSN)
    assert shown == "127.0.0.1:5436/gestion" and "ssl" not in shown and "agent" not in shown


def test_the_connection_string_reaches_the_driver_unchanged(driver, monkeypatch):
    driver.real()
    monkeypatch.setenv(bc._INCIDENT_DSN_ENV_VAR, INCIDENT_DSN)
    monkeypatch.setattr(bc, "_overlap_settings", lambda: None)
    driver.script = [(True,), None, ("FND-0400",)]
    bc.report_incident(object_name="pipeline.step", scenario_description="it crashed")
    sent = driver.calls[0][0]
    assert sent == INCIDENT_DSN and all(p in sent for p in ("sslmode=verify-full", "sslcert=", "sslkey=", "sslrootcert="))


# --- static ----------------------------------------------------------------------------------------------------------------------------------

def test_no_code_outside_the_client_names_an_allow_listed_operation():
    """The exemption lives in ONE place: nothing else may pass operation= to the guard or open an allow-listed connection."""
    offenders = []
    for base in ("scripts", "shared", "proyecto1", "proyecto2", "proyecto3"):
        for path in (_ROOT / base).rglob("*.py"):
            rel = path.relative_to(_ROOT).as_posix()
            if rel in ("shared/backlog_client.py",) or "/tests/" in rel or rel.startswith("shared/testing/"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"operation\s*=\s*[\"'](agent_log|incident)[\"']|_connect_allowlisted|NON_INTERACTIVE_OPERATIONS", text):
                offenders.append(rel)
    assert not offenders, offenders


def test_only_report_incident_and_append_log_use_the_allow_listed_factory():
    text = (_ROOT / "shared" / "backlog_client.py").read_text(encoding="utf-8")
    users = re.findall(r"(?:_connect_allowlisted\(\s*\"|operation=\")(\w+)\"", text)
    assert sorted(set(users)) == ["agent_log", "incident"], users


def test_the_test_sandbox_hides_every_live_connection_string_the_client_knows():
    """A new DSN variable that the sandbox does not hide would let a test reach the live backlog."""
    from shared.testing import sandbox
    known = {bc._DSN_ENV_VAR, bc._HUMAN_DSN_ENV_VAR, bc._LOGGER_DSN_ENV_VAR, bc._INCIDENT_DSN_ENV_VAR}
    assert set(sandbox._LIVE_BACKLOG_DSNS) == known
    declared = set(re.findall(r'^_\w*DSN\w*ENV_VAR = "(FONDOS_BACKLOG_PG_DSN\w*)"', (_ROOT / "shared" / "backlog_client.py").read_text(encoding="utf-8"), re.M))
    assert declared == known
