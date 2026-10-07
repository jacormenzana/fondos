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


# FND-0137 Section B (2026-09-29): the 3 new boundary/lifespan queries never leak an
# un-adapted {isin_filter} or {window} token or a bare `?` -- same discipline as the
# timeseries queries above.

def test_boundary_and_lifespan_queries_never_leak_unadapted_tokens():
    for q in (
        runner._TS_WINDOW_DEFLATION_QUERY, runner._NAV_DATES_QUERY,
        runner._INFLATION_BOUNDARY_QUERY, runner._MACRO_BOUNDARY_QUERY, runner._NAV_LIFESPAN_QUERY,
    ):
        adapted = runner._sql(object(), q, isin_filter="AND t.isin IN (%s)")
        assert "{isin_filter}" not in adapted
        assert "{window}" not in adapted
        assert "?" not in adapted


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


def _dominant_series() -> pd.Series:
    # 4/6 = 66.7% dominant -> mass_class TEMPLATE_OR_DEFAULT (threshold 0.40), not zero-inflated.
    return pd.Series([1.0, 1.0, 1.0, 1.0, 2.0, 3.0])


def test_block1_peer_population_suppresses_findings():
    # Confirms the population gate still suppresses PEER segments after the B4 fix (2026-09-28)
    # widened it from "!= GLOBAL" to "PEER:*" -- PEER findings stay context-only, not duplicated.
    run = runner.AuditRun(domain="p2_metrics")
    runner._block1(run, "vol_ann|since_inception|0|v1", _dominant_series(), 6,
                    population="PEER:Renta Variable")
    assert not run.findings
    assert run.statistics  # still recorded for the distribution table


def test_block1_timeseries_population_escalates_findings():
    # B4 (FND-0126, 2026-09-28): TIMESERIES is a genuinely distinct population (the latest
    # fund_metric_timeseries snapshot, not the fund_metrics scalar) and must escalate findings on
    # its own account -- the pre-fix "!= GLOBAL" gate would have silently swallowed this.
    run = runner.AuditRun(domain="p2_metrics")
    runner._block1(run, "vol_ann|rolling_1y|0", _dominant_series(), 6, population="TIMESERIES")
    assert any(f["rule_id"] == "DOMINANT_VALUE_CONCENTRATION" for f in run.findings)


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
    # Level-type statistics: a uniform deflator shifts them, so real==nominal IS a signal.
    # vol_ann was asserted here by FND-0121 and removed by FND-0174 (see the dispersion test
    # below). return_ann / sharpe / ret_vol_simple are pinned as still-meaningful so the exclusions
    # added for FND-0174 / FND-0234 cannot silently widen into "exclude everything".
    assert runner._deflation_meaningful("return_ann")
    assert runner._deflation_meaningful("sharpe")
    assert runner._deflation_meaningful("ret_vol_simple")


def test_deflation_meaningful_false_for_point_and_saturating_metrics():
    """FND-0234 (2026-10-07): the real-nominal gap of these is not set by the window's CPI -- see _deflation_meaningful."""
    for metric in ("worst_month", "max_dd", "sortino"):
        assert not runner._deflation_meaningful(metric), metric


# FND-0174 (2026-10-01): two more false-positive classes, found because the 40-ISIN sample
# audit returned RC=1 on six BLOCK2 REAL_EQUALS_NOMINAL_* findings. Measured, not argued:
#  - *_pctile_self (rank within the fund's OWN history): all 98 interior real==nominal pairs were
#    EXACT rank ties (|diff| == 0.0) -- a rank is unchanged whenever deflation does not reorder it.
#  - vol_ann (dispersion): an independent recomputation from raw NAV + CPI reproduced the stored
#    REAL volatility to 1e-6 on 12/12 flagged rows -- deflation was applied; a smooth deflator
#    barely moves a standard deviation, so "real must visibly differ" has no power for it.

def test_deflation_meaningful_false_for_pctile_self_rank_statistics():
    for metric in ("max_dd_pctile_self", "return_ann_pctile_self", "sharpe_pctile_self",
                   "sortino_pctile_self", "vol_ann_pctile_self"):
        assert not runner._deflation_meaningful(metric), metric


def test_deflation_meaningful_false_for_dispersion_statistics():
    for metric in ("vol_ann", "vol_ann_expansion", "srri_volatility", "fx_volatility_ann"):
        assert not runner._deflation_meaningful(metric), metric


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


# Wave 2 B1 (2026-09-28, FND-0123): cost Block 6, _run_cost_schedule_integrity.

