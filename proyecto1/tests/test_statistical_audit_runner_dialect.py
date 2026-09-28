# proyecto1/tests/test_statistical_audit_runner_dialect.py
# -*- coding: utf-8 -*-
"""Helpers de dialecto de scripts/audit/run_statistical_audit.py (puerto a Postgres).
Cumple R-7: sin importar pipeline.py ni core.io."""

import datetime as dt
import importlib.util
import os
import sys
import types

import pandas as pd

_ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit", os.path.join(_ROOT_DIR, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def test_sql_postgres_uses_percent_s_and_window_label():
    out = runner._sql(object(), "SELECT 1 WHERE metric = ? AND {window} = ?")
    assert out == "SELECT 1 WHERE metric = %s AND window_label = %s"


def test_timeseries_queries_never_leak_bare_window_into_postgres():
    for q in (runner._TS_LATEST_QUERY, runner._TS_SERIES_SUMMARY_QUERY):
        adapted = runner._sql(object(), q)
        assert "{window}" not in adapted and "?" not in adapted
        assert "window_label" in adapted
        assert " window " not in adapted.replace("window_label", "")


def test_restore_case_maps_lowercased_postgres_columns_back():
    df = pd.DataFrame(columns=["isin", "ongoing_charge_recurrent", "extra"])
    out = runner._restore_case(df, ("ISIN", "Ongoing_Charge_Recurrent"))
    assert list(out.columns) == ["ISIN", "Ongoing_Charge_Recurrent", "extra"]


def test_month_span_accepts_date_objects_and_strings():
    assert runner._month_span(dt.date(2024, 1, 31), dt.date(2024, 3, 31)) == 3
    assert runner._month_span("2024-01-31", "2024-03-31") == 3


# Wave 0 (2026-09-28, FND-0118): --isin filter plumbing.

def test_isin_filter_empty_when_no_isins():
    frag, params = runner._isin_filter(object(), None, "ISIN")
    assert frag == "" and params == ()
    frag, params = runner._isin_filter(object(), [], "ISIN")
    assert frag == "" and params == ()


def test_isin_filter_builds_in_clause_with_adapted_placeholders():
    frag, params = runner._isin_filter(object(), ["LU1", "LU2", "LU3"], "m.ISIN")
    assert frag == "AND m.ISIN IN (%s,%s,%s)"
    assert params == ("LU1", "LU2", "LU3")


def test_sql_substitutes_isin_filter_before_placeholder_pass():
    frag, _ = runner._isin_filter(object(), ["LU1"], "isin")
    out = runner._sql(object(), "SELECT 1 WHERE metric = ? {isin_filter} GROUP BY isin", isin_filter=frag)
    assert out == "SELECT 1 WHERE metric = %s AND isin IN (%s) GROUP BY isin"


def test_domain_queries_never_leak_bare_isin_filter_token():
    for q in (runner._COST_MASTER_QUERY, runner._COST_SCHEDULE_QUERY, runner._P2_METRICS_QUERY):
        assert "{isin_filter}" in q  # sanity: the token is actually present pre-substitution
        assert "{isin_filter}" not in q.replace("{isin_filter}", "")


# Wave 1 A2 (2026-09-28, FND-0120): PATHOLOGICAL_SHAPE regime carve-out.

def _pathological_series() -> pd.Series:
    # 27 near-zero values + 3 extreme outliers: n=30 clears profile_moments' min_n,
    # dominant_pct stays low (no DOMINANT_VALUE_CONCENTRATION noise), and the outliers
    # push kurtosis well past the >10 pathological-shape threshold.
    return pd.Series(list(range(27)) + [1000.0, -1000.0, 2000.0])


def test_pathological_shape_flagged_warn_when_not_regime_family():
    run = runner.AuditRun(domain="p2_metrics")
    runner._block1(run, "return_ann|since_inception|0|v1", _pathological_series(), 30)
    hits = [f for f in run.findings if f["rule_id"] == "PATHOLOGICAL_SHAPE"]
    assert len(hits) == 1 and hits[0]["severity"] == "WARN"
    assert not any(f["rule_id"] == "PATHOLOGICAL_SHAPE_EXPECTED" for f in run.findings)


