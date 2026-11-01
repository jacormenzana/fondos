# tests/test_backlog_digest.py
# -*- coding: utf-8 -*-
"""scripts/audit/backlog_digest.py: the weekly read-only digest (Backlog v3.1). Rendering, the once-a-week file and a block that cannot be read. No database, no network (R-7)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_digest as dg  # noqa: E402

FACTS = {"by_status": {"CLOSED": 240, "IN_PROGRESS": 25}, "moves_by_actor_kind": {"agent": {"n": 30, "forced": 0}, "human": {"n": 10, "forced": 2}, "system": {"n": 4, "forced": 0}},
         "reopens_by_cause": {"INCOMPLETE_FIX": 2}, "open_holds": 0, "idle_in_progress": 15, "deploy_facts": 3, "undecided_pairs": 0}
RECORD = {"mode": "SHADOW", "decisions": 7, "contradicted": 0, "reopened_closures": 0}
A2 = {"live_non_system_since_last_v00": 20, "v00_total": 0, "last_v00_ts": None}
VIOL = {"applied": True, "total": 96, "by_code": [], "top": [{"code": "V-FAST", "n": 79}]}
TODAY = date(2026, 11, 1)


def test_the_week_key_is_the_iso_week_across_a_year_boundary():
    assert dg.week_key(date(2026, 11, 1)) == "2026-W44" and dg.week_key(date(2026, 1, 1)) == "2026-W01" and dg.week_key(date(2025, 12, 29)) == "2026-W01"
    assert dg.week_key(date(2027, 1, 1)) == "2026-W53"


def test_the_digest_leads_with_how_many_transitions_a_person_made_and_shows_every_block():
    text = dg.render(FACTS, RECORD, A2, VIOL, TODAY)
    assert text.startswith("# Backlog digest 2026-W44") and "**10 of 44 transitions were made by a person** (23%)" in text and "human: 10 (forced: 2)" in text
    assert "INCOMPLETE_FIX: 2" in text and "mode SHADOW" in text and "7 decision(s) to act" in text and "CLOSED: 240" in text
    assert "idle for 14 days or more: 15" in text and "DEPLOY facts recorded this week: 3" in text and "violations on record: 96 (V-FAST 79)" in text and "20 of 50" in text
    assert "asks nothing" in text and "nothing here needs approval" in text


def test_a_block_that_cannot_be_read_says_so_and_an_empty_week_does_not_divide_by_zero():
    text = dg.render({k: None for k in FACTS}, None, None, None, TODAY)
    assert text.count("not available") >= 5 and "A-2 gate" not in text and "violations on record" not in text
    quiet = dg.render({**FACTS, "moves_by_actor_kind": {}, "reopens_by_cause": {}}, RECORD, A2, {"applied": False}, TODAY)
    assert "**0 of 0 transitions were made by a person**" in quiet and "- none" in quiet


def test_the_digest_is_written_once_per_week_and_a_new_week_gets_a_new_file(tmp_path):
    built = []
    path = dg.write_if_due(lambda: built.append(1) or "week one", TODAY, tmp_path / "out")
    assert path == tmp_path / "out" / "2026-W44.md" and path.read_text(encoding="utf-8") == "week one"
    assert dg.write_if_due(lambda: built.append(1) or "again", date(2026, 10, 28), tmp_path / "out") is None and len(built) == 1          # same ISO week: nothing is built, nothing changes
    assert path.read_text(encoding="utf-8") == "week one"
    assert dg.write_if_due(lambda: "week two", date(2026, 11, 2), tmp_path / "out").name == "2026-W45.md"


def test_build_text_survives_blocks_whose_objects_are_not_applied_yet():
    class Bc:
        def digest_facts(self):
            return FACTS

        def autopilot_track_record(self):
            raise RuntimeError("relation reopen_event does not exist")

        def shadow_progress(self):
            raise RuntimeError("A-1 not applied")

        def violation_summary(self):
            return VIOL

    text = dg.build_text(Bc(), TODAY)
    assert "## Autopilot\n- not available" in text and "A-2 gate" not in text and "violations on record: 96" in text


def test_the_runner_writes_the_digest_but_never_fails_because_of_it():
    src = (_ROOT / "scripts" / "audit" / "backlog_autopilot.py").read_text(encoding="utf-8")
    block = src.split("import backlog_digest")[1].split("print(summary)")[0]
    assert "write_if_due" in block and "except Exception" in block
