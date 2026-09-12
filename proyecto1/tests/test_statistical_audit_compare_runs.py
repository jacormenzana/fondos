# proyecto1/tests/test_statistical_audit_compare_runs.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/compare_runs.py (compare_runs
— funcion #13, doc/reglas/AUDITORIA_ESTADISTICA.md §4) y de
statistics_to_frame() en persistence.py (el conversor que le permite operar
sobre un AuditRun recien calculado sin pasar antes por la BD).

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

from shared.statistical_audit.compare_runs import compare_runs
from shared.statistical_audit.persistence import statistics_to_frame


def _frame(rows):
    return pd.DataFrame(rows, columns=["population", "group_key", "stat_name", "stat_value"])


class TestCompareRuns:
    def test_delta_and_pct_change_computed(self):
        previous = _frame([("GLOBAL", "vol_ann", "p50", 0.10)])
        current = _frame([("GLOBAL", "vol_ann", "p50", 0.07)])
        result = compare_runs(previous, current)
        row = result.deltas.iloc[0]
        assert row["delta"] == pytest.approx(-0.03)
        assert row["pct_change"] == pytest.approx(-0.30)

    def test_new_group_detected(self):
        previous = _frame([("GLOBAL", "vol_ann", "p50", 0.10)])
        current = _frame([
            ("GLOBAL", "vol_ann", "p50", 0.10),
            ("GLOBAL", "max_dd", "p50", -0.15),
        ])
        result = compare_runs(previous, current)
        assert result.new_groups == ["max_dd"]
        assert result.dropped_groups == []

    def test_dropped_group_detected(self):
        previous = _frame([
            ("GLOBAL", "vol_ann", "p50", 0.10),
            ("GLOBAL", "max_dd", "p50", -0.15),
        ])
        current = _frame([("GLOBAL", "vol_ann", "p50", 0.10)])
        result = compare_runs(previous, current)
        assert result.dropped_groups == ["max_dd"]

    def test_pct_change_guarded_against_zero_previous_value(self):
        previous = _frame([("GLOBAL", "vol_ann", "zero_pct", 0.0)])
        current = _frame([("GLOBAL", "vol_ann", "zero_pct", 0.5)])
        result = compare_runs(previous, current)
        pct = result.deltas.iloc[0]["pct_change"]
        assert pct is None or (isinstance(pct, float) and math.isnan(pct))

    def test_non_drift_stat_names_filtered_out(self):
        previous = _frame([("GLOBAL", "vol_ann", "cv_status", None)])
        current = _frame([("GLOBAL", "vol_ann", "cv_status", None)])
        result = compare_runs(previous, current)
        assert result.deltas.empty


class TestStatisticsToFrame:
    def test_converts_audit_run_statistics_shape(self):
        statistics = [
            ("vol_ann|since_inception|0|v1", {"n_valid": 100, "p50": 0.12, "cv_status": "OK"}, 100),
        ]
        frame = statistics_to_frame(statistics)
        assert set(frame["stat_name"]) == {"n_valid", "p50", "cv_status"}
        cv_row = frame[frame["stat_name"] == "cv_status"].iloc[0]
        # non-numeric status text has no stat_value; pandas stores the None
        # as NaN once the column also holds float values from other rows.
        assert pd.isna(cv_row["stat_value"])

    def test_output_feeds_compare_runs_directly(self):
        previous = _frame([("GLOBAL", "vol_ann|since_inception|0|v1", "p50", 0.15)])
        current = statistics_to_frame(
            [("vol_ann|since_inception|0|v1", {"p50": 0.071}, 2900)]
        )
        result = compare_runs(previous, current)
        row = result.deltas.iloc[0]
        assert row["delta"] == pytest.approx(0.071 - 0.15)