def test_pathological_shape_downgraded_to_info_for_regime_family():
    run = runner.AuditRun(domain="p2_metrics")
    runner._block1(run, "return_ann_crisis_financiera|since_inception|0|v1", _pathological_series(), 30,
                    high_kurtosis_expected=True)
    hits = [f for f in run.findings if f["rule_id"] == "PATHOLOGICAL_SHAPE_EXPECTED"]
    assert len(hits) == 1 and hits[0]["severity"] == "INFO"
    assert not any(f["rule_id"] == "PATHOLOGICAL_SHAPE" for f in run.findings)


# Wave 1 A4 (2026-09-28, FND-0122): coverage-cliff (>2%) as an automatic finding.

def _drift_row(population, group_key, stat_name, previous_value, current_value, pct_change):
    return {
        "population": population, "group_key": group_key, "stat_name": stat_name,
        "previous_value": previous_value, "current_value": current_value, "pct_change": pct_change,
    }


def test_coverage_cliff_flagged_past_threshold():
    run = runner.AuditRun(domain="p2_metrics")
    deltas = pd.DataFrame([
        _drift_row("GLOBAL", "return_ann|since_inception|0|v1", "n_valid", 100, 95, -0.05),
    ])
    runner._apply_coverage_cliff_findings(run, types.SimpleNamespace(deltas=deltas))
    hits = [f for f in run.findings if f["rule_id"] == "COVERAGE_CLIFF"]
    assert len(hits) == 1 and hits[0]["severity"] == "WARN"
    assert hits[0]["group_key"] == "return_ann|since_inception|0|v1"


def test_coverage_cliff_not_flagged_below_threshold():
    run = runner.AuditRun(domain="p2_metrics")
    deltas = pd.DataFrame([
        _drift_row("GLOBAL", "return_ann|since_inception|0|v1", "n_valid", 100, 99, -0.01),
    ])
    runner._apply_coverage_cliff_findings(run, types.SimpleNamespace(deltas=deltas))
    assert not run.findings


def test_coverage_cliff_ignores_peer_segments_and_other_stats():
    run = runner.AuditRun(domain="p2_metrics")
    deltas = pd.DataFrame([
        _drift_row("PEER:Renta Variable", "return_ann|since_inception|0|v1", "n_valid", 100, 50, -0.50),
        _drift_row("GLOBAL", "return_ann|since_inception|0|v1", "mean", 0.05, 0.01, -0.80),
    ])
    runner._apply_coverage_cliff_findings(run, types.SimpleNamespace(deltas=deltas))
    assert not run.findings


def test_coverage_cliff_skips_nan_pct_change_without_error():
    run = runner.AuditRun(domain="p2_metrics")
    deltas = pd.DataFrame([
        _drift_row("GLOBAL", "beta_gold|since_inception|0|v1", "n_valid", 0, 5, float("nan")),
    ])
    runner._apply_coverage_cliff_findings(run, types.SimpleNamespace(deltas=deltas))
    assert not run.findings


# A3 (2026-09-28, FND-0121): _deflation_meaningful() false-positive guard.
# Regression coverage for a real defect caught by a live 3-ISIN smoke test: a naive "every
# metric with both real_flag values" sweep flagged drawdown_duration, the three pct_*_months
# metrics, and return_ann_zscore_cat as REAL_EQUALS_NOMINAL "defects" -- all five are
# mathematically guaranteed identical under a uniform per-run deflator, not coincidences.

def test_deflation_meaningful_true_for_continuous_return_metrics():
    assert runner._deflation_meaningful("return_ann")
    assert runner._deflation_meaningful("vol_ann")
    assert runner._deflation_meaningful("worst_month")


def test_deflation_meaningful_false_for_count_metric():
    assert not runner._deflation_meaningful("drawdown_duration")


def test_deflation_meaningful_false_for_bounded_unit_month_share_metrics():
    assert not runner._deflation_meaningful("pct_positive_months")
    assert not runner._deflation_meaningful("pct_negative_months")
    assert not runner._deflation_meaningful("pct_severe_loss_months")


def test_deflation_meaningful_false_for_zscore_and_pctile_cat_regardless_of_inherited_type():
    # return_ann_zscore_cat inherits return_ann's continuous_signed type via the regime-suffix
    # prefix match in catalog_metrics.py -- the suffix check must override that inherited type.
    assert not runner._deflation_meaningful("return_ann_zscore_cat")
    assert not runner._deflation_meaningful("sharpe_zscore_cat")
    assert not runner._deflation_meaningful("vol_ann_pctile_cat")
