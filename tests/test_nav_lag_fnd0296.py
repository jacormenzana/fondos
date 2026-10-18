"""
tests/test_nav_lag_fnd0296.py -- the cycle report's NAV-lag line (scripts/launch/p1p2_cycle_report.py: nav_lag_summary, collect_nav_lag).

Why: the 2026-10-18 NAV load was PARTIAL (752 of 3,739 funds without data, FND-0295) and nothing reported it: the report only counted NAV older than 60 days.
578 active funds sat a week behind the rest; the audit's 5-day cross-section tolerance then held them out and the promotion gate refused the DRAFT.
The centrepiece replays the real distribution of newest NAV dates of that night. R-7: pure functions and a fake connection, no database.
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "p1p2_cycle_report_lag", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)

# newest monthly NAV date -> number of ACTIVE funds, as read on 2026-10-18 (2,934 funds)
REAL = {"2026-09-30": 3, "2026-10-06": 2, "2026-10-07": 1, "2026-10-08": 89, "2026-10-09": 489, "2026-10-10": 3, "2026-10-14": 17,
        "2026-10-15": 559, "2026-10-16": 1738, "2026-10-18": 23}


def _funds(dist):
    out, n = {}, 0
    for d, k in dist.items():
        for _ in range(k):
            out[f"LU{n:010d}"] = date.fromisoformat(d)
            n += 1
    return out


def test_the_real_distribution_flags_exactly_the_funds_a_week_behind():
    s = cr.nav_lag_summary(_funds(REAL))
    assert s["funds"] == 2924 and s["reference"] == "2026-10-16"
    assert s["lagging"] == 3 + 2 + 1 + 89 + 489 + 3 == 587                     # everything before 2026-10-11 (reference - 5 days)
    assert round(100 * s["share"]) == 20 and s["oldest"] == "2026-09-30"


def test_a_few_weekend_stamped_points_do_not_move_the_reference():
    """23 funds carry a Sunday 2026-10-18 point; the maximum would be 10-18, the 90th percentile stays at the last trading day."""
    with_outliers = cr.nav_lag_summary(_funds(REAL))
    without = cr.nav_lag_summary(_funds({d: k for d, k in REAL.items() if d != "2026-10-18"}))
    assert with_outliers["reference"] == without["reference"] == "2026-10-16"
    assert with_outliers["lagging"] == without["lagging"]


def test_the_tolerance_edge_is_inclusive_of_five_days():
    newest = {"A": date(2026, 10, 16), "B": date(2026, 10, 11), "C": date(2026, 10, 10)}
    newest.update({f"X{i}": date(2026, 10, 16) for i in range(40)})            # the reference is the 16th
    s = cr.nav_lag_summary(newest)
    assert s["reference"] == "2026-10-16" and s["lagging"] == 1 and s["examples"] == ["C"]     # 5 days behind is fine, 6 is not


def test_a_fully_current_universe_and_an_empty_one_report_nothing():
    assert cr.nav_lag_summary({f"I{i}": date(2026, 10, 16) for i in range(50)})["lagging"] == 0
    assert cr.nav_lag_summary({}) == {"funds": 0, "reference": None, "lagging": 0, "share": 0.0, "examples": []}


def _report(nav_lag):
    return {"universe": {"active": 1, "inactive": 0, "total": 1}, "wrong_doc_stale": [], "nav_stale": [], "real_orphans": {}, "nav_lag": nav_lag}


def test_the_attention_item_names_the_count_the_reference_and_the_remedy():
    items = cr.attention_items(_report(cr.nav_lag_summary(_funds(REAL))), None)
    text = " | ".join(i for i in items if "carga NAV parcial" in i)
    assert "587 de 2924" in text and "2026-10-16" in text and "DATA_COVERAGE" in text and "nav_discovery --mode load" in text


def test_a_small_lag_is_not_an_attention_item_and_an_error_is():
    small = {**{f"I{i}": date(2026, 10, 16) for i in range(97)}, **{f"L{i}": date(2026, 10, 1) for i in range(3)}}      # 3%
    assert not [i for i in cr.attention_items(_report(cr.nav_lag_summary(small)), None) if "carga NAV parcial" in i]
    err = cr.attention_items(_report({"error": "RuntimeError: db down"}), None)
    assert any("desfase de NAV" in i and "db down" in i for i in err)


def test_a_report_written_before_this_check_still_renders():
    assert not [i for i in cr.attention_items({"universe": {"active": 1, "inactive": 0, "total": 1}, "wrong_doc_stale": [], "nav_stale": [],
                                               "real_orphans": {}}, None) if "NAV" in i and "parcial" in i]


class _Conn:
    def __init__(self, rows):
        self._rows, self.sql = rows, []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        return self

    def fetchall(self):
        return self._rows

    def rollback(self):
        pass


def test_collect_reads_the_active_universe_and_never_raises():
    conn = _Conn([("A", date(2026, 10, 16)), ("B", date(2026, 10, 8))] + [(f"X{i}", date(2026, 10, 16)) for i in range(18)])
    out = cr.collect_nav_lag(conn)
    assert out["funds"] == 20 and out["lagging"] == 1 and "in_current_universe = 1" in conn.sql[0]
    assert "<> 'STALE_FROZEN'" in conn.sql[0]                  # structurally frozen funds (Russia/EE, newest NAV 2022) are reported apart, not as a partial load

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def rollback(self):
            pass
    assert "RuntimeError" in cr.collect_nav_lag(Boom())["error"]