def _master(rows):
    return pd.DataFrame(rows, columns=["ISIN", "ACI_RHP"])


def _schedule(rows):
    cols = ["ISIN", "Horizon_Years", "Is_RHP", "Total_Costs_EUR", "Total_Costs_Pct", "Annual_Impact_Pct"]
    return pd.DataFrame(rows, columns=cols)


def _finding_ids(run):
    return [f["rule_id"] for f in run.findings]


def test_cost_schedule_integrity_skips_on_empty_schedule():
    run = runner.AuditRun(domain="cost_attributes")
    runner._run_cost_schedule_integrity(run, _master([]), _schedule([]))
    assert not run.findings
    assert any("empty" in s for s in run.skipped)


def test_eur_pct_coherence_violation_flagged():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 0, 350.0, 1.0, None]])  # implied 3.5% vs stated 1.0%
    runner._run_cost_schedule_integrity(run, _master([]), schedule)
    assert "SCHEDULE_EUR_PCT_COHERENCE" in _finding_ids(run)


def test_eur_pct_coherence_within_tolerance_not_flagged():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 0, 350.02, 3.50, None]])  # diff 0.0002pp
    runner._run_cost_schedule_integrity(run, _master([]), schedule)
    assert "SCHEDULE_EUR_PCT_COHERENCE" not in _finding_ids(run)


def test_eur_misparse_flagged_at_horizon_ge_1():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 1.0, 0, 5.0, None, None]])
    runner._run_cost_schedule_integrity(run, _master([]), schedule)
    assert "SCHEDULE_EUR_MISPARSE" in _finding_ids(run)


def test_eur_misparse_not_flagged_below_1y_horizon():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 0.5, 0, 5.0, None, None]])
    runner._run_cost_schedule_integrity(run, _master([]), schedule)
    assert "SCHEDULE_EUR_MISPARSE" not in _finding_ids(run)


def test_multiple_rhp_rows_flagged():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([
        ["LU1", 3.0, 1, 300.0, 3.0, 3.0],
        ["LU1", 5.0, 1, 400.0, 4.0, 4.0],
    ])
    runner._run_cost_schedule_integrity(run, _master([]), schedule)
    assert "SCHEDULE_MULTIPLE_RHP_ROWS" in _finding_ids(run)


def test_single_rhp_row_not_flagged():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 1, 300.0, 3.0, 3.0]])
    runner._run_cost_schedule_integrity(run, _master([["LU1", 3.0]]), schedule)
    assert "SCHEDULE_MULTIPLE_RHP_ROWS" not in _finding_ids(run)


def test_rhp_aci_mismatch_flagged():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 1, 300.0, 3.0, 5.0]])  # Annual_Impact_Pct=5.0
    master = _master([["LU1", 3.0]])  # ACI_RHP=3.0, diverged
    runner._run_cost_schedule_integrity(run, master, schedule)
    assert "SCHEDULE_RHP_ACI_MISMATCH" in _finding_ids(run)


def test_rhp_aci_agreement_within_tolerance_not_flagged():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 1, 300.0, 3.0, 5.0]])
    master = _master([["LU1", 5.02]])  # diff 0.02pp, within KID_ROUNDING_TOLERANCE_PP
    runner._run_cost_schedule_integrity(run, master, schedule)
    assert "SCHEDULE_RHP_ACI_MISMATCH" not in _finding_ids(run)


def test_aci_rhp_orphan_flagged_when_no_rhp_row_exists():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 0, 300.0, 3.0, 3.0]])  # no Is_RHP=1 row at all
    master = _master([["LU1", 3.0]])
    runner._run_cost_schedule_integrity(run, master, schedule)
    assert "SCHEDULE_ACI_RHP_ORPHAN" in _finding_ids(run)


def test_aci_rhp_orphan_not_flagged_when_aci_rhp_is_null():
    run = runner.AuditRun(domain="cost_attributes")
    schedule = _schedule([["LU1", 3.0, 0, 300.0, 3.0, 3.0]])
    master = _master([["LU1", None]])
    runner._run_cost_schedule_integrity(run, master, schedule)
    assert "SCHEDULE_ACI_RHP_ORPHAN" not in _finding_ids(run)


# Wave 2 B2 (2026-09-28, FND-0124): _run_beta_orphan_check.

def _long_df(rows):
    cols = ["isin", "metric", "horizon", "value", "real_flag", "metric_version", "batch_id"]
    return pd.DataFrame(rows, columns=cols)


