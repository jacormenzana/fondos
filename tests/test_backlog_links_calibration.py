# tests/test_backlog_links_calibration.py
# -*- coding: utf-8 -*-
"""scripts/audit/backlog_links_calibration.py: point-in-time state, known positives and the false-merge count of the replay.

Fixtures only (R-7). The live replay is the script's own report.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_links_calibration as C  # noqa: E402

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _t(i, day, closed_day=None, obj="mod.py", text="", title="t", solution=None):
    return {"action_point_id": f"FND-{i:04d}", "creation_ts": T0 + timedelta(days=day),
            "closure_ts": None if closed_day is None else T0 + timedelta(days=closed_day),
            "object_name": obj, "scenario_description": text, "title": title, "solution_type": solution}


def test_state_as_of_is_creation_le_ts_lt_closure_and_excludes_the_ticket_itself():
    ts = [_t(1, 0, closed_day=5), _t(2, 3), _t(3, 10), _t(4, 1, closed_day=2)]
    ids = lambda at, **kw: [t["action_point_id"] for t in C.state_as_of(ts, T0 + timedelta(days=at), **kw)]
    assert ids(4) == ["FND-0001", "FND-0002"]                      # 3 not yet created, 4 already closed
    assert ids(6) == ["FND-0002"]                                  # 1 closed on day 5
    assert ids(4, exclude="FND-0002") == ["FND-0001"]


def test_known_positives_from_duplicated_tickets_and_confirmed_duplicates_links():
    ts = [_t(1, 0), _t(2, 1, solution="DUPLICATED", text="Closed as duplicate of FND-0001"), _t(3, 2), _t(4, 3)]
    links = [{"from_id": "FND-0004", "to_id": "FND-0003", "link_type": "DUPLICATES", "status": "CONFIRMED"},
             {"from_id": "FND-0004", "to_id": "FND-0001", "link_type": "DUPLICATES", "status": "PROPOSED"},
             {"from_id": "FND-0003", "to_id": "FND-0001", "link_type": "OVERLAPS", "status": "CONFIRMED"}]
    assert C.known_positives(ts, links) == {("FND-0002", "FND-0001"), ("FND-0004", "FND-0003")}


def test_replay_counts_a_high_as_a_false_merge_unless_it_is_a_known_positive():
    same = "psycopg UndefinedColumn real_flag in fund_metric_timeseries writer while computing rolling rows"
    ts = [_t(1, 0, obj="run_pipeline.py", text=same), _t(2, 1, obj="run_pipeline.py", text=same),
          _t(3, 2, obj="other.py", text="completely different subject about benchmark loader tokens")]
    res = C.replay(ts, positives=set(), high=0.70, medium=0.45)
    assert [(r["incident"], r["target"]) for r in res["high"]] == [("FND-0002", "FND-0001")]
    assert res["false_merges"] == res["high"]
    ok = C.replay(ts, positives={("FND-0002", "FND-0001")}, high=0.70, medium=0.45)
    assert ok["high"] and ok["false_merges"] == []


def test_replay_never_merges_into_a_ticket_that_did_not_exist_yet():
    ts = [_t(1, 5, obj="a.py", text="same words repeated exactly here"), _t(2, 0, obj="a.py", text="same words repeated exactly here")]
    res = C.replay(ts, positives=set(), high=0.70, medium=0.45)
    assert [(r["incident"], r["target"]) for r in res["high"]] == [("FND-0001", "FND-0002")]   # only the later one sees the earlier
