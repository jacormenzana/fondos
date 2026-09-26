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
            ("GLOBAL", "vol_ann|since_inception|0|v1", {"n_valid": 100, "p50": 0.12, "cv_status": "OK"}, 100),
        ]
        frame = statistics_to_frame(statistics)
        assert set(frame["stat_name"]) == {"n_valid", "p50", "cv_status"}
        cv_row = frame[frame["stat_name"] == "cv_status"].iloc[0]
        # non-numeric status text has no stat_value; pandas stores the None
        # as NaN once the column also holds float values from other rows.
        assert pd.isna(cv_row["stat_value"])

    def test_peer_population_preserved_not_forced_to_global(self):
        statistics = [
            ("PEER:Renta Variable", "vol_ann|since_inception|0|v1", {"p50": 0.18}, 40),
        ]
        frame = statistics_to_frame(statistics)
        assert frame.iloc[0]["population"] == "PEER:Renta Variable"

    def test_output_feeds_compare_runs_directly(self):
        previous = _frame([("GLOBAL", "vol_ann|since_inception|0|v1", "p50", 0.15)])
        current = statistics_to_frame(
            [("GLOBAL", "vol_ann|since_inception|0|v1", {"p50": 0.071}, 2900)]
        )
        result = compare_runs(previous, current)
        row = result.deltas.iloc[0]
        assert row["delta"] == pytest.approx(0.071 - 0.15)


class TestNoiseFilter:
    """2026-09-26: the launcher's post-run drift report listed 28 'non-zero deltas' that were all
    floating-point noise (~1e-16, +0.0%). Material shifts must survive; noise must be flagged."""

    def _frame(self, values):
        import pandas as pd
        return pd.DataFrame([{"population": "GLOBAL", "group_key": g, "stat_name": "mean", "stat_value": v}
                             for g, v in values.items()])

    def test_float_noise_is_flagged_and_a_real_shift_is_not(self):
        prev = self._frame({"a": 1.2918, "b": 10.0, "c": 5.0})
        curr = self._frame({"a": 1.2918 * (1 + 1e-15), "b": 10.0 * 1.05, "c": 5.0})
        r = compare_runs(prev, curr).deltas.set_index("group_key")
        assert bool(r.loc["a", "is_noise"]) and r.loc["a", "delta"] != 0        # noise: differs, but immaterial
        assert not bool(r.loc["b", "is_noise"])                                  # 5% shift: material
        assert bool(r.loc["c", "is_noise"]) and r.loc["c", "delta"] == 0         # identical

    def test_a_tiny_but_real_shift_is_not_hidden(self):
        prev = self._frame({"x": 1.0})
        curr = self._frame({"x": 1.0 + 1e-6})                                    # 1e-6 relative >> 1e-9
        assert not bool(compare_runs(prev, curr).deltas.iloc[0]["is_noise"])

    def test_values_near_zero_use_the_absolute_tolerance(self):
        prev = self._frame({"z": 0.0})
        curr = self._frame({"z": 1e-14})
        assert bool(compare_runs(prev, curr).deltas.iloc[0]["is_noise"])
