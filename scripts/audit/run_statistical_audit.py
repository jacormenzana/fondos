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

Fixed 2026-09-13 (AUDITORIA_ESTADISTICA.md §2.6, closing the item-4 scope
gaps from the prior version of this file):
  - VOL_ANN_EQUALS_SRRI_VOL removed from the P2 pairs catalog: investigated
    the live 100%-match finding and confirmed vol_ann(since_inception,
    nominal) and srri_volatility are mathematically identical by
    construction (same `std(ddof=1)*sqrt(12)` formula over the same NAV
    series) — not a binding defect, and the pair could never have detected
    one as specified.
  - REAL_EQUALS_NOMINAL and SCALAR_EQUALS_TIMESERIES are now wired.
    REAL_EQUALS_NOMINAL uses a single coarse ES-CPI YoY scalar as its IPC
    eligibility gate (not a per-fund/per-horizon calculation).
    SCALAR_EQUALS_TIMESERIES runs 25 separate per-(metric,window) queries
    against fund_metric_timeseries (each a clean prefix match on
    idx_fmts_isin_metric_window_real_date/idx_fmts_mwr_isin_date, ~3s each,
    ~70-90s total) rather than one combined query, which was measured at
    120s for the same total row count.
  - excess_return (return_ann - RISK_FREE_RATE_ANN) and
    return_ann_nominal/return_ann_real (pivoted from real_flag) are now
    derived and merged into the Block 5 invariant frame, activating
    SORTINO_VS_SHARPE_UP/DOWN and DEFLATION_ORDER. N_OBS_NONNEG was
    corrected to the 7 real per-regime `n_obs_{regime}` columns (the
    original bare `n_obs` never existed).

P1.1 (2026-09-15): `periodic_return_variance` (FROZEN_NAV_ZERO_VOL) is now
wired — derived directly from fund_nav_monthly (per-ISIN sample variance,
ddof=1, of monthly simple returns; see _periodic_return_variance()) rather
than from vol_ann, which would make the invariant circular and vacuously
true. Merged on isin only (broadcasts across all of that isin's
horizon/metric_version rows — the variance is a property of the NAV series,
not of any one horizon). Guarded so a run with no vol_ann column degrades to
the previous skip instead of erroring.
  - PEER segmentation (Blocks 1/4) is now wired for the 5 curated metrics
    at since_inception/nominal, segmented by Fund_Nature (min 5 peers) —
    the same scope the production category-snapshot alert engine targets,
    not all 486 groups x ~7 natures.
  - Two cost Block-5 invariants (ANNUAL_LE_ACCUMULATED,
    ANNUAL_EQUALS_TOTAL_AT_1Y) had their tolerance widened from 0.0001 to
    0.06 percentage points, grounded in the KID's own 1-decimal RIY
    publication rounding — this eliminated ~1,780 of ~2,042 false-positive
    violations while correctly preserving the genuine ~264-fund defect
    (Total_Costs_Pct/EUR duplicated identically across a fund's different
    Horizon_Years rows).

Tolerance audit + cost-schedule duplication detector (2026-09-13,
AUDITORIA_ESTADISTICA.md §2.8): OC_NOT_CONTAMINATED and SHARPE_EQUALS_SORTINO
were swept and confirmed to already be at the correct value (renamed to
FLOAT_IDENTITY_TOLERANCE, unchanged numerically); REAL_EQUALS_NOMINAL's
tolerance was tied to the same IPC_ELIGIBILITY_FLOOR constant that already
gated its eligibility (0.0001 -> 0.001); SCALAR_EQUALS_TIMESERIES was
audited and found NOT to fit any single-constant fix (per-metric divergence
never converges to a shared absolute floor) — left unresolved and flagged,
not silently widened. All tolerances now live in
shared/statistical_audit/tolerances.py as named, sweep-justified constants.
Added TOTAL_COSTS_PCT/EUR_CONSTANT_ACROSS_HORIZONS (new function #17,
check_group_constancy) to detect the cross-horizon duplication defect
itself, wired into run_cost_audit via _run_group_checks — detection only,
no repair.

