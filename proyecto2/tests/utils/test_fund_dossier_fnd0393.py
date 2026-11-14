# proyecto2/tests/utils/test_fund_dossier_fnd0393.py
# -*- coding: utf-8 -*-
"""FND-0393: the pure parts of scripts/audit/nav/fund_dossier.py (the flag that says why an anomalous metric may not be a real signal, calendar returns, the downside drill-down, the reading).

R-7: no DB. The script is loaded by path; its database parts run read-only against the live store when the dossier is produced. They sit in proyecto2/tests because the script imports proyecto2's `src`.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np
import pandas as pd

from shared import config

spec = importlib.util.spec_from_file_location("fund_dossier", _ROOT / "scripts" / "audit" / "nav" / "fund_dossier.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fund_dossier"] = fd
spec.loader.exec_module(fd)


def _nav(values, start="2021-01-31"):
    return pd.DataFrame({"date": pd.date_range(start, periods=len(values), freq="ME"), "nav": [float(v) for v in values]})


def test_a_regime_statistic_below_the_floor_is_flagged_stale_and_a_covered_one_is_not():
    nobs = {"n_obs_expansion": 11.0, "n_obs_contraccion": 14.0}
    assert fd.flag_row("vol_ann_expansion", "since_inception", 0, 106, nobs, 70, 155).startswith("STALE_REGIME (only 11 months")
    assert fd.flag_row("vol_ann_contraccion", "since_inception", 0, 106, nobs, 70, 155) == "ADEQUATE"
    assert fd.flag_row("vol_ann_expansion", "since_inception", 1, 106, nobs, 70, 155) == "ADEQUATE"            # the real-return variant has no regime row: the rule is for real_flag 0 only
    assert fd.flag_row("vol_ann_expansion", "rolling_3y", 0, 106, nobs, 70, 155) == "ADEQUATE"                 # another horizon


def test_a_short_slope_and_a_small_sample_are_flagged_and_the_order_is_rule_before_statistics():
    assert fd.flag_row("sharpe_slope", "rolling_3y", 0, 3, {}, 35, 36).startswith("SMALL_SLOPE (3 points")
    assert fd.flag_row("sharpe_slope", "rolling_3y", 0, 36, {}, 35, 36) == "ADEQUATE"
    assert fd.flag_row("return_ann_expansion", "since_inception", 0, 36, {}, 82, 186).startswith("SMALL_SAMPLE")     # no n_obs known: the statistical test decides
    assert fd.flag_row("return_ann_expansion", "since_inception", 0, 94, {}, 95, 186) == "ADEQUATE"                  # below the p10 but not below half the median (93)
    assert fd.flag_row("return_ann_expansion", "since_inception", 0, 80, {}, 82, 186).startswith("SMALL_SAMPLE")    # below both
    assert fd.flag_row("x", "since_inception", 0, None, {}, None, None) == "ADEQUATE"                              # unknown evidence is not a small sample


def test_the_crisis_scores_belong_to_the_crisis_regime():
    assert fd.STAT_SUFFIX["crisis_stress_score_mdd"] == "crisis_financiera"
    assert fd.flag_row("crisis_stress_score_mdd", "since_inception", 0, 100, {"n_obs_crisis_financiera": 4.0}, None, None).startswith("STALE_REGIME")


def test_calendar_returns_use_the_last_nav_of_each_year_and_skip_the_first_year():
    nav = _nav([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 120] + [121] * 11 + [180])             # 2021 ends 120, 2022 ends 180
    assert fd.calendar_returns(nav) == {2022: 0.5}
    assert fd.calendar_returns(_nav([100, 101])) == {}


def test_the_downside_drilldown_counts_the_months_below_the_risk_free_rate():
    nav = _nav([100.0, 102.0, 101.0, 105.0, 107.0, 106.0, 110.0])                                              # 6 monthly returns
    d = fd.downside_drilldown(nav, pd.Timestamp("2021-02-01"), pd.Timestamp("2021-12-31"))
    assert d["n_months"] == 6 and d["n_below_rf"] == 2 and d["downside_dev_monthly"] > 0
    assert len(d["returns"]) == len(d["months"]) == 6
    empty = fd.downside_drilldown(nav, pd.Timestamp("2030-01-01"), pd.Timestamp("2030-12-31"))
    assert empty["n_months"] == 0 and empty["downside_dev_monthly"] is None


def test_the_reading_separates_stale_rows_from_anomalies_that_stand_on_their_own():
    rec = {"anomalies": [{"metric": "vol_ann_expansion", "horizon": "since_inception", "z": 99.0, "flag": "STALE_REGIME (only 0 months in that regime, floor 12)"},
                         {"metric": "sortino", "horizon": "crisis_2022", "z": 62.0, "flag": "ADEQUATE"}],
           "validators": {"validate_nav": True, "validate_nav_stale_flat": True, "validate_nav_unshared_jump": True},
           "nav": {"calendar_returns": {2022: 0.758, 2023: -0.007}}, "drilldown": {"sortino|crisis_2022": {"n_months": 12, "n_below_rf": 2, "downside_dev_monthly": 0.004}}}
    text = " ".join(fd.reading(rec))
    assert "1 of 2 anomalous rows should not exist" in text and "stand on their own" in text and "sortino|crisis_2022 (z 62.0)" in text
    assert "+75.8% in 2022" in text and "publisher" in text                                                       # a 50%+ year is put to the reviewer, not decided
    assert "only 2 month(s) below the risk-free rate" in text or "2 month(s) below the risk-free rate out of 12" in text
    assert fd.reading({"anomalies": []}) == ["No stored metric reaches the z threshold."]
    rec["validators"]["validate_nav_unshared_jump"] = False
    assert "rejects the series: validate_nav_unshared_jump" in " ".join(fd.reading(rec))


def test_the_floors_the_flags_quote_are_the_pipelines_floors():
    assert config.REGIME_MIN_OBS == 12 and config.ROLLING_SLOPE_MIN_OBS == 12
