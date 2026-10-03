"""
tests/test_harvest_gate.py -- the shrink gate of P1_harvestFunds.bat (pure logic, no database).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "harvest_gate", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "harvest_gate.py")
hg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hg)


def test_stable_catalogue_passes():
    ok, _ = hg.evaluate(("b", 33000, 3100), ("a", 33800, 3150), 10.0, 1000)
    assert ok


def test_row_drop_beyond_threshold_fails():
    ok, msgs = hg.evaluate(("b", 20000, 3100), ("a", 33800, 3150), 10.0, 1000)
    assert not ok and any("rows dropped" in m for m in msgs)


def test_isin_drop_beyond_threshold_fails_even_if_rows_hold():
    ok, msgs = hg.evaluate(("b", 33800, 1500), ("a", 33800, 3150), 10.0, 1000)
    assert not ok and any("isins dropped" in m for m in msgs)


def test_growth_passes():
    ok, _ = hg.evaluate(("b", 40000, 3300), ("a", 33800, 3150), 10.0, 1000)
    assert ok


def test_empty_catalogue_fails_min_rows_without_previous():
    ok, _ = hg.evaluate(("a", 12, 3), None, 10.0, 1000)
    assert not ok


def test_first_harvest_above_min_rows_passes():
    ok, msgs = hg.evaluate(("a", 33800, 3150), None, 10.0, 1000)
    assert ok and any("no previous" in m for m in msgs)
