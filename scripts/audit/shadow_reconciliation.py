#!/usr/bin/env python
"""scripts/audit/shadow_reconciliation.py — FND-0137 Section D (Shadow Reconciliation).

An isolated, clean-room recomputation of the 5 curated since_inception nominal metrics
(return_ann, vol_ann, max_dd, sharpe, sortino — the same set catalog_metrics.py/_PEER_METRICS
already treats as the curated core) from raw fund_nav_monthly, compared cell-by-cell against the
stored fund_metrics values. It exists to catch an IMPLEMENTATION bug that a comparison against
itself (production re-running its own code) can never see.

Clean-room boundary (the directive's actual requirement, honored strictly): this module imports
NOTHING from proyecto2.src.calculations — no returns.py, no drawdown.py, no rolling_stats.py. The
5 formulas below are re-derived independently from the same public financial-math SPEC production
uses (read, not imported, from proyecto2/src/calculations/returns.py and drawdown.py while writing
this — geometric annualized return, sample-stdev volatility, peak-to-trough drawdown, Sharpe,
and the FND-0075-unified downside-deviation Sortino: population semi-variance against a
per-period MAR of risk_free_rate/12 over ALL periods, not just the negative subset, floored at
_MIN_DOWNSIDE_DEV_ANN to avoid a near-zero-denominator explosion). A shared bug in returns.py
would reproduce identically in both sides of a comparison that imports it — the whole point of a
clean room is that it cannot.

What IS shared, deliberately (a connection helper and a fallback numeric constant are not
calculation logic):
  - shared.db.get_connection() — a DB connection, not a formula.
  - shared.config.RISK_FREE_RATE_ANN — used only as shadow_resolve_rf_rate()'s FALLBACK, exactly
    the role it plays in production too (resolve_rf_rate()'s own `fallback` argument). The actual
    risk-free rate sharpe/sortino uses is date-resolved from a historical monthly series
    (series_macro/rate_deposit/EU, read here via raw SQL, not imported) — discovered live the
    first time this tool ran: comparing shadow sharpe/sortino against the flat constant produced a
    systematic, misleading ~80-comparison "divergence" that was entirely an input-choice
    difference (see doc/reglas/AUDITORIA_ESTADISTICA.md §2.9 — the scalar-vs-rolling write-path
    asymmetry was already known and documented BEFORE this tool existed), not a formula bug.
    shadow_resolve_rf_rate() independently re-derives the SAME date-alignment algorithm
    (resolve_rf_rate()'s spec, read not imported) so sharpe/sortino get a meaningful comparison
    too, not just return_ann/vol_ann/max_dd.

Section A (FND-0137) already exactly verifies the deflation step (WINDOW_FISHER_IDENTITY); this
module's marginal value is the 4 NON-deflation formulas (return_ann's nominal calc, vol_ann,
max_dd, sharpe/sortino's math beyond the deflator) that Section A does not touch.

Population-scale mandate (feedback_validate_on_isin_samples): this script refuses to run without
either --isin or --stratified-sample (capped at STRATIFIED_SAMPLE_MAX); --population is a
separate, explicit flag for an owner-run full sweep, never the default.

Recompute certification: this module does not add a new hash/idempotency checker. If a
divergence here ever motivates a fix, certify the fix's rollout the same way FND-0114's was
(run_statistical_audit.py --state-snapshot / --verify-recompute, shared/statistical_audit/
recompute_gate.py::assert_recompute_happened) — reusing that path rather than building a second.

Usage:
    python -X utf8 scripts/audit/shadow_reconciliation.py --isin LU1234567890,LU2345678901
    python -X utf8 scripts/audit/shadow_reconciliation.py --stratified-sample 40
    python -X utf8 scripts/audit/shadow_reconciliation.py --population   # owner-run only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd

from shared.config import RISK_FREE_RATE_ANN
# FND-0243: the EUR view is INPUT preparation (the same NAV production computed on), not metric logic, so it is shared on purpose;
# the formulas below stay independent. Identity while EUR_NAV_CONVERSION_ENABLED is off.
from shared.eur_nav import apply_eur_view
from shared.db import get_connection

# ============================================================
# Clean-room formulas — independently re-derived, not imported from proyecto2.src.calculations.
# ============================================================

_MIN_OBS = 12          # matches shared.config.MIN_NAV_ROWS's since_inception floor
_MIN_DOWNSIDE_DEV_ANN = 0.001   # same numerical-artifact floor as returns.py::downside_deviation_ann
_METRICS = ("return_ann", "vol_ann", "max_dd", "sharpe", "sortino")


def shadow_monthly_returns(nav: np.ndarray) -> np.ndarray:
    """Simple period-over-period returns: r[t] = nav[t]/nav[t-1] - 1."""
    nav = np.asarray(nav, dtype=float)
    if len(nav) < 2:
        return np.array([])
    return nav[1:] / nav[:-1] - 1.0


def shadow_return_ann(nav: np.ndarray, periods_per_year: int = 12) -> float:
    """Geometric annualized return over the full series: (nav[-1]/nav[0])^(periods_per_year/n) - 1,
    n = number of NAV observations (not the number of returns)."""
    nav = np.asarray(nav, dtype=float)
    if len(nav) < 2 or nav[0] <= 0:
        return float("nan")
    total = nav[-1] / nav[0]
    years = len(nav) / periods_per_year
    return float(total ** (1.0 / years) - 1.0)


def shadow_vol_ann(nav: np.ndarray, periods_per_year: int = 12) -> float:
    """Annualized volatility: sample stdev (ddof=1) of simple period returns, times sqrt(periods)."""
    rets = shadow_monthly_returns(nav)
    if len(rets) < 2:
        return float("nan")
    return float(np.std(rets, ddof=1) * np.sqrt(periods_per_year))


def shadow_max_dd(nav: np.ndarray) -> float:
    """Peak-to-trough drawdown (<= 0): min over t of nav[t]/running_max[t] - 1."""
    nav = np.asarray(nav, dtype=float)
    if len(nav) < 2:
        return float("nan")
    running_max = np.maximum.accumulate(nav)
    return float((nav / running_max - 1.0).min())


def shadow_downside_deviation_ann(rets: np.ndarray, mar_per_period: float,
                                   periods_per_year: int = 12) -> float:
    """Sortino's canonical downside deviation: population (not sample) semi-variance against a
    per-period Minimum Acceptable Return, over ALL periods (zero contribution when a period meets
    or beats the MAR), annualized. Floored at _MIN_DOWNSIDE_DEV_ANN -- a near-flat series produces
    a numerically tiny but nonzero variance whose ratio would otherwise explode to a meaningless
    magnitude."""
    rets = np.asarray(rets, dtype=float)
    if len(rets) < 2:
        return float("nan")
    downside = rets - mar_per_period
    downside_sq = np.where(downside < 0, downside ** 2, 0.0)
    var = float(downside_sq.mean())
    if var <= 0:
        return float("nan")
    dd_ann = float(np.sqrt(var) * np.sqrt(periods_per_year))
    return float("nan") if dd_ann < _MIN_DOWNSIDE_DEV_ANN else dd_ann


def shadow_sharpe(nav: np.ndarray, risk_free_rate_ann: float, periods_per_year: int = 12) -> float:
    ret = shadow_return_ann(nav, periods_per_year)
    vol = shadow_vol_ann(nav, periods_per_year)
    if np.isnan(ret) or np.isnan(vol) or vol == 0:
        return float("nan")
    return float((ret - risk_free_rate_ann) / vol)


def shadow_sortino(nav: np.ndarray, risk_free_rate_ann: float, periods_per_year: int = 12) -> float:
    ret = shadow_return_ann(nav, periods_per_year)
    if np.isnan(ret):
        return float("nan")
    rets = shadow_monthly_returns(nav)
    mar_per_period = risk_free_rate_ann / periods_per_year
    dd = shadow_downside_deviation_ann(rets, mar_per_period, periods_per_year)
    if np.isnan(dd) or dd == 0:
        return float("nan")
    return float((ret - risk_free_rate_ann) / dd)


def shadow_resolve_rf_rate(target_date, rf_series: pd.DataFrame | None, fallback: float) -> float:
    """Independently re-derived from the spec read (not imported) in
    proyecto2/src/calculations/rolling_stats.py::resolve_rf_rate() -- the ACTUAL risk-free rate
    the since_inception scalar sharpe/sortino uses is date-resolved from a historical monthly
    series (ECB deposit rate, series_macro/rate_deposit/EU), not the flat RISK_FREE_RATE_ANN
    constant (see doc/reglas/AUDITORIA_ESTADISTICA.md §2.9 and run_pipeline.py's own comment at
    _process_horizon -- this was already a known, documented asymmetry between the scalar and
    rolling write-paths before this tool existed). Comparing shadow sharpe/sortino against a flat
    4% would show a large, SYSTEMATIC, misleading "divergence" that is really just an input-choice
    difference, not a formula bug -- confirmed live the first time this tool ran (see FND-0137's
    own memory/commit for the numbers). Reindex + ffill + bfill point lookup, same algorithm
    Section C (FND-0137) already reviewed and allowlisted as DESIGN-safe for
    rolling_stats.py::resolve_rf_rate -- reused here independently, not imported."""
    if rf_series is None or rf_series.empty:
        return fallback
    s = rf_series.copy()
    s["date"] = pd.to_datetime(s["date"]) + pd.offsets.MonthEnd(0)
    s = s.sort_values("date").drop_duplicates("date", keep="last").set_index("date")["rate"]
    target = pd.Timestamp(target_date).normalize() + pd.offsets.MonthEnd(0)
    aligned = s.reindex(s.index.union([target])).sort_index().ffill().bfill()
    value = aligned.get(target)
    return fallback if value is None or pd.isna(value) else float(value)


def compute_shadow_metrics(nav: np.ndarray, risk_free_rate_ann: float = RISK_FREE_RATE_ANN,
                            periods_per_year: int = 12) -> dict[str, float]:
    """The 5 curated since_inception nominal metrics from a raw NAV array, independently."""
    return {
        "return_ann": shadow_return_ann(nav, periods_per_year),
        "vol_ann": shadow_vol_ann(nav, periods_per_year),
        "max_dd": shadow_max_dd(nav),
        "sharpe": shadow_sharpe(nav, risk_free_rate_ann, periods_per_year),
        "sortino": shadow_sortino(nav, risk_free_rate_ann, periods_per_year),
    }


# ============================================================
# Divergence comparison — pure, no DB (testable independently of compute_shadow_metrics's inputs).
# ============================================================

def reconcile_metrics(
    shadow: dict[str, float], prod: dict[str, float], *, rel_tol: float = 1e-6, abs_tol: float = 1e-9,
) -> list[dict]:
    """One row per metric present on EITHER side. A metric missing from one side (NaN or absent)
    is reported as its own verdict ('shadow_only'/'prod_only'/'both_nan'), never silently skipped
    or counted as a numeric match — the same "undecidable is not the same as violating" discipline
    Section A's check_invariant() follows, applied here to "can't compare" vs "compared and
    matched/diverged"."""
    rows = []
    for metric in _METRICS:
        s = shadow.get(metric)
        p = prod.get(metric)
        s_nan = s is None or (isinstance(s, float) and np.isnan(s))
        p_nan = p is None or (isinstance(p, float) and np.isnan(p))
        if s_nan and p_nan:
            verdict = "both_nan"
        elif s_nan:
            verdict = "shadow_nan_prod_has_value"
        elif p_nan:
            verdict = "prod_nan_shadow_has_value"
        else:
            diff = abs(s - p)
            tol = abs_tol + rel_tol * max(abs(s), abs(p))
            verdict = "match" if diff <= tol else "DIVERGE"
        rows.append({
            "metric": metric, "shadow_value": s, "prod_value": p,
            "abs_diff": None if (s_nan or p_nan) else abs(s - p),
            "verdict": verdict,
        })
    return rows


