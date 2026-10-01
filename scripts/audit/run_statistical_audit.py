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
    reconcile_with_alerts) is wired in (2026-09-28), gated on a freshness
    check (_alerts_are_stale) comparing the latest fund_metric_alerts.detected_at
    against the latest fund_metrics.calculation_date for the population --
    reconciliation is skipped, not silently run against stale data, when the
    alert engine hasn't run since the last P2 recompute.

D1 (FND-0134, 2026-09-28, Wave 4 boundary declaration): this module produces
the raw findings/statistics data both skills' §5 Output Format is built from --
it does NOT itself produce that section's narrative synthesis (corrections-
applied summary, residual inventory, ranked action items). That synthesis is
the calling skill session's responsibility, working from this module's
--persist'd audit_statistic/audit_finding rows or its --mode report stdout.
Deliberate, not a gap: the module stays read-only/detection-only (P#2, R-2).

Usage:
    python -X utf8 scripts/audit/run_statistical_audit.py --domain costs
    python -X utf8 scripts/audit/run_statistical_audit.py --domain p2 --mode check
    python -X utf8 scripts/audit/run_statistical_audit.py --domain costs --persist
    python -X utf8 scripts/audit/run_statistical_audit.py --domain costs --compare-to audit_20260912T223518Z
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from shared.config import MIN_NAV_ROWS, RISK_FREE_RATE_ANN, ROLLING_WINDOWS
from shared.db import get_connection
from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS
from shared.statistical_audit.catalog_version import compute_catalog_version
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS
from shared.statistical_audit.catalog_metric_bounds import METRIC_BOUNDS, get_metric_bound
from shared.statistical_audit.catalog_retired import RETIRED_RULES
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
from shared.statistical_audit.reconcile import reconcile_with_alerts
from shared.statistical_audit.recompute_gate import assert_recompute_happened, capture_state
from shared.statistical_audit.snapshot import build_population, build_snapshot
from shared.statistical_audit.timeseries import build_window_deflation_frame
from shared.statistical_audit.tolerances import KID_ROUNDING_TOLERANCE_PP

MIN_PEERS = 5

# A4 (FND-0122, 2026-09-28): both skills' Block 1 mandate flagging a coverage (n_valid) drop of
# more than this fraction vs. the prior persisted run. Unlike shared/statistical_audit/tolerances.py's
# constants, this is a drift-detection threshold, not a measured comparison tolerance between two
# same-run values -- it belongs here, not in that file's audited-sweep-only scope.
COVERAGE_CLIFF_PCT = 0.02

# B1 (FND-0123, 2026-09-28): cost skill Block 6 misparse guard -- a schedule row with
# Total_Costs_EUR below this floor at Horizon_Years >= 1 has likely lost a thousands separator
# during extraction (e.g. "1.234" parsed as 1.234 EUR instead of 1234). Same reasoning class as
# COVERAGE_CLIFF_PCT: a heuristic detection threshold, not a measured comparison tolerance.
SCHEDULE_EUR_MISPARSE_FLOOR = 20

# B3 (FND-0125, 2026-09-28): both skills' snapshot contract -- per-ISIN MAX(date) settlement lag
# tolerance before a fund is held out of cross-sectional fund_metric_timeseries blocks. Same
# reasoning class as COVERAGE_CLIFF_PCT/SCHEDULE_EUR_MISPARSE_FLOOR (a stated default from the
# skill text, not a live-DB-swept comparison tolerance) -- deliberately not in tolerances.py.
SNAPSHOT_MAX_SPREAD_DAYS = 5


# ============================================================
# Backend dialect helpers (SQLite retired 2026-09-23; Postgres is primary).
# Every query below is written once, unquoted, and adapted here rather than
# duplicated per dialect.
# ============================================================

def _ph(conn) -> str:
    # Runner-local `?` -> `%s` is safe: none of these queries touch jsonb, whose
    # native `?` operator is why shared/db.py refuses a global translator.
    return "%s"


def _sql(conn, query: str, isin_filter: str = "") -> str:
    """Adapts a query written with `?` placeholders, a `{window}` slot, and an
    optional `{isin_filter}` slot for the connected backend. fund_metric_timeseries.window
    is `window_label` in Postgres (`window` is a reserved word there; db/pg/rename_map.yaml).
    `isin_filter` (from _isin_filter()) is substituted before the `?`->placeholder pass so
    its own `?`s get adapted too."""
    window_col = "window_label"
    return (
        query.replace("{window}", window_col)
        .replace("{isin_filter}", isin_filter)
        .replace("?", _ph(conn))
    )


def _isin_filter(conn, isins: "Sequence[str] | None", column: str) -> tuple[str, tuple]:
    """Wave 0 (2026-09-28, audit-skill-alignment plan): an opt-in `{isin_filter}` fragment
    so every domain query can be scoped to a fixed ISIN list -- required to validate this
    module's changes on the 40-ISIN sample (feedback_validate_on_isin_samples) instead of
    the full universe. Returns ("", ()) when isins is falsy, so every call site that always
    substitutes {isin_filter} works unchanged whether or not --isin was passed."""
    if not isins:
        return "", ()
    placeholders = ",".join(_ph(conn) for _ in isins)
    return f"AND {column} IN ({placeholders})", tuple(isins)


def _df(conn, query: str, params=()) -> pd.DataFrame:
    """pd.read_sql_query(query, conn, ...) emits a UserWarning on a plain psycopg3 connection
    ("only supports SQLAlchemy connectable ... or sqlite3 DBAPI2") on every single call -- pure log
    noise on a hermetic/live run (FND-0108). fetchall()+DataFrame(columns=...), same pattern already
    used by export_tables.py/pipeline.py/fund_scorer.py/db_readers.py for the identical reason."""
    cur = conn.execute(query, params)
    cols = [d[0] for d in cur.description]   # index, not .name: portable across psycopg3/sqlite3
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


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
    {isin_filter}
"""

_COST_SCHEDULE_QUERY = """
    SELECT s.ISIN, s.Horizon_Years, s.Is_RHP, s.Total_Costs_EUR,
           s.Total_Costs_Pct, s.Annual_Impact_Pct
    FROM fund_cost_schedule s
    JOIN fund_master m ON m.ISIN = s.ISIN
    WHERE m.In_Current_Universe = 1
    {isin_filter}
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


def _run_cost_schedule_integrity(run: "AuditRun", master: pd.DataFrame, schedule: pd.DataFrame) -> None:
    """Block 6 (costs, B1/FND-0123, 2026-09-28): fund_cost_schedule <-> fund_master coherence.
    Five checks the skill mandates and the runner never implemented at all -- pure pandas on the
    two frames run_cost_audit already loaded, no new SQL.
    """
    if schedule.empty:
        run.skipped.append("BLOCK6 cost schedule integrity: fund_cost_schedule is empty for this population")
        return

    # (a) EUR<->Pct coherence: a 10,000 EUR notional means Total_Costs_EUR == 100 * Total_Costs_Pct.
    coherence = schedule.dropna(subset=["Total_Costs_EUR", "Total_Costs_Pct"])
    if not coherence.empty:
        implied_pct = coherence["Total_Costs_EUR"] / 100.0
        n_mismatch = int(((implied_pct - coherence["Total_Costs_Pct"]).abs() >= KID_ROUNDING_TOLERANCE_PP).sum())
        if n_mismatch:
            run.findings.append({
                "block": "BLOCK6", "rule_id": "SCHEDULE_EUR_PCT_COHERENCE",
                "rule_class": "PLAUSIBILITY", "severity": "WARN", "group_key": "SCHEDULE_EUR_PCT_COHERENCE",
                "value": None, "reference_value": None, "threshold": KID_ROUNDING_TOLERANCE_PP,
                "distance": float(n_mismatch),
                "evidence": f"{n_mismatch}/{len(coherence)} rows: Total_Costs_EUR/100 != Total_Costs_Pct "
                            f"beyond {KID_ROUNDING_TOLERANCE_PP}pp",
                "root_cause_candidate": "Total_Costs_EUR/Total_Costs_Pct extracted from inconsistent sources "
                                         "or a scale error",
            })

    # (b) Total_Costs_EUR < floor misparse guard (thousands-separator loss), Horizon_Years >= 1
    # only -- genuine sub-1-year horizons can legitimately carry a small EUR cost.
    candidates = schedule.dropna(subset=["Total_Costs_EUR", "Horizon_Years"])
    misparse = candidates[
        (candidates["Total_Costs_EUR"] < SCHEDULE_EUR_MISPARSE_FLOOR) & (candidates["Horizon_Years"] >= 1)
    ]
    if not misparse.empty:
        run.findings.append({
            "block": "BLOCK6", "rule_id": "SCHEDULE_EUR_MISPARSE",
            "rule_class": "PLAUSIBILITY", "severity": "WARN", "group_key": "SCHEDULE_EUR_MISPARSE",
            "value": None, "reference_value": None, "threshold": SCHEDULE_EUR_MISPARSE_FLOOR,
            "distance": float(len(misparse)),
            "evidence": f"{len(misparse)} rows with Total_Costs_EUR < {SCHEDULE_EUR_MISPARSE_FLOOR} "
                        f"at Horizon_Years >= 1",
            "root_cause_candidate": "Thousands separator lost during extraction",
        })

    rhp_rows = schedule[schedule["Is_RHP"] == 1]

    # (e) >1 Is_RHP=1 row per ISIN -- no UNIQUE constraint enforces this (idx_cost_schedule_rhp is
    # a plain partial index).
    rhp_counts = rhp_rows.groupby("ISIN").size()
    dup_rhp = rhp_counts[rhp_counts > 1]
    if not dup_rhp.empty:
        run.findings.append({
            "block": "BLOCK6", "rule_id": "SCHEDULE_MULTIPLE_RHP_ROWS",
            "rule_class": "HARD_INVARIANT", "severity": "ALARM", "group_key": "SCHEDULE_MULTIPLE_RHP_ROWS",
            "value": None, "reference_value": None, "threshold": 1,
            "distance": float(len(dup_rhp)),
            "evidence": f"{len(dup_rhp)} ISINs have >1 Is_RHP=1 row (max {int(dup_rhp.max())})",
            "root_cause_candidate": "Schedule-build logic marked more than one Horizon_Years row as the RHP row",
        })

    # (c) Is_RHP=1 row's Annual_Impact_Pct vs fund_master.ACI_RHP -- ACI_RHP is DERIVED from this
    # value by construction (fund_writer.py/priips_cost_extractor.py FIX-ACI-SCHEDULE-INJECT), so
    # a mismatch beyond KID rounding means the two have gone out of sync.
    rhp_join = rhp_rows.dropna(subset=["Annual_Impact_Pct"]).merge(
        master[["ISIN", "ACI_RHP"]].dropna(subset=["ACI_RHP"]), on="ISIN", how="inner",
    )
    if not rhp_join.empty:
        n_disagree = int(
            ((rhp_join["Annual_Impact_Pct"] - rhp_join["ACI_RHP"]).abs() >= KID_ROUNDING_TOLERANCE_PP).sum()
        )
        if n_disagree:
            run.findings.append({
                "block": "BLOCK6", "rule_id": "SCHEDULE_RHP_ACI_MISMATCH",
                "rule_class": "HARD_INVARIANT", "severity": "ALARM", "group_key": "SCHEDULE_RHP_ACI_MISMATCH",
                "value": None, "reference_value": None, "threshold": KID_ROUNDING_TOLERANCE_PP,
                "distance": float(n_disagree),
                "evidence": f"{n_disagree}/{len(rhp_join)} Is_RHP=1 rows: Annual_Impact_Pct != "
                            f"fund_master.ACI_RHP beyond {KID_ROUNDING_TOLERANCE_PP}pp",
                "root_cause_candidate": "fund_master.ACI_RHP and the Is_RHP=1 schedule row were written by "
                                         "different, now-diverged extraction passes",
            })

    # (d) ACI_RHP set with no Is_RHP=1 row at all for that ISIN.
    isins_with_rhp_row = set(rhp_rows["ISIN"])
    orphans = master[master["ACI_RHP"].notna() & ~master["ISIN"].isin(isins_with_rhp_row)]
    if not orphans.empty:
        run.findings.append({
            "block": "BLOCK6", "rule_id": "SCHEDULE_ACI_RHP_ORPHAN",
            "rule_class": "HARD_INVARIANT", "severity": "ALARM", "group_key": "SCHEDULE_ACI_RHP_ORPHAN",
            "value": None, "reference_value": None, "threshold": None,
            "distance": float(len(orphans)),
            "evidence": f"{len(orphans)} ISINs have fund_master.ACI_RHP set but no fund_cost_schedule "
                        f"Is_RHP=1 row",
            "root_cause_candidate": "ACI_RHP set by a fallback path that never wrote/promoted a schedule row "
                                     "(see FIX-ACI-SCHEDULE-INJECT)",
        })


def run_cost_audit(conn: "psycopg.Connection", isins: Sequence[str] | None = None) -> "AuditRun":
    master_filter, master_params = _isin_filter(conn, isins, "ISIN")
    schedule_filter, schedule_params = _isin_filter(conn, isins, "s.ISIN")
    master_query = _COST_MASTER_QUERY.replace("{isin_filter}", master_filter)
    schedule_query = _COST_SCHEDULE_QUERY.replace("{isin_filter}", schedule_filter)
    master = _restore_case(build_population(conn, master_query, master_params), _COST_MASTER_COLS)
    schedule = _restore_case(build_population(conn, schedule_query, schedule_params), _COST_SCHEDULE_COLS)
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
    _run_cost_schedule_integrity(run, master, schedule)

    return run


# ============================================================
# P2 metrics domain
# ============================================================

_P2_METRICS_QUERY = """
    SELECT fm.ISIN, fm.metric, fm.horizon, fm.value, fm.real_flag, fm.metric_version,
           fm.batch_id, m.Fund_Nature
    FROM fund_metrics fm
    JOIN fund_master m ON m.ISIN = fm.ISIN
    WHERE m.In_Current_Universe = 1
    {isin_filter}
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

# FND-0137 Section B (2026-09-29): Reference Boundary Audit + Orphaned Asset Identification
# (AUDITORIA_ESTADISTICA.md §4 Block 6, temporal integrity). INFO-severity only -- a fund whose
# NAV history predates or outlasts a macro source's coverage is an expected, correctly-handled
# degradation (deflate_nav()'s leading-gap bfill / a merge_asof backward match that's simply the
# most recent one available for a trailing date), not a defect; catalog_invariants.py's
# WINDOW_FISHER_IDENTITY (Section A) already row-checks that the degradation was applied
# correctly, so this section reports COVERAGE, not correctness.
_INFLATION_BOUNDARY_QUERY = (
    "SELECT geography, MIN(date) AS min_date, MAX(date) AS max_date FROM series_inflation "
    "GROUP BY geography ORDER BY geography"
)
_MACRO_BOUNDARY_QUERY = (
    "SELECT indicator, geography, MIN(date) AS min_date, MAX(date) AS max_date FROM series_macro "
    "GROUP BY indicator, geography ORDER BY indicator, geography"
)

# Operational lifespan comes from fund_nav_monthly (the actual deflation INPUT and the same
# population the FND-0114 investigation itself used to count the 275 affected ISINs), not from
# fund_metric_timeseries: the timeseries table only carries ROLLING-WINDOW END dates -- a strict
# subset of a fund's true lifespan that starts well after inception and would understate any
# leading-gap overrun.
_NAV_LIFESPAN_QUERY = """
    SELECT n.ISIN AS isin, MIN(n.Date) AS min_date, MAX(n.Date) AS max_date
    FROM fund_nav_monthly n
    JOIN fund_master m ON m.ISIN = n.ISIN
    WHERE m.In_Current_Universe = 1
    {isin_filter}
    GROUP BY n.ISIN
"""

# B5 (FND-0127, 2026-09-28): Block 4 <-> fund_metric_alerts reconciliation (function #12).
_ALERTS_QUERY = """
    SELECT a.isin, a.metric, a.detected_at
    FROM fund_metric_alerts a
    JOIN fund_master m ON m.ISIN = a.isin
    WHERE m.In_Current_Universe = 1
    {isin_filter}
"""

_MAX_CALC_DATE_QUERY = """
    SELECT MAX(fm.calculation_date) AS max_calc_date
    FROM fund_metrics fm
    JOIN fund_master m ON m.ISIN = fm.ISIN
    WHERE m.In_Current_Universe = 1
    {isin_filter}
"""

# SCALAR_EQUALS_TIMESERIES (2026-09-13): per-(metric,window) queries, each a
# clean prefix match on idx_fmts_mwr_isin_date (~3s, ~25 combos ≈ 70-90s
# total) — verified empirically that combining all 5 metrics x 5 windows into
# one IN-list query still rides the index but does ~25x the work in one call
# (120s for 184k rows); per-combo calls are equivalent total work, cleaner to
# reason about, and let each combo report independently.
_SCALAR_TIMESERIES_METRICS = ("vol_ann", "max_dd", "return_ann", "sharpe", "sortino")
# B7 (FND-0129, 2026-09-28): derived from shared.config.ROLLING_WINDOWS (P#11/R-1) instead of a
# hardcoded tuple literal. No behaviour change today -- ROLLING_WINDOWS has no 1m/3m/6m keys;
# those are SHORT_WINDOWS, written into fund_metrics with metric_version='d1', not into
# fund_metric_timeseries (the P2 skill's scope text naming them here is itself stale -- Wave 3).
_SCALAR_TIMESERIES_WINDOWS = tuple(ROLLING_WINDOWS.keys())

# v27 pivot: fund_metric_timeseries no longer carries real_flag (pivoted into value_nominal/
# value_real, see db/pg/30_gold.sql) — v_fund_metric_timeseries_long reconstructs the pre-pivot
# long shape this audit still needs, losslessly.
_TS_LATEST_QUERY = """
    SELECT t.isin, t.real_flag, t.value AS ts_value, t.date
    FROM v_fund_metric_timeseries_long t
    JOIN (
        SELECT isin, real_flag, MAX(date) AS max_date
        FROM v_fund_metric_timeseries_long
        WHERE metric = ? AND {window} = ?
        {isin_filter}
        GROUP BY isin, real_flag
    ) latest ON latest.isin = t.isin AND latest.real_flag = t.real_flag AND latest.max_date = t.date
    WHERE t.metric = ? AND t.{window} = ?
"""


def _pivot_by_real_flag(long_df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """<metric>_nominal/<metric>_real as sibling columns of the same row
    (isin, horizon, metric_version) for one metric — needed by REAL_EQUALS_NOMINAL
    (A3, FND-0121: generalized from return_ann-only to every metric with both
    real_flag values present) and by DEFLATION_ORDER (return_ann only). Neither can
    be expressed on _pivot_all_metrics' output (there, real_flag is part of the
    index, so nominal and real values for the same metric never appear side by
    side in one row). Empty when the metric has no overlapping nominal/real rows
    (the common case — most metrics are never deflated).
    """
    join_keys = ["isin", "horizon", "metric_version"]
    subset = long_df[long_df["metric"] == metric]
    nominal = subset[subset["real_flag"] == 0][join_keys + ["value"]].rename(
        columns={"value": f"{metric}_nominal"})
    real = subset[subset["real_flag"] == 1][join_keys + ["value"]].rename(
        columns={"value": f"{metric}_real"})
    return nominal.merge(real, on=join_keys, how="inner")


def _latest_ipc_yoy(conn: "psycopg.Connection") -> float | None:
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


def _fetch_latest_timeseries_snapshot(
    conn: "psycopg.Connection", metric: str, window: str, isins: Sequence[str] | None = None,
) -> pd.DataFrame:
    isin_filter, isin_params = _isin_filter(conn, isins, "isin")
    query = _sql(conn, _TS_LATEST_QUERY, isin_filter=isin_filter)
    return _df(conn, query, (metric, window, *isin_params, metric, window))


# v27 pivot: same reason as _TS_LATEST_QUERY above — reads the long-shape compatibility view.
# n_null_batch/n_below_min_obs (B2/FND-0124, 2026-09-28): the two Block 6 provenance checks the
# P2 skill mandates -- added as aggregate FILTER columns rather than a second query, riding the
# same GROUP BY and the same idx_fmts_mwr_isin_date-style index scan as the four columns above.
_TS_SERIES_SUMMARY_QUERY = """
    SELECT isin, real_flag,
           MIN(date) AS min_date, MAX(date) AS max_date,
           COUNT(DISTINCT date) AS n_dates, COUNT(*) AS n_rows,
           COUNT(*) FILTER (WHERE batch_id IS NULL) AS n_null_batch,
           COUNT(*) FILTER (WHERE value IS NOT NULL AND source_rows < ?) AS n_below_min_obs
    FROM v_fund_metric_timeseries_long
    WHERE metric = ? AND {window} = ?
    {isin_filter}
    GROUP BY isin, real_flag
