# proyecto1/tests/test_statistical_audit_real_nominal_point_metrics.py
# -*- coding: utf-8 -*-
"""FND-0234: REAL_EQUALS_NOMINAL is not evaluated for point / saturating metrics. R-7: pure.

Why (live, 2026-10-07): worst_month 1,212 / max_dd 236 / sortino 31 hits among pairs whose WINDOW CPI is above the eligibility
floor. A healthy deflation of a point metric moves it by ~|value| x CPI of the single month / episode that defines it
(0.1 x 0.2% = 2e-4), below the 1e-3 tolerance, whatever the window did."""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.catalog_pairs import P2_PAIRS  # noqa: E402
from shared.statistical_audit.comparisons import compare_pairs  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_statistical_audit", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

RULE = P2_PAIRS["REAL_EQUALS_NOMINAL"]


def _healthy_point_metric_pairs(n=400, seed=0):
    """Worst-month style values: nominal ~ -10%, real = (1+nom)/(1+cpi_month) - 1 with a ~0.2% monthly CPI; window CPI is large."""
    rng = np.random.default_rng(seed)
    nom = -rng.uniform(0.04, 0.2, n)
    cpi_month = rng.uniform(0.0, 0.004, n)
    real = (1 + nom) / (1 + cpi_month) - 1
    return pd.DataFrame({"m_nominal": nom, "m_real": real, "window_cpi_ann": rng.uniform(0.02, 0.06, n)})


def test_a_healthy_deflated_point_metric_trips_the_rule_on_a_fraction_of_rows():
    """The reason the rule is ill-posed for point metrics: correct deflation is within tolerance on many rows."""
    frame = _healthy_point_metric_pairs()
    assert (frame.m_real - frame.m_nominal).abs().lt(RULE.tolerance).mean() > 0.2
    assert compare_pairs(frame, "m_nominal", "m_real", RULE).triggered


def test_the_runner_does_not_evaluate_those_metrics():
    for metric in ("worst_month", "max_dd", "sortino"):
        assert not runner._deflation_meaningful(metric), metric


def test_window_integrated_level_metrics_are_still_evaluated():
    for metric in ("return_ann", "sharpe", "ret_vol_simple"):
        assert runner._deflation_meaningful(metric), metric


def test_a_window_integrated_metric_with_deflation_not_applied_still_triggers():
    """The rule keeps its power where the gap IS set by the window CPI: real == nominal for many funds is a defect."""
    n = 40
    frame = pd.DataFrame({"return_ann_nominal": np.linspace(0.02, 0.09, n), "window_cpi_ann": np.full(n, 0.03)})
    frame["return_ann_real"] = frame["return_ann_nominal"]
    assert compare_pairs(frame, "return_ann_nominal", "return_ann_real", RULE).triggered
