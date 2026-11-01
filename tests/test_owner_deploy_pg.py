# tests/test_owner_deploy_pg.py
# -*- coding: utf-8 -*-
"""The restore-point step of the AUTO deployment (scripts/ops/owner_deploy.py RESTORE_POINT_CODE) run for real against a throwaway PostgreSQL: the child process takes the owner string from its
environment, creates a named restore point and prints it. Skipped without FONDOS_TEST_PG_DSN (python scripts/ops/run_pg_tests.py supplies one)."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import owner_deploy as od  # noqa: E402

DSN = os.environ.get("FONDOS_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="FONDOS_TEST_PG_DSN is not set (python scripts/ops/run_pg_tests.py supplies a throwaway server)")


def run_child(name: str, dsn: str):
    env = {**os.environ, od.OWNER_VAR: dsn, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-c", od.RESTORE_POINT_CODE, name], env=env, capture_output=True, text=True, timeout=60)


def test_the_child_creates_a_named_restore_point_from_the_owner_string_in_its_environment():
    name = od.restore_point_name("c" * 40, datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))
    r = run_child(name, DSN)
    assert r.returncode == 0, r.stderr[-300:]
    assert r.stdout.startswith("RESTORE_POINT ") and len(r.stdout.split()) == 2                       # the LSN of the restore point


def test_a_server_that_refuses_the_restore_point_makes_the_child_fail_so_that_nothing_is_applied():
    bad = DSN.replace("postgres@", "nobody_here@", 1) if "postgres@" in DSN else DSN + "&user=nobody_here"
    r = run_child("backlog_deploy_test", bad)
    assert r.returncode != 0 and "Traceback" in r.stderr