# ============================================================
# DB access (connection + raw SQL only — no calculation logic shared with production).
# ============================================================

_NAV_QUERY = "SELECT Date AS date, NAV AS nav FROM fund_nav_monthly WHERE ISIN = %s ORDER BY Date"

_PROD_METRICS_QUERY = """
    SELECT metric, value FROM fund_metrics
    WHERE ISIN = %s AND horizon = 'since_inception' AND real_flag = 0 AND metric_version = 'v1'
      AND metric IN ('return_ann', 'vol_ann', 'max_dd', 'sharpe', 'sortino')
"""

# Same source resolve_rf_rate()'s caller (run_pipeline.py::load_rf_rate) reads -- raw SQL, not a
# shared helper. rate_deposit/EU is the ECB deposit rate, the production default.
_RF_RATE_QUERY = (
    "SELECT date, value AS rate FROM series_macro WHERE indicator = 'rate_deposit' "
    "AND geography = 'EU' ORDER BY date"
)

STRATIFIED_SAMPLE_MAX = 60   # population-scale mandate: a "sample" larger than this is a population run


def load_nav(conn, isin: str) -> pd.DataFrame:
    """Columns: date, nav -- both needed (dates to resolve the risk-free rate at the horizon's
    end date, not just the NAV levels the formulas consume)."""
    rows = conn.execute(_NAV_QUERY, (isin,)).fetchall()
    return apply_eur_view(conn, isin, pd.DataFrame(rows, columns=["date", "nav"]).astype({"nav": float}))


