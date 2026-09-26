"""
tests/test_launcher_state_guard.py — the REFUSAL paths of P1_P2_Complete.bat, end to end.

The decisions themselves are tested in tests/test_p1p2_state.py (pure Python, any OS). This file only
proves the batch wrapper wires the helper's exit codes through correctly. Every case here exits in
the pre-run check, before a log is created and before any step starts, so nothing else runs. Allowed
paths are deliberately NOT exercised here: `--from 1` after a failed run would start the real chain.
Windows only, because the launcher is a .bat (every canonical launcher in this repo is).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "scripts" / "launch" / "P1_P2_Complete.bat"
PYTHON = Path(r"C:\data\envs\des\python.exe")     # the interpreter the launcher itself hard-codes

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not PYTHON.exists(),
    reason="needs Windows and the launcher's conda interpreter",
)


def _run(state: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "P1P2_STATE_FILE": str(state)}
    return subprocess.run(["cmd", "/c", str(BAT), *args], env=env, capture_output=True,
                          text=True, timeout=120, cwd=str(ROOT))


def _seed(path: Path, result="FAILED", step=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"LAST_RESULT={result}\nFAILED_STEP={step}\nSTAMP=20260926_101500\nLAST_FROM_ANY=0\n")


def test_from_a_later_step_than_the_one_that_failed_is_refused_with_rc_2(tmp_path):
    state = tmp_path / "state"
    _seed(state, step=1)
    r = _run(state, "--from", "3")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "REFUSED" in r.stdout


def test_from_without_any_previous_state_is_refused_fail_closed(tmp_path):
    r = _run(tmp_path / "missing" / "state", "--from", "2")
    assert r.returncode == 2, r.stdout + r.stderr


def test_from_after_a_successful_run_is_refused(tmp_path):
    state = tmp_path / "state"
    _seed(state, result="OK", step=0)
    assert _run(state, "--from", "1").returncode == 2


def test_out_of_range_step_is_a_usage_error_rc_4(tmp_path):
    state = tmp_path / "state"
    _seed(state, step=3)
    assert _run(state, "--from", "9").returncode == 4


def test_unknown_argument_is_a_usage_error_rc_4_and_runs_nothing(tmp_path):
    r = _run(tmp_path / "state", "--frobnicate")
    assert r.returncode == 4 and "Argumento desconocido" in r.stdout


def test_unwritable_state_path_aborts_before_any_step_rc_3(tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    r = _run(blocker / "child" / "state", "--from", "1")
    assert r.returncode == 3, r.stdout + r.stderr


def test_refusals_create_no_log_file(tmp_path):
    before = set((ROOT / "proyecto1" / "log").glob("log_P1_P2_complete_*.log"))
    state = tmp_path / "state"
    _seed(state, step=1)
    _run(state, "--from", "4")
    assert set((ROOT / "proyecto1" / "log").glob("log_P1_P2_complete_*.log")) == before