"""


# FND-0114 (2026-09-29): inputs to timeseries.build_window_deflation_frame() -- the per-window
# Block 5 deflation invariants (WINDOW_NOMINAL_IDENTITY/WINDOW_DEFLATION_STRICT/
# WINDOW_FISHER_IDENTITY, catalog_invariants.py). Reads the base fund_metric_timeseries table
# directly (post-v27-pivot, nominal/real already sit side by side per row -- no long-shape view
# needed) rather than v_fund_metric_timeseries_long, since this needs both variants on one row,
# not the long/pivoted shape the view reconstructs. Step-0 timing (2026-09-29, live DB, count-only
# then full fetch): ~1.4s/window fetch x 5 windows = 7.3s total, well under the 120s gate -- no
# SQL-side prefilter needed.
_TS_WINDOW_DEFLATION_QUERY = """
    SELECT t.isin, t.date, t.value_nominal AS w_return_nominal, t.value_real AS w_return_real,
           t.source_rows AS w_n_obs
    FROM fund_metric_timeseries t
    JOIN fund_master m ON m.ISIN = t.isin
    WHERE t.metric = 'return_ann' AND t.{window} = ?
      AND t.has_real AND t.value_nominal IS NOT NULL AND t.value_real IS NOT NULL
      AND m.In_Current_Universe = 1
    {isin_filter}
