"""
tests/test_mirror_guard.py — the legacy SQLite->Superset mirror must refuse to run once Postgres is
the primary store (FND-0069, 2026-09-23), otherwise it would publish a frozen SQLite snapshot as if
it were current.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.load_fondos_to_postgres import primary_backend_refusal  # noqa: E402


def test_runs_normally_while_sqlite_is_primary():
    assert primary_backend_refusal({}) is None
    assert primary_backend_refusal({"FONDOS_DB_BACKEND": "sqlite"}) is None


def test_refuses_when_postgres_is_primary():
    for v in ("postgres", "POSTGRES", " postgres "):
        msg = primary_backend_refusal({"FONDOS_DB_BACKEND": v})
        assert msg and "REFUSING" in msg and "FND-0069" in msg


def _run_main(tmp_path, extra_env):
    """Drive the real main() with harmless targets: if the guard were broken it would fall through
    to a nonexistent SQLite path and an unreachable Postgres, never to real data."""
    code = ("import shared.load_fondos_to_postgres as m;"
            "m.SQLITE_PATH='does_not_exist.sqlite';"
            "m.PG_URL='postgresql+psycopg2://x:x@127.0.0.1:1/x';"
            "m.main()")
    env = {k: v for k, v in os.environ.items() if k != "FONDOS_DB_BACKEND"}
    env.update({"PYTHONPATH": str(_ROOT), **extra_env})
    return subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def test_main_exits_3_when_backend_comes_from_a_real_env_var(tmp_path):
    r = _run_main(tmp_path, {"FONDOS_DB_BACKEND": "postgres"})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "REFUSING TO RUN" in r.stderr


def test_main_exits_3_when_backend_comes_from_the_env_file(tmp_path):
    """The real launcher path: no env var set, only .env (via FONDOS_ENV_FILE here)."""
    f = tmp_path / "scratch.env"
    f.write_text("FONDOS_DB_BACKEND=postgres\n", encoding="utf-8")
    r = _run_main(tmp_path, {"FONDOS_ENV_FILE": str(f)})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "REFUSING TO RUN" in r.stderr
