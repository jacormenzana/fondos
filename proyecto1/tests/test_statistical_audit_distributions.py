# proyecto1/tests/test_statistical_audit_distributions.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/distributions.py
(profile_coverage, profile_location, profile_moments, profile_mass_points —
funciones #3-#6, doc/reglas/AUDITORIA_ESTADISTICA.md §4).

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import math
import os
import sys

import pandas as pd
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.distributions import (
    profile_coverage,
    profile_location,
    profile_mass_points,
    profile_moments,
)


class TestProfileCoverage:
    def test_counts_and_pcts(self):
        s = pd.Series([1.0, 2.0, None, 4.0, None])
        r = profile_coverage(s, n_expected=10)
        assert r["n_total"] == 5
        assert r["n_valid"] == 3
        assert r["n_null"] == 2
        assert r["null_pct"] == pytest.approx(2 / 5)
        assert r["coverage_pct"] == pytest.approx(3 / 10)

    def test_empty_series_does_not_divide_by_zero(self):
        r = profile_coverage(pd.Series([], dtype=float))
        assert r["n_total"] == 0
        assert math.isnan(r["null_pct"])
        assert math.isnan(r["coverage_pct"])


class TestProfileLocation:
    def test_quantiles(self):
        s = pd.Series(range(1, 101))  # 1..100
        r = profile_location(s)
        assert r["min"] == 1
        assert r["max"] == 100
        assert r["p50"] == pytest.approx(50.5)

    def test_all_null_returns_nan(self):
        r = profile_location(pd.Series([None, None]))
        assert all(math.isnan(v) for v in r.values())


class TestProfileMoments:
    def test_cv_guarded_when_mean_near_zero(self):
        s = pd.Series([0.001, -0.001, 0.0005, -0.0005] * 10)
        r = profile_moments(s, min_n=5, cv_epsilon=1e-3)
        assert r["cv_status"] == "MEAN_NEAR_ZERO"
        assert math.isnan(r["cv"])

    def test_cv_computed_when_mean_away_from_zero(self):
        s = pd.Series([1.0] * 20 + [2.0] * 20)
        r = profile_moments(s, min_n=5)
        assert r["cv_status"] == "OK"
        assert r["sd"] > 0

    def test_shape_stats_gated_by_min_n(self):
        s = pd.Series([1.0, 2.0, 3.0, 100.0])  # n=4
        r = profile_moments(s, min_n=30)
        assert r["shape_status"] == "N_TOO_SMALL"
        assert math.isnan(r["skew"])
        assert math.isnan(r["kurtosis"])

    def test_shape_stats_match_pandas_fisher_convention(self):
        s = pd.Series([1.0] * 29 + [1000.0])  # heavily right-skewed, n=30
        r = profile_moments(s, min_n=30)
        assert r["shape_status"] == "OK"
        assert r["skew"] == pytest.approx(s.skew())
        assert r["kurtosis"] == pytest.approx(s.kurt())
        assert r["skew"] > 3  # matches the skill's |skew|>3 trigger


class TestProfileMassPoints:
    def test_zero_inflated_classification(self):
        s = pd.Series([0.0] * 80 + [1.0] * 20)
        r = profile_mass_points(s)
        assert r["zero_pct"] == pytest.approx(0.80)
        assert r["mass_class"] == "ZERO_INFLATED"

    def test_template_value_classification(self):
        s = pd.Series([0.019] * 50 + list(range(50)))
        r = profile_mass_points(s, dominant_threshold=0.40)
        assert r["dominant_value"] == 0.019
        assert r["dominant_pct"] == pytest.approx(0.50)
        assert r["mass_class"] == "TEMPLATE_OR_DEFAULT"

    def test_normal_population_not_flagged(self):
        s = pd.Series(range(100))
        r = profile_mass_points(s)
        assert r["mass_class"] == "NORMAL"

    def test_rounding_tolerance_collapses_near_duplicates(self):
        # 0.143628 vs 0.143629 vs 0.143630 — exact-mode would see 3 distinct
        # values; rounding to 3 digits collapses all three into one bucket
        # (all round to 0.144).
        s = pd.Series([0.143628, 0.143629, 0.143630] * 10 + list(range(10)))
        r = profile_mass_points(s, rounding_digits=3, dominant_threshold=0.40)
        assert r["dominant_value"] == 0.144
        assert r["dominant_pct"] == pytest.approx(30 / 40)
