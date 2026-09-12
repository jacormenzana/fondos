#!/usr/bin/env python
"""Runner for the statistical distribution audit (doc/reglas/
AUDITORIA_ESTADISTICA.md). Computes Blocks 1, 2, 4, 5 and 7 for one domain
(costs = fund_master/fund_cost_schedule, p2 = fund_metrics) against the live
database, using only the generic functions in shared/statistical_audit/ and
the declarative catalogs — no metric or column name is decided in this file
beyond what the catalogs already say.

Deliberately NOT dependency-free, unlike scripts/audit/sync_agents_md.py:
this tool needs pandas and a live SQLite connection and is meant to run
under the `des` Conda env like every other P1/P2 tool. It mirrors
sync_agents_md.py only in report-vs-check CLI shape and exit codes.

Known scope limits in this first version (reported in the run's own output,
never silently skipped):
  - P2 Block 1/3/4/7 profile the GLOBAL population only; PEER segmentation
    by Fund_Nature (compute_category_snapshot's own segmentation) is a
    follow-up.
  - P2 Block 2 implements SHARPE_EQUALS_SORTINO, CAPTURE_UP_EQUALS_DOWN and
    VOL_ANN_EQUALS_SRRI_VOL (same-group metric pivots). REAL_EQUALS_NOMINAL
    (needs an IPC join) and SCALAR_EQUALS_TIMESERIES (needs a
    fund_metric_timeseries join) are cataloged but not yet wired.
  - P2 Block 5 invariants referencing synthetic columns not present in
    fund_metrics' long format (excess_return, periodic_return_variance,
    return_ann_real/nominal, ipc_yoy) are skipped — fund_metrics has no
    column by those names; deriving them is a follow-up, not a silent gap
    (this runner reports exactly which rules were skipped and why).
  - Block 6 (fund_metric_timeseries temporal integrity) is out of scope.
  - Block 4's reconciliation against fund_metric_alerts (function #12,
    reconcile_with_alerts) is not wired into this runner yet. The three
    root-cause defects that blocked it (AUDITORIA_ESTADISTICA.md §2.5:
    value/reference_value NULL, VOL_CAT_P90/P97 dead) were fixed in
    rolling_stats.py / shared/config.py on 2026-09-13, but the live
    fund_metric_alerts table still holds rows written by the old code
    until the next real P2 run — wiring #12 in now would reconcile
    against stale data, so it waits for that recompute.

Usage:
    python -X utf8 scripts/audit/run_statistical_audit.py --domain costs
    python -X utf8 scripts/audit/run_statistical_audit.py --domain p2 --mode check
    python -X utf8 scripts/audit/run_statistical_audit.py --domain costs --persist
    python -X utf8 scripts/audit/run_statistical_audit.py --domain costs --compare-to audit_20260912T223518Z
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from shared.config import DB_PATH
from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS
from shared.statistical_audit.catalog_metric_bounds import get_metric_bound
from shared.statistical_audit.catalog_metrics import get_metric_spec
from shared.statistical_audit.catalog_pairs import COST_PAIRS, P2_PAIRS
from shared.statistical_audit.comparisons import compare_pairs
from shared.statistical_audit.distributions import (
    profile_coverage,
    profile_location,
    profile_mass_points,
    profile_moments,
)
from shared.statistical_audit.invariants import (
    BoundRule,
    check_bounds,
    check_invariant,
    expression_identifiers,
)
from shared.statistical_audit.compare_runs import compare_runs, load_run_statistics
from shared.statistical_audit.outliers import detect_outliers
from shared.statistical_audit.persistence import emit_findings, emit_statistics, statistics_to_frame
from shared.statistical_audit.snapshot import build_population

_NOT_YET_WIRED_P2_PAIRS = {"REAL_EQUALS_NOMINAL", "SCALAR_EQUALS_TIMESERIES"}


# ============================================================
# Cost domain
# ============================================================

_COST_MASTER_QUERY = """
    SELECT ISIN, Ongoing_Charge_Recurrent, Entry_Fee_Pct, Exit_Fee_Pct,
           Entry_Fee_Pct_Max, Exit_Fee_Pct_Max, Management_Fee_Pct,
           Transaction_Cost_Pct, Performance_Fee_Pct, Performance_Fee_Basis,
           ACI_1Y, ACI_RHP, Cost_RHP_Years
    FROM fund_master
    WHERE In_Current_Universe = 1