def load_rf_series(conn) -> pd.DataFrame:
    """series_macro.value for rate_deposit/EU is stored as a PERCENTAGE (2.25 meaning 2.25%), not
    a decimal fraction -- found live the first time this tool ran (an initial pass without the /
    100.0 below produced sharpe values around -20, since a "4% rate" was read as 400%). Confirmed
    against db_readers.py::load_rf_rate()'s own conversion (`rate.astype(float) / 100.0  # % ->
    decimal`, read, not imported)."""
    rows = conn.execute(_RF_RATE_QUERY).fetchall()
    df = pd.DataFrame(rows, columns=["date", "rate"]).astype({"rate": float})
    df["rate"] = df["rate"] / 100.0
    return df


def load_prod_metrics(conn, isin: str) -> dict[str, float]:
    rows = conn.execute(_PROD_METRICS_QUERY, (isin,)).fetchall()
    return {r[0]: float(r[1]) for r in rows if r[1] is not None}


def stratified_sample(conn, per_cell: int = 3) -> list[str]:
    """Fund_Nature x vintage-bucket (with an explicit pre-2000 bucket, the FND-0114 class) x NAV
    completeness -- up to `per_cell` ISINs per non-empty cell, capped overall at
    STRATIFIED_SAMPLE_MAX. Deterministic (ORDER BY ISIN), so re-running with the same per_cell
    reproduces the same sample."""
    rows = conn.execute("""
        SELECT fm.ISIN, fm.Fund_Nature,
               CASE
                   WHEN MIN(n.Date) < '2000-01-01' THEN 'pre_2000'
                   WHEN MIN(n.Date) < '2010-01-01' THEN '2000s'
                   WHEN MIN(n.Date) < '2020-01-01' THEN '2010s'
                   ELSE '2020s'
               END AS vintage,
               CASE WHEN COUNT(*) >= 60 THEN 'long' ELSE 'short' END AS completeness
        FROM fund_master fm JOIN fund_nav_monthly n ON n.ISIN = fm.ISIN
        WHERE fm.In_Current_Universe = 1
        GROUP BY fm.ISIN, fm.Fund_Nature
        HAVING COUNT(*) >= %s
        ORDER BY fm.ISIN
    """, (_MIN_OBS,)).fetchall()

    cells: dict[tuple, list[str]] = {}
    for isin, nature, vintage, completeness in rows:
        key = (nature, vintage, completeness)
        bucket = cells.setdefault(key, [])
        if len(bucket) < per_cell:
            bucket.append(isin)

    sample = sorted({isin for bucket in cells.values() for isin in bucket})
    return sample[:STRATIFIED_SAMPLE_MAX]


