# tests/test_backlog_calibrator.py
# -*- coding: utf-8 -*-
"""Phase C slice 3 (FND-0334), no database: scripts/audit/gate_calibrator.py decides from numbers only. It tightens T0 -> T1 on day 10 of AUDIT when the share of commits introducing a pair is
above 10 %, retires the gate (OFF) when a window with enough sample fails the precision or recall floors, extends a window with too little sample (at most 42 days, then reports
insufficient data and retires nothing), and never lowers BLOCK to AUDIT, never loosens, never acts on a gate that is already OFF."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gate_calibrator as cal  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
AUDIT_T0 = {"mode": "AUDIT", "tier": "T0"}


def days_ago(n):
    return NOW - timedelta(days=n)


def win(adjudicated=20, positive=10, confirmed=12, flagged=6):
    return {"adjudicated": adjudicated, "positive": positive, "confirmed": confirmed, "flagged": flagged}


def ev(state=AUDIT_T0, start=14, anchor=14, share=0.05, window=None):
    return cal.evaluate(state, NOW, days_ago(start) if start is not None else None, days_ago(anchor) if anchor is not None else None, share, window or win())


def test_nothing_to_do_while_the_gate_is_off_or_no_audit_started():
    assert ev({"mode": "OFF", "tier": "T0"})["action"] == "NONE" and ev({"mode": "OFF", "tier": "T0"})["verdict"] == "OFF"
    r = ev(start=None, anchor=None)
    assert r["action"] == "NONE" and r["verdict"] == "NO_DATA"


def test_day_10_tightens_only_above_10_percent_and_only_once():
    assert ev(start=9, anchor=9, share=0.5)["action"] == "NONE"                          # day 9: too early
    r = ev(start=10, anchor=10, share=0.11)
    assert r["action"] == "TIGHTEN" and "T0 -> T1" in r["reason"] and r["evidence"]["share_3d"] == 0.11
    assert ev(start=10, anchor=10, share=0.10)["action"] == "NONE"                       # 10 % exactly is not above
    assert ev(start=10, anchor=10, share=None)["action"] == "NONE"                       # nothing evaluated in 3 days
    assert ev({"mode": "AUDIT", "tier": "T1"}, start=12, anchor=12, share=0.9)["action"] == "NONE"   # already T1: never repeated, never loosened
    assert ev({"mode": "BLOCK", "tier": "T0"}, start=30, anchor=3, share=0.9)["action"] == "NONE"     # BLOCK is not tightened by this rule


def test_a_window_needs_14_days_and_the_sample_floor_before_any_verdict():
    assert ev(start=13, anchor=13, window=win(positive=0))["verdict"] == "WINDOW_OPEN"                  # 13 days: no retirement even with no precision
    r = ev(window=win(adjudicated=14, positive=0))
    assert r["action"] == "NONE" and r["verdict"] == "EXTENDING" and "14/15" in r["reason"]
    r = ev(window=win(confirmed=9, flagged=0))
    assert r["action"] == "NONE" and r["verdict"] == "EXTENDING" and "9/10" in r["reason"]


def test_after_42_days_without_sample_it_reports_insufficient_data_and_retires_nothing():
    r = ev(start=50, anchor=42, window=win(adjudicated=2, positive=0, confirmed=1, flagged=0))
    assert r["action"] == "NONE" and r["verdict"] == "INSUFFICIENT_DATA" and "nothing is retired" in r["reason"]


@pytest.mark.parametrize("window, why", [
    (win(adjudicated=20, positive=5, confirmed=12, flagged=6), "precision 25% < 30%"),
    (win(adjudicated=20, positive=10, confirmed=20, flagged=0), "recall floor 0% < 5%"),
])
def test_a_failed_floor_retires_the_gate_with_the_numbers(window, why):
    r = ev(window=window)
    assert r["action"] == "RETIRE" and why in r["reason"] and "precision" in r["evidence"] and "recall_floor" in r["evidence"]
    for state in ({"mode": "AUDIT", "tier": "T0"}, {"mode": "BLOCK", "tier": "T1"}):               # in AUDIT and in BLOCK
        assert ev(state, window=window)["action"] == "RETIRE"


def test_the_floors_at_their_exact_limits_pass_and_a_good_window_keeps_the_gate():
    r = ev(window=win(adjudicated=20, positive=6, confirmed=20, flagged=1))                        # precision 30 %, recall 5 %: both exactly at the floor
    assert r["action"] == "NONE" and r["verdict"] == "OK" and r["evidence"]["precision"] == 0.3 and r["evidence"]["recall_floor"] == 0.05


def test_retirement_comes_before_tightening():
    r = ev(window=win(adjudicated=20, positive=1), share=0.9)                                      # both would apply: a failing gate is retired, not tightened
    assert r["action"] == "RETIRE"


def test_a_valid_window_does_not_block_the_tightening():
    r = ev(window=win(), share=0.2)
    assert r["action"] == "TIGHTEN" and r["verdict"] == "OK"


def test_report_lines_start_with_alerta_on_a_retirement():
    lines = cal.report_lines(ev(window=win(adjudicated=20, positive=1)))
    assert lines[0].startswith("[ALERTA] collision gate would be RETIRED") and "precision" in lines[0]
    assert cal.report_lines(ev(window=win(adjudicated=20, positive=1)), applied=True)[0].startswith("[ALERTA] collision gate RETIRED")
    quiet = cal.report_lines(ev())
    assert not quiet[0].startswith("[ALERTA]") and "verdict OK" in quiet[-1]