"""

_COST_SCHEDULE_QUERY = """
    SELECT s.ISIN, s.Horizon_Years, s.Is_RHP, s.Total_Costs_EUR,
           s.Total_Costs_Pct, s.Annual_Impact_Pct
    FROM fund_cost_schedule s
    JOIN fund_master m ON m.ISIN = s.ISIN
    WHERE m.In_Current_Universe = 1
"""


def run_cost_audit(conn: sqlite3.Connection) -> "AuditRun":
    master = build_population(conn, _COST_MASTER_QUERY)
    schedule = build_population(conn, _COST_SCHEDULE_QUERY)
    frames_by_table = {"fund_master": master, "fund_cost_schedule": schedule}

    run = AuditRun(domain="cost_attributes")
    run.universe_size = len(master)

    for column, spec in COST_COLUMNS.items():
        frame = frames_by_table[spec.table]
        if column not in frame.columns:
            run.skipped.append(f"BLOCK1/4/7 {column}: not present in the queried frame")
            continue
        series = frame[column]
        is_numeric = spec.scale != "categorical"
        _block1(run, column, series, len(frame), numeric=is_numeric)

        if is_numeric:
            indexed = series.set_axis(frame["ISIN"])
            for method in ("IQR", "MAD_Z"):
                _block4(run, column, indexed, method)

        for bound in (spec.hard_bound, spec.plausibility_bound):
            if bound is None:
                continue
            rule = BoundRule(column, bound.min_value, bound.max_value, bound.bound_type)
            _block7(run, column, frame, column, rule)

    for rule in COST_PAIRS.values():
        left, right = rule.rule_id.split("__EQUALS__")
        result = compare_pairs(master, left, right, rule)
        if result.triggered:
            run.findings.append({
                "block": "BLOCK2", "rule_id": rule.rule_id, "rule_class": "STATISTICAL_ANOMALY",
                "severity": rule.severity, "group_key": rule.rule_id,
                "value": None, "reference_value": None, "threshold": rule.tolerance,
                "distance": float(result.n_matches),
                "evidence": f"{result.n_matches}/{result.n_eligible} eligible rows within tolerance",
                "root_cause_candidate": rule.diagnosis,
            })

    _run_invariants(run, COST_INVARIANTS, [master, schedule], block="BLOCK5")

    return run


# ============================================================
# P2 metrics domain
# ============================================================

_P2_METRICS_QUERY = """
    SELECT fm.ISIN, fm.metric, fm.horizon, fm.value, fm.real_flag, fm.metric_version
    FROM fund_metrics fm
    JOIN fund_master m ON m.ISIN = fm.ISIN
    WHERE m.In_Current_Universe = 1
