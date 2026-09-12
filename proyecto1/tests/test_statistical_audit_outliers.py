# proyecto1/tests/test_statistical_audit_outliers.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/outliers.py (detect_outliers —
funcion #7, doc/reglas/AUDITORIA_ESTADISTICA.md §4).

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.outliers import detect_outliers


class TestIQR:
    def test_flags_true_outliers(self):
        s = pd.Series(list(range(1, 21)) + [1000.0])  # 1..20 plus one extreme
        r = detect_outliers(s, method="IQR")
        assert r.available
        assert 1000.0 in r.flags["value"].values
        assert len(r.flags) == 1

    def test_no_false_positives_on_clean_uniform_population(self):
        s = pd.Series(range(1, 51))
        r = detect_outliers(s, method="IQR")
        assert r.available
        assert r.flags.empty

    def test_degenerate_iqr_reports_unavailable_not_empty(self):
        s = pd.Series([5.0] * 19 + [5.001])  # 95% at 5.0 -> Q1==Q3==5.0, IQR=0
        r = detect_outliers(s, method="IQR")
        assert not r.available
        assert r.unavailable_reason == "IQR_ZERO"


class TestMadRobustZ:
    def test_warn_and_alarm_severity_thresholds(self):
        # median=16.5, MAD=8.0: z(65)~4.09 (WARN tier), z(1000)~82.9 (ALARM tier)
        s = pd.Series(list(range(1, 31)) + [65.0, 1000.0])
        r = detect_outliers(s, method="MAD_Z")
        assert r.available
        severities = dict(zip(r.flags["value"], r.flags["severity"]))
        assert severities[65.0] == "WARN"
        assert severities[1000.0] == "ALARM"

    def test_mad_zero_guard_independent_of_zero_inflation(self):
        # Concentrated population (median repeats), zero_pct == 0 —
        # exercises the MAD=0 guard on its own, not via the zero-inflation path.
        s = pd.Series([5.0] * 11 + [100.0])
        assert (s == 0).mean() == 0
        r = detect_outliers(s, method="MAD_Z")
        assert not r.available
        assert r.unavailable_reason == "MAD_ZERO"


class TestSharedGuards:
    def test_zero_inflation_guard_applies_to_both_methods(self):
        s = pd.Series([0.0] * 80 + list(range(1, 21)))  # zero_pct = 0.8
        for method in ("IQR", "MAD_Z"):
            r = detect_outliers(s, method=method, zero_pct_guard=0.70)
            assert not r.available
            assert r.unavailable_reason == "ZERO_INFLATED"

    def test_n_too_small_guard(self):
        s = pd.Series([1.0, 2.0, 3.0])
        r = detect_outliers(s, method="IQR", min_n=8)
        assert not r.available
        assert r.unavailable_reason == "N_TOO_SMALL"
