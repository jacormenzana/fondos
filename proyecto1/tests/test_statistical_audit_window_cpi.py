# proyecto1/tests/test_statistical_audit_window_cpi.py
# -*- coding: utf-8 -*-
"""FND-0234: DEFLATION_ORDER (and REAL_EQUALS_NOMINAL) are gated on the CPI change over each row's OWN window, not on
today's YoY. R-7: pure, no pipeline or core.io imports.

Fixture: a fund launched in April 2008 whose crisis_2008 window ends in March 2009, i.e. inside the 2008-09 CPI decline --
real > nominal there is CORRECT. The same pair over a window with rising CPI would be a genuine deflation inversion.
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.catalog_invariants import P2_INVARIANTS  # noqa: E402
from shared.statistical_audit.catalog_pairs import P2_PAIRS, _ipc_eligibility  # noqa: E402
from shared.statistical_audit.timeseries import nav_with_ipc, scalar_window_cpi  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit_wcpi", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_statistical_audit_wcpi"] = runner
_spec.loader.exec_module(runner)

# CPI index, Jan 2008 .. Dec 2009: rises to Aug 2008, falls to Feb 2009, then rises again.
CPI = [100, 100.5, 101, 101.5, 102, 102.5, 103, 103.5, 103, 102.5, 102, 101.5,
       101, 100.5, 100.8, 101.1, 101.4, 101.7, 102, 102.3, 102.6, 102.9, 103.2, 103.5]
MONTHS = pd.date_range("2008-01-31", periods=24, freq="ME")
IPC = pd.DataFrame({"date": MONTHS, "ipc_index": CPI})
CRISIS_END = {"crisis_x": "2009-03-31"}


def _nav(isin, first_idx, last_idx=23):
    dates = MONTHS[first_idx:last_idx + 1]
    return pd.DataFrame({"isin": isin, "date": dates, "nav": np.linspace(100, 110, len(dates))})


def _rows(*specs):
    return pd.DataFrame(specs, columns=["isin", "horizon", "metric_version", "n_obs"])


def _cpi(rows, nav, window_ends=None):
    return scalar_window_cpi(rows, nav, IPC, window_ends).set_index(["isin", "horizon"])["window_cpi_ann"]


# ---------------------------------------------------------------- scalar_window_cpi
def test_a_crisis_window_inside_the_deflation_has_negative_cpi():
    nav = _nav("A", first_idx=3)                                    # launched April 2008, 21 NAV points
    out = _cpi(_rows(("A", "crisis_x", "v1", 12)), nav, CRISIS_END)  # Apr-08 .. Mar-09 = 12 points
    assert out[("A", "crisis_x")] == pytest.approx(100.8 / 101.5 - 1)


def test_since_inception_ends_at_the_last_nav_and_sees_the_net_inflation():
    nav = _nav("A", first_idx=3)
    out = _cpi(_rows(("A", "since_inception", "v1", 21)), nav, CRISIS_END)
    assert out[("A", "since_inception")] == pytest.approx((103.5 / 101.5) ** (12 / 21) - 1)
    assert out[("A", "since_inception")] > 0


def test_rolling_windows_end_at_the_last_nav():
    nav = _nav("A", first_idx=0)
    out = _cpi(_rows(("A", "rolling_1y", "v1", 13)), nav, CRISIS_END)   # last 13 points: Dec-08 .. Dec-09
    assert out[("A", "rolling_1y")] == pytest.approx((103.5 / 101.5) ** (12 / 13) - 1)   # years = n_obs / 12


def test_a_window_that_cannot_be_located_is_undecidable_not_zero():
    nav = _nav("A", first_idx=3)
    out = _cpi(_rows(("A", "since_inception", "v1", 40), ("B", "since_inception", "v1", 5)), nav)
    assert np.isnan(out[("A", "since_inception")])                  # n_obs reaches before the series start
    assert np.isnan(out[("B", "since_inception")])                  # fund has no NAV rows at all


def test_a_horizon_without_an_end_date_is_not_cut():
    nav = _nav("A", first_idx=3)
    out = _cpi(_rows(("A", "crisis_x", "v1", 12)), nav, window_ends={"crisis_x": None})
    assert out[("A", "crisis_x")] != pytest.approx(100.8 / 101.5 - 1)   # treated as ending at the last NAV


def test_nav_before_the_cpi_coverage_takes_the_earliest_known_index():
    """deflate_nav()'s contract (leading bfill) is reproduced, not re-invented."""
    early = pd.DataFrame({"isin": "E", "date": pd.date_range("2007-10-31", periods=5, freq="ME"), "nav": range(5)})
    joined = nav_with_ipc(early, IPC)
    assert joined["ipc_index"].iloc[0] == CPI[0]                    # Oct-07 has no CPI: first known value, not NaN


