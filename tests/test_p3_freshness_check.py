"""
tests/test_p3_freshness_check.py -- the read-only freshness verdict of P1_P2_Complete.bat.

The gate itself (proyecto3/src/data_freshness.py) is tested in its own module; this only proves the
launcher-facing wrapper maps "something is stale" to the exit code P3 itself uses.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "p3_freshness_check", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p3_freshness_check.py")
fc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fc)


def test_nothing_stale_is_rc_0():
    assert fc.verdict_rc([], 2) == 0


def test_anything_stale_is_the_exit_code_p3_would_use():
    assert fc.verdict_rc(["nav_p10"], 2) == 2


def test_the_launcher_documents_rc_2_as_p3_would_refuse():
    from shared.config import P3_EXIT_STALE_INPUTS
    assert P3_EXIT_STALE_INPUTS == 2
