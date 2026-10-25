"""FND-0317 (monitor v2, narrow slice): headroom per rule, a lag warning and a reachability check, all MONITOR-ONLY and without any threshold change.

Pure: synthetic yield series, no database, no network. The thresholds stay pinned by test_sovereign_stress_fnd0294.py; this file pins that the
headroom is the distance to those same thresholds (0 exactly when the rule is ON), that a late feed is named, and that a rule which never fired in a
long history is flagged as possibly unreachable.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from shared import sovereign_stress as ss

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("p1p2_cycle_report_v2_t", _ROOT / "scripts/launch/p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

BASE_LEVEL = {"DE": 3.0, "IT": 3.9, "ES": 3.5, "FR": 3.4, "US": 4.5, "GB": 4.8, "JP": 1.0}          # IT-DE 0.9, ES-DE 0.5, FR-DE 0.4


def _months(first, last):
    out, (y, m) = [], map(int, first.split("-"))
    while f"{y:04d}-{m:02d}" <= last:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _y(first="2016-01", last="2026-09", **override):
    ms = _months(first, last)
    return {c: {m: (override[c](m) if c in override else lvl) for m in ms} for c, lvl in BASE_LEVEL.items()}


def _lift(country, tail_months, deltas):
    tail = dict(zip(tail_months, deltas))
    return lambda m: BASE_LEVEL[country] + tail.get(m, 0.0)


LAST3 = ["2026-07", "2026-08", "2026-09"]


def test_headroom_is_the_distance_to_each_pinned_threshold_in_a_calm_market():
    h = ss.monitor(_y())["headroom"]
    assert h["R1"] == pytest.approx(ss.RULE_PARAMS["periphery_level_pp"] - 0.9)          # IT-DE 0.9 vs 2.0
    assert h["R2"] == pytest.approx(ss.RULE_PARAMS["periphery_widening_pp"])             # no widening yet
    assert h["R4"] == pytest.approx(ss.RULE_PARAMS["shock_strict_pp"])
    assert h["R5"] == pytest.approx(ss.RULE_PARAMS["shock_pp"])


def test_headroom_reads_like_the_report_example_and_shrinks_as_the_market_moves():
    res = ss.monitor(_y(US=_lift("US", LAST3, [0.1, 0.3, 0.52])))                         # the 2026-09 US reading: +0.52pp in 3 months
    assert res["headroom"]["R5"] == pytest.approx(0.23) and res["headroom"]["R4"] == pytest.approx(0.48)
    more = ss.monitor(_y(US=_lift("US", LAST3, [0.1, 0.3, 0.70])))
    assert more["headroom"]["R5"] < res["headroom"]["R5"]


@pytest.mark.parametrize("name,override", [
    ("calm", {}),
    ("R1", {"IT": _lift("IT", ["2026-08", "2026-09"], [1.3, 1.4])}),
    ("R2", {"ES": _lift("ES", LAST3, [0.2, 0.4, 0.6])}),
    ("R4/R5", {"US": _lift("US", LAST3, [0.3, 0.7, 1.1])}),
    ("R5 only", {"GB": _lift("GB", LAST3, [0.2, 0.5, 0.8])}),
])
def test_a_rule_is_on_exactly_when_its_headroom_is_zero(name, override):
    res = ss.monitor(_y(**override))
    for rid in ("R1", "R2", "R4", "R5"):
        assert (res["headroom"][rid] == 0.0) == res["rules"][rid], f"{name}: {rid}"


def test_r3_headroom_is_the_widening_still_missing_and_zero_when_the_watch_is_on():
    flat = ss.monitor(_y())
    assert flat["headroom"]["R3"] == pytest.approx(ss.RULE_PARAMS["core_widening_pp"])
    on = ss.monitor(_y(FR=_lift("FR", ["2026-06", "2026-07", "2026-08", "2026-09"], [0.0, 0.2, 0.4, 0.6])))
    assert on["rules"]["R3"] is True and on["headroom"]["R3"] == 0.0


def test_r3_headroom_is_not_applicable_when_the_percentile_leg_is_off():
    # FR-DE spread was high for years and has fallen to a level inside the bulk of its own history: the percentile leg cannot be met by widening alone
    ms = _months("2016-01", "2026-09")
    fr = {m: 3.0 + (1.2 if m < "2026-01" else 0.1) for m in ms}
    h = ss.monitor(_y(FR=lambda m: fr[m]))["headroom"]
    assert h["R3"] is None


def test_the_status_line_shows_the_margin_of_rules_that_are_off_and_not_of_rules_that_are_on():
    off = ss.status_line(ss.monitor(_y(US=_lift("US", LAST3, [0.1, 0.3, 0.52]))))
    assert "margen" in off and "R5 +0.23pp" in off and "R4 +0.48pp" in off
    on = ss.status_line(ss.monitor(_y(US=_lift("US", LAST3, [0.3, 0.7, 1.1]))))
    margin = on.split("margen", 1)[1] if "margen" in on else ""
    assert "R5 +" not in margin and "R4 +" not in margin


# ---------------------------------------------------------------- lag warning

def test_a_feed_two_months_behind_is_named_but_one_month_is_normal_publication_lag():
    one = _y()
    del one["JP"]["2026-09"]
    res1 = ss.monitor(one)
    assert res1["month"] == "2026-08" and res1["lagging"] == {"JP": 1}
    assert ss.attention_lines(res1) == []                                              # one month behind: no noise

    two = _y()
    del two["JP"]["2026-09"], two["JP"]["2026-08"]
    res2 = ss.monitor(two)
    assert res2["month"] == "2026-07" and res2["lagging"] == {"JP": 2}
    lines = ss.attention_lines(res2)
    assert len(lines) == 1 and "JP" in lines[0] and "2026-07" in lines[0] and "2026-09" in lines[0]


# ---------------------------------------------------------------- reachability

def test_a_long_history_where_a_rule_never_fired_flags_it_as_possibly_unreachable():
    y = _y(first="2005-01", IT=_lift("IT", ["2010-09", "2010-10", "2010-11"], [1.5, 1.7, 1.9]))      # only the periphery rules can fire
    res = ss.monitor(y)
    assert res["reach_assessed"] is True and res["reach_months"] >= ss.REACHABILITY_MIN_MONTHS
    assert res["unreachable"] == ["R3", "R4", "R5"]
    lines = ss.attention_lines(res)
    assert any("R3, R4, R5" in ln and "inalcanzable" in ln for ln in lines)


def test_a_short_history_is_not_assessed_so_a_calm_synthetic_market_stays_silent():
    res = ss.monitor(_y())                                                              # 129 months < REACHABILITY_MIN_MONTHS
    assert res["reach_assessed"] is False and res["unreachable"] == []
    assert ss.attention_lines(res) == []


def test_every_pinned_threshold_was_reached_in_a_history_that_contains_its_episode():
    ms = ["2010-09", "2010-10", "2010-11"]
    y = _y(first="2005-01",
           IT=_lift("IT", ms, [1.5, 1.7, 1.9]),
           FR=_lift("FR", ["2011-06", "2011-07", "2011-08", "2011-09"], [0.0, 0.3, 0.6, 0.9]),
           US=_lift("US", ["2022-09", "2022-10", "2022-11"], [0.4, 0.9, 1.3]))
    res = ss.monitor(y)
    assert res["reach_assessed"] is True and res["unreachable"] == []


def test_the_rules_are_not_changed_by_the_new_fields():
    y = _y(US=_lift("US", LAST3, [0.2, 0.5, 0.8]))
    assert ss.monitor(y)["rules"] == {rid: bool(pred("2026-09")) for rid, (_, _, pred) in ss.build_rules(y).items()}


# ---------------------------------------------------------------- the cycle report carries the new fields

class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=()):
        return self

    def fetchall(self):
        return self._rows

    def rollback(self):
        pass


def test_the_collector_exposes_headroom_and_lag_for_the_cycle_json():
    y = _y(US=_lift("US", LAST3, [0.1, 0.3, 0.52]))
    rows = [(date(int(m[:4]), int(m[5:]), 1), c, v) for c, s in y.items() for m, v in s.items()]
    out = cr.collect_sovereign_stress(_Conn(rows))
    assert out["headroom"]["R5"] == pytest.approx(0.23) and out["lagging"] == {} and "margen" in out["status"]
