"""
tests/test_env_file.py — the central backend switch (FND-0068, 2026-09-23).

Background: .env.example told people to "copy to .env" but nothing loaded it, and no launcher sets
FONDOS_DB_BACKEND / FONDOS_PG_DSN. After cutover a single step that missed the switch would keep
writing to the retired SQLite with exit code 0 while the rest of a run wrote to Postgres. These
tests pin the mechanism that closes that: one env file read by shared/config.py (real environment
variables always win, never loaded under pytest), and a once-per-process line saying which backend a
process really used.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.db as db  # noqa: E402
from shared.config import load_env_file  # noqa: E402


def test_loads_key_value_lines_ignoring_comments_blanks_and_junk(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# comment\n\nFONDOS_DB_BACKEND=postgres\nFOO = "quoted value"\nBAR=\'single\'\n'
                 "no_equals_sign\nEMPTY=\n", encoding="utf-8")
    env: dict = {}
    loaded = load_env_file(f, env)
    assert env == {"FONDOS_DB_BACKEND": "postgres", "FOO": "quoted value", "BAR": "single", "EMPTY": ""}
    assert loaded == env


def test_a_real_environment_variable_always_beats_the_file(tmp_path):
    """The safety valve: an operator can override the file for one run without editing it."""
    f = tmp_path / ".env"
    f.write_text("FONDOS_DB_BACKEND=postgres\nOTHER=from_file\n", encoding="utf-8")
    env = {"FONDOS_DB_BACKEND": "sqlite"}
    loaded = load_env_file(f, env)
    assert env["FONDOS_DB_BACKEND"] == "sqlite"          # not overwritten
    assert env["OTHER"] == "from_file"
    assert "FONDOS_DB_BACKEND" not in loaded             # reports only what it actually set


def test_missing_or_unreadable_file_is_not_an_error(tmp_path):
    env: dict = {"KEEP": "1"}
    assert load_env_file(tmp_path / "does_not_exist.env", env) == {}
    assert env == {"KEEP": "1"}


def test_passwords_in_values_are_kept_verbatim(tmp_path):
    f = tmp_path / ".env"
    f.write_text("PGPASSWORD=p@ss=word*08\n", encoding="utf-8")      # '=' inside the value
    env: dict = {}
    load_env_file(f, env)
    assert env["PGPASSWORD"] == "p@ss=word*08"


def test_env_file_is_never_autoloaded_under_pytest(monkeypatch, tmp_path):
    """A developer's .env with FONDOS_DB_BACKEND=postgres must not silently redirect the test suite."""
    from shared import config
    f = tmp_path / ".env"
    f.write_text("FONDOS_TEST_ONLY_SENTINEL=leaked\n", encoding="utf-8")
    monkeypatch.setenv("FONDOS_ENV_FILE", str(f))
    monkeypatch.delenv("FONDOS_TEST_ONLY_SENTINEL", raising=False)
    config._autoload_env_file()
    import os
    assert "FONDOS_TEST_ONLY_SENTINEL" not in os.environ


def test_unset_backend_resolves_to_postgres(monkeypatch):
    """FND-0102: with FONDOS_DB_BACKEND unset, get_connection() resolves to postgres, so an unset
    FONDOS_PG_DSN fails loudly (there is no other backend to fall back to)."""
    monkeypatch.delenv("FONDOS_DB_BACKEND", raising=False)
    monkeypatch.delenv("FONDOS_PG_DSN", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="FONDOS_PG_DSN"):
        db.get_connection()


def test_sqlite_backend_no_longer_exists(monkeypatch):
    import pytest
    monkeypatch.setenv("FONDOS_PG_DSN", "postgresql://x@127.0.0.1:1/x")
    with pytest.raises(ValueError, match="SQLite fue retirado"):
        db.get_connection(backend="sqlite")
    monkeypatch.setenv("FONDOS_DB_BACKEND", "sqlite")
    with pytest.raises(ValueError, match="SQLite fue retirado"):
        db.get_connection()


def test_postgres_announcement_shows_host_port_dbname_and_nothing_else(monkeypatch, capsys):
    """The DSN can carry a user name and (against the repo convention) a password; only
    host/port/dbname are ever printed."""
    from types import SimpleNamespace
    monkeypatch.setattr(db, "_BACKEND_ANNOUNCED", False)
    conn = SimpleNamespace(info=SimpleNamespace(host="db.example", port=5432, dbname="fondos"),
                           dsn="postgresql://user:SECRET@db.example:5432/fondos")
    db._announce_backend(conn, "postgres", "env")
    err = capsys.readouterr().err
    assert err.strip() == "[DB] backend=postgres (env) host=db.example port=5432 dbname=fondos"
    assert "SECRET" not in err and "user" not in err


def test_importing_shared_db_autoloads_the_env_file_in_a_clean_process(tmp_path):
    """Regression (2026-09-26): shared.db used to import shared.config only for DB_PATH; deleting DB_PATH
    silently removed the .env autoload from every entry point, so no process found FONDOS_PG_DSN.
    Runs in a subprocess because autoload is (deliberately) disabled under pytest."""
    import subprocess
    import sys
    f = tmp_path / "test.env"
    f.write_text("FONDOS_PG_DSN=postgresql://autoload@127.0.0.1:1/x\n", encoding="utf-8")
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("FONDOS_PG_DSN", "PYTEST_CURRENT_TEST")}
    env["FONDOS_ENV_FILE"] = str(f)
    out = subprocess.run(
        [sys.executable, "-c", "import shared.db, os; print(os.environ.get('FONDOS_PG_DSN'))"],
        cwd=str(Path(__file__).resolve().parent.parent), env=env, capture_output=True, text=True, timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert out.stdout.strip() == "postgresql://autoload@127.0.0.1:1/x", out.stderr[-400:]