Remaining scope limits (reported in the run's own output, never silently
skipped):
  - Block 6 (fund_metric_timeseries temporal integrity): gap #11 wired
    2026-09-15 -- see _run_timeseries_integrity(). Implemented as an
    aggregate MIN/MAX/COUNT query per (metric, window), NOT via
    shared.statistical_audit.timeseries.check_timeseries_integrity(), whose
    row-level DataFrame approach measured >10 minutes on this 32M-row table
    (vs. ~25s aggregated). Duplicate-key detection is a paranoia check (the
    PK should make it structurally impossible); gap detection is per-series
    (each fund's own [min,max] history), not a universe-wide calendar, so
    short/new funds are never penalized.
  - SCALAR_EQUALS_TIMESERIES's absolute tolerance is unresolved (see above);
    fixing it properly needs a relative-tolerance extension to PairRule or a
    root-cause look at the divergence itself, deferred pending a decision.
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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from shared.config import DB_PATH, RISK_FREE_RATE_ANN
from shared.db import get_connection, is_postgres_connection
from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS
from shared.statistical_audit.catalog_version import compute_catalog_version
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS
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
from shared.statistical_audit.group_checks import check_group_constancy
from shared.statistical_audit.invariants import (
    BoundRule,
    check_bounds,
    check_invariant,
    expression_identifiers,
)
from shared.statistical_audit.compare_runs import compare_runs, load_run_statistics
from shared.statistical_audit.outliers import detect_outliers
from shared.statistical_audit.persistence import clear_run, emit_findings, emit_statistics, statistics_to_frame
from shared.statistical_audit.snapshot import build_population

MIN_PEERS = 5


# ============================================================
# Backend dialect helpers (SQLite retired 2026-09-23; Postgres is primary).
# Every query below is written once, unquoted, and adapted here rather than
# duplicated per dialect.
# ============================================================

def _ph(conn) -> str:
    # Runner-local `?` -> `%s` is safe: none of these queries touch jsonb, whose
    # native `?` operator is why shared/db.py refuses a global translator.
    return "%s" if is_postgres_connection(conn) else "?"


def _sql(conn, query: str) -> str:
    """Adapts a query written with `?` placeholders and a `{window}` slot for
    the connected backend. fund_metric_timeseries.window is `window_label` in
    Postgres (`window` is a reserved word there; db/pg/rename_map.yaml)."""
    window_col = "window_label" if is_postgres_connection(conn) else "window"
    return query.replace("{window}", window_col).replace("?", _ph(conn))


def _restore_case(df: pd.DataFrame, canonical: tuple[str, ...]) -> pd.DataFrame:
    """Postgres folds unquoted result columns to lowercase; the catalogs and
    downstream code use the SQLite-declared spelling (e.g. ISIN, Fund_Nature)."""
    by_lower = {c.lower(): c for c in canonical}
    return df.rename(columns={c: by_lower[c.lower()] for c in df.columns if c.lower() in by_lower})


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


_COST_MASTER_COLS = (
    "ISIN", "Ongoing_Charge_Recurrent", "Entry_Fee_Pct", "Exit_Fee_Pct",
    "Entry_Fee_Pct_Max", "Exit_Fee_Pct_Max", "Management_Fee_Pct",
    "Transaction_Cost_Pct", "Performance_Fee_Pct", "Performance_Fee_Basis",
    "ACI_1Y", "ACI_RHP", "Cost_RHP_Years",
)
_COST_SCHEDULE_COLS = (
    "ISIN", "Horizon_Years", "Is_RHP", "Total_Costs_EUR", "Total_Costs_Pct", "Annual_Impact_Pct",
)


def run_cost_audit(conn: sqlite3.Connection) -> "AuditRun":
    master = _restore_case(build_population(conn, _COST_MASTER_QUERY), _COST_MASTER_COLS)
    schedule = _restore_case(build_population(conn, _COST_SCHEDULE_QUERY), _COST_SCHEDULE_COLS)
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
    _run_group_checks(run, COST_GROUP_CHECKS, schedule)

    return run


# ============================================================
# P2 metrics domain
# ============================================================

_P2_METRICS_QUERY = """
    SELECT fm.ISIN, fm.metric, fm.horizon, fm.value, fm.real_flag, fm.metric_version,
           m.Fund_Nature
    FROM fund_metrics fm
    JOIN fund_master m ON m.ISIN = fm.ISIN
    WHERE m.In_Current_Universe = 1
"""

_P2_GROUP_KEYS = ("metric", "horizon", "real_flag", "metric_version")

_P2_SAME_GROUP_PAIRS = {
    "SHARPE_EQUALS_SORTINO": ("sharpe", "sortino"),
    "CAPTURE_UP_EQUALS_DOWN": ("upside_capture", "downside_capture"),
}

# PEER segmentation (2026-09-13, closes an item-4 scope gap): limited to the 5
# curated metrics at since_inception/nominal — the same scope the production
# category-snapshot alert engine (compute_category_snapshot) targets — rather
# than exploding across all 486 groups x ~7 Fund_Nature values, which would
# multiply runtime and audit_statistic row count for little incremental value
# beyond this core set.
_PEER_METRICS = ("vol_ann", "max_dd", "return_ann", "sharpe", "sortino")
_PEER_HORIZON = "since_inception"
_PEER_REAL_FLAG = 0

_IPC_QUERY = "SELECT date, ipc_index FROM series_inflation WHERE geography = 'ES' ORDER BY date"

# SCALAR_EQUALS_TIMESERIES (2026-09-13): per-(metric,window) queries, each a
# clean prefix match on idx_fmts_mwr_isin_date (~3s, ~25 combos ≈ 70-90s
# total) — verified empirically that combining all 5 metrics x 5 windows into
# one IN-list query still rides the index but does ~25x the work in one call
# (120s for 184k rows); per-combo calls are equivalent total work, cleaner to
# reason about, and let each combo report independently.
_SCALAR_TIMESERIES_METRICS = ("vol_ann", "max_dd", "return_ann", "sharpe", "sortino")
_SCALAR_TIMESERIES_WINDOWS = ("rolling_1y", "rolling_2y", "rolling_3y", "rolling_5y", "rolling_10y")

_TS_LATEST_QUERY = """
    SELECT t.isin, t.real_flag, t.value AS ts_value
    FROM fund_metric_timeseries t
    JOIN (
        SELECT isin, real_flag, MAX(date) AS max_date
        FROM fund_metric_timeseries
        WHERE metric = ? AND {window} = ?
        GROUP BY isin, real_flag
    ) latest ON latest.isin = t.isin AND latest.real_flag = t.real_flag AND latest.max_date = t.date
    WHERE t.metric = ? AND t.{window} = ?
"""


def _pivot_return_ann_by_real_flag(long_df: pd.DataFrame) -> pd.DataFrame:
    """return_ann_nominal/return_ann_real as sibling columns of the same row
    (isin, horizon, metric_version) — needed by REAL_EQUALS_NOMINAL and
    DEFLATION_ORDER, neither of which can be expressed on _pivot_all_metrics'
    output (there, real_flag is part of the index, so nominal and real values
    for the same metric never appear side by side in one row).
    """
    join_keys = ["isin", "horizon", "metric_version"]
    subset = long_df[long_df["metric"] == "return_ann"]
    nominal = subset[subset["real_flag"] == 0][join_keys + ["value"]].rename(
        columns={"value": "return_ann_nominal"})
    real = subset[subset["real_flag"] == 1][join_keys + ["value"]].rename(
        columns={"value": "return_ann_real"})
    return nominal.merge(real, on=join_keys, how="inner")


def _latest_ipc_yoy(conn: sqlite3.Connection) -> float | None:
    """Single scalar YoY inflation rate from the latest available ES CPI
    index vs. ~12 months prior — a coarse eligibility gate for
    REAL_EQUALS_NOMINAL/DEFLATION_ORDER, not a per-fund/per-horizon
    calculation. Deliberately approximate: these two checks only need to know
    whether deflation was possible at all, not the exact rate.
    """
    df = build_population(conn, _IPC_QUERY, require_universe_filter=False)
    if len(df) < 13:
        return None
    df["date"] = pd.to_datetime(df["date"])
    latest = df.iloc[-1]
    year_ago_cutoff = latest["date"] - pd.DateOffset(years=1)
    prior = df[df["date"] <= year_ago_cutoff]
    if prior.empty:
        return None
    prior_index = prior.iloc[-1]["ipc_index"]
    if prior_index == 0:
        return None
    return float(latest["ipc_index"] / prior_index - 1.0)


def _fetch_latest_timeseries_snapshot(conn: sqlite3.Connection, metric: str, window: str) -> pd.DataFrame:
    return pd.read_sql_query(_sql(conn, _TS_LATEST_QUERY), conn, params=(metric, window, metric, window))


_TS_SERIES_SUMMARY_QUERY = """
    SELECT isin, real_flag,
           MIN(date) AS min_date, MAX(date) AS max_date,
           COUNT(DISTINCT date) AS n_dates, COUNT(*) AS n_rows
    FROM fund_metric_timeseries
    WHERE metric = ? AND {window} = ?
    GROUP BY isin, real_flag
"""


def _month_span(min_date: str, max_date: str) -> int:
    """Inclusive month count between two 'YYYY-MM-DD' dates (e.g. same month
    -> 1). Cheap string-slice parse -- these are always month-end dates
    written by the pipeline, never free-text."""
    min_date, max_date = str(min_date), str(max_date)  # Postgres returns datetime.date
    y1, m1 = int(min_date[:4]), int(min_date[5:7])
    y2, m2 = int(max_date[:4]), int(max_date[5:7])
    return (y2 - y1) * 12 + (m2 - m1) + 1


def _run_timeseries_integrity(run: "AuditRun", conn: sqlite3.Connection) -> None:
    """Function #11 (gap #11, wired 2026-09-15) over each (metric, window)
    combo already scoped by _SCALAR_TIMESERIES_METRICS/_WINDOWS.

    NOT implemented via shared.statistical_audit.timeseries.
    check_timeseries_integrity() -- that function needs a full row-level
    DataFrame (isin, date, ...) per series, which on this table (32M+ rows)
    measured >10 minutes for a single (metric, window) combo, let alone all
    25. This instead pulls one aggregate row per (isin, real_flag) --
    MIN(date), MAX(date), COUNT(DISTINCT date), COUNT(*) -- and derives gap/
    duplicate counts from those four numbers (measured ~1s per combo,
    ~25s total). check_timeseries_integrity itself stays correct and tested
    for smaller-scale or ad-hoc callers; this is a deliberate, size-driven
    reimplementation of the same two checks for this one high-volume table.

    Gaps are per-series (each fund's own [min_date, max_date] span), never a
    universe-wide calendar -- a short or recently-launched fund is never
    penalized for months that simply predate its own history.
    """
    for metric in _SCALAR_TIMESERIES_METRICS:
        for window in _SCALAR_TIMESERIES_WINDOWS:
            rows = conn.execute(_sql(conn, _TS_SERIES_SUMMARY_QUERY), (metric, window)).fetchall()
            if not rows:
                run.skipped.append(f"BLOCK5/6 TIMESERIES_INTEGRITY {metric}/{window}: no timeseries rows")
                continue
            group_key = f"{metric}|{window}"

            # PK (isin, metric, window, date, real_flag) makes n_rows > n_dates
            # structurally impossible for a fixed (metric, window) -- this is
            # a paranoia check for a schema/migration defect, not a data issue.
            n_dup_series = sum(1 for _, _, _, _, n_dates, n_rows in rows if n_rows > n_dates)
            if n_dup_series:
                run.findings.append({
                    "block": "BLOCK5", "rule_id": "TIMESERIES_DUPLICATE",
                    "rule_class": "HARD_INVARIANT", "severity": "ALARM",
                    "group_key": group_key, "value": None, "reference_value": None, "threshold": None,
                    "distance": float(n_dup_series),
                    "evidence": f"{n_dup_series} (isin,real_flag) series have n_rows > n_distinct_dates "
                                f"-- the PK should make this impossible",
                    "root_cause_candidate": "Schema/migration defect, not a calculation issue",
                })

            n_series_with_gaps = 0
            n_gap_months = 0
            for _isin, _rf, min_d, max_d, n_dates, _n_rows in rows:
                expected = _month_span(min_d, max_d)
                gap = expected - n_dates
                if gap > 0:
                    n_series_with_gaps += 1
                    n_gap_months += gap
            if n_series_with_gaps:
                run.findings.append({
                    "block": "BLOCK4", "rule_id": "TIMESERIES_GAP",
                    "rule_class": "STATISTICAL_ANOMALY", "severity": "WARN",
                    "group_key": group_key, "value": None, "reference_value": None, "threshold": None,
                    "distance": float(n_gap_months),
                    "evidence": f"{n_gap_months} missing months across {n_series_with_gaps}/{len(rows)} "
                                f"series (within each series' own [min,max] history -- "
                                f"not vs. a universe calendar)",
                    "root_cause_candidate": "Skipped month in a fund's own rolling-metric "
                                             "history (a genuine hole, not a short/new fund)",
                })


def _periodic_return_variance(conn: sqlite3.Connection, isins: list[str]) -> pd.DataFrame:
    """Per-ISIN sample variance (ddof=1) of monthly simple returns, derived
    directly from fund_nav_monthly -- NOT from vol_ann, which would make
    FROZEN_NAV_ZERO_VOL circular and vacuously true (P1.1, 2026-09-15).

    Returns one row per isin; the caller merges on isin only (not
    horizon/metric_version) since this is a property of the NAV series
    itself, broadcasting the same value across all of that isin's rows.
    """
    if not isins:
        return pd.DataFrame(columns=["isin", "periodic_return_variance"])
    placeholders = ",".join(_ph(conn) for _ in isins)
    nav_df = pd.read_sql_query(
        f"""SELECT ISIN AS isin, Date AS date, NAV AS nav FROM fund_nav_monthly
            WHERE ISIN IN ({placeholders}) ORDER BY ISIN, Date""",
        conn, params=isins,
    )
    if nav_df.empty:
        return pd.DataFrame(columns=["isin", "periodic_return_variance"])
    variances = (
        nav_df.groupby("isin")["nav"]
        .apply(lambda s: s.pct_change().var(ddof=1))
        .rename("periodic_return_variance")
        .reset_index()
    )
    return variances


_P2_METRICS_COLS = ("isin", "metric", "horizon", "value", "real_flag", "metric_version", "Fund_Nature")


def run_p2_audit(conn: sqlite3.Connection) -> "AuditRun":
    long_df = _restore_case(build_population(conn, _P2_METRICS_QUERY), _P2_METRICS_COLS)

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

        if (
            metric in _PEER_METRICS and horizon == _PEER_HORIZON
            and real_flag == _PEER_REAL_FLAG and metric_version == "v1"
        ):
            for nature, peer_group in group.groupby("Fund_Nature"):
                if len(peer_group) < MIN_PEERS:
                    run.skipped.append(
                        f"BLOCK1/4 PEER:{nature} {group_key}: {len(peer_group)} < MIN_PEERS={MIN_PEERS}"
                    )
                    continue
                peer_key = f"{group_key}|PEER:{nature}"
                peer_series = peer_group["value"]
                _block1(run, group_key, peer_series, len(peer_group),
                        numeric=True, supports_moments=spec.supports_moments,
                        population=f"PEER:{nature}")
                if spec.statistical_type in ("continuous_positive", "continuous_signed", "bounded_unit"):
                    peer_indexed = peer_series.set_axis(peer_group["isin"])
                    for method in ("IQR", "MAD_Z"):
                        _block4(run, peer_key, peer_indexed, method)

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

    ipc_yoy = _latest_ipc_yoy(conn)
    deflation_frame = _pivot_return_ann_by_real_flag(long_df)
    if deflation_frame.empty:
        run.skipped.append("BLOCK2 REAL_EQUALS_NOMINAL: no overlapping nominal/real return_ann rows")
    else:
        deflation_frame = deflation_frame.assign(ipc_yoy=ipc_yoy if ipc_yoy is not None else float("nan"))
        rule = P2_PAIRS["REAL_EQUALS_NOMINAL"]
        result = compare_pairs(deflation_frame, "return_ann_nominal", "return_ann_real", rule)
        if result.triggered:
            run.findings.append({
                "block": "BLOCK2", "rule_id": "REAL_EQUALS_NOMINAL", "rule_class": "STATISTICAL_ANOMALY",
                "severity": rule.severity, "group_key": "REAL_EQUALS_NOMINAL",
                "value": None, "reference_value": None, "threshold": rule.tolerance,
                "distance": float(result.n_matches),
                "evidence": (
                    f"{result.n_matches}/{result.n_eligible} eligible rows within tolerance "
                    f"(ipc_yoy={ipc_yoy:.4f})" if ipc_yoy is not None else
                    f"{result.n_matches}/{result.n_eligible} eligible rows within tolerance"
                ),
                "root_cause_candidate": rule.diagnosis,
            })

    for metric in _SCALAR_TIMESERIES_METRICS:
        for window in _SCALAR_TIMESERIES_WINDOWS:
            ts_snapshot = _fetch_latest_timeseries_snapshot(conn, metric, window)
            if ts_snapshot.empty:
                run.skipped.append(f"BLOCK2 SCALAR_EQUALS_TIMESERIES {metric}/{window}: no timeseries rows")
                continue
            scalar_slice = long_df[(long_df["metric"] == metric) & (long_df["horizon"] == window)]
            merged = scalar_slice.merge(ts_snapshot, on=["isin", "real_flag"], how="inner")
            if merged.empty:
                run.skipped.append(f"BLOCK2 SCALAR_EQUALS_TIMESERIES {metric}/{window}: no overlapping rows")
                continue
            rule_id = f"SCALAR_EQUALS_TIMESERIES_{metric}_{window}"
            rule = replace(P2_PAIRS["SCALAR_EQUALS_TIMESERIES"], rule_id=rule_id)
            merged = merged.rename(columns={"value": "scalar_value"})
            result = compare_pairs(merged, "scalar_value", "ts_value", rule)
            # Some divergence is expected from ordinary calc-timing lag between
            # the two write paths (fund_metrics is INSERT OR REPLACE'd fresh
            # each run; fund_metric_timeseries is INSERT OR IGNORE, append-only
            # per new date) — gate on the same n>=8 significance convention
            # used throughout this catalog, not a zero-tolerance count.
            n_divergent = result.n_eligible - result.n_matches
            if n_divergent >= rule.min_matches:
                run.findings.append({
                    "block": "BLOCK2", "rule_id": rule_id, "rule_class": "STATISTICAL_ANOMALY",
                    "severity": "WARN", "group_key": f"{metric}|{window}",
                    "value": None, "reference_value": None, "threshold": rule.tolerance,
                    "distance": float(n_divergent),
                    "evidence": f"{n_divergent}/{result.n_eligible} funds diverge between "
                                f"fund_metrics scalar and latest fund_metric_timeseries snapshot",
                    "root_cause_candidate": P2_PAIRS["SCALAR_EQUALS_TIMESERIES"].diagnosis,
                })

    _run_timeseries_integrity(run, conn)

    wide_for_invariants = _pivot_all_metrics(long_df)
    if not deflation_frame.empty:
        wide_for_invariants = wide_for_invariants.merge(
            deflation_frame, on=["isin", "horizon", "metric_version"], how="left",
        )
    if "return_ann" in wide_for_invariants.columns:
        wide_for_invariants["excess_return"] = wide_for_invariants["return_ann"] - RISK_FREE_RATE_ANN
    if "vol_ann" in wide_for_invariants.columns:
        prv = _periodic_return_variance(conn, wide_for_invariants["isin"].unique().tolist())
        if not prv.empty:
            wide_for_invariants = wide_for_invariants.merge(prv, on="isin", how="left")
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
        self.statistics: list[tuple] = []  # (population, group_key, stats_dict, n)
        self.findings: list[dict] = []
        self.skipped: list[str] = []


def _block1(
    run: AuditRun, group_key: str, series: pd.Series, n_expected: int,
    numeric: bool = True, supports_moments: bool = True, population: str = "GLOBAL",
) -> None:
    stats: dict = profile_coverage(series, n_expected=n_expected)
    if numeric:
        stats.update(profile_location(series))
        if supports_moments:
            stats.update(profile_moments(series))
    stats.update(profile_mass_points(series))
    run.statistics.append((population, group_key, stats, stats.get("n_valid")))

    # PEER findings are additional context for a GLOBAL group already profiled
    # above; only escalate to a finding once, at GLOBAL, to avoid duplicate
    # noise for every peer segment of the same underlying group.
    if population != "GLOBAL":
        return

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


def _run_group_checks(run: AuditRun, rules, frame: pd.DataFrame, block: str = "BLOCK5") -> None:
    """Detection only (AUDITORIA_ESTADISTICA.md §2.7) — see group_checks.py
    for why this can't be expressed via _run_invariants/check_invariant.
    """
    for rule in rules:
        if rule.group_column not in frame.columns or rule.value_column not in frame.columns:
            run.skipped.append(
                f"{block} {rule.rule_id}: columns not present in the queried frame "
                f"({rule.group_column!r}, {rule.value_column!r})"
            )
            continue

        result = check_group_constancy(frame, rule)
        if result.n_violating_groups == 0:
            continue
        run.findings.append({
            "block": block, "rule_id": rule.rule_id, "rule_class": "HARD_INVARIANT",
            "severity": "ALARM", "group_key": rule.rule_id, "value": None,
            "reference_value": None, "threshold": None,
            "distance": float(result.n_violating_groups),
            "evidence": f"{result.n_violating_groups}/{result.n_groups_checked} ISINs with "
                        f"{rule.min_group_size}+ Horizon_Years rows have identical "
                        f"{rule.value_column} across every row",
            "root_cause_candidate": rule.description,
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
    catalog_version = compute_catalog_version()
    clear_run(conn, run_id, run.domain)
    n_stats = 0
    for population, group_key, stats, n in run.statistics:
        n_stats += emit_statistics(conn, run_id, run.domain, population, group_key, stats, n=n,
                                   catalog_version=catalog_version)
    n_findings = emit_findings(conn, run_id, run.domain, run.findings, catalog_version=catalog_version)
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
    moved = moved[(moved["delta"] != 0) & ~moved["is_noise"]]
    n_noise = int(((result.deltas["delta"] != 0) & result.deltas["is_noise"]).sum())
    moved = moved.sort_values("delta", key=lambda s: s.abs(), ascending=False)
    print(f"Largest deltas (top {top_n} of {len(moved)} material; {n_noise} floating-point-noise differences hidden):")
    for _, row in moved.head(top_n).iterrows():
        pct = f" ({row['pct_change']:+.1%})" if pd.notna(row["pct_change"]) else ""
        print(f"  {row['group_key']} [{row['stat_name']}]: {row['previous_value']:.6g} -> {row['current_value']:.6g}{pct}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=["costs", "p2"], required=True)
    parser.add_argument("--mode", choices=["report", "check"], default="report")
    parser.add_argument("--backend", choices=["sqlite", "postgres"], default=None,
                        help="Default: FONDOS_DB_BACKEND (postgres since the 2026-09-23 cutover)")
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite path; only used with --backend sqlite")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--compare-to", default=None, help="Prior run_id to diff against (function #13)")
    args = parser.parse_args()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("audit_%Y%m%dT%H%M%SZ")
    keep_open = args.persist or args.compare_to

    conn = get_connection(Path(args.db), backend=args.backend)
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