def run_reconciliation(conn, isins: Sequence[str], rf_series: pd.DataFrame | None = None) -> list[dict]:
    """Runs the full clean-room-vs-production comparison for each ISIN. Returns a flat divergence
    trace (one row per (isin, metric)) -- the caller decides how to report it.

    rf_series: pass load_rf_series(conn)'s result once and reuse it across every ISIN (mirrors
    run_pipeline.py loading rf_rate_df once per run, not once per fund) -- when None, falls back
    to the flat RISK_FREE_RATE_ANN for every ISIN (a coarser, still-correct-for-return_ann/vol_ann/
    max_dd comparison, just less precise for sharpe/sortino).
    """
    trace = []
    for isin in isins:
        nav_df = load_nav(conn, isin)
        if len(nav_df) < _MIN_OBS:
            trace.append({"isin": isin, "metric": "<all>", "shadow_value": None,
                          "prod_value": None, "abs_diff": None, "verdict": "skipped_too_few_obs"})
            continue
        rf_for_horizon = shadow_resolve_rf_rate(nav_df["date"].max(), rf_series, RISK_FREE_RATE_ANN)
        shadow = compute_shadow_metrics(nav_df["nav"].to_numpy(dtype=float), rf_for_horizon)
        prod = load_prod_metrics(conn, isin)
        for row in reconcile_metrics(shadow, prod):
            row["isin"] = isin
            trace.append(row)
    return trace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--isin", help="Comma-separated explicit ISIN list.")
    parser.add_argument("--stratified-sample", type=int, metavar="PER_CELL",
                         help=f"Build a fresh stratified sample, PER_CELL ISINs per "
                              f"(Fund_Nature, vintage, completeness) cell, capped at "
                              f"{STRATIFIED_SAMPLE_MAX} total.")
    parser.add_argument("--population", action="store_true",
                         help="Run against the entire In_Current_Universe=1 population. "
                              "Owner-run only -- never the default.")
    parser.add_argument("--rel-tol", type=float, default=1e-6)
    args = parser.parse_args()

    conn = get_connection()

    if args.population:
        isins = [r[0] for r in conn.execute(
            "SELECT ISIN FROM fund_master WHERE In_Current_Universe = 1 ORDER BY ISIN").fetchall()]
    elif args.isin:
        isins = [s.strip() for s in args.isin.split(",") if s.strip()]
    elif args.stratified_sample:
        isins = stratified_sample(conn, per_cell=args.stratified_sample)
    else:
        parser.error(
            "Refusing to run without a bounded scope. Pass --isin, --stratified-sample "
            "(capped at %d), or --population (owner-run only)." % STRATIFIED_SAMPLE_MAX
        )
        return 2

    rf_series = load_rf_series(conn)
    print(f"Shadow reconciliation: {len(isins)} ISINs "
          f"(risk-free rate: {'resolved from ' + str(len(rf_series)) + ' monthly points' if not rf_series.empty else f'flat fallback {RISK_FREE_RATE_ANN}'})")
    trace = run_reconciliation(conn, isins, rf_series=rf_series)
    conn.close()

    diverging = [r for r in trace if r["verdict"] == "DIVERGE"]
    for r in trace:
        if r["verdict"] not in ("match",):
            print(f"  [{r['verdict']}] {r['isin']} {r['metric']}: "
                  f"shadow={r['shadow_value']} prod={r['prod_value']} diff={r['abs_diff']}")

    n_compared = sum(1 for r in trace if r["verdict"] in ("match", "DIVERGE"))
    print(f"\n{n_compared} metric comparisons, {len(diverging)} divergences.")
    return 1 if diverging else 0


if __name__ == "__main__":
    sys.exit(main())