"""

_NAV_DATES_QUERY = """
    SELECT n.ISIN AS isin, n.Date AS date, n.NAV AS nav
    FROM fund_nav_monthly n
    JOIN fund_master m ON m.ISIN = n.ISIN
    WHERE m.In_Current_Universe = 1
    {isin_filter}
"""


def _build_window_deflation_frame(
    conn: "psycopg.Connection", isins: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Fetches the three raw inputs (per-window return_ann pairs, NAV dates+values, ES CPI) and
    hands them to the pure builder (shared.statistical_audit.timeseries.build_window_deflation_frame,
    R-7-testable without a DB). One frame across all ROLLING_WINDOWS keys, concatenated with a
    window_label column so _run_invariants can route the 3 FND-0114 rules to it in one pass.
    Empty (not an error) when the population has no eligible rows yet -- the same "skip, don't
    fail" contract every other frame builder in this module follows.
    """
    isin_filter, isin_params = _isin_filter(conn, isins, "t.isin")
    ts_query = _sql(conn, _TS_WINDOW_DEFLATION_QUERY, isin_filter=isin_filter)
    ts_frames = []
    for window_label in ROLLING_WINDOWS:
        df = _df(conn, ts_query, (window_label, *isin_params))
        if df.empty:
            continue
        df["window_label"] = window_label
        ts_frames.append(df)
    if not ts_frames:
        return pd.DataFrame()
    ts = pd.concat(ts_frames, ignore_index=True)

    nav_isin_filter, nav_isin_params = _isin_filter(conn, isins, "n.ISIN")
    nav_query = _sql(conn, _NAV_DATES_QUERY, isin_filter=nav_isin_filter)
    nav_dates = _df(conn, nav_query, nav_isin_params)
    if nav_dates.empty:
        return pd.DataFrame()

    ipc = build_population(conn, _IPC_QUERY, require_universe_filter=False)
    if ipc.empty:
        return pd.DataFrame()

    return build_window_deflation_frame(ts, nav_dates, ipc)


def _month_span(min_date: str, max_date: str) -> int:
    """Inclusive month count between two 'YYYY-MM-DD' dates (e.g. same month
    -> 1). Cheap string-slice parse -- these are always month-end dates
    written by the pipeline, never free-text."""
    min_date, max_date = str(min_date), str(max_date)  # Postgres returns datetime.date
    y1, m1 = int(min_date[:4]), int(min_date[5:7])
    y2, m2 = int(max_date[:4]), int(max_date[5:7])
    return (y2 - y1) * 12 + (m2 - m1) + 1


def _run_timeseries_integrity(
    run: "AuditRun", conn: "psycopg.Connection", isins: Sequence[str] | None = None,
) -> None:
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
    isin_filter, isin_params = _isin_filter(conn, isins, "isin")
    summary_query = _sql(conn, _TS_SERIES_SUMMARY_QUERY, isin_filter=isin_filter)
    for metric in _SCALAR_TIMESERIES_METRICS:
        for window in _SCALAR_TIMESERIES_WINDOWS:
            rows = conn.execute(summary_query, (MIN_NAV_ROWS, metric, window, *isin_params)).fetchall()
            if not rows:
                run.skipped.append(f"BLOCK5/6 TIMESERIES_INTEGRITY {metric}/{window}: no timeseries rows")
                continue
            group_key = f"{metric}|{window}"

            # PK (isin, metric, window, date, real_flag) makes n_rows > n_dates
            # structurally impossible for a fixed (metric, window) -- this is
            # a paranoia check for a schema/migration defect, not a data issue.
            n_dup_series = sum(1 for row in rows if row[5] > row[4])
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
            n_null_batch_total = 0
            n_below_min_obs_total = 0
            for _isin, _rf, min_d, max_d, n_dates, _n_rows, n_null_batch, n_below_min_obs in rows:
                expected = _month_span(min_d, max_d)
                gap = expected - n_dates
                if gap > 0:
                    n_series_with_gaps += 1
                    n_gap_months += gap
                n_null_batch_total += n_null_batch
                n_below_min_obs_total += n_below_min_obs
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

            # B2/FND-0124 (2026-09-28): the two P2 Block 6 provenance checks the skill mandates.
            if n_null_batch_total:
                run.findings.append({
                    "block": "BLOCK6", "rule_id": "TIMESERIES_NULL_PROVENANCE",
                    "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO",
                    "group_key": group_key, "value": None, "reference_value": None, "threshold": None,
                    "distance": float(n_null_batch_total),
                    "evidence": f"{n_null_batch_total} rows have batch_id IS NULL -- a NULL means the row "
                                f"predates the v26 audit columns (first-insert provenance; INSERT OR IGNORE "
                                f"preserves it)",
                    "root_cause_candidate": "Row written before the v26 batch_id/algorithm_version columns "
                                             "existed",
                })
            if n_below_min_obs_total:
                run.findings.append({
                    "block": "BLOCK6", "rule_id": "TIMESERIES_BELOW_MIN_OBS",
                    "rule_class": "STATISTICAL_ANOMALY", "severity": "WARN",
                    "group_key": group_key, "value": None, "reference_value": None,
                    "threshold": MIN_NAV_ROWS, "distance": float(n_below_min_obs_total),
                    "evidence": f"{n_below_min_obs_total} rows have a non-NULL value with "
                                f"source_rows < {MIN_NAV_ROWS} (the min-obs floor compute_rolling_rows() "
                                f"uses today -- historical rows written under an earlier, lower floor are "
                                f"a plausible non-defect explanation, not just a current bug)",
                    "root_cause_candidate": "A value was written despite insufficient observations for "
                                             "that window, or MIN_NAV_ROWS was raised after these rows "
                                             "were written",
                })