def test_beta_orphan_skipped_without_batch_id_column():
    run = runner.AuditRun(domain="p2_metrics")
    df = pd.DataFrame(columns=["isin", "metric", "horizon", "value", "real_flag", "metric_version"])
    runner._run_beta_orphan_check(run, df)
    assert not run.findings
    assert any("batch_id not present" in s for s in run.skipped)


def test_beta_orphan_skipped_when_no_beta_rows():
    run = runner.AuditRun(domain="p2_metrics")
    df = _long_df([["LU1", "sharpe", "since_inception", 1.0, 0, "v1", "P2-batch-1"]])
    runner._run_beta_orphan_check(run, df)
    assert not run.findings
    assert any("no beta_* rows" in s for s in run.skipped)


def test_beta_orphan_not_flagged_when_batches_consistent():
    run = runner.AuditRun(domain="p2_metrics")
    df = _long_df([
        ["LU1", "beta_oil", "since_inception", 0.1, 0, "v1", "P2-batch-1"],
        ["LU1", "beta_gold", "since_inception", 0.2, 0, "v1", "P2-batch-1"],
        ["LU1", "macro_r2", "since_inception", 0.5, 0, "v1", "P2-batch-1"],
    ])
    runner._run_beta_orphan_check(run, df)
    assert "BETA_ORPHAN_BATCH" not in _finding_ids(run)


def test_beta_orphan_flagged_on_internal_batch_split():
    run = runner.AuditRun(domain="p2_metrics")
    df = _long_df([
        ["LU1", "beta_oil", "since_inception", 0.1, 0, "v1", "P2-batch-1"],
        ["LU1", "beta_gold", "since_inception", 0.2, 0, "v1", "P2-batch-2"],  # stale batch
        ["LU1", "macro_r2", "since_inception", 0.5, 0, "v1", "P2-batch-1"],
    ])
    runner._run_beta_orphan_check(run, df)
    assert "BETA_ORPHAN_BATCH" in _finding_ids(run)


# Wave 2 B5 (2026-09-28, FND-0127): alert reconciliation freshness gate + application.

def test_alerts_are_stale_when_alert_predates_calc():
    import datetime as _dt
    alert_ts = pd.Timestamp("2026-08-01")
    calc_date = _dt.date(2026, 9, 1)
    assert runner._alerts_are_stale(alert_ts, calc_date)


def test_alerts_not_stale_when_alert_postdates_calc():
    import datetime as _dt
    alert_ts = pd.Timestamp("2026-09-15")
    calc_date = _dt.date(2026, 9, 1)
    assert not runner._alerts_are_stale(alert_ts, calc_date)


def test_alerts_not_stale_when_calc_date_missing():
    assert not runner._alerts_are_stale(pd.Timestamp("2026-09-15"), None)


def test_alerts_not_stale_when_alert_date_is_nat():
    import datetime as _dt
    assert not runner._alerts_are_stale(pd.NaT, _dt.date(2026, 9, 1))


def test_alert_reconciliation_suppresses_findings_already_known_to_alerts():
    # Exercises reconcile_with_alerts() exactly as _run_alert_reconciliation applies it: BLOCK4
    # findings carrying isin+group_key get filtered against alerts_df; everything else is kept.
    run = runner.AuditRun(domain="p2_metrics")
    run.findings = [
        {"block": "BLOCK4", "rule_id": "OUTLIER_IQR", "isin": "LU1", "group_key": "vol_ann|since_inception|0|v1"},
        {"block": "BLOCK4", "rule_id": "OUTLIER_IQR", "isin": "LU2", "group_key": "vol_ann|since_inception|0|v1"},
        {"block": "BLOCK1", "rule_id": "DOMINANT_VALUE_CONCENTRATION", "isin": None, "group_key": "vol_ann|since_inception|0|v1"},
    ]
    alerts_df = pd.DataFrame([{"isin": "LU1", "metric": "vol_ann", "detected_at": "2026-09-15"}])
    before = len(run.findings)
    run.findings = runner.reconcile_with_alerts(run.findings, alerts_df)
    assert len(run.findings) == before - 1
    assert not any(f["isin"] == "LU1" and f["rule_id"] == "OUTLIER_IQR" for f in run.findings)
    assert any(f["isin"] == "LU2" for f in run.findings)  # different fund, still incremental
    assert any(f["block"] == "BLOCK1" for f in run.findings)  # group-level, always kept


def test_beta_orphan_flagged_when_diverges_from_macro_r2():
    run = runner.AuditRun(domain="p2_metrics")
    df = _long_df([
        ["LU1", "beta_oil", "since_inception", 0.1, 0, "v1", "P2-batch-1"],
        ["LU1", "macro_r2", "since_inception", 0.5, 0, "v1", "P2-batch-2"],  # different run
    ])
    runner._run_beta_orphan_check(run, df)
    assert "BETA_ORPHAN_BATCH" in _finding_ids(run)


