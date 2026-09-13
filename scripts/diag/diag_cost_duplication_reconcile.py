#!/usr/bin/env python
"""Diagnostic (read-only, no writes): reconcile the 328-ISIN figure the
statistical audit engine measures for the cross-horizon Total_Costs_Pct/EUR
duplication defect (shared/statistical_audit/catalog_group_checks.py,
AUDITORIA_ESTADISTICA.md §2.8) against the 447 informally cited when the
defect was first diagnosed (§2.7, no artifact/script backs that number).

Reproduces the exact 328-ISIN baseline (check_group_constancy, restricted to
In_Current_Universe=1 — the same scope build_population enforces for every
other rule in this catalog) and then relaxes that scope one assumption at a
time, printing both the count AND the set difference (which ISINs newly
appear) for each relaxation, plus raw evidence rows for a sample — per
P#7, this is a diagnostic SELECT script, no writes.

Usage:
    python -X utf8 scripts/diag/diag_cost_duplication_reconcile.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from shared.config import DB_PATH
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS
from shared.statistical_audit.group_checks import check_group_constancy
from shared.statistical_audit.snapshot import build_population


def _flagged_isins(schedule: pd.DataFrame) -> set[str]:
    isins: set[str] = set()
    for rule in COST_GROUP_CHECKS:
        result = check_group_constancy(schedule, rule)
        isins |= set(result.violations["ISIN"])
    return isins


def main() -> int:
    conn = sqlite3.connect(str(DB_PATH))

    # ---- Baseline: exactly what the audit engine measures -----------------
    baseline = build_population(conn, """
        SELECT s.ISIN, s.Horizon_Years, s.Is_RHP, s.Total_Costs_EUR, s.Total_Costs_Pct
        FROM fund_cost_schedule s
        JOIN fund_master m ON m.ISIN = s.ISIN
        WHERE m.In_Current_Universe = 1
    """)
    baseline_isins = _flagged_isins(baseline)
    print(f"=== Baseline (In_Current_Universe=1, min_group_size=2) ===")
    print(f"  {len(baseline_isins)} ISINs flagged")

    # ---- Relaxation A: no In_Current_Universe filter -----------------------
    # build_population() *requires* the universe filter by design
    # (UniverseFilterMissingError) — this relaxation deliberately bypasses it
    # via a raw query to test the hypothesis, not to suggest changing the
    # engine's own architecture.
    no_universe_filter = pd.read_sql_query("""
        SELECT s.ISIN, s.Horizon_Years, s.Is_RHP, s.Total_Costs_EUR, s.Total_Costs_Pct
        FROM fund_cost_schedule s
        JOIN fund_master m ON m.ISIN = s.ISIN
    """, conn)
    no_filter_isins = _flagged_isins(no_universe_filter)
    gap_a = no_filter_isins - baseline_isins
    print(f"\n=== Relaxation A: no In_Current_Universe filter ===")
    print(f"  {len(no_filter_isins)} ISINs flagged ({len(gap_a)} new vs. baseline)")

    # ---- Relaxation B: no join to fund_master at all (true orphans) -------
    no_join = pd.read_sql_query("""
        SELECT ISIN, Horizon_Years, Is_RHP, Total_Costs_EUR, Total_Costs_Pct
        FROM fund_cost_schedule
    """, conn)
    no_join_isins = _flagged_isins(no_join)
    gap_b = no_join_isins - no_filter_isins
    print(f"\n=== Relaxation B: no join to fund_master (raw fund_cost_schedule) ===")
    print(f"  {len(no_join_isins)} ISINs flagged ({len(gap_b)} new vs. relaxation A)")

    # ---- Evidence: inspect the gap closed by relaxation A ------------------
    print(f"\n=== Evidence for the {len(gap_a)}-ISIN gap closed by dropping the universe filter ===")
    if gap_a:
        sample = list(gap_a)[:10]
        placeholders = ",".join("?" * len(sample))
        evidence = pd.read_sql_query(f"""
            SELECT ISIN, In_Current_Universe
            FROM fund_master
            WHERE ISIN IN ({placeholders})
        """, conn, params=sample)
        print(evidence.to_string(index=False))
        print(f"\n  In_Current_Universe value counts across the full {len(gap_a)}-ISIN gap:")
        placeholders_full = ",".join("?" * len(gap_a))
        full_counts = pd.read_sql_query(f"""
            SELECT In_Current_Universe, COUNT(*) as n
            FROM fund_master
            WHERE ISIN IN ({placeholders_full})
            GROUP BY In_Current_Universe
        """, conn, params=list(gap_a))
        print(full_counts.to_string(index=False))
        # ISINs in the gap that aren't even in fund_master at all
        placeholders_full2 = ",".join("?" * len(gap_a))
        present = set(pd.read_sql_query(
            f"SELECT ISIN FROM fund_master WHERE ISIN IN ({placeholders_full2})",
            conn, params=list(gap_a),
        )["ISIN"])
        absent_from_master = gap_a - present
        print(f"  Of the {len(gap_a)}, {len(absent_from_master)} are absent from fund_master entirely (true orphans)")

    print(f"\n=== Evidence for the {len(gap_b)}-ISIN gap closed by also dropping the join ===")
    if gap_b:
        sample_b = list(gap_b)[:10]
        placeholders_b = ",".join("?" * len(sample_b))
        raw_rows = pd.read_sql_query(f"""
            SELECT ISIN, Horizon_Years, Is_RHP, Total_Costs_EUR, Total_Costs_Pct
            FROM fund_cost_schedule
            WHERE ISIN IN ({placeholders_b})
            ORDER BY ISIN, Horizon_Years
        """, conn, params=sample_b)
        print(raw_rows.to_string(index=False))
    else:
        print("  (none — every schedule ISIN has a fund_master row)")

    print(f"\n=== Summary ===")
    print(f"  328-equivalent baseline (active universe, this engine's scope): {len(baseline_isins)}")
    print(f"  + inactive/retired funds (universe filter dropped):              {len(gap_a)}")
    print(f"  + true orphans (no fund_master row at all):                     {len(gap_b)}")
    print(f"  = total without any filter:                                     {len(no_join_isins)}")
    print(f"  Originally cited (§2.7, no backing script):                     447")
    print(f"  Difference vs. total-without-filter:                            {447 - len(no_join_isins)}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
