# tests/test_owner_vault.py
# -*- coding: utf-8 -*-
"""The owner vault (shared/owner_vault.py, scripts/ops/owner_vault.py): the owner-only strings reach an OWNER TOOL, in its own process, only when a person is at a real console. No database, no
network. The values used here are throwaway strings, never a real secret (R-7)."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import owner_vault as ov  # noqa: E402
import owner_vault as cli  # noqa: E402

OWNER, HUMAN, ADJ, GHA = "FONDOS_PG_DSN_OWNER", "FONDOS_BACKLOG_PG_DSN_HUMAN", "FONDOS_BACKLOG_PG_DSN_ADJUDICATOR", "GITHUB_ADMIN_TOKEN"
VALUE = "postgresql://throwaway:not-a-real-password@127.0.0.1:1/none"


class FakeCipher:
    """Reversible and visibly NOT plaintext, so a test can check the file never holds the value."""

    def protect(self, data: bytes) -> bytes:
        return b"enc:" + data[::-1]

    def unprotect(self, blob: bytes) -> bytes:
        assert blob.startswith(b"enc:"), "not a blob of this cipher"
        return blob[4:][::-1]


@pytest.fixture
def vault(tmp_path):
    return ov.Vault(tmp_path / "vault.json", FakeCipher())


# ---------------------------------------------------------------- the store

def test_a_stored_value_round_trips_and_the_file_never_holds_it(vault):
    vault.set(OWNER, VALUE)
    assert vault.get(OWNER) == VALUE and vault.names() == [OWNER] and vault.has(OWNER) and not vault.has(HUMAN)
    text = vault.path.read_text(encoding="utf-8")
    assert VALUE not in text and "not-a-real-password" not in text and base64.b64encode(FakeCipher().protect(VALUE.encode())).decode() in text


def test_only_vault_variables_are_accepted_and_a_value_is_one_non_empty_line(vault):
    for bad in ("FONDOS_PG_DSN", "FONDOS_BACKLOG_PG_DSN_AGENT", "PATH", "FONDOS_BACKLOG_PG_DSN_AUTOPILOT"):
        with pytest.raises(ov.VaultError):
            vault.set(bad, VALUE)                                                    # the agent and autopilot strings belong in .env and are not vault variables
    for bad in ("", "two\nlines", "carriage\rreturn"):
        with pytest.raises(ov.VaultError):
            vault.set(OWNER, bad)
    assert vault.names() == []


def test_remove_a_missing_name_and_a_damaged_file(vault):
    assert vault.remove(OWNER) is False
    vault.set(OWNER, VALUE)
    assert vault.remove(OWNER) is True and vault.names() == []
    vault.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ov.VaultError, match="damaged"):
        vault.names()
    assert VALUE not in str(pytest.raises(ov.VaultError, vault.get, OWNER).value)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_the_real_dpapi_cipher_round_trips_and_does_not_leave_the_value_in_the_file(tmp_path):
    v = ov.Vault(tmp_path / "v.json")
    v.set(GHA, "throwaway-token-value")
    assert v.get(GHA) == "throwaway-token-value" and "throwaway-token-value" not in v.path.read_text(encoding="utf-8")
    other = ov.Vault(tmp_path / "v.json", FakeCipher())                              # a blob made by DPAPI is not readable with another cipher
    with pytest.raises(ov.VaultError, match="could not be decrypted"):
        other.get(GHA)


# ---------------------------------------------------------------- injection

def test_a_tool_at_a_console_receives_only_its_own_variables_for_this_process(vault):
    for n in (OWNER, HUMAN, ADJ):
        vault.set(n, f"v-{n}")
    env: dict = {}
    assert ov.inject("migrate_backlog_v2.py", env, vault=vault, console=True) == [OWNER]
    assert env == {OWNER: f"v-{OWNER}"}                                              # not the human string, not the adjudicator's
    env2: dict = {}
    assert ov.inject("scripts/audit/gate_adjudicate.py", env2, vault=vault, console=True) == [ADJ] and env2 == {ADJ: f"v-{ADJ}"}


def test_without_a_console_nothing_is_injected_and_nothing_is_read(vault):
    vault.set(OWNER, VALUE)
    env: dict = {}
    assert ov.inject("migrate_backlog_v2.py", env, vault=vault, console=False) == [] and env == {}


def test_an_unknown_tool_an_already_set_variable_and_an_undecryptable_value_are_left_alone(vault):
    vault.set(OWNER, VALUE)
    assert ov.inject("some_other_script.py", {}, vault=vault, console=True) == []
    env = {OWNER: "typed-by-hand"}
    assert ov.inject("migrate_backlog_v2.py", env, vault=vault, console=True) == [] and env[OWNER] == "typed-by-hand"        # a value set by hand wins
    broken = ov.Vault(vault.path, FakeCipher())
    broken.path.write_text('{"version": 1, "entries": {"%s": "bm90IGEgYmxvYg=="}}' % OWNER, encoding="utf-8")             # not a blob of this cipher
    assert ov.inject("migrate_backlog_v2.py", {}, vault=broken, console=True) == []


def test_injection_never_happens_inside_pytest():
    assert ov.inject_for_current_tool() == [] and OWNER not in os.environ


def test_the_manifest_names_real_scripts_and_only_vault_variables():
    scripts = {p.name for p in (_ROOT / "scripts").rglob("*.py")}
    for tool, names in ov.OWNER_TOOLS.items():
        assert tool in scripts, f"{tool} is not a script under scripts/"
        assert names and all(n in ov.VAULT_VARIABLES for n in names), tool


def test_every_script_that_reads_a_vault_variable_is_in_the_manifest_so_none_is_forgotten():
    """A new owner tool that reads FONDOS_PG_DSN_OWNER must be added to OWNER_TOOLS or it would need the string typed again."""
    missing = []
    for path in (_ROOT / "scripts").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for var in ov.VAULT_VARIABLES:
            if re.search(r"environ(?:\.get\(|\[)\s*[\"']" + var + r"[\"']", text) and var not in ov.OWNER_TOOLS.get(path.name, ()):
                missing.append((path.name, var))
    assert not missing, f"scripts that read a vault variable without being in OWNER_TOOLS: {missing}"


# ---------------------------------------------------------------- the .env check

def test_the_env_file_check_reports_names_only_and_ignores_comments(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"FOO=1\n# {OWNER}=commented\n{HUMAN}={VALUE}\nexport {GHA}=tok\nFONDOS_BACKLOG_PG_DSN_AGENT=scoped\n", encoding="utf-8")
    assert ov.env_file_secret_names(f) == sorted([HUMAN, GHA])
    assert ov.env_file_secret_names(tmp_path / "missing.env") == []


# ---------------------------------------------------------------- the command-line setup

def test_set_needs_a_console_and_two_equal_entries_and_never_echoes_the_value(vault, capsys):
    assert cli.main(["set", OWNER], vault=vault, console=False, ask=lambda _: VALUE) == 2 and vault.names() == []        # an agent's shell cannot fill it
    answers = iter([VALUE, "different"])
    assert cli.main(["set", OWNER], vault=vault, console=True, ask=lambda _: next(answers)) == 2 and vault.names() == []
    assert cli.main(["set", OWNER], vault=vault, console=True, ask=lambda _: VALUE) == 0
    out = capsys.readouterr()
    assert vault.get(OWNER) == VALUE and VALUE not in out.out + out.err and "migrate_backlog_v2.py" in out.out


def test_list_check_and_remove_print_names_and_verdicts_only(vault, capsys):
    vault.set(OWNER, VALUE)
    assert cli.main(["list"], vault=vault, console=False) == 0 and cli.main(["check"], vault=vault, console=False) == 0
    out = capsys.readouterr().out
    assert OWNER in out and "stored" in out and "ok" in out and VALUE not in out and "not-a-real-password" not in out        # the shape is shown, never the password
    vault.path.write_text('{"version": 1, "entries": {"%s": "bm90IGEgYmxvYg=="}}' % OWNER, encoding="utf-8")
    assert cli.main(["check"], vault=vault, console=False) == 1                      # a value this user cannot decrypt is reported
    assert cli.main(["remove", OWNER], vault=vault, console=False) == 0 and vault.names() == []


# ---------------------------------------------------------------- a real process without a console gets nothing

@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_a_real_owner_tool_started_without_a_console_does_not_receive_the_string(tmp_path):
    """The shell tool of an agent has no console: the same vault, the same tool name, and the variable stays unset."""
    vault_file = tmp_path / "vault.json"
    ov.Vault(vault_file).set(OWNER, VALUE)                                           # a real DPAPI entry
    tool = tmp_path / "migrate_backlog_v2.py"                                        # same file name as a manifest tool, so shared.config would inject if it were allowed to
    tool.write_text("import os, sys\nsys.path.insert(0, sys.argv[1])\nimport shared.config\nprint('SET' if os.environ.get('FONDOS_PG_DSN_OWNER') else 'UNSET')\n", encoding="utf-8")
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTEST_CURRENT_TEST", OWNER)}
    env.update(FONDOS_OWNER_VAULT=str(vault_file), FONDOS_ENV_FILE=str(empty))
    r = subprocess.run([sys.executable, "-X", "utf8", str(tool), str(_ROOT)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.stdout.strip() == "UNSET", (r.stdout, r.stderr[-300:])
    assert VALUE not in r.stdout + r.stderr


# ---------------------------------------------------------------- the shape of a connection string (a wrong one used to surface inside a migration)

@pytest.mark.parametrize("value", ['"' + VALUE + '"', "'" + VALUE + "'", VALUE + "'", OWNER + "=" + VALUE, "not-a-connection-string", "just-a-password", "postgresql://user:pw@/db", "host=127.0.0.1 port=5436",
                                   "postgresql://:pw@127.0.0.1:5436/db"])
def test_set_refuses_a_connection_string_that_does_not_parse_and_says_why_without_the_value(vault, capsys, value):
    assert cli.main(["set", OWNER], vault=vault, console=True, ask=lambda _: value) == 2 and vault.names() == []
    err = capsys.readouterr().err
    assert "not a usable connection string" in err and "pw" not in err.replace("percent-encoded", "") and "just-a-password" not in err


@pytest.mark.parametrize("value", [VALUE, "host=127.0.0.1 port=5436 dbname=fondos user=fondos_owner password=x", "postgresql://fondos_owner:p%40ss@127.0.0.1:5436/fondos",
                                   "postgresql://fondos_owner:abc@127.0.0.1", "host=127.0.0.1 port=5436 dbname=fondos user=fondos_owner password=p@ss/w#rd?%"])
def test_set_accepts_both_forms_of_connection_string(vault, value):
    assert cli.main(["set", OWNER], vault=vault, console=True, ask=lambda _: value) == 0 and vault.get(OWNER) == value


def test_a_token_is_not_checked_for_the_shape_of_a_connection_string(vault):
    assert cli.dsn_problem("GITHUB_ADMIN_TOKEN", "ghp_whatever") is None and cli.main(["set", GHA], vault=vault, console=True, ask=lambda _: "ghp_whatever") == 0


def test_check_reports_a_stored_value_of_the_wrong_shape_and_shows_where_a_good_one_points(vault, capsys):
    vault.set(OWNER, '"' + VALUE + '"')                                                # stored by an older version, before the shape was checked
    vault.set(HUMAN, "postgresql://backlog_human:secret-pw@127.0.0.1:5436/gestion")
    assert cli.main(["check"], vault=vault, console=False) == 1
    out = capsys.readouterr().out
    assert "WRONG SHAPE" in out and "wrapped in quotes" in out and "owner_vault.py set " + OWNER in out
    assert "backlog_human@127.0.0.1:5436/gestion" in out and "secret-pw" not in out and "not-a-real-password" not in out
    vault.set(OWNER, VALUE)
    assert cli.main(["check"], vault=vault, console=False) == 0


@pytest.mark.parametrize("value, expect", [
    ("postgresql://fondos_owner:abc%zz@127.0.0.1:5436/db", "'%' signs not followed by two hex digits: 1"),
    ("postgresql://fondos_owner:ab/cd@127.0.0.1:5436/db", "a '/' or '?' appears before the '@'"),
    ("postgresql://fondos_owner:abc@127.0.0.1:5436/db\x16", "control characters: 1"),
    ("fondos_owner:abc@127.0.0.1:5436/db", "does not start with postgresql:// or postgres://"),
    ("postgresql://fondos_owner:abc@127.0.0.1:5436/db extra", "whitespace characters: 1")])
def test_the_refusal_describes_the_structure_of_the_entry_and_never_shows_any_of_it(vault, capsys, value, expect):
    assert cli.main(["set", OWNER], vault=vault, console=True, ask=lambda _: value) == 2 and vault.names() == []
    err = capsys.readouterr().err
    assert expect in err and f"length {len(value)} characters" in err
    for fragment in ("fondos_owner", "abc", "127.0.0.1", "5436", "extra"):                 # no piece of the value is echoed (the suggested form uses a placeholder user and password)
        assert fragment not in err.replace("user=fondos_owner password=YOURPASSWORD", "").replace("host=127.0.0.1 port=5436", ""), fragment


def test_check_shows_the_same_structure_for_a_stored_value_of_the_wrong_shape(vault, capsys):
    vault.set(OWNER, "postgresql://fondos_owner:abc%zz@127.0.0.1:5436/db")
    assert cli.main(["check"], vault=vault, console=False) == 1
    out = capsys.readouterr().out
    assert "WRONG SHAPE" in out and "'%' signs not followed by two hex digits: 1" in out and "abc" not in out


# ---------------------------------------------------------------- only the password is typed (--password-only) and a window accepts a paste (--window)

LIVE_ENV = {"FONDOS_PG_DSN": "postgresql://fondos_app:app-pw@127.0.0.1:5436/fondos"}
HARD = "p@ss/w#rd?%:'" + chr(92) + " with space"                                                  # every character that breaks a URI or a key=value pair


def test_the_builder_fills_role_database_host_and_port_and_the_driver_quotes_any_password():
    from psycopg.conninfo import conninfo_to_dict
    d = conninfo_to_dict(cli.build_dsn(OWNER, HARD, environ=LIVE_ENV))
    assert (d["user"], d["dbname"], d["host"], d["port"], d["password"]) == ("fondos_owner", "fondos", "127.0.0.1", "5436", HARD)
    h = conninfo_to_dict(cli.build_dsn(HUMAN, "x", environ={"FONDOS_BACKLOG_PG_DSN": "postgresql://w:w@10.0.0.9:6000/gestion", **LIVE_ENV}))
    assert (h["user"], h["dbname"], h["host"], h["port"]) == ("backlog_human", "gestion", "10.0.0.9", "6000")           # the backlog string is preferred, and it is the same server
    assert conninfo_to_dict(cli.build_dsn(OWNER, "x", environ={}))["host"] == "127.0.0.1"                                  # nothing in the environment: the live server
    assert conninfo_to_dict(cli.build_dsn(OWNER, "x", environ={"FONDOS_PG_DSN": "garbage"}))["port"] == "5436"
    o = conninfo_to_dict(cli.build_dsn(OWNER, "x", user="other", dbname="other_db", host="h", port="1", environ=LIVE_ENV))
    assert (o["user"], o["dbname"], o["host"], o["port"]) == ("other", "other_db", "h", "1")
    with pytest.raises(ValueError):
        cli.build_dsn("GITHUB_ADMIN_TOKEN", "x", environ={})


def test_every_connection_string_of_the_vault_has_a_default_role():
    assert {n for n in ov.VAULT_VARIABLES if "DSN" in n} == set(cli.ROLE_DEFAULTS)


def test_password_only_asks_for_nothing_but_the_password_and_stores_a_string_that_parses(vault, capsys):
    from psycopg.conninfo import conninfo_to_dict
    questions = []

    def ask(prompt):
        questions.append(prompt)
        return HARD

    assert cli.main(["set", OWNER, "--password-only"], vault=vault, console=True, ask=ask, environ=LIVE_ENV) == 0
    stored = conninfo_to_dict(vault.get(OWNER))
    assert stored["password"] == HARD and stored["user"] == "fondos_owner" and stored["dbname"] == "fondos"
    assert len(questions) == 2 and all("password" in q or "again" in q for q in questions)
    out = capsys.readouterr()
    assert "fondos_owner@127.0.0.1:5436/fondos" in out.out and HARD not in out.out + out.err
    assert cli.main(["check"], vault=vault, console=False) == 0


def test_password_only_refuses_empty_or_different_entries_tokens_and_a_missing_console(vault):
    answers = iter(["one", "two"])
    assert cli.main(["set", OWNER, "--password-only"], vault=vault, console=True, ask=lambda _: next(answers), environ=LIVE_ENV) == 2
    assert cli.main(["set", OWNER, "--password-only"], vault=vault, console=True, ask=lambda _: "", environ=LIVE_ENV) == 2
    assert cli.main(["set", GHA, "--password-only"], vault=vault, console=True, ask=lambda _: "x", environ=LIVE_ENV) == 2
    assert cli.main(["set", OWNER, "--password-only"], vault=vault, console=False, ask=lambda _: "x", environ=LIVE_ENV) == 2
    assert vault.names() == []


def test_window_uses_the_window_for_both_entries_and_still_needs_a_console(vault, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "ask_window", lambda prompt: seen.append(prompt) or VALUE)
    assert cli.main(["set", OWNER, "--window"], vault=vault, console=True, ask=lambda _: pytest.fail("the console prompt must not be used"), environ=LIVE_ENV) == 0
    assert len(seen) == 2 and vault.get(OWNER) == VALUE
    assert cli.main(["set", HUMAN, "--window"], vault=vault, console=False, environ=LIVE_ENV) == 2            # an agent's shell cannot fill it, window or not
    monkeypatch.setattr(cli, "ask_window", lambda prompt: "p1" if "again" not in prompt else "p1")
    assert cli.main(["set", ADJ, "--window", "--password-only"], vault=vault, console=True, environ=LIVE_ENV) == 0
    assert vault.get(ADJ) and "backlog_adjudicator" in vault.get(ADJ)


# ---------------------------------------------------------------- check --connect: a wrong password is found when the string is stored, not inside a deployment

class Conn:
    def close(self):
        pass


def test_check_connect_logs_in_with_each_connection_string_and_reports_only_the_verdict(vault, capsys):
    vault.set(OWNER, "host=127.0.0.1 port=5436 dbname=fondos user=fondos_owner password=good-pw")
    vault.set(HUMAN, "host=127.0.0.1 port=5436 dbname=gestion user=backlog_human password=wrong-pw")
    vault.set(GHA, "ghp_token_not_a_dsn")
    tried = []

    def connect(dsn):
        tried.append(dsn)
        if "wrong-pw" in dsn:
            raise RuntimeError('connection failed: FATAL:  password authentication failed for user "backlog_human" (wrong-pw)')
        return Conn()

    assert cli.main(["check", "--connect"], vault=vault, console=False, connect=connect) == 1
    out = capsys.readouterr().out
    assert len(tried) == 2 and "login: OK" in out and "login: FAILED" in out and "password authentication failed for user" in out       # the token is not a connection string: never tried
    assert "good-pw" not in out and "wrong-pw" not in out and "ghp_token" not in out


def test_check_without_connect_never_connects_and_an_all_good_vault_exits_zero(vault, capsys):
    vault.set(OWNER, VALUE)
    assert cli.main(["check"], vault=vault, console=False, connect=lambda dsn: pytest.fail("no login without --connect")) == 0
    assert cli.main(["check", "--connect"], vault=vault, console=False, connect=lambda dsn: Conn()) == 0
    assert "login: OK" in capsys.readouterr().out


def test_try_connect_removes_the_string_and_its_password_from_any_message():
    dsn = "host=h port=1 dbname=d user=u password=sekret-pw"
    why = cli.try_connect(dsn, lambda d: (_ for _ in ()).throw(RuntimeError(f"bad {d} and sekret-pw")))
    assert why.startswith("RuntimeError:") and "sekret-pw" not in why and dsn not in why