# Wave 2 B3 (2026-09-28, FND-0125): snapshot staleness gate wiring. build_snapshot() itself has
# its own 8-test suite (functions #1-#2); these confirm the runner's usage contract -- the exact
# columns/kwargs run_p2_audit's loop relies on -- against the real function, not a mock.

def test_build_snapshot_wiring_holds_out_stale_isin_and_reports_spread():
    ts_raw = pd.DataFrame([
        {"isin": "LU1", "real_flag": 0, "ts_value": 0.05, "date": "2026-08-31"},
        {"isin": "LU2", "real_flag": 0, "ts_value": 0.06, "date": "2026-03-31"},  # 5 months stale
    ])
    snap = runner.build_snapshot(
        ts_raw, entity_key="isin", slice_keys=["real_flag"], date_column="date",
        tolerance_days=runner.SNAPSHOT_MAX_SPREAD_DAYS,
    )
    assert list(snap.held_out["isin"]) == ["LU2"]
    assert list(snap.eligible["isin"]) == ["LU1"]
    assert snap.date_spread_days > runner.SNAPSHOT_MAX_SPREAD_DAYS


def test_build_snapshot_wiring_holds_out_nothing_when_dates_aligned():
    ts_raw = pd.DataFrame([
        {"isin": "LU1", "real_flag": 0, "ts_value": 0.05, "date": "2026-08-31"},
        {"isin": "LU2", "real_flag": 0, "ts_value": 0.06, "date": "2026-08-30"},  # 1 day apart
    ])
    snap = runner.build_snapshot(
        ts_raw, entity_key="isin", slice_keys=["real_flag"], date_column="date",
        tolerance_days=runner.SNAPSHOT_MAX_SPREAD_DAYS,
    )
    assert snap.held_out.empty
    assert set(snap.eligible["isin"]) == {"LU1", "LU2"}


def test_emit_snapshot_held_out_finding_when_present():
    run = runner.AuditRun(domain="p2_metrics")
    run.snapshot_held_out.update(["LU2", "LU3"])
    run.snapshot_max_spread_days = 42.0
    runner._emit_snapshot_held_out_finding(run)
    hits = [f for f in run.findings if f["rule_id"] == "SNAPSHOT_HELD_OUT"]
    assert len(hits) == 1 and hits[0]["severity"] == "INFO" and hits[0]["distance"] == 2.0


def test_emit_snapshot_held_out_finding_absent_when_nothing_held_out():
    run = runner.AuditRun(domain="p2_metrics")
    runner._emit_snapshot_held_out_finding(run)
    assert not run.findings


def _finding(block, rule_id, rule_class, severity):
    return {"block": block, "rule_id": rule_id, "rule_class": rule_class, "severity": severity}


def _run_with(*findings):
    run = runner.AuditRun(domain="p2_metrics")
    run.findings.extend(findings)
    return run


def test_blocking_hard_invariant_blocks():
    assert runner._has_blocking_findings(
        _run_with(_finding("BLOCK6", "SCHEDULE_MULTIPLE_RHP_ROWS", "HARD_INVARIANT", "ALARM")))


def test_blocking_block2_blocks_even_at_warn():
    assert runner._has_blocking_findings(
        _run_with(_finding("BLOCK2", "SCALAR_EQUALS_TIMESERIES_X", "STATISTICAL", "WARN")))


def test_blocking_timeseries_duplicate_blocks():
    assert runner._has_blocking_findings(
        _run_with(_finding("BLOCK5", "TIMESERIES_DUPLICATE", "STATISTICAL", "ALARM")))


def test_blocking_outlier_alarm_does_not_block_fnd0175():
    assert not runner._has_blocking_findings(
        _run_with(_finding("BLOCK4", "OUTLIER_MAD_Z", "STATISTICAL", "ALARM")))


def test_blocking_other_warnings_do_not_block():
    assert not runner._has_blocking_findings(_run_with(
        _finding("BLOCK4", "OUTLIER_IQR", "STATISTICAL", "WARN"),
        _finding("BLOCK4", "TIMESERIES_GAP", "STATISTICAL", "WARN"),
        _finding("BLOCK1", "COVERAGE_CLIFF", "STATISTICAL", "WARN")))


def test_blocking_empty_run_does_not_block():
    assert not runner._has_blocking_findings(_run_with())
