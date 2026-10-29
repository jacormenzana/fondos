# tests/test_backlog_live_guard.py
# -*- coding: utf-8 -*-
"""Owner orders of 2026-10-29: (1) a NON-interactive process (an agent's shell tool, a script, a pipeline, a hook) is HARD-REFUSED, exit code 1, when it writes to the LIVE backlog, and a person at a terminal
must confirm; (2) the agent identity of a Claude Code session comes from the git-ignored .env, and only inside the agent harness. Pure: no database.

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


def test_a_non_interactive_process_is_refused_and_never_asked():
    with pytest.raises(bc.LiveWriteForbidden, match="REFUSED.*exit code 1.*non-interactive.*Nothing was written"):
        bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=False, ask=ask_never, environ=REAL_ENV)
    assert bc._live_write_confirmed is False
    assert issubclass(bc.LiveWriteForbidden, bc.BacklogUnavailable) and bc.LIVE_WRITE_EXIT_CODE == 1


def test_the_nul_device_and_a_pipe_are_not_a_console_even_where_isatty_lies(tmp_path):
    """The bug the subprocess tests found: on Windows isatty() is True for NUL (the stdin of a tool shell); only a console handle counts."""
    with open(os.devnull, "r", encoding="utf-8") as nul:
        assert bc._is_console(nul) is False
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with open(f, "r", encoding="utf-8") as plain:
        assert bc._is_console(plain) is False
    assert bc._is_console(None) is False
    r, w = os.pipe()
    try:
        with os.fdopen(r, "r") as pipe_end:
            assert bc._is_console(pipe_end) is False
    finally:
        os.close(w)


def test_a_fake_terminal_that_claims_to_be_a_tty_but_has_no_real_console_is_not_a_console(monkeypatch):
    class Liar:
        def isatty(self):
            return True

        def fileno(self):
            return 99999                                              # not a handle at all
    assert bc._is_console(Liar()) is False if os.name == "nt" else True


def test_the_refusal_names_the_target_and_the_identity_and_there_is_no_bypass_in_the_environment():
    env = {"FONDOS_ACTOR": "agent:claude-sonnet-5-5", "FONDOS_LIVE_WRITE_CONFIRMED": "1", "CI": "true", "FONDOS_ASSUME_YES": "1", "PYTHONINSPECT": "1"}   # none of these may open the gate
    with pytest.raises(bc.LiveWriteForbidden) as e:
        bc.confirm_live_write("127.0.0.1:5436/gestion", interactive=False, ask=ask_never, environ=env)
    assert "127.0.0.1:5436/gestion" in str(e.value) and "agent:claude-sonnet-5-5" in str(e.value)


def test_the_cli_helper_returns_exit_code_one_and_prints_the_reason_on_stderr(capsys):
    assert bc.require_live_write_cli("postgresql://w:pw@127.0.0.1:5436/gestion", environ=REAL_ENV, interactive=False) == 1
    err = capsys.readouterr().err
    assert err.startswith("REFUSED: live write REFUSED") and "pw" not in err.replace("write", "") and "127.0.0.1:5436/gestion" in err
    assert bc.require_live_write_cli("postgresql://w:pw@127.0.0.1:5436/gestion", environ=REAL_ENV, interactive=True, ask=lambda _: "no") == 1
    assert bc.require_live_write_cli("postgresql://w:pw@127.0.0.1:5436/gestion", environ=REAL_ENV, interactive=True, ask=lambda _: bc.LIVE_WRITE_PHRASE) is None
    assert bc.require_live_write_cli("postgresql://w:pw@127.0.0.1:5436/gestion", environ={"PYTEST_CURRENT_TEST": "x"}, interactive=False) is None


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


def test_a_non_interactive_write_helper_on_its_own_connection_is_refused_before_any_connection_is_opened(monkeypatch):
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN", "postgresql://w:pw@127.0.0.1:5436/gestion")
    monkeypatch.setattr(bc, "psycopg", SimpleNamespace(connect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("a connection was opened"))))
    monkeypatch.setattr(bc.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)        # pytest is the one context that is exempt: take it away to exercise the real rule
    with pytest.raises(bc.LiveWriteForbidden):
        bc.open_ticket(title="t", nature="BUG", object_type="N/A", priority="LOW", session_id="FND_20261029_DEV_X", description="d", first_log="l")
    with pytest.raises(bc.LiveWriteForbidden):
        bc.append_log("FND-0001", "agent:x", "a note")
    with pytest.raises(bc.LiveWriteForbidden):
        bc.record_link_event(from_id="FND-0001", to_id="FND-0002", link_type="RELATED", status="PROPOSED", evidence="e", username="agent:x")


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


# --- the command-line writers: exit code 1 before touching anything (a real process, non-interactive, not under pytest) -------------------------------------------------------------

import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _run(args, tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", "FONDOS_BACKLOG_PG_DSN_HUMAN")}
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("", encoding="utf-8")
    env.update(FONDOS_ENV_FILE=str(empty_env_file), FONDOS_BACKLOG_PG_DSN="postgresql://w:pw@127.0.0.1:1/none", FONDOS_ACTOR="agent:claude-sonnet-5-5", FONDOS_PG_DSN="postgresql://a:pw@127.0.0.1:1/none")
    return subprocess.run([sys.executable, "-X", "utf8", *args], cwd=_ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=120)


@pytest.mark.parametrize("args", [
    ["scripts/ops/close_release.py", "--release", "R2026.10-GUARD-01", "--tickets", "FND-0001", "--log", "a closing log long enough to pass the argument validation of the tool", "--actor", "agent:claude-sonnet-5-5", "--apply"],
    ["scripts/ops/close_release.py", "--release", "R2026.10-GUARD-01", "--tickets", "FND-0001", "--log", "a closing log long enough to pass the argument validation of the tool", "--actor", "agent:claude-sonnet-5-5"],
    ["scripts/ops/backlog_step0.py", "--apply", "--actor", "agent:claude-sonnet-5-5"],
], ids=["close_release --apply", "close_release dry run (it writes and rolls back)", "step 0 --apply"])
def test_the_writing_tools_exit_with_code_one_before_opening_any_connection(args, tmp_path):
    r = _run(args, tmp_path)
    assert r.returncode == 1, (r.returncode, r.stderr[-400:])
    assert "REFUSED: live write REFUSED" in r.stderr and "Traceback" not in r.stderr          # a refusal, not a crash (an unreachable DSN would have produced a connection error instead)


def test_the_step_0_dry_run_only_reads_and_is_not_refused_by_the_write_gate(tmp_path):
    r = _run(["scripts/ops/backlog_step0.py"], tmp_path)
    assert "live write REFUSED" not in r.stderr            # it will fail for lack of a reachable database, but not because of the gate


def test_every_command_line_tool_that_calls_a_write_helper_goes_through_the_live_write_gate():
    """A new script that writes must not forget the gate: any file under scripts/ that calls a state-changing helper has to call require_live_write_cli() or handle LiveWriteForbidden."""
    import re
    writes = re.compile(r"\bbc\.(open_ticket|transition|raise_hold|ack_review|release_hold|set_gate|annotate_wait|update_attributes|append_log|record_link_events?)\(")
    offenders = []
    for path in (_ROOT / "scripts").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if writes.search(text) and "require_live_write_cli" not in text and "LiveWriteForbidden" not in text:
            offenders.append(path.relative_to(_ROOT).as_posix())
    assert not offenders, f"scripts that write to the backlog without the live-write gate: {offenders}"