def _periodic_return_variance(conn: "psycopg.Connection", isins: list[str]) -> pd.DataFrame:
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
    nav_df = _df(
        conn,
        f"""SELECT ISIN AS isin, Date AS date, NAV AS nav FROM fund_nav_monthly
            WHERE ISIN IN ({placeholders}) ORDER BY ISIN, Date""",
        isins,
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


_P2_METRICS_COLS = (
    "isin", "metric", "horizon", "value", "real_flag", "metric_version", "batch_id", "Fund_Nature",
)


def _deflation_meaningful(metric: str) -> bool:
    """REAL_EQUALS_NOMINAL (A3, FND-0121, 2026-09-28) only means anything for a metric whose raw
    value scales with a uniform per-run deflator (one ES-CPI scalar applied to every fund).
    Guards against a real, empirically-confirmed false-positive class (live 3-ISIN smoke test,
    2026-09-28): count/fraction/rank statistics are UNCHANGED by a uniform deflator by
    construction -- real==nominal there is guaranteed, not a defect, the exact reason
    VOL_ANN_EQUALS_SRRI_VOL was retired. drawdown_duration (count) and pct_*_months (bounded_unit,
    sign-of-return-derived) are excluded by statistical_type; *_zscore_cat/*_pctile_cat
    (rolling_stats.py) are cross-sectional standardized statistics -- a z-score or percentile
    rank is exactly invariant to scaling every peer's raw value by the same constant, regardless
    of the underlying metric's own statistical_type (return_ann_zscore_cat inherits return_ann's
    continuous_signed type via catalog_metrics.py's regime-suffix prefix match, so the suffix
    check must be independent of, not folded into, the type filter).

    FND-0174 (2026-10-01) added two more classes, both found because a 40-ISIN sample audit
    returned RC=1 on six BLOCK2 findings (BLOCK2 blocks unconditionally), and both measured
    rather than assumed:
      * any *_pctile_* rank statistic -- including *_pctile_self (rank within the fund's OWN
        history), which the cross-sectional check above missed. All 98 interior real==nominal
        pairs on the sample were EXACT rank ties (|diff| == 0.0): a rank is unchanged whenever
        deflation does not reorder it.
      * dispersion statistics (vol_*, *volatility*). A smooth deflator barely moves a standard
        deviation, so "real must visibly differ from nominal" has no power for them: an
        independent recomputation from raw NAV + CPI reproduced the stored REAL vol_ann to 1e-6
        on 12/12 flagged rows (e.g. real 0.135806 vs nominal 0.135337) -- deflation was applied.
    Deflation correctness stays covered by DEFLATION_ORDER and WINDOW_FISHER_IDENTITY (level-type
    return_ann), neither of which is touched here.
    """
    if metric.endswith("_zscore_cat") or "_pctile_" in metric:
        return False
    if metric.startswith("vol_") or "volatility" in metric:
        return False
    return get_metric_spec(metric).statistical_type in ("continuous_positive", "continuous_signed")


def _run_reference_boundary_audit(run: "AuditRun", conn: "psycopg.Connection") -> None:
    """FND-0137 Section B, part 1 (Reference Boundary Audit): logs the absolute MIN/MAX date of
    every external macro source (series_inflation per geography, series_macro per
    indicator/geography) as an INFO Block 6 finding. Pure coverage reporting -- a source with a
    late start or an old MAX date is normal (macro data is published with a lag; ES CPI itself
    only starts 2000-01), not itself a defect. Run once per audit, at initialization, so the
    boundaries are visible in every report even when nothing downstream trips on them.
    """
    for geography, min_date, max_date in _df(conn, _sql(conn, _INFLATION_BOUNDARY_QUERY)).itertuples(index=False):
        run.findings.append({
            "block": "BLOCK6", "rule_id": "MACRO_SOURCE_BOUNDARY",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO",
            "group_key": f"series_inflation|{geography}", "value": None, "reference_value": None,
            "threshold": None, "distance": None,
            "evidence": f"coverage [{min_date}, {max_date}]",
            "root_cause_candidate": "Reference boundary, not a defect -- context for any "
                                     "leading/trailing-gap finding below",
        })
    for indicator, geography, min_date, max_date in _df(conn, _sql(conn, _MACRO_BOUNDARY_QUERY)).itertuples(index=False):
        run.findings.append({
            "block": "BLOCK6", "rule_id": "MACRO_SOURCE_BOUNDARY",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO",
            "group_key": f"series_macro|{indicator}|{geography}", "value": None,
            "reference_value": None, "threshold": None, "distance": None,
            "evidence": f"coverage [{min_date}, {max_date}]",
            "root_cause_candidate": "Reference boundary, not a defect -- context for any "
                                     "leading/trailing-gap finding below",
        })


def _run_orphaned_asset_check(
    run: "AuditRun", conn: "psycopg.Connection", isins: Sequence[str] | None = None,
) -> None:
    """FND-0137 Section B, part 2 (Orphaned Asset Identification): funds whose NAV lifespan
    extends beyond the ES CPI series' own coverage on either edge. INFO only, by design --
    Section A's WINDOW_FISHER_IDENTITY/WINDOW_DEFLATION_STRICT already verify, ROW BY ROW, that
    the degradation for these funds was applied CORRECTLY (deflate_nav()'s leading-gap bfill, or
    a merge_asof(backward) match against the latest known CPI point for a trailing date); this
    finding is coverage context, not a second correctness check on the same funds.

    Named CPI_COVERAGE_LEADING_GAP / CPI_COVERAGE_TRAILING_GAP to distinguish the two edges:
    leading (NAV starts before CPI coverage -- the FND-0114 population, 275/2953 live ISINs)
    needs the leading-gap bfill; trailing (NAV outlives the latest known CPI point, normal
    operational lag between NAV and CPI publication) needs no fill at all -- merge_asof(backward)
    naturally reuses the latest known value, which is correct behaviour, not a gap.
    """
    isin_filter, isin_params = _isin_filter(conn, isins, "n.ISIN")
    lifespan = _df(conn, _sql(conn, _NAV_LIFESPAN_QUERY, isin_filter=isin_filter), isin_params)
    if lifespan.empty:
        run.skipped.append("BLOCK6 CPI_COVERAGE_LEADING_GAP/CPI_COVERAGE_TRAILING_GAP: no NAV rows")
        return

    ipc_bounds = _df(conn, _sql(conn, _INFLATION_BOUNDARY_QUERY))
    es_bounds = ipc_bounds[ipc_bounds["geography"] == "ES"]
    if es_bounds.empty:
        run.skipped.append("BLOCK6 CPI_COVERAGE_LEADING_GAP/CPI_COVERAGE_TRAILING_GAP: no ES CPI coverage")
        return
    ipc_min, ipc_max = es_bounds.iloc[0][["min_date", "max_date"]]

    lifespan["min_date"] = pd.to_datetime(lifespan["min_date"])
    lifespan["max_date"] = pd.to_datetime(lifespan["max_date"])
    leading = lifespan[lifespan["min_date"] < pd.Timestamp(ipc_min)]
    trailing = lifespan[lifespan["max_date"] > pd.Timestamp(ipc_max)]

    if not leading.empty:
        run.findings.append({
            "block": "BLOCK6", "rule_id": "CPI_COVERAGE_LEADING_GAP",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO",
            "group_key": "CPI_COVERAGE_LEADING_GAP", "value": None, "reference_value": None,
            "threshold": None, "distance": float(len(leading)),
            "evidence": f"{len(leading)}/{len(lifespan)} ISINs' NAV history starts before ES CPI "
                        f"coverage ({ipc_min}) -- deflate_nav()'s leading-gap bfill applies; "
                        f"verify row-by-row correctness via WINDOW_FISHER_IDENTITY (Section A), "
                        f"not here",
            "root_cause_candidate": "Expected degradation for a fund older than ES CPI coverage, "
                                     "not itself a defect",
            "violating_isins": tuple(sorted(leading["isin"].tolist())),
        })
    if not trailing.empty:
        run.findings.append({
            "block": "BLOCK6", "rule_id": "CPI_COVERAGE_TRAILING_GAP",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO",
            "group_key": "CPI_COVERAGE_TRAILING_GAP", "value": None, "reference_value": None,
            "threshold": None, "distance": float(len(trailing)),
            "evidence": f"{len(trailing)}/{len(lifespan)} ISINs' NAV history extends past the "
                        f"latest known ES CPI point ({ipc_max}) -- normal NAV-vs-CPI publication "
                        f"lag; deflator for those recent dates is carried forward flat via "
                        f"merge_asof(backward), not a gap needing a fill",
            "root_cause_candidate": "Normal operational lag between NAV and macro-source "
                                     "publication, not a defect",
            "violating_isins": tuple(sorted(trailing["isin"].tolist())),
        })


def _run_beta_orphan_check(run: "AuditRun", long_df: pd.DataFrame) -> None:
    """Block 6 (P2, B2/FND-0124, 2026-09-28): flags beta_* rows whose batch_id is not internally
    consistent -- either the beta_* group itself spans more than one batch_id for the same
    (isin, horizon, metric_version), or it diverges from macro_r2's batch_id there.
    replace_beta_set() (proyecto2/src/writers/metrics_writer.py) writes every beta_*/macro_r2/
    energy_sensitivity_pct/hy_spread_sensitivity_pct row of one OLS step in a single atomic
    DELETE+INSERT stamped with one batch_id -- divergence is structurally impossible for a
    healthy write, so any hit here is a genuine defect (an interrupted/partial write, or rows
    surviving from a superseded OLS run).

    Deliberately NOT compared against "the fund's latest P2 run batch": control.fund_metric_state
    carries no batch_id, and OLS is quarter-cached (last_ols_quarter) rather than recomputed every
    run, so that comparison would false-positive on every fund whose OLS legitimately didn't
    recompute this cycle.
    """
    if "batch_id" not in long_df.columns:
        run.skipped.append("BLOCK6 BETA_ORPHAN_BATCH: batch_id not present in the queried frame")
        return

    join_keys = ["isin", "horizon", "metric_version"]
    beta = long_df[long_df["metric"].str.startswith("beta_")]
    if beta.empty:
        run.skipped.append("BLOCK6 BETA_ORPHAN_BATCH: no beta_* rows in this population")
        return

    n_internal = int((beta.groupby(join_keys)["batch_id"].nunique(dropna=False) > 1).sum())

    macro_r2 = long_df[long_df["metric"] == "macro_r2"][join_keys + ["batch_id"]].rename(
        columns={"batch_id": "macro_r2_batch_id"})
    beta_batch = beta.groupby(join_keys)["batch_id"].first().reset_index()
    cross = beta_batch.merge(macro_r2, on=join_keys, how="inner")
    n_cross = int((cross["batch_id"] != cross["macro_r2_batch_id"]).sum())

    n_total = n_internal + n_cross
    if n_total:
        run.findings.append({
            "block": "BLOCK6", "rule_id": "BETA_ORPHAN_BATCH",
            "rule_class": "HARD_INVARIANT", "severity": "ALARM", "group_key": "BETA_ORPHAN_BATCH",
            "value": None, "reference_value": None, "threshold": None,
            "distance": float(n_total),
            "evidence": f"{n_internal} (isin,horizon,metric_version) groups have beta_* rows spanning "
                        f"more than one batch_id; {n_cross} groups have a beta_* batch_id diverging from "
                        f"macro_r2's -- replace_beta_set() writes both atomically, so this is structurally "
                        f"impossible for a healthy write",
            "root_cause_candidate": "Interrupted/partial OLS write, or beta_* rows surviving from a "
                                     "superseded run",
        })


def _emit_snapshot_held_out_finding(run: "AuditRun") -> None:
    """B3 (FND-0125, 2026-09-28): one aggregate INFO finding covering every ISIN the snapshot
    staleness gate held out across all (metric, window) combos -- not one finding per combo,
    which would be 25 near-duplicate lines for the same underlying stale funds. No-op when
    run.snapshot_held_out is empty (the common case)."""
    if not run.snapshot_held_out:
        return
    run.findings.append({
        "block": "BLOCK1", "rule_id": "SNAPSHOT_HELD_OUT",
        "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO", "group_key": "SNAPSHOT_HELD_OUT",
        "value": None, "reference_value": None, "threshold": SNAPSHOT_MAX_SPREAD_DAYS,
        "distance": float(len(run.snapshot_held_out)),
        "evidence": f"{len(run.snapshot_held_out)} distinct ISINs held out of cross-sectional "
                    f"timeseries blocks (beyond {SNAPSHOT_MAX_SPREAD_DAYS} calendar days of their "
                    f"slice's max date); max observed spread {run.snapshot_max_spread_days:.0f} days",
        "root_cause_candidate": "Settlement lag or stale NAV for these funds relative to the universe",
    })


def _alerts_are_stale(max_alert_date, max_calc_date) -> bool:
    """B5 (FND-0127, 2026-09-28): the alert engine has not run since the last P2 recompute for
    this population when its latest detected_at predates the latest fund_metrics.calculation_date.
    Pure comparison, factored out of _run_alert_reconciliation() for testability without a DB.
    `max_alert_date` is a pandas Timestamp (or NaT); `max_calc_date` a datetime.date (or None).
    """
    if max_calc_date is None or pd.isna(max_alert_date):
        return False
    return max_alert_date.date() < max_calc_date


def _run_alert_reconciliation(
    run: "AuditRun", conn: "psycopg.Connection", isins: Sequence[str] | None = None,
) -> None:
    """Block 4 <-> fund_metric_alerts reconciliation (function #12, B5/FND-0127, 2026-09-28).
    reconcile_with_alerts() (shared/statistical_audit/reconcile.py, tested, previously unwired)
    filters run.findings down to the subset NOT already surfaced by the production alert engine
    -- the skill's own framing: "report only outliers that the operational alarm engine did not
    already surface -- these are the incremental audit findings." Call this LAST, after every
    other step that can add a BLOCK4 finding (including B4's TIMESERIES-population outliers).

    Freshness gate (_alerts_are_stale): if the alert engine has not run since the last P2
    recompute for this population, reconciling would compare this run's fresh findings against a
    stale alert snapshot and could silently drop a real incremental finding -- skip reconciliation
    entirely rather than mislead.
    """
    alerts_filter, alerts_params = _isin_filter(conn, isins, "a.isin")
    alerts_query = _ALERTS_QUERY.replace("{isin_filter}", alerts_filter)
    alerts_df = _restore_case(build_population(conn, alerts_query, alerts_params), ("isin", "metric", "detected_at"))
    if alerts_df.empty:
        run.skipped.append("BLOCK4 ALERT_RECONCILIATION: no fund_metric_alerts rows for this population")
        return

    calc_filter, calc_params = _isin_filter(conn, isins, "m.ISIN")
    calc_query = _MAX_CALC_DATE_QUERY.replace("{isin_filter}", calc_filter)
    max_calc_date = conn.execute(calc_query, calc_params).fetchone()[0]
    max_alert_date = pd.to_datetime(alerts_df["detected_at"]).max()

    if _alerts_are_stale(max_alert_date, max_calc_date):
        run.skipped.append(
            f"BLOCK4 ALERT_RECONCILIATION: alerts stale (latest detected_at {max_alert_date.date()} < "
            f"latest fund_metrics.calculation_date {max_calc_date}) -- alert engine has not run since "
            f"the last P2 recompute"
        )
        return

    before = len(run.findings)
    run.findings = reconcile_with_alerts(run.findings, alerts_df)
    n_suppressed = before - len(run.findings)
    if n_suppressed:
        run.skipped.append(
            f"BLOCK4 ALERT_RECONCILIATION: {n_suppressed} findings already surfaced by the production "
            f"alert engine, suppressed as non-incremental"
        )


def run_p2_audit(conn: "psycopg.Connection", isins: Sequence[str] | None = None) -> "AuditRun":
    metrics_filter, metrics_params = _isin_filter(conn, isins, "fm.ISIN")
    metrics_query = _P2_METRICS_QUERY.replace("{isin_filter}", metrics_filter)
    long_df = _restore_case(build_population(conn, metrics_query, metrics_params), _P2_METRICS_COLS)

    run = AuditRun(domain="p2_metrics")
    run.universe_size = int(long_df["isin"].nunique())

    # FND-0137 Section B: run at initialization so the boundaries are visible in every report,
    # independent of whether anything below trips on them.
    _run_reference_boundary_audit(run, conn)
    _run_orphaned_asset_check(run, conn, isins=isins)

    for keys, group in long_df.groupby(list(_P2_GROUP_KEYS)):
        metric, horizon, real_flag, metric_version = keys
        group_key = f"{metric}|{horizon}|{real_flag}|{metric_version}"
        spec = get_metric_spec(metric)
        series = group["value"]

        _block1(run, group_key, series, len(group), numeric=True, supports_moments=spec.supports_moments,
                high_kurtosis_expected=spec.high_kurtosis_expected)

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
    deflation_frame = _pivot_by_real_flag(long_df, "return_ann")
    if deflation_frame.empty:
        run.skipped.append("BLOCK5 DEFLATION_ORDER: no overlapping nominal/real return_ann rows")
    else:
        deflation_frame = deflation_frame.assign(ipc_yoy=ipc_yoy if ipc_yoy is not None else float("nan"))

    # A3 (FND-0121, 2026-09-28): REAL_EQUALS_NOMINAL generalized from return_ann-only to every
    # metric with both real_flag values present (P2 skill states the rule generically, "same
    # metric"). DEFLATION_ORDER stays return_ann-only above -- ordering isn't a meaningful check
    # for e.g. vol_ann/sharpe. See _deflation_meaningful() for the false-positive guard this needs.
    n_real_nominal_checked = 0
    for metric in sorted(long_df["metric"].unique()):
        if not _deflation_meaningful(metric):
            continue
        pivot = deflation_frame if metric == "return_ann" else _pivot_by_real_flag(long_df, metric)
        if pivot.empty:
            continue
        if "ipc_yoy" not in pivot.columns:
            pivot = pivot.assign(ipc_yoy=ipc_yoy if ipc_yoy is not None else float("nan"))
        n_real_nominal_checked += 1
        rule = replace(P2_PAIRS["REAL_EQUALS_NOMINAL"], rule_id=f"REAL_EQUALS_NOMINAL_{metric}")
        result = compare_pairs(pivot, f"{metric}_nominal", f"{metric}_real", rule)
        if result.triggered:
            run.findings.append({
                "block": "BLOCK2", "rule_id": rule.rule_id, "rule_class": "STATISTICAL_ANOMALY",
                "severity": rule.severity, "group_key": rule.rule_id,
                "value": None, "reference_value": None, "threshold": rule.tolerance,
                "distance": float(result.n_matches),
                "evidence": (
                    f"{result.n_matches}/{result.n_eligible} eligible rows within tolerance "
                    f"(ipc_yoy={ipc_yoy:.4f})" if ipc_yoy is not None else
                    f"{result.n_matches}/{result.n_eligible} eligible rows within tolerance"
                ),
                "root_cause_candidate": rule.diagnosis,
            })
    if n_real_nominal_checked == 0:
        run.skipped.append("BLOCK2 REAL_EQUALS_NOMINAL: no metric has overlapping nominal/real rows")

    for metric in _SCALAR_TIMESERIES_METRICS:
        for window in _SCALAR_TIMESERIES_WINDOWS:
            ts_raw = _fetch_latest_timeseries_snapshot(conn, metric, window, isins=isins)
            if ts_raw.empty:
                run.skipped.append(f"BLOCK2 SCALAR_EQUALS_TIMESERIES {metric}/{window}: no timeseries rows")
                continue
            # B3 (FND-0125, 2026-09-28): snapshot staleness/tolerance gate -- both skills mandate
            # holding funds beyond SNAPSHOT_MAX_SPREAD_DAYS of their slice's own max date out of
            # cross-sectional blocks, never silently dropping them. build_snapshot()'s
            # per-(slice_key, entity) latest-row reduction is a no-op here (ts_raw is already one
            # row per (isin, real_flag) from the SQL's own correlated MAX(date)); this call's real
            # job is the tolerance split + date-spread measurement.
            snap = build_snapshot(
                ts_raw, entity_key="isin", slice_keys=["real_flag"], date_column="date",
                tolerance_days=SNAPSHOT_MAX_SPREAD_DAYS,
            )
            run.snapshot_held_out.update(snap.held_out["isin"].tolist())
            run.snapshot_max_spread_days = max(run.snapshot_max_spread_days, snap.date_spread_days)
            ts_snapshot = snap.eligible
            if ts_snapshot.empty:
                run.skipped.append(
                    f"BLOCK2 SCALAR_EQUALS_TIMESERIES {metric}/{window}: all rows held out by the "
                    f"snapshot staleness gate"
                )
                continue

            # B4 (FND-0126, 2026-09-28): profile fund_metric_timeseries' own distribution as a
            # population distinct from fund_metrics' since_inception scalar -- Blocks 1/4/7 on the
            # staleness-filtered snapshot, split by real_flag. Reuses ts_snapshot as fetched for
            # SCALAR_EQUALS_TIMESERIES just below -- zero extra DB round-trips.
            ts_spec = get_metric_spec(metric)
            for real_flag_value, rf_group in ts_snapshot.groupby("real_flag"):
                ts_group_key = f"{metric}|{window}|{real_flag_value}"
                ts_series = rf_group["ts_value"]
                _block1(run, ts_group_key, ts_series, len(rf_group), numeric=True,
                        supports_moments=ts_spec.supports_moments, population="TIMESERIES")
                if ts_spec.statistical_type in ("continuous_positive", "continuous_signed", "bounded_unit"):
                    ts_indexed = ts_series.set_axis(rf_group["isin"])
                    for method in ("IQR", "MAD_Z"):
                        _block4(run, ts_group_key, ts_indexed, method)
                ts_bound = get_metric_bound(metric)
                if ts_bound is not None:
                    _block7(run, ts_group_key, rf_group, "ts_value", ts_bound)

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

    _emit_snapshot_held_out_finding(run)
    _run_timeseries_integrity(run, conn, isins=isins)
    _run_beta_orphan_check(run, long_df)

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

    # FND-0114 (2026-09-29): separate frame -- WINDOW_NOMINAL_IDENTITY/WINDOW_DEFLATION_STRICT/
    # WINDOW_FISHER_IDENTITY need per-window rows (isin, date, window_label), which
    # wide_for_invariants (indexed by isin/horizon/real_flag/metric_version) cannot carry. Empty
    # is a legitimate "nothing eligible yet" result, not an error -- _run_invariants already
    # reports a per-rule skip when none of the frames it's given carry a rule's columns, so no
    # separate empty-check is needed here.
    window_deflation_frame = _build_window_deflation_frame(conn, isins=isins)
    _run_invariants(
        run, P2_INVARIANTS, [wide_for_invariants, window_deflation_frame], block="BLOCK5",
    )

    _run_alert_reconciliation(run, conn, isins=isins)

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
        # B3 (FND-0125, 2026-09-28): snapshot staleness gate -- populated only by run_p2_audit
        # (fund_metric_timeseries has no cost-domain equivalent); always present so _print_report
        # stays domain-agnostic (prints the line only when non-empty).
        self.snapshot_held_out: set[str] = set()
        self.snapshot_max_spread_days: float = 0.0


def _block1(
    run: AuditRun, group_key: str, series: pd.Series, n_expected: int,
    numeric: bool = True, supports_moments: bool = True, population: str = "GLOBAL",
    high_kurtosis_expected: bool = False,
) -> None:
    stats: dict = profile_coverage(series, n_expected=n_expected)
    if numeric:
        stats.update(profile_location(series))
        if supports_moments:
            stats.update(profile_moments(series))
    stats.update(profile_mass_points(series))
    run.statistics.append((population, group_key, stats, stats.get("n_valid")))

    # PEER findings are additional context for a GLOBAL group already profiled above; only
    # escalate to a finding once, at GLOBAL, to avoid duplicate noise for every peer segment of
    # the same underlying group. TIMESERIES (B4/FND-0126, 2026-09-28) is NOT that -- it is a
    # genuinely distinct population (the latest fund_metric_timeseries snapshot, not the
    # fund_metrics since_inception scalar) that can carry its own independent defects, so it
    # must escalate findings on its own account, not be silently suppressed like PEER segments.
    if population.startswith("PEER:"):
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
        # A4 (FND-0120, 2026-09-28): regime-family groups (few obs per regime) are expected to
        # have high kurtosis by design (P2 skill Block 3 carve-out) -- route them to an INFO
        # bucket instead of the WARN a non-regime group's pathological shape gets.
        if high_kurtosis_expected:
            run.findings.append({
                "block": "BLOCK3", "rule_id": "PATHOLOGICAL_SHAPE_EXPECTED",
                "rule_class": "STATISTICAL_ANOMALY", "severity": "INFO", "group_key": group_key,
                "value": stats["skew"], "reference_value": 3.0, "threshold": 3.0,
                "distance": None,
                "evidence": f"skew={stats['skew']:.2f} kurtosis={stats['kurtosis']:.2f}",
                "root_cause_candidate": "Expected by design -- few observations per regime, not a defect",
            })
        else:
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
        # FND-0114 (2026-09-29): when the violating rows carry an isin column (the per-window
        # deflation frame does; the since_inception wide frame does too), name the affected ISINs
        # in the evidence and keep the raw list on the finding (NOT one of _FINDING_COLUMNS, so
        # emit_findings() never persists it) -- _print_report's remediation block reads it back.
        evidence = f"{result.n_violations}/{result.n_applicable} applicable rows violate"
        violating_isins: tuple[str, ...] = ()
        if "isin" in result.violations.columns:
            violating_isins = tuple(sorted(result.violations["isin"].dropna().unique().tolist()))
            if violating_isins:
                evidence += f" across {len(violating_isins)} ISINs"
        run.findings.append({
            "block": block, "rule_id": rule.rule_id, "rule_class": rule.bound_type,
            "severity": "ALARM" if rule.bound_type == "HARD_INVARIANT" else "WARN",
            "group_key": rule.rule_id, "value": None, "reference_value": None, "threshold": None,
            "distance": float(result.n_violations),
            "evidence": evidence,
            "root_cause_candidate": rule.description,
            "violating_isins": violating_isins,
        })


# ============================================================
# C2 (FND-0131, 2026-09-28): --list-catalog -- rules generated inline in this file rather than
# from one of the declarative catalog dicts (COST_PAIRS, P2_PAIRS, COST_INVARIANTS, P2_INVARIANTS,
# METRIC_BOUNDS, COST_COLUMNS, COST_GROUP_CHECKS -- those are read directly by _print_catalog).
# Kept as one flat tuple, not a class hierarchy: this is a documentation registry, not executed
# logic, so a dict-of-fields per row is the simplest thing that can print itself and be diffed
# against a skill .md file by check_audit_skill_sync.py (C3).
# ============================================================

_PROCEDURAL_RULES: tuple[dict, ...] = (
    {"rule_id": "DOMINANT_VALUE_CONCENTRATION", "block": "BLOCK1", "domain": "both",
     "description": "mode% > 40% (dominant_threshold) -- template/hardcoded value or upstream bleed"},
    {"rule_id": "PATHOLOGICAL_SHAPE", "block": "BLOCK3", "domain": "both",
     "description": "|skew| > 3 or kurtosis > 10 on a non-regime, non-peer group"},
    {"rule_id": "PATHOLOGICAL_SHAPE_EXPECTED", "block": "BLOCK3", "domain": "p2",
     "description": "same threshold as PATHOLOGICAL_SHAPE but on a regime-suffixed metric "
                     "(MetricSpec.high_kurtosis_expected) -- INFO, not WARN"},
    {"rule_id": "OUTLIER_IQR / OUTLIER_MAD_Z", "block": "BLOCK4", "domain": "both",
     "description": "IQR fence (1.5x) / MAD robust-z (WARN >=3.5, ALARM >=5); skipped when "
                     "zero_pct >= 0.70 or n < 8"},
    {"rule_id": "BOUND_<metric-or-column>", "block": "BLOCK7", "domain": "both",
     "description": "value outside [min, max] from METRIC_BOUNDS (p2) or "
                     "CostColumnSpec.hard_bound/plausibility_bound (costs, both layers checked "
                     "independently); crisis_-prefixed horizons carved out (P2 only)"},
    {"rule_id": "SCHEDULE_EUR_PCT_COHERENCE", "block": "BLOCK6", "domain": "costs",
     "description": "Total_Costs_EUR/100 vs Total_Costs_Pct beyond KID_ROUNDING_TOLERANCE_PP"},
    {"rule_id": "SCHEDULE_EUR_MISPARSE", "block": "BLOCK6", "domain": "costs",
     "description": f"Total_Costs_EUR < {SCHEDULE_EUR_MISPARSE_FLOOR} at Horizon_Years >= 1"},
    {"rule_id": "SCHEDULE_MULTIPLE_RHP_ROWS", "block": "BLOCK6", "domain": "costs",
     "description": ">1 Is_RHP=1 row per ISIN (no UNIQUE constraint enforces this)"},
    {"rule_id": "SCHEDULE_RHP_ACI_MISMATCH", "block": "BLOCK6", "domain": "costs",
     "description": "Is_RHP=1 row's Annual_Impact_Pct vs fund_master.ACI_RHP beyond "
                     "KID_ROUNDING_TOLERANCE_PP"},
    {"rule_id": "SCHEDULE_ACI_RHP_ORPHAN", "block": "BLOCK6", "domain": "costs",
     "description": "fund_master.ACI_RHP set with no Is_RHP=1 schedule row"},
    {"rule_id": "BETA_ORPHAN_BATCH", "block": "BLOCK6", "domain": "p2",
     "description": "beta_* rows not internally consistent on batch_id, or diverging from "
                     "macro_r2's batch_id (same OLS-step atomic write)"},
    {"rule_id": "TIMESERIES_DUPLICATE", "block": "BLOCK5", "domain": "p2",
     "description": "n_rows > n_distinct_dates for an (isin, real_flag) series -- the PK should "
                     "make this impossible"},
    {"rule_id": "TIMESERIES_GAP", "block": "BLOCK4", "domain": "p2",
     "description": "missing months within a series' own [min,max] history"},
    {"rule_id": "TIMESERIES_NULL_PROVENANCE", "block": "BLOCK6", "domain": "p2",
     "description": "batch_id IS NULL -- row predates the v26 audit columns"},
    {"rule_id": "TIMESERIES_BELOW_MIN_OBS", "block": "BLOCK6", "domain": "p2",
     "description": f"non-NULL value with source_rows < MIN_NAV_ROWS ({MIN_NAV_ROWS})"},
    {"rule_id": "SNAPSHOT_HELD_OUT", "block": "BLOCK1", "domain": "p2",
     "description": f"ISIN's latest date beyond {SNAPSHOT_MAX_SPREAD_DAYS} calendar days of its "
                     f"slice's max date -- held out of cross-sectional timeseries blocks"},
    {"rule_id": "COVERAGE_CLIFF", "block": "BLOCK1", "domain": "both",
     "description": f"n_valid drop > {COVERAGE_CLIFF_PCT:.0%} vs the prior persisted run "
                     f"(--compare-to only)"},
    {"rule_id": "REAL_EQUALS_NOMINAL_<metric>", "block": "BLOCK2", "domain": "p2",
     "description": "generalized per-metric from the REAL_EQUALS_NOMINAL PairRule -- one per "
                     "metric with both real_flag values present and _deflation_meaningful()"},
    {"rule_id": "SCALAR_EQUALS_TIMESERIES_<metric>_<window>", "block": "BLOCK2", "domain": "p2",
     "description": "generalized per-(metric,window) from the SCALAR_EQUALS_TIMESERIES PairRule"},
    {"rule_id": "MACRO_SOURCE_BOUNDARY", "block": "BLOCK6", "domain": "p2",
     "description": "INFO: MIN/MAX date of every series_inflation geography and series_macro "
                     "indicator/geography -- coverage context, not a defect (FND-0137 §B)"},
    {"rule_id": "CPI_COVERAGE_LEADING_GAP", "block": "BLOCK6", "domain": "p2",
     "description": "INFO: ISIN's NAV history starts before ES CPI coverage begins -- expected "
                     "degradation (deflate_nav()'s leading-gap bfill), verified row-by-row by "
                     "WINDOW_FISHER_IDENTITY, not a second correctness check here (FND-0137 §B)"},
    {"rule_id": "CPI_COVERAGE_TRAILING_GAP", "block": "BLOCK6", "domain": "p2",
     "description": "INFO: ISIN's NAV history extends past the latest known ES CPI point -- "
                     "normal NAV-vs-CPI publication lag, not a gap needing a fill (FND-0137 §B)"},
)


def _print_catalog(domain: str | None) -> None:
    show_costs = domain in (None, "costs")
    show_p2 = domain in (None, "p2")

    if show_costs:
        print("=== COST domain ===")
        print("-- Block 2 (cross-component pairs) --")
        for rule in COST_PAIRS.values():
            print(f"  {rule.rule_id:45s} tol={rule.tolerance}")
        print("-- Block 5 (invariants) --")
        for rule in COST_INVARIANTS:
            print(f"  {rule.rule_id:32s} [{rule.bound_type:16s}] {rule.expression}")
        print("-- Block 5/6 (group-constancy, function #17) --")
        for rule in COST_GROUP_CHECKS:
            print(f"  {rule.rule_id:45s} group={rule.group_column} value={rule.value_column}")
        print("-- Block 7 (bounds -- dual hard/plausibility layer) --")
        for column, spec in COST_COLUMNS.items():
            for label, bound in (("hard", spec.hard_bound), ("plausibility", spec.plausibility_bound)):
                if bound is not None:
                    print(f"  {column:28s} [{label:12s}] [{bound.min_value}, {bound.max_value}] "
                          f"({bound.bound_type})")
        print()

    if show_p2:
        print("=== P2 domain ===")
        print("-- Block 2 (cross-value pairs) --")
        for rule in P2_PAIRS.values():
            print(f"  {rule.rule_id:32s} tol={rule.tolerance}")
        print("-- Block 5 (invariants) --")
        for rule in P2_INVARIANTS:
            when = f" when {rule.when}" if rule.when else ""
            print(f"  {rule.rule_id:32s} [{rule.bound_type:16s}] {rule.expression}{when}")
        print("-- Block 7 (bounds) --")
        for metric, bound in METRIC_BOUNDS.items():
            print(f"  {metric:28s} [{bound.min_value}, {bound.max_value}] ({bound.bound_type})")
        print(f"  {'<bounded_unit metrics>':28s} [0.0, 1.0] (PLAUSIBILITY) -- generic rule, one "
              f"per bounded_unit MetricSpec not already listed above")
        print()

    print("=== Procedural rules (generated inline, not from a catalog dict) ===")
    for rule in _PROCEDURAL_RULES:
        if domain is not None and rule["domain"] not in (domain, "both"):
            continue
        print(f"  {rule['rule_id']:45s} {rule['block']:7s} [{rule['domain']:5s}] {rule['description']}")
    print()

    print("=== Retired/corrected rules (RETIRED_RULES) ===")
    for rule_id, entry in RETIRED_RULES.items():
        if entry.replaced_by is None:
            status = "removed, no replacement"
        elif entry.replaced_by == rule_id:
            status = "active under the same id -- definition corrected"
        else:
            status = f"replaced by {entry.replaced_by}"
        print(f"  {rule_id:32s} [{entry.date}] {status}")
        print(f"    {entry.reason}")


# ============================================================
# Persistence, reporting, CLI
# ============================================================

def _persist(conn: "psycopg.Connection", run: "AuditRun", run_id: str) -> tuple[int, int]:
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
    # B3 (FND-0125, 2026-09-28): printed only when non-empty -- always present on AuditRun but
    # only run_p2_audit ever populates it (fund_metric_timeseries has no cost-domain equivalent).
    if run.snapshot_held_out:
        print(f"Snapshot: max date spread {run.snapshot_max_spread_days:.0f}d, "
              f"{len(run.snapshot_held_out)} ISINs held out (tolerance {SNAPSHOT_MAX_SPREAD_DAYS}d)")
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
    print()

    # D1 (FND-0134, 2026-09-28): this module produces the data both skills' Section 5 Output
    # Format is built from, not that section's narrative synthesis itself -- said once, here,
    # rather than only in the module docstring, so it is visible on every run.
    print("Note: narrative synthesis (corrections applied, residual inventory, ranked action "
          "items -- skill Section 5) is the calling skill session's job, built from the findings "
          "above. This module is read-only/detection-only by design (P#2, R-2).")

    # D2 (FND-0135, 2026-09-28): redirected from Gemini's CST-03 (a write path into
    # fund_cost_corrections from this runner) -- rejected, would violate the read-only boundary
    # above and duplicate persistence.preserve_and_write (function #16), which remediation owns.
    # Instead: name the actual remediation command for this run's own per-fund findings.
    if run.domain == "cost_attributes":
        isins_with_findings = sorted({f["isin"] for f in run.findings if f.get("isin")})
        if isins_with_findings:
            isin_csv = ",".join(isins_with_findings)
            print(f"Remediation: run_block.py --nature-first --master-db --recompute-costs "
                  f"--list-isin {isin_csv}")
        else:
            print("Remediation: no per-fund finding carries an ISIN this run (only "
                  "population-level findings) -- investigate the root cause before targeting a "
                  "--recompute-costs batch.")

    # FND-0114 (2026-09-29): same idea as the cost_attributes block above, for the two deflation
    # invariants (WINDOW_DEFLATION_STRICT, WINDOW_FISHER_IDENTITY) -- point straight at the
    # affected ISINs rather than leaving the reader to re-derive them from the count.
    if run.domain == "p2_metrics":
        _DEFLATION_RULES = ("WINDOW_DEFLATION_STRICT", "WINDOW_FISHER_IDENTITY")
        defl_isins = sorted({
            isin for f in run.findings if f["rule_id"] in _DEFLATION_RULES
            for isin in f.get("violating_isins", ())
        })
        if defl_isins:
            isin_csv = ",".join(defl_isins)
            print(f"\nRemediation (FND-0114 class, {len(defl_isins)} ISINs): "
                  f"run_pipeline.py --isin {isin_csv} --force")
            print("Certify with: --state-snapshot snap.json --isin <ISINs> (before) -> "
                  "run_pipeline.py --force -> --verify-recompute snap.json --isin <ISINs> "
                  "(every input_hash must change) -> re-run this audit on the same ISINs and "
                  "confirm 0 WINDOW_DEFLATION_STRICT/WINDOW_FISHER_IDENTITY violations.")


def _has_blocking_findings(run: "AuditRun") -> bool:
    for f in run.findings:
        if f["rule_class"] == "HARD_INVARIANT":
            return True
        if f["block"] == "BLOCK2":
            return True
        if f["block"] == "BLOCK4" and f["severity"] == "ALARM":
            return True
    return False


def _compute_drift(conn: "psycopg.Connection", run: "AuditRun", compare_to: str):
    """Loads the prior run's persisted statistics and compares them against the current
    run's in-memory statistics. Returns None when no prior audit_statistic rows exist for
    compare_to/domain -- computed once and shared by _apply_coverage_cliff_findings (must run
    before _print_report so the finding is counted) and _print_drift_report (prints after)."""
    previous = load_run_statistics(conn, compare_to, run.domain)
    if previous.empty:
        return None
    current = statistics_to_frame(run.statistics)
    return compare_runs(previous, current)


def _apply_coverage_cliff_findings(run: "AuditRun", result) -> None:
    """A4 (FND-0122, 2026-09-28): both skills mandate flagging a coverage (n_valid) drop of more
    than COVERAGE_CLIFF_PCT vs. the prior persisted run as an automatic Block 1 finding -- not
    just a printed drift-report line, which --compare-to already gave for free. GLOBAL-only, same
    as every other _block1 escalation (PEER segments are context for an already-flagged GLOBAL
    group, never flagged a second time)."""
    n_valid_deltas = result.deltas[
        (result.deltas["stat_name"] == "n_valid") & (result.deltas["population"] == "GLOBAL")
    ]
    for _, row in n_valid_deltas.iterrows():
        pct_change = row["pct_change"]
        if pd.isna(pct_change) or pct_change > -COVERAGE_CLIFF_PCT:
            continue
        run.findings.append({
            "block": "BLOCK1", "rule_id": "COVERAGE_CLIFF",
            "rule_class": "STATISTICAL_ANOMALY", "severity": "WARN", "group_key": row["group_key"],
            "value": row["current_value"], "reference_value": row["previous_value"],
            "threshold": -COVERAGE_CLIFF_PCT, "distance": float(pct_change),
            "evidence": f"n_valid {row['previous_value']:.0f} -> {row['current_value']:.0f} ({pct_change:+.1%})",
            "root_cause_candidate": "Upstream NAV loss or calc regression -- coverage dropped for this group",
        })


def _print_drift_report(result, compare_to: str, top_n: int = 20) -> None:
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


def _print_recompute_verification(result: "RecomputeCheckResult") -> int:
    print(f"=== Recompute verification: {result.n_checked} (isin, metric_version) rows checked ===")
    if result.unchanged.empty and result.missing_after.empty:
        print("OK: every row's input_hash/calculated_at changed since the snapshot.")
        return 0
    if not result.unchanged.empty:
        print(f"! {len(result.unchanged)} rows UNCHANGED (idempotency cache bypassed processing -- "
              f"the 'after' numbers are stale, per Method Control #3):")
        for _, row in result.unchanged.iterrows():
            print(f"  {row['isin']} / {row['metric_version']}: "
                  f"input_hash={row['input_hash']} calculated_at={row['calculated_at']}")
    if not result.missing_after.empty:
        print(f"! {len(result.missing_after)} rows MISSING from the current state "
              f"(dropped from fund_metric_state since the snapshot):")
        for _, row in result.missing_after.iterrows():
            print(f"  {row['isin']} / {row['metric_version']}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domain", choices=["costs", "p2"], default=None)
    parser.add_argument("--mode", choices=["report", "check"], default="report")
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--compare-to", default=None, help="Prior run_id to diff against (function #13)")
    parser.add_argument(
        "--isin", default=None,
        help="Comma-separated ISIN list to scope the audit to (e.g. the 40-ISIN validation sample). "
             "Omit for the full In_Current_Universe population.",
    )
    parser.add_argument(
        "--state-snapshot", metavar="PATH", default=None,
        help="Capture control.fund_metric_state for --isin to PATH (JSON) and exit -- run this "
             "before applying a fix. P2 only (function #14, Method Control #3).",
    )
    parser.add_argument(
        "--verify-recompute", metavar="PATH", default=None,
        help="Compare current control.fund_metric_state for --isin against a prior "
             "--state-snapshot capture at PATH; exit 1 if any (isin, metric_version) row's "
             "input_hash/calculated_at is unchanged. P2 only (function #14, Method Control #3).",
    )
    parser.add_argument(
        "--list-catalog", action="store_true",
        help="Print every active rule (from the declarative catalogs and the procedural rules "
             "generated inline in this file) plus RETIRED_RULES, then exit. No DB connection. "
             "Combine with --domain to filter to one domain.",
    )
    args = parser.parse_args()

    isins = [i.strip() for i in args.isin.split(",") if i.strip()] if args.isin else None

    # C2 (FND-0131, 2026-09-28): standalone, no DB connection, independent of --isin/--mode.
    if args.list_catalog:
        _print_catalog(args.domain)
        return 0

    # B6 (FND-0128, 2026-09-28): --state-snapshot/--verify-recompute are a standalone P2-only
    # utility, independent of --domain/--mode -- they read/write control.fund_metric_state
    # directly and never run the audit itself.
    if args.state_snapshot or args.verify_recompute:
        if not isins:
            parser.error("--state-snapshot/--verify-recompute require --isin")
        conn = get_connection()
        try:
            current = capture_state(conn, isins)
        finally:
            conn.close()
        if args.state_snapshot:
            with open(args.state_snapshot, "w", encoding="utf-8") as f:
                json.dump(current.to_dict(orient="records"), f, indent=2)
            print(f"Captured state for {len(current)} (isin, metric_version) rows -> {args.state_snapshot}")
            return 0
        with open(args.verify_recompute, "r", encoding="utf-8") as f:
            before = pd.DataFrame(json.load(f))
        result = assert_recompute_happened(before, current)
        return _print_recompute_verification(result)

    if not args.domain:
        parser.error("--domain is required (unless --state-snapshot/--verify-recompute is given)")

    # A --run-id the caller typed explicitly is trusted as-is; the auto-generated default gets a
    # _sample suffix under --isin so a sample-scoped run can never silently become a --compare-to
    # baseline for a full-universe run (feedback_validate_on_isin_samples).
    if args.run_id:
        run_id = args.run_id
    else:
        run_id = datetime.now(timezone.utc).strftime("audit_%Y%m%dT%H%M%SZ")
        if isins:
            run_id += "_sample"
    keep_open = args.persist or args.compare_to

    conn = get_connection()
    try:
        run = run_cost_audit(conn, isins=isins) if args.domain == "costs" else run_p2_audit(conn, isins=isins)
    finally:
        if not keep_open:
            conn.close()

    drift_result = _compute_drift(conn, run, args.compare_to) if args.compare_to else None
    if drift_result is not None:
        _apply_coverage_cliff_findings(run, drift_result)

    _print_report(run, run_id)

    if args.compare_to:
        print()
        if drift_result is None:
            print(f"! --compare-to {args.compare_to}: no audit_statistic rows found for domain={run.domain}")
        else:
            _print_drift_report(drift_result, args.compare_to)

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
