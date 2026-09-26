# proyecto3/tests/test_data_freshness.py
# -*- coding: utf-8 -*-
"""
FND-0098: universe-level freshness gate for the P3 build.

R-7: imports ONLY data_freshness / regime_classifier constants / shared.config — no
pipeline.py, no core.io, no DB (evaluate_freshness is a pure function).

Run from repo root:
    python -m pytest proyecto3/tests/test_data_freshness.py -v
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.data_freshness import (  # noqa: E402
    evaluate_freshness, format_report, parse_harvest_ts, stale_checks,
)
from proyecto3.src.regime_classifier import REGIME_INPUT_INDICATORS  # noqa: E402
from shared.config import (  # noqa: E402
    P3_FRESHNESS_MAX_AGE_DAYS as LIMITS,
    P3_MACRO_RELEASE_LAG_INDICATORS as LAG,
    P3_NAV_UNIVERSE_PERCENTILE as PCT,
)

TODAY = date(2026, 9, 27)


def _run(nav, macro, harvest="20260920_010101"):
    return evaluate_freshness(nav, macro, harvest, TODAY, LIMITS, PCT, LAG)


def _fresh_macro():
    # month-end index: the current month (2026-09-30) is in the future -> age 0
    return {c: pd.Timestamp("2026-09-30") for c in REGIME_INPUT_INDICATORS}


def _by_name(checks):
    return {c.name: c for c in checks}


def test_all_fresh_passes():
    checks = _run([date(2026, 9, 25)] * 50, _fresh_macro())
    assert stale_checks(checks) == []


def test_stale_nav_load_fails_even_if_one_fund_is_fresh():
    # a max()-based gate would pass this; the percentile gate must not
    nav = [date(2026, 8, 1)] * 99 + [date(2026, 9, 26)]
    assert _by_name(_run(nav, _fresh_macro()))["nav_p10"].ok is False


def test_a_few_frozen_funds_do_not_block():
    # 5% permanently frozen (FND-0041-like) must not block a run whose load is current
    nav = [date(2022, 2, 27)] * 5 + [date(2026, 9, 25)] * 95
    assert _by_name(_run(nav, _fresh_macro()))["nav_p10"].ok is True


def test_release_lag_indicator_gets_the_looser_limit():
    macro = _fresh_macro()
    macro["ipc_yoy_avg"] = pd.Timestamp("2026-07-31")   # 58 d: too old for a market series
    macro["oil_yoy"] = pd.Timestamp("2026-07-31")       # same age, market series
    got = _by_name(_run([date(2026, 9, 25)] * 20, macro))
    assert got["macro:ipc_yoy_avg"].ok is True
    assert got["macro:oil_yoy"].ok is False


def test_missing_or_empty_data_is_a_failure_not_a_crash():
    macro = _fresh_macro()
    macro["vix_yoy"] = None
    got = _by_name(_run([], macro, harvest=None))
    assert got["nav_p10"].ok is False and got["nav_p10"].newest is None
    assert got["macro:vix_yoy"].ok is False and got["macro:vix_yoy"].age_days is None
    assert got["harvest"].ok is False


def test_harvest_age_and_parsing():
    assert parse_harvest_ts("20260717_232557") == date(2026, 7, 17)
    assert parse_harvest_ts("garbage") is None and parse_harvest_ts(None) is None
    assert _by_name(_run([date(2026, 9, 25)] * 20, _fresh_macro(), "20260717_232557"))["harvest"].ok is False


def test_text_dates_from_sqlite_are_accepted():
    checks = _run(["2026-09-25"] * 20, {c: "2026-09-30" for c in REGIME_INPUT_INDICATORS})
    assert stale_checks(checks) == []


def test_report_marks_stale_lines():
    checks = _run([date(2026, 8, 1)] * 20, _fresh_macro())
    report = format_report(checks)
    assert "[STALE] nav_p10" in report and "[OK ] harvest" in report


# ---- metric-version uniformity (assessment 2026-09-26: mixed CALC_VERSIONs must not pass silently) ----

def _fresh_inputs():
    return [date(2026, 9, 25)] * 20, _fresh_macro()


def test_uniform_metrics_version_passes():
    nav, macro = _fresh_inputs()
    checks = evaluate_freshness(nav, macro, "20260920_000000", TODAY, LIMITS, PCT, LAG,
                                metric_versions=["20260918"] * 100, min_uniform_share=0.98)
    assert _by_name(checks)["metrics_calc_version"].ok is True


def test_mixed_metrics_versions_fail_and_name_the_versions():
    nav, macro = _fresh_inputs()
    versions = ["20260917"] * 95 + ["20260918"] * 5          # a partial refresh after a version bump
    checks = evaluate_freshness(nav, macro, "20260920_000000", TODAY, LIMITS, PCT, LAG,
                                metric_versions=versions, min_uniform_share=0.98)
    c = _by_name(checks)["metrics_calc_version"]
    assert c.ok is False and "20260917" in c.detail and "NEWER version 20260918" in c.detail
    assert "metrics_calc_version" in [x.name for x in stale_checks(checks)]


def test_no_metrics_at_all_is_a_failure_not_a_crash():
    nav, macro = _fresh_inputs()
    checks = evaluate_freshness(nav, macro, "20260920_000000", TODAY, LIMITS, PCT, LAG,
                                metric_versions=[], min_uniform_share=0.98)
    assert _by_name(checks)["metrics_calc_version"].ok is False


def test_uniformity_check_is_skipped_when_versions_are_not_supplied():
    nav, macro = _fresh_inputs()
    assert "metrics_calc_version" not in _by_name(_run(nav, macro))


def test_report_renders_the_uniformity_line_without_a_date_column():
    nav, macro = _fresh_inputs()
    checks = evaluate_freshness(nav, macro, "20260920_000000", TODAY, LIMITS, PCT, LAG,
                                metric_versions=["20260917"] * 90 + ["20260918"] * 10, min_uniform_share=0.98)
    report = format_report(checks)
    assert "[STALE] metrics_calc_version" in report and "90.0%" in report


def test_undated_version_labels_are_never_reported_as_the_newer_version():
    nav, macro = _fresh_inputs()
    checks = evaluate_freshness(nav, macro, "20260920_000000", TODAY, LIMITS, PCT, LAG,
                                metric_versions=["20260917"] * 95 + ["PRE_V26_UNKNOWN"] * 5, min_uniform_share=0.98)
    assert "NEWER version" not in _by_name(checks)["metrics_calc_version"].detail
