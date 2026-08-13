# scripts/diag/diag_ols_coverage.py
# -*- coding: utf-8 -*-
"""
P2-11 — OLS beta coverage diagnostic.

Read-only. Queries fund_metrics for beta_* coverage counts and prints a
columnar summary (metric, n_funds, coverage %). Also shows P3-03/P3-04
scenario metrics and the overall n_obs distribution.

Usage (from repo root):
    C:\\Users\\Administrador\\anaconda3\\envs\\des\\python.exe scripts/diag/diag_ols_coverage.py
"""

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH


def main() -> None:
    conn = sqlite3.connect(DB_PATH)

    total_nav = conn.execute(
        "SELECT COUNT(DISTINCT ISIN) FROM fund_nav_monthly"
    ).fetchone()[0]

    total_master = conn.execute(
        "SELECT COUNT(*) FROM fund_master"
    ).fetchone()[0]

    print(f"\n{'OLS COVERAGE REPORT — P2-11':=^65}")
    print(f"  ISINs in fund_nav_monthly : {total_nav:>6,d}")
    print(f"  ISINs in fund_master      : {total_master:>6,d}")

    # ── OLS beta metrics ──────────────────────────────────────────────
    beta_rows = conn.execute(
        """
        SELECT metric, COUNT(*) AS n
        FROM fund_metrics
        WHERE metric LIKE 'beta_%'
          AND horizon = 'since_inception'
          AND real_flag = 0
          AND value IS NOT NULL
        GROUP BY metric
        ORDER BY metric
        """
    ).fetchall()

    print(f"\n  {'Beta factor':<36} {'n_funds':>8} {'% of NAV':>10}")
    print("  " + "-" * 58)
    for metric, n in beta_rows:
        pct = n / total_nav * 100 if total_nav else 0.0
        print(f"  {metric:<36} {n:>8,d} {pct:>9.1f}%")

    # ── Scenario metrics (P3-03 / P3-04) ─────────────────────────────
    scen_rows = conn.execute(
        """
        SELECT metric, COUNT(*) AS n
        FROM fund_metrics
        WHERE metric IN ('energy_sensitivity_pct', 'hy_spread_sensitivity_pct')
          AND horizon = 'since_inception'
          AND real_flag = 0
          AND value IS NOT NULL
        GROUP BY metric
        ORDER BY metric
        """
    ).fetchall()

    if scen_rows:
        print(f"\n  {'Scenario metric (P3-03/04)':<36} {'n_funds':>8} {'% of NAV':>10}")
        print("  " + "-" * 58)
        for metric, n in scen_rows:
            pct = n / total_nav * 100 if total_nav else 0.0
            print(f"  {metric:<36} {n:>8,d} {pct:>9.1f}%")

    # ── n_obs distribution (OLS observation count) ───────────────────
    nobs_row = conn.execute(
        """
        SELECT
            COUNT(*)                                 AS n_funds,
            CAST(AVG(value) AS INTEGER)              AS avg_obs,
            MIN(value)                               AS min_obs,
            MAX(value)                               AS max_obs,
            COUNT(*) FILTER (WHERE value < 60)       AS below_min
        FROM fund_metrics
        WHERE metric = 'macro_n_obs'
          AND horizon = 'since_inception'
          AND real_flag = 0
          AND value IS NOT NULL
        """
    ).fetchone()

    if nobs_row and nobs_row[0]:
        n, avg, mn, mx, below = nobs_row
        print(f"\n  OLS n_obs (macro_n_obs): {n:,d} funds computed")
        print(f"    avg={avg}  min={int(mn)}  max={int(mx)}  below_60_obs={below}")

    # ── Zero-beta funds (OLS ran but beta is exactly 0) ──────────────
    zero_beta = conn.execute(
        """
        SELECT COUNT(DISTINCT isin)
        FROM fund_metrics
        WHERE metric LIKE 'beta_%'
          AND horizon = 'since_inception'
          AND real_flag = 0
          AND value = 0.0
        """
    ).fetchone()[0]
    if zero_beta:
        print(f"\n  Funds with at least one beta=0.0 : {zero_beta:,d}")

    print()
    conn.close()


if __name__ == "__main__":
    main()
