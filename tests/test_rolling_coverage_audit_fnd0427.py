# tests/test_rolling_coverage_audit_fnd0427.py
# -*- coding: utf-8 -*-
"""FND-0427: the ROLLING_COVERAGE audit rule (shared/statistical_audit/window_coverage.py) and its liveness canary. Pure, hermetic (R-7): no database, no pipeline."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from shared import config
from shared.statistical_audit import canaries as cn
from shared.statistical_audit import completeness as cpl
from shared.statistical_audit import window_coverage as wc

WINDOWS = {"rolling_1y": 12, "rolling_3y": 36, "rolling_10y": 120}
KW = dict(tolerance_days=35, min_coverage=0.80, max_gap_run=6)


def _nav(isin, first, last, drop=()):
    d = pd.date_range(first, last, freq="ME")
    if len(drop):
        d = d[~d.isin(pd.to_datetime(list(drop)))]
    return pd.DataFrame({"isin": isin, "date": d})


def _stored(isin, *horizons):
    return pd.DataFrame({"isin": [isin] * len(horizons), "horizon": list(horizons)})


def test_a_ten_year_window_on_a_three_year_history_is_a_violation():
    v = wc.rolling_coverage_violations(_nav("A", "2023-10-31", "2026-10-31"), _stored("A", "rolling_1y", "rolling_3y", "rolling_10y"), WINDOWS, **KW)
    assert list(v["horizon"]) == ["rolling_10y"] and "does not reach back 120 months" in v["reason"].iloc[0]


def test_the_tolerance_absorbs_the_last_monthly_close():
    # 37 month-ends 2023-10-31..2026-10-31: a 36-month window starts at 2023-10-31 + 35 days of tolerance
    assert wc.rolling_coverage_violations(_nav("A", "2023-11-30", "2026-10-31"), _stored("A", "rolling_3y"), WINDOWS, **KW).empty


def test_an_internal_hole_beyond_the_continuity_thresholds_is_a_violation():
    drop = pd.date_range("2025-01-31", periods=7, freq="ME")                        # seven missing months in a row
    v = wc.rolling_coverage_violations(_nav("A", "2020-01-31", "2026-10-31", drop=drop), _stored("A", "rolling_3y"), WINDOWS, **KW)
    assert len(v) == 1 and v["reason"].iloc[0]


def test_a_stored_window_with_no_nav_is_a_violation_and_unknown_horizons_are_ignored():
    v = wc.rolling_coverage_violations(_nav("A", "2000-01-31", "2026-10-31"), _stored("B", "rolling_1y").assign(), WINDOWS, **KW)
    assert len(v) == 1 and v["isin"].iloc[0] == "B"
    assert wc.rolling_coverage_violations(_nav("A", "2000-01-31", "2026-10-31"), _stored("A", "crisis_2022"), WINDOWS, **KW).empty


def test_a_covered_fund_is_clean():
    assert wc.rolling_coverage_violations(_nav("A", "2010-01-31", "2026-10-31"), _stored("A", *WINDOWS), WINDOWS, **KW).empty


def test_the_canary_fixture_fires_the_detector():
    nav, stored, windows = cn.rolling_coverage_fixture()
    assert not wc.rolling_coverage_violations(nav, stored, windows, **KW).empty


def _runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "audit" / "stat" / "run_statistical_audit.py"
    spec = importlib.util.spec_from_file_location("run_statistical_audit_fnd0427", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return mod


def test_the_runner_finding_is_a_hard_invariant_naming_its_funds():
    runner = _runner()
    nav, stored, windows = cn.rolling_coverage_fixture()
    finding = runner._rolling_coverage_finding(wc.rolling_coverage_violations(nav, stored, windows, **KW), 1)
    assert finding["rule_id"] == "ROLLING_COVERAGE" and finding["rule_class"] == "HARD_INVARIANT"
    assert finding["violating_isins"] == (cn.CANARY_ISIN,)
    assert runner._rolling_coverage_finding(pd.DataFrame(columns=wc.COLUMNS), 5) is None


def test_the_rule_is_skipped_while_the_switch_is_off(monkeypatch):
    runner = _runner()
    monkeypatch.setattr(config, "ROLLING_WINDOW_COVERAGE_ENABLED", False)

    class _Run:
        def __init__(self):
            self.findings, self.skips = [], []

        def skip(self, check, kind, detail):
            self.skips.append((check, kind))

    run = _Run()
    runner._run_rolling_coverage(run, pd.DataFrame(), pd.DataFrame())
    assert run.findings == [] and run.skips == [("ROLLING_COVERAGE", cpl.KIND_NOT_APPLICABLE)]


def test_the_check_is_registered_in_the_completeness_contract():
    assert "ROLLING_COVERAGE" in cpl.CHECKS
