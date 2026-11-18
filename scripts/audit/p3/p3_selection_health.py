# scripts/audit/p3/p3_selection_health.py
# -*- coding: utf-8 -*-
"""P3-1.6 -- calibrate the selection_health monitors over a SAVED point-in-time run (roadmap of 2026-11-18, doc/backlog/PKG_L_p3_alignment.md, FND-0430).

Read-only: it reads `scores_long.parquet` and `inputs/attrs.parquet` of a PIT run folder, never the database, and writes only its own report folder.

    python scripts/audit/p3/p3_selection_health.py --run-dir out/reports/pit_backtest/<STAMP> [--n 10] [--q 0.95] [--out-dir DIR]

It computes the monitors of proyecto3/src/selection_health.py for every (date, sub-portfolio) of the run, the empirical quantiles (q50 / q90 / q95 / q99), the thresholds at `--q`
(default 0.95), the share of months each monitor would have alerted (about 1 - q by construction) and the values of the last 12 months against the thresholds. Thresholds are
EMPIRICAL percentiles of the history, not guesses; the live block stays report-only until the owner has read them. A run saved with `--current-universe-only` is survivor-only: the
report says so (the early eligible sets are then shorter than live ones, which moves the eligible-set change). Exit codes: 0 done, 2 usage or missing input.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from proyecto3.src import selection_health as H  # noqa: E402

RC_OK, RC_USAGE = 0, 2


def render_md(r: dict) -> str:
    L = [f"# selection_health calibration, generated {r['generated']}", ""]
    if r["survivor_only"]:
        L += ["> **SURVIVOR-ONLY RUN** (`--current-universe-only`): no fund that later left the universe is held, so early eligible sets are shorter than live ones. Read the eligible-set "
              "monitor with that in mind; calibrate again on a default-universe run before relying on the thresholds.", ""]
    L += [f"Run `{r['run_dir']}`; {r['n_dates']} dates; top set = best {r['n']} eligible funds by score_final; thresholds at the {r['q']:.0%} quantile; "
          f"managers {'from attrs' if r['has_manager'] else 'NOT available (top_manager_max is empty)'}.", "",
          "## Empirical quantiles and thresholds", "", "| Sub-portfolio | Monitor | n | q50 | q90 | q95 | q99 | Threshold | Share of months above |", "|---|---|---|---|---|---|---|---|---|"]
    for row in r["table"]:
        L.append(f"| {row['sub']} | {row['monitor']} | {row['n']} | {row['q50']:.3f} | {row['q90']:.3f} | {row['q95']:.3f} | {row['q99']:.3f} | "
                 f"{'' if row['threshold'] is None else format(row['threshold'], '.3f')} | {'' if row['alert_rate'] is None else format(row['alert_rate'], '.1%')} |")
    if r["degenerate"]:
        L += ["", "**Monitors that cannot fire** (their threshold equals the largest value ever seen, so they have no threshold): " + ", ".join(r["degenerate"]) + ". The raw top-N list is "
              "dominated by one nature by design (nature bonus, within-nature percentiles); diversification is enforced later by the selection constraints, so concentration should be "
              "monitored on the CONSTRUCTED portfolio, which the PIT scores do not carry (live block, next step)."]
    L += ["", "## Last 12 months against the thresholds (report-only: an alert never blocks)", "", "| Date | Sub-portfolio | Monitor | Value | Threshold |", "|---|---|---|---|---|"]
    if not r["recent_alerts"]:
        L.append("| (none) | | | | |")
    for a in r["recent_alerts"]:
        L.append(f"| {a['as_of']} | {a['sub']} | {a['monitor']} | {a['value']:.3f} | {a['threshold']:.3f} |")
    L += ["", "## How to read", "",
          "- `eligible_change` is the share of the eligible set that entered or left since the previous date. A partial scoring run (E-7) makes it jump; so do real events (a P2 recompute, a new hold).",
          "- `top_turnover` is how much of the best-N list changed in a month; `top_nature_max` and `top_manager_max` are concentration counts of that list.",
          "- The share of score weight on a missing metric (E-5) and the counts of funds under a data hold or data-suspicion flag are live-only monitors, added when the live block is wired.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--q", type=float, default=0.95)
    ap.add_argument("--out-dir")
    args = ap.parse_args(argv)
    run = Path(args.run_dir)
    need = [run / "scores_long.parquet", run / "manifest.json"]
    if any(not p.exists() for p in need):
        print(f"ERROR: {run} is not a saved PIT run (needs scores_long.parquet and manifest.json)", file=sys.stderr)
        return RC_USAGE
    scores = pd.read_parquet(run / "scores_long.parquet")
    attrs_path = run / "inputs" / "attrs.parquet"
    manager = pd.read_parquet(attrs_path)["Management_Company"] if attrs_path.exists() else None
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))

    health = H.health_series(scores, manager, n=args.n)
    cal = H.calibrate(health)
    limits = H.thresholds(cal, args.q)
    rate = H.historical_alert_rate(health, limits)
    dead = H.degenerate(cal, args.q)
    table = []
    for (sub, mon), row in cal.iterrows():
        table.append({"sub": sub, "monitor": mon, "n": int(row["n"]), "q50": row["q50"], "q90": row["q90"], "q95": row["q95"], "q99": row["q99"],
                      "threshold": limits.get((sub, mon)), "alert_rate": float(rate[(sub, mon)]) if (sub, mon) in rate.index else None})
    last = sorted(health.index.get_level_values("as_of").unique())[-12:]
    recent = []
    for as_of in last:
        for sub in sorted(health.index.get_level_values("subportfolio").unique()):
            if (as_of, sub) in health.index:
                for mon, val, thr in H.evaluate(health.loc[(as_of, sub)], sub, limits):
                    recent.append({"as_of": str(pd.Timestamp(as_of).date()), "sub": sub, "monitor": mon, "value": val, "threshold": thr})
    result = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "run_dir": str(run), "n_dates": int(health.index.get_level_values("as_of").nunique()),
              "n": args.n, "q": args.q, "survivor_only": bool(manifest.get("args", {}).get("current_universe_only")), "has_manager": manager is not None,
              "table": table, "recent_alerts": recent, "degenerate": [f"{s}|{m}" for s, m in dead], "thresholds": {f"{s}|{m}": t for (s, m), t in limits.items()}}
    out = Path(args.out_dir) if args.out_dir else ROOT / "out" / "reports" / "p3_selection_health" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "selection_health.json").write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    (out / "selection_health.md").write_text(render_md(result), encoding="utf-8")
    print(render_md(result))
    print(f"\nwritten to {out}")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