"""

_P2_GROUP_KEYS = ("metric", "horizon", "real_flag", "metric_version")

_P2_SAME_GROUP_PAIRS = {
    "SHARPE_EQUALS_SORTINO": ("sharpe", "sortino"),
    "CAPTURE_UP_EQUALS_DOWN": ("upside_capture", "downside_capture"),
    "VOL_ANN_EQUALS_SRRI_VOL": ("vol_ann", "srri_volatility"),
}


def run_p2_audit(conn: sqlite3.Connection) -> "AuditRun":
    long_df = build_population(conn, _P2_METRICS_QUERY)

    run = AuditRun(domain="p2_metrics")
    run.universe_size = int(long_df["isin"].nunique())

    for keys, group in long_df.groupby(list(_P2_GROUP_KEYS)):
        metric, horizon, real_flag, metric_version = keys
        group_key = f"{metric}|{horizon}|{real_flag}|{metric_version}"
        spec = get_metric_spec(metric)
        series = group["value"]

        _block1(run, group_key, series, len(group), numeric=True, supports_moments=spec.supports_moments)

        if spec.statistical_type in ("continuous_positive", "continuous_signed", "bounded_unit"):
            indexed = series.set_axis(group["isin"])
            for method in ("IQR", "MAD_Z"):
                _block4(run, group_key, indexed, method)

        bound = get_metric_bound(metric)
        if bound is not None:
            _block7(run, group_key, group, "value", bound, horizon_column="horizon")

    for rule_id, (metric_a, metric_b) in _P2_SAME_GROUP_PAIRS.items():
        rule = P2_PAIRS[rule_id]
        wide = _pivot_two_metrics(long_df, metric_a, metric_b)
        if wide.empty:
            run.skipped.append(f"BLOCK2 {rule_id}: no overlapping rows for {metric_a}/{metric_b}")
            continue
        result = compare_pairs(wide, metric_a, metric_b, rule)
        if result.triggered:
            run.findings.append({
                "block": "BLOCK2", "rule_id": rule_id, "rule_class": "STATISTICAL_ANOMALY",
                "severity": rule.severity, "group_key": rule_id,
                "value": None, "reference_value": None, "threshold": rule.tolerance,
                "distance": float(result.n_matches),
                "evidence": f"{result.n_matches}/{result.n_eligible} eligible rows within tolerance",
                "root_cause_candidate": rule.diagnosis,
            })

    for rule_id in _NOT_YET_WIRED_P2_PAIRS:
        run.skipped.append(f"BLOCK2 {rule_id}: not yet wired (see module docstring)")

    wide_for_invariants = _pivot_all_metrics(long_df)
    _run_invariants(run, P2_INVARIANTS, [wide_for_invariants], block="BLOCK5")

    return run


def _pivot_two_metrics(long_df: pd.DataFrame, metric_a: str, metric_b: str) -> pd.DataFrame:
    join_keys = ["isin", "horizon", "real_flag", "metric_version"]
    left = long_df[long_df["metric"] == metric_a][join_keys + ["value"]].rename(columns={"value": metric_a})
    right = long_df[long_df["metric"] == metric_b][join_keys + ["value"]].rename(columns={"value": metric_b})
    return left.merge(right, on=join_keys, how="inner")


def _pivot_all_metrics(long_df: pd.DataFrame) -> pd.DataFrame:
    join_keys = ["isin", "horizon", "real_flag", "metric_version"]
    return long_df.pivot_table(index=join_keys, columns="metric", values="value", aggfunc="first").reset_index()


# ============================================================
# Shared block helpers
# ============================================================

class AuditRun:
    def __init__(self, domain: str):
        self.domain = domain
        self.universe_size = 0
        self.statistics: list[tuple] = []  # (group_key, stats_dict, n)
        self.findings: list[dict] = []
        self.skipped: list[str] = []


def _block1(
    run: AuditRun, group_key: str, series: pd.Series, n_expected: int,
    numeric: bool = True, supports_moments: bool = True,
) -> None:
    stats: dict = profile_coverage(series, n_expected=n_expected)
    if numeric:
        stats.update(profile_location(series))
        if supports_moments:
            stats.update(profile_moments(series))
    stats.update(profile_mass_points(series))
    run.statistics.append((group_key, stats, stats.get("n_valid")))

    if stats.get("mass_class") == "TEMPLATE_OR_DEFAULT":
        run.findings.append({
            "block": "BLOCK1", "rule_id": "DOMINANT_VALUE_CONCENTRATION",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "WARN", "group_key": group_key,
            "value": stats["dominant_pct"], "reference_value": 0.40, "threshold": 0.40,
            "distance": stats["dominant_pct"] - 0.40,
            "evidence": f"dominant_value={stats['dominant_value']} at {stats['dominant_pct']:.1%}",
            "root_cause_candidate": "Template/hardcoded value or upstream bleed — not a valid population",
        })

    if stats.get("shape_status") == "OK" and (abs(stats.get("skew", 0) or 0) > 3 or (stats.get("kurtosis", 0) or 0) > 10):
        run.findings.append({
            "block": "BLOCK3", "rule_id": "PATHOLOGICAL_SHAPE",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "WARN", "group_key": group_key,
            "value": stats["skew"], "reference_value": 3.0, "threshold": 3.0,
            "distance": None,
            "evidence": f"skew={stats['skew']:.2f} kurtosis={stats['kurtosis']:.2f}",
            "root_cause_candidate": "Scale contamination or a NAV/value anomaly survived upstream guards",
        })


_OUTLIER_SEVERITY_MAP = {"OUTLIER": "WARN", "WARN": "WARN", "ALARM": "ALARM"}


def _block4(run: AuditRun, group_key: str, indexed_series: pd.Series, method: str) -> None:
    result = detect_outliers(indexed_series, method=method)
    if not result.available:
        run.skipped.append(f"BLOCK4 {group_key} [{method}]: {result.unavailable_reason}")
        return
    for isin, row in result.flags.iterrows():
        # detect_outliers uses "OUTLIER" as IQR's own severity label (outliers.py
        # has no WARN/ALARM tiering for that method); audit_finding.severity's
        # CHECK constraint only allows INFO/WARN/ALARM, so IQR hits map to WARN.
        severity = _OUTLIER_SEVERITY_MAP.get(row.get("severity"), "WARN")
        run.findings.append({
            "block": "BLOCK4", "rule_id": f"OUTLIER_{method}",
            "rule_class": "STATISTICAL_ANOMALY", "severity": severity,
            "group_key": group_key, "isin": isin if isinstance(isin, str) else None,
            "value": float(row["value"]), "reference_value": None, "threshold": None,
            "distance": float(row["robust_z"]) if "robust_z" in row.index else None,
            "evidence": ", ".join(f"{k}={v}" for k, v in row.items()),
            "root_cause_candidate": None,
        })


def _block7(
    run: AuditRun, group_key: str, frame: pd.DataFrame, value_column: str, rule: BoundRule,
    horizon_column: str | None = None,
) -> None:
    result = check_bounds(frame, value_column, rule, horizon_column=horizon_column)
    if result.n_breaches == 0:
        return
    run.findings.append({
        "block": "BLOCK7", "rule_id": f"BOUND_{rule.metric}", "rule_class": rule.bound_type,
        "severity": "ALARM" if rule.bound_type == "HARD_INVARIANT" else "WARN",
        "group_key": group_key, "value": None,
        "reference_value": rule.max_value if rule.max_value is not None else rule.min_value,
        "threshold": rule.max_value if rule.max_value is not None else rule.min_value,
        "distance": float(result.n_breaches),
        "evidence": f"{result.n_breaches} rows outside [{rule.min_value}, {rule.max_value}]"
                    + (f"; {result.n_carved_out} carved out under crisis_ prefix" if result.n_carved_out else ""),
        "root_cause_candidate": None,
    })


def _run_invariants(run: AuditRun, rules, frames: list[pd.DataFrame], block: str) -> None:
    for rule in rules:
        needed = expression_identifiers(rule.expression)
        if rule.when:
            needed |= expression_identifiers(rule.when)

        frame = next((f for f in frames if needed <= set(f.columns)), None)
        if frame is None:
            run.skipped.append(f"{block} {rule.rule_id}: columns not present in any queried frame ({sorted(needed)})")
            continue

        result = check_invariant(frame, rule)
        if result.n_violations == 0:
            continue
        run.findings.append({
            "block": block, "rule_id": rule.rule_id, "rule_class": rule.bound_type,
            "severity": "ALARM" if rule.bound_type == "HARD_INVARIANT" else "WARN",
            "group_key": rule.rule_id, "value": None, "reference_value": None, "threshold": None,
            "distance": float(result.n_violations),
            "evidence": f"{result.n_violations}/{result.n_applicable} applicable rows violate",
            "root_cause_candidate": rule.description,
        })


# ============================================================
# Persistence, reporting, CLI
# ============================================================

def _persist(conn: sqlite3.Connection, run: "AuditRun", run_id: str) -> tuple[int, int]:
    n_stats = 0
    for group_key, stats, n in run.statistics:
        n_stats += emit_statistics(conn, run_id, run.domain, "GLOBAL", group_key, stats, n=n)
    n_findings = emit_findings(conn, run_id, run.domain, run.findings)
    return n_stats, n_findings


def _print_report(run: "AuditRun", run_id: str) -> None:
    print(f"=== Statistical audit — domain={run.domain} run_id={run_id} ===")
    print(f"Universe size: {run.universe_size}")
    print(f"Groups/columns profiled: {len(run.statistics)}")
    print()
    print(f"Findings: {len(run.findings)}")
    by_block: dict[str, int] = {}
    for f in run.findings:
        by_block[f["block"]] = by_block.get(f["block"], 0) + 1
    for block, count in sorted(by_block.items()):
        print(f"  {block}: {count}")
    print()
    for f in run.findings:
        if f["block"] == "BLOCK4":
            continue  # per-ISIN outlier rows are numerous; summarized above, not listed
        print(f"  [{f['severity']}] {f['block']} {f['rule_id']} ({f['group_key']}): {f['evidence']}")
    print()
    print(f"Skipped ({len(run.skipped)}):")
    for s in run.skipped:
        print(f"  ! {s}")


def _has_blocking_findings(run: "AuditRun") -> bool:
    for f in run.findings:
        if f["rule_class"] == "HARD_INVARIANT":
            return True
        if f["block"] == "BLOCK2":
            return True
        if f["block"] == "BLOCK4" and f["severity"] == "ALARM":
            return True
    return False


def _print_drift_report(conn: sqlite3.Connection, run: "AuditRun", compare_to: str, top_n: int = 20) -> None:
    previous = load_run_statistics(conn, compare_to, run.domain)
    if previous.empty:
        print(f"! --compare-to {compare_to}: no audit_statistic rows found for domain={run.domain}")
        return

    current = statistics_to_frame(run.statistics)
    result = compare_runs(previous, current)

    print(f"=== Drift vs run_id={compare_to} ===")
    print(f"New groups: {len(result.new_groups)}  Dropped groups: {len(result.dropped_groups)}")
    if result.new_groups:
        print(f"  + {', '.join(result.new_groups[:top_n])}")
    if result.dropped_groups:
        print(f"  - {', '.join(result.dropped_groups[:top_n])}")

    moved = result.deltas.dropna(subset=["delta"])
    moved = moved[moved["delta"] != 0]
    moved = moved.sort_values("delta", key=lambda s: s.abs(), ascending=False)
    print(f"Largest deltas (top {top_n} of {len(moved)} non-zero):")
    for _, row in moved.head(top_n).iterrows():
        pct = f" ({row['pct_change']:+.1%})" if pd.notna(row["pct_change"]) else ""
        print(f"  {row['group_key']} [{row['stat_name']}]: {row['previous_value']:.6g} -> {row['current_value']:.6g}{pct}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=["costs", "p2"], required=True)
    parser.add_argument("--mode", choices=["report", "check"], default="report")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--compare-to", default=None, help="Prior run_id to diff against (function #13)")
    args = parser.parse_args()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("audit_%Y%m%dT%H%M%SZ")
    keep_open = args.persist or args.compare_to

    conn = sqlite3.connect(args.db)
    try:
        run = run_cost_audit(conn) if args.domain == "costs" else run_p2_audit(conn)
    finally:
        if not keep_open:
            conn.close()

    _print_report(run, run_id)

    if args.compare_to:
        print()
        _print_drift_report(conn, run, args.compare_to)

    if args.persist:
        n_stats, n_findings = _persist(conn, run, run_id)
        print()
        print(f"Persisted: {n_stats} audit_statistic rows, {n_findings} audit_finding rows (run_id={run_id})")

    if keep_open:
        conn.close()

    if args.mode == "check" and _has_blocking_findings(run):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
