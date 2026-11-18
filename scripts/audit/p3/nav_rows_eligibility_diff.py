#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
nav_rows_eligibility_diff.py -- read-only measurement for P3-1.7 (roadmap of 2026-11-18, FND-0430): what a minimum-history rule would change in the live eligible set.

    python scripts/audit/p3/nav_rows_eligibility_diff.py [--regime Shock_Energetico] [--min-rows 12] [--max-nature-share 0.05] [--list out.csv]

The live scorer never checks `MIN_NAV_ROWS` (the PIT backtest does, REQ-02) and a fund with metrics but NO row in fund_nav_monthly passes the NAV-age rule (E-6). This script counts, per
Fund_Nature, how many funds of the latest scoring run (eligible, score > 0) have fewer than `--min-rows` monthly NAV rows (0 included) and so would be excluded. Nothing is written to the
database and no switch exists yet: it is information for the owner, who orders any change.

Decision rule (fixed BEFORE the numbers are read; decision card row 4): recommend ON only when every excluded fund has fewer than `--min-rows` rows (true by construction) AND no
Fund_Nature loses more than `--max-nature-share` of its eligible funds. The share is a placeholder until P3-1.6 measures the normal month-to-month change of the eligible set; without
`--max-nature-share` the script prints the figures and no recommendation. Exit code 0 whatever the result.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import shared.config as cfg  # noqa: E402,F401  (loads the repository .env before the connection is opened)
from shared.db import get_connection  # noqa: E402

SQL_BY_NATURE = """
    WITH runs AS (SELECT block, MAX(as_of_date) AS d FROM fund_scores WHERE score_version = %s AND regime = %s GROUP BY block),
    latest AS (
        SELECT fs.isin FROM fund_scores fs JOIN runs r ON r.block = fs.block AND r.d = fs.as_of_date
        WHERE fs.score_version = %s AND fs.regime = %s AND fs.eligible = 1 AND fs.score_total > 0
        GROUP BY fs.isin),
    n AS (SELECT isin, COUNT(*) AS nrows FROM fund_nav_monthly GROUP BY isin)
    SELECT COALESCE(fm.Fund_Nature, '?') AS nature, COUNT(*) AS eligible_funds,
           COUNT(*) FILTER (WHERE COALESCE(n.nrows, 0) = 0) AS no_monthly_nav,
           COUNT(*) FILTER (WHERE COALESCE(n.nrows, 0) > 0 AND n.nrows < %s) AS under_min_rows,
           COUNT(*) FILTER (WHERE COALESCE(n.nrows, 0) < %s) AS excluded
    FROM latest JOIN fund_master fm ON fm.ISIN = latest.isin LEFT JOIN n ON n.isin = latest.isin
    GROUP BY 1 ORDER BY 2 DESC LIMIT 50
"""

SQL_EXCLUDED = """
    WITH runs AS (SELECT block, MAX(as_of_date) AS d FROM fund_scores WHERE score_version = %s AND regime = %s GROUP BY block),
    latest AS (
        SELECT fs.isin FROM fund_scores fs JOIN runs r ON r.block = fs.block AND r.d = fs.as_of_date
        WHERE fs.score_version = %s AND fs.regime = %s AND fs.eligible = 1 AND fs.score_total > 0
        GROUP BY fs.isin),
    n AS (SELECT isin, COUNT(*) AS nrows FROM fund_nav_monthly GROUP BY isin)
    SELECT latest.isin, COALESCE(fm.Fund_Nature, '?'), COALESCE(n.nrows, 0)
    FROM latest JOIN fund_master fm ON fm.ISIN = latest.isin LEFT JOIN n ON n.isin = latest.isin
    WHERE COALESCE(n.nrows, 0) < %s ORDER BY latest.isin
"""


def summarise(rows: list, max_share: "float | None") -> dict:
    """Per-nature shares and the decision-rule reading from the aggregate rows (nature, eligible, no_monthly_nav, under_min_rows, excluded). Pure."""
    per = []
    for nature, eligible, none, under, excluded in rows:
        per.append({"nature": nature, "eligible": int(eligible), "no_monthly_nav": int(none), "under_min_rows": int(under), "excluded": int(excluded),
                    "share": (excluded / eligible) if eligible else 0.0})
    total_e, total_x = sum(p["eligible"] for p in per), sum(p["excluded"] for p in per)
    worst = max(per, key=lambda p: p["share"]) if per else None
    out = {"per_nature": per, "eligible": total_e, "excluded": total_x, "share": (total_x / total_e) if total_e else 0.0,
           "worst_nature": worst["nature"] if worst else None, "worst_share": worst["share"] if worst else 0.0, "recommendation": None}
    if max_share is not None:
        out["recommendation"] = "ON" if (total_x == 0 or out["worst_share"] <= max_share) else "NOT ON"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--regime", help="regime of the scoring run (default: the regime of the PRODUCTION scenario)")
    ap.add_argument("--score-version", default="v1")
    ap.add_argument("--min-rows", type=int, default=cfg.MIN_NAV_ROWS)
    ap.add_argument("--max-nature-share", type=float, help="decision-rule threshold (placeholder until P3-1.6); without it no recommendation is printed")
    ap.add_argument("--list", help="write the full list of the funds that would be excluded to this CSV")
    args = ap.parse_args(argv)

    conn = get_connection()
    regime = args.regime
    if not regime:
        row = conn.execute("SELECT macro_regime FROM portfolio_scenarios WHERE status = 'PRODUCTION' LIMIT 1").fetchone()
        regime = row[0] if row else None
    if not regime:
        print("ERROR: no PRODUCTION scenario and no --regime", file=sys.stderr)
        return 2
    rows = [tuple(r) for r in conn.execute(SQL_BY_NATURE, (args.score_version, regime, args.score_version, regime, args.min_rows, args.min_rows)).fetchall()]
    s = summarise(rows, args.max_nature_share)
    print(f"Latest scoring run, regime {regime}, version {args.score_version}; rule: fewer than {args.min_rows} monthly NAV rows (0 included)")
    print(f"Eligible funds {s['eligible']}; would be excluded {s['excluded']} ({s['share']:.1%}); worst nature {s['worst_nature']} {s['worst_share']:.1%}")
    print(f"{'nature':28s} {'eligible':>8s} {'no NAV':>7s} {'<min':>6s} {'excluded':>9s} {'share':>7s}")
    for p in s["per_nature"]:
        print(f"{p['nature']:28s} {p['eligible']:8d} {p['no_monthly_nav']:7d} {p['under_min_rows']:6d} {p['excluded']:9d} {p['share']:7.1%}")
    if s["recommendation"]:
        print(f"Decision rule (max nature share {args.max_nature_share:.1%}): recommend {s['recommendation']}")
    else:
        print("Decision rule: no --max-nature-share given, no recommendation (decision card row 4: set after P3-1.6)")
    if args.list:
        ex = conn.execute(SQL_EXCLUDED, (args.score_version, regime, args.score_version, regime, args.min_rows)).fetchall()
        with open(args.list, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["isin", "fund_nature", "monthly_nav_rows"])
            w.writerows(tuple(r) for r in ex)
        print(f"{len(ex)} funds written to {args.list}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