# ---------------------------------------------------------------- the rule
DEFLATION_ORDER = next(r for r in P2_INVARIANTS if r.rule_id == "DEFLATION_ORDER")


def _violations(frame):
    run = runner.AuditRun("p2_metrics")
    runner._run_invariants(run, [DEFLATION_ORDER], [frame], block="BLOCK5")
    return run.findings


def _pair(isin, nominal, real, window_cpi):
    return {"isin": isin, "horizon": "crisis_2008", "metric_version": "v1", "return_ann_nominal": nominal,
            "return_ann_real": real, "window_cpi_ann": window_cpi}


def test_real_above_nominal_over_a_deflationary_window_is_not_a_finding():
    frame = pd.DataFrame([_pair("A", -0.02, -0.013, -0.007)])
    assert _violations(frame) == []


def test_real_above_nominal_over_an_inflationary_window_is_still_an_alarm_naming_the_isin():
    frame = pd.DataFrame([_pair("A", 0.04, 0.05, 0.03), _pair("B", 0.04, 0.01, 0.03)])
    findings = _violations(frame)
    assert len(findings) == 1
    assert findings[0]["severity"] == "ALARM" and findings[0]["violating_isins"] == ("A",)


def test_an_undecidable_window_is_never_a_violation():
    frame = pd.DataFrame([_pair("A", 0.04, 0.05, float("nan"))])
    assert _violations(frame) == []


def test_the_rule_no_longer_depends_on_todays_yoy():
    assert "ipc_yoy" not in (DEFLATION_ORDER.when or "")
    assert "window_cpi_ann" in DEFLATION_ORDER.when


# ---------------------------------------------------------------- REAL_EQUALS_NOMINAL eligibility
def test_pair_eligibility_prefers_the_per_row_window_cpi():
    df = pd.DataFrame({"window_cpi_ann": [-0.01, 0.0, 0.03], "ipc_yoy": [0.05, 0.05, 0.05]})
    assert _ipc_eligibility(df).tolist() == [False, False, True]


def test_pair_eligibility_still_accepts_a_scalar_ipc_yoy_and_fails_closed_without_either():
    assert _ipc_eligibility(pd.DataFrame({"ipc_yoy": [0.0, 0.05]})).tolist() == [False, True]
    assert _ipc_eligibility(pd.DataFrame({"x": [1, 2]})).tolist() == [False, False]
    assert P2_PAIRS["REAL_EQUALS_NOMINAL"].eligibility is not None


# ---------------------------------------------------------------- the runner's table builder
def test_runner_builds_one_cpi_row_per_stored_window_from_the_nominal_return_ann_rows():
    long_df = pd.DataFrame([
        {"isin": "A", "metric": "return_ann", "horizon": "crisis_x", "real_flag": 0, "metric_version": "v1",
         "value": -0.02, "source_rows": 12},
        {"isin": "A", "metric": "return_ann", "horizon": "crisis_x", "real_flag": 1, "metric_version": "v1",
         "value": -0.013, "source_rows": 12},
        {"isin": "A", "metric": "sharpe", "horizon": "crisis_x", "real_flag": 0, "metric_version": "v1",
         "value": 0.1, "source_rows": 12},
    ])
    runner.CRISIS_WINDOWS = {"crisis_x": ("2007-10-01", "2009-03-31")}      # the module-level name the helper reads
    table = runner._scalar_window_cpi_table(long_df, _nav("A", first_idx=3), IPC)
    assert len(table) == 1 and table.iloc[0]["window_cpi_ann"] == pytest.approx(100.8 / 101.5 - 1)


def test_runner_table_is_empty_not_an_error_without_inputs():
    long_df = pd.DataFrame(columns=["isin", "metric", "horizon", "real_flag", "metric_version", "value", "source_rows"])
    assert runner._scalar_window_cpi_table(long_df, pd.DataFrame(), IPC).empty
