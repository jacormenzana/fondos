# tests/test_timeseries_coverage_audit_fnd0443.py
# -*- coding: utf-8 -*-
"""FND-0443 (c): the TIMESERIES_WINDOW_COVERAGE audit rule and its liveness canary. ROLLING_COVERAGE judges the headline `rolling_Ny` values only, so partial-window points
stayed unseen in fund_metric_timeseries until a deflator identity broke. The span condition of FND-0427 is applied to the EARLIEST stored point of each fund and window (the
strictest one): one aggregate query covers the table. Active only while ROLLING_UNCOVERED_PRUNE_ENABLED is ON (until then the leftovers are the known state). Hermetic (R-7)."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd

from shared import config
from shared.statistical_audit import canaries as cn
from shared.statistical_audit import completeness as cpl
from shared.statistical_audit import window_coverage as wc

WINDOWS = {"rolling_1y": 12, "rolling_3y": 36, "rolling_10y": 120}


def _stored(*rows):
    return pd.DataFrame(list(rows), columns=["isin", "window", "first_point"])


def _first(**isins):
    return {k: pd.Timestamp(v).date() for k, v in isins.items()}


def test_a_point_older_than_the_history_supports_is_a_violation():
    v = wc.timeseries_coverage_violations(_first(A="2023-10-31"), _stored(("A", "rolling_10y", "2026-10-31"), ("A", "rolling_1y", "2024-10-31")), WINDOWS, 35)
    assert list(v["window"]) == ["rolling_10y"] and "does not reach back 120 months" in v["reason"].iloc[0]


def test_the_tolerance_is_the_one_of_the_coverage_rule():
    edge = _stored(("A", "rolling_3y", "2026-10-31"))
    assert wc.timeseries_coverage_violations(_first(A="2023-11-30"), edge, WINDOWS, 35).empty
    assert not wc.timeseries_coverage_violations(_first(A="2023-12-31"), edge, WINDOWS, 35).empty


def test_a_point_with_no_nav_is_a_violation_and_unknown_windows_are_ignored():
    v = wc.timeseries_coverage_violations({}, _stored(("Z", "rolling_1y", "2026-10-31"), ("Z", "short_30d", "2026-10-31")), WINDOWS, 35)
    assert list(v["window"]) == ["rolling_1y"] and "no NAV" in v["reason"].iloc[0]


def test_the_canary_fixture_fires_the_detector():
    first, stored, windows = cn.timeseries_coverage_fixture()
    assert not wc.timeseries_coverage_violations(first, stored, windows, 35).empty


def _runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit" / "stat" / "run_statistical_audit.py"
    spec = importlib.util.spec_from_file_location("run_statistical_audit_fnd0443", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


class _Run:
    def __init__(self):
        self.findings, self.skips = [], []

    def skip(self, check, kind, detail):
        self.skips.append((check, kind))


class _Conn:
    def __init__(self, points, first_nav):
        self.points, self.first_nav, self.sql = points, first_nav, []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        rows = self.points if "fund_metric_timeseries" in sql else self.first_nav
        return type("C", (), {"fetchall": lambda _s: rows})()


def test_the_finding_is_a_hard_invariant_naming_its_funds():
    runner = _runner()
    first, stored, windows = cn.timeseries_coverage_fixture()
    finding = runner._timeseries_coverage_finding(wc.timeseries_coverage_violations(first, stored, windows, 35), len(stored))
    assert finding["rule_id"] == "TIMESERIES_WINDOW_COVERAGE" and finding["rule_class"] == "HARD_INVARIANT" and finding["violating_isins"] == (cn.CANARY_ISIN,)
    assert runner._timeseries_coverage_finding(pd.DataFrame(columns=wc.TS_COLUMNS), 5) is None


def test_the_rule_is_skipped_while_the_cleanup_switch_is_off(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(config, "ROLLING_UNCOVERED_PRUNE_ENABLED", False)
    run, conn = _Run(), _Conn([], [])
    runner._run_timeseries_coverage(run, conn)
    assert run.findings == [] and run.skips == [("TIMESERIES_WINDOW_COVERAGE", cpl.KIND_NOT_APPLICABLE)] and conn.sql == []


def test_switched_on_it_reads_the_store_and_reports(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(config, "ROLLING_UNCOVERED_PRUNE_ENABLED", True)
    run = _Run()
    conn = _Conn([("A", "rolling_10y", pd.Timestamp("2026-10-31").date()), ("B", "rolling_1y", pd.Timestamp("2026-10-31").date())],
                 [("A", pd.Timestamp("2023-10-31").date()), ("B", pd.Timestamp("2020-01-31").date())])
    runner._run_timeseries_coverage(run, conn)
    assert [f["rule_id"] for f in run.findings] == ["TIMESERIES_WINDOW_COVERAGE"] and run.findings[0]["violating_isins"] == ("A",)
    assert all(s.lstrip().upper().startswith("SELECT") for s in conn.sql)


def test_the_check_is_registered_and_wired():
    assert cpl.CHECKS.get("TIMESERIES_WINDOW_COVERAGE") is False                       # like ROLLING_COVERAGE: skipped while its switch is off
    src = (Path(__file__).resolve().parents[1] / "scripts" / "audit" / "stat" / "run_statistical_audit.py").read_text(encoding="utf-8")
    assert src.count("_run_timeseries_coverage(run, conn, isins=isins)") == 1 and 'rule_id = "TIMESERIES_WINDOW_COVERAGE"' in src      # run and canary
