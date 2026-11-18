# scripts/audit/p3/p3_pit_benchmarks.py
# -*- coding: utf-8 -*-
"""P3-1.4 -- the decision benchmarks of checkpoint C1 (roadmap of 2026-11-18, doc/backlog/PKG_L_p3_alignment.md, FND-0430).

Read-only: it reads a SAVED PIT run (out/reports/pit_backtest/<STAMP>/, with its inputs/ snapshot), never the database, and writes only its own report folder. It rebuilds the
champion of that run (band 0 as saved, band 0.10 for reference), the pool without constraints (the FND-0429 comparator), the mandate-matched pool, the persistent random selection
R1 and the monthly-redraw random selection R2 (an upper bound on turnover), all with the production selection engine and the same 25 bp per side, gross and net, and prints the
two-part turnover (regime jumps, holdings) and the mandate-level statistics (median and 25th percentile of the annualised excess over IPC+M3 on 36 and 60 month windows).

    python scripts/audit/p3/p3_pit_benchmarks.py --run-dir out/reports/pit_backtest/<STAMP> [--draws 50] [--r2-draws 10] [--dry-run]

C1 (decision card, confirmed by the owner 2026-11-18) is evaluated mechanically and reported, never acted on: PROCEED to the construction study when (a) the gross construction gap
(R1 - matched pool, annualised return) has its interval below zero and the net gap does not reverse it, or (b) the champion's annual cost drag exceeds R1's by more than
`--drag-threshold` (0.25 pp); CANCEL only when both fail and the Sharpe construction gap lies inside +-`--delta` (0.10); anything else is INCONCLUSIVE and goes to the owner with the numbers.
A run saved with `--current-universe-only` is survivor-only: the report says so on its first lines and its levels are optimistic (doc/audit/PIT_FIDELITY_REGISTER.md).
Exit codes: 0 done, 2 usage or missing input.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from proyecto3.src import pit_benchmarks as B  # noqa: E402
from proyecto3.src.pit_backtest import build_weights, prepare_pit_backtest, series_stats  # noqa: E402
from proyecto3.src.pit_inputs import has_inputs_snapshot  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS  # noqa: E402
from shared.config import MAX_NON_EUR_UNHEDGED_WEIGHT  # noqa: E402

RC_OK, RC_USAGE = 0, 2


def _arm(series: pd.DataFrame, parts: pd.DataFrame, cash: pd.Series, bps: float) -> dict:
    return {"net": series_stats(series["net"], cash), "gross": series_stats(series["gross"], cash),
            "turnover": {"total": float(parts["total"].mean()), "regime": float(parts["regime"].mean()), "holdings": float(parts["holdings"].mean())},
            "annual_cost_drag": float(parts["total"].mean() * 12 * bps / 1e4)}


def _mean_arm(arms: "list[dict]") -> dict:
    """Mean over draws of every numeric leaf of the per-draw summaries, plus the spread (sd) of the net Sharpe across draws."""
    def avg(path):
        vals = []
        for a in arms:
            v = a
            for p in path:
                v = v[p]
            vals.append(v)
        return float(np.nanmean(vals))
    out = {k: {m: avg((k, m)) for m in arms[0][k]} for k in ("net", "gross", "turnover")}
    out["annual_cost_drag"] = avg(("annual_cost_drag",))
    out["net_sharpe_sd_across_draws"] = float(np.nanstd([a["net"]["sharpe"] for a in arms], ddof=1)) if len(arms) > 1 else 0.0
    out["n_draws"] = len(arms)
    return out


def _neg(g: dict) -> dict:
    return {**g, "point": -g["point"], "ci_low": -g["ci_high"], "ci_high": -g["ci_low"], "prob_positive": 1.0 - g["prob_positive"]}


def _gaps(champion: dict, pool_free: pd.DataFrame, pool_match: pd.DataFrame, r1: "list[pd.DataFrame]", r2: "list[pd.DataFrame]", cash: pd.Series, args) -> dict:
    kw = dict(n_boot=args.boot, mean_block=args.mean_block, seed=args.seed)
    out = {}
    for level in ("gross", "net"):
        c, pf, pm, r = champion[level], pool_free[level], pool_match[level], [s[level] for s in r1]
        out[level] = {
            "construction R1 - matched pool": {k: B.bootstrap_gap(r, pm, k, cash, **kw) for k in ("ann_return", "sharpe")},
            "selection + rotation champion - R1": {k: _neg(B.bootstrap_gap(r, c, k, cash, **kw)) for k in ("ann_return", "sharpe")},
            "champion - matched pool": {k: B.bootstrap_gap([c], pm, k, cash, **kw) for k in ("ann_return", "sharpe")},
            "champion - unconstrained pool (the FND-0429 comparator)": {k: B.bootstrap_gap([c], pf, k, cash, **kw) for k in ("ann_return", "sharpe")},
        }
    # Gross-only separations that use the monthly-redraw variant. R1 keeps its holdings until they stop being eligible, so "R1 - pool" mixes the cost of the caps
    # with a persistence effect; R2 re-selects every month like the champion, so R2 - pool isolates the caps and champion - R2 is the gross selection effect. R2's NET
    # is meaningless (it trades about 185 % of the capital a month), hence gross only. The persistence row compares with the MEAN of the R2 draws (descriptive).
    r2g = [s[level_] for s in r2 for level_ in ("gross",)]
    r2_mean = pd.concat(r2g, axis=1).mean(axis=1)
    pm, c = pool_match["gross"], champion["gross"]
    out["gross"]["caps R2 - matched pool (fresh random each month)"] = {k: B.bootstrap_gap(r2g, pm, k, cash, **kw) for k in ("ann_return", "sharpe")}
    out["gross"]["persistence R1 - R2 (mean of the R2 draws)"] = {k: B.bootstrap_gap([s["gross"] for s in r1], r2_mean, k, cash, **kw) for k in ("ann_return", "sharpe")}
    out["gross"]["selection champion - R2 (both re-select every month)"] = {k: _neg(B.bootstrap_gap(r2g, c, k, cash, **kw)) for k in ("ann_return", "sharpe")}
    return out


def c1_rule(gaps: dict, arms: dict, args) -> dict:
    g_gross, g_net = gaps["gross"]["construction R1 - matched pool"], gaps["net"]["construction R1 - matched pool"]
    a_ok = bool(g_gross["ann_return"]["ci_high"] < 0 and not g_net["ann_return"]["point"] > 0)
    drag_diff = arms["champion_band0"]["annual_cost_drag"] - arms["R1_persistent"]["annual_cost_drag"]
    b_ok = bool(drag_diff > args.drag_threshold)
    s = g_gross["sharpe"]
    inside = bool(s["ci_low"] > -args.delta and s["ci_high"] < args.delta)
    outcome = "PROCEED" if (a_ok or b_ok) else ("CANCEL" if inside else "INCONCLUSIVE")
    caps = gaps["gross"]["caps R2 - matched pool (fresh random each month)"]["ann_return"]
    pers = gaps["gross"]["persistence R1 - R2 (mean of the R2 draws)"]["ann_return"]
    return {"outcome": outcome,
            "information_not_in_the_rule": {"gross_caps_gap_R2_minus_pool": caps["point"], "gross_caps_gap_interval": [caps["ci_low"], caps["ci_high"]],
                                            "gross_persistence_gap_R1_minus_R2": pers["point"], "champion_drag_minus_pool_drag": float(arms["champion_band0"]["annual_cost_drag"] - arms["pool_matched"]["annual_cost_drag"]),
                                            "note": "R1 keeps its holdings, so R1 - pool mixes the caps with a persistence effect; the rule above follows the approved plan (R1)"}, "a_gross_construction_gap_below_zero_and_net_not_reversed": a_ok, "b_champion_drag_minus_R1_drag": float(drag_diff),
            "b_exceeds_threshold": b_ok, "drag_threshold": args.drag_threshold, "sharpe_gap_inside_equivalence_margin": inside, "delta": args.delta,
            "reading": "mechanical reading of the decision card; the owner decides at C1"}


def fmt(x, pct=True, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else (f"{100 * x:.{nd}f}%" if pct else f"{x:.{nd + 1}f}")


def render_md(r: dict) -> str:
    L = [f"# P3-1.4 decision benchmarks (C1 inputs), generated {r['generated']}", ""]
    if r["survivor_only"]:
        L += ["> **SURVIVOR-ONLY RUN.** The PIT run was saved with `--current-universe-only`: no fund that later left the universe is ever held. Levels are optimistic and this is NOT the C1 evidence; "
              "it is a dry run for sizes and compute (doc/audit/PIT_FIDELITY_REGISTER.md item 1).", ""]
    L += [f"Run `{r['run_dir']}` (run commit {r['run_git']}); {r['n_dates']} dates; {r['bps']} bp per side; R1 {r['draws']} draws, R2 {r['r2_draws']} draws; elapsed {r['elapsed_s']:.0f} s.", "",
          "## Arms (monthly chained series, net of the 25 bp rotation cost unless stated)", "",
          "| Arm | Net ann. return | Net Sharpe | Net max DD | Gross ann. return | Gross Sharpe | Turnover/month | of which regime | of which holdings | Annual cost drag |", "|---|---|---|---|---|---|---|---|---|---|"]
    for name, a in r["arms"].items():
        L.append(f"| {name} | {fmt(a['net']['ann_return'])} | {fmt(a['net']['sharpe'], False)} | {fmt(a['net']['max_drawdown'], nd=1)} | {fmt(a['gross']['ann_return'])} | "
                 f"{fmt(a['gross']['sharpe'], False)} | {fmt(a['turnover']['total'], nd=1)} | {fmt(a['turnover']['regime'], nd=1)} | {fmt(a['turnover']['holdings'], nd=1)} | {fmt(a['annual_cost_drag'])} |")
    L += ["", "R1 and R2 rows are means over draws (the draw-to-draw sd of the net Sharpe is in the JSON). R2 redraws every month: an upper bound on turnover, a bound only.", "",
          "## Mandate-level statistics: annualised excess over IPC+M3, full rolling windows (descriptive; overlapping windows carry no interval)", "",
          "| Arm | Window | n windows | Median | 25th pct | Mean | Share above target |", "|---|---|---|---|---|---|---|"]
    for name, ws in r["mandate"].items():
        for w, s in ws.items():
            L.append(f"| {name} | {w} m | {s['n']} | {fmt(s['median'])} | {fmt(s['p25'])} | {fmt(s['mean'])} | {fmt(s['share_above'], nd=0)} |")
    L += ["", "## Gaps (paired stationary block bootstrap of the months, 90 % interval; R1 draws share one resampling)", ""]
    for level in ("gross", "net"):
        L += [f"### {level}", "", "| Gap | Ann. return gap | 90 % interval | Sharpe gap | 90 % interval |", "|---|---|---|---|---|"]
        for name, g in r["gaps"][level].items():
            a, s = g["ann_return"], g["sharpe"]
            L.append(f"| {name} | {fmt(a['point'])} | {fmt(a['ci_low'])} .. {fmt(a['ci_high'])} | {fmt(s['point'], False)} | {fmt(s['ci_low'], False)} .. {fmt(s['ci_high'], False)} |")
        L.append("")
    c = r["c1"]
    L += ["## C1, mechanical reading (decision card thresholds)", "", f"**{c['outcome']}**", "",
          f"- (a) gross construction gap below zero with its interval, and net not reversed: {c['a_gross_construction_gap_below_zero_and_net_not_reversed']}",
          f"- (b) champion annual cost drag minus R1's: {fmt(c['b_champion_drag_minus_R1_drag'])} against the threshold {fmt(c['drag_threshold'])}: {c['b_exceeds_threshold']}",
          f"- Sharpe construction gap inside +-{c['delta']}: {c['sharpe_gap_inside_equivalence_margin']}", "", c["reading"] + ".", ""]
    i = c["information_not_in_the_rule"]
    L += ["### Not in the rule, for the reading", "",
          f"- Gross caps effect (R2 - matched pool): {fmt(i['gross_caps_gap_R2_minus_pool'])} a year, 90 % interval {fmt(i['gross_caps_gap_interval'][0])} .. {fmt(i['gross_caps_gap_interval'][1])}.",
          f"- Gross persistence effect (R1 - R2): {fmt(i['gross_persistence_gap_R1_minus_R2'])} a year: part of R1 - pool is NOT the construction rules.",
          f"- Champion annual cost drag minus the pool's: {fmt(i['champion_drag_minus_pool_drag'])}.", f"- {i['note']}.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--draws", type=int, default=50)
    ap.add_argument("--r2-draws", type=int, default=10)
    ap.add_argument("--bps", type=float, default=25.0)
    ap.add_argument("--seed", type=int, default=20261118)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--mean-block", type=float, default=6.0)
    ap.add_argument("--delta", type=float, default=0.10)
    ap.add_argument("--drag-threshold", type=float, default=0.0025)
    ap.add_argument("--out-dir")
    ap.add_argument("--dry-run", action="store_true", help="load the run, time one R1 draw and stop")
    args = ap.parse_args(argv)
    run = Path(args.run_dir)
    if not (run / "manifest.json").exists() or not has_inputs_snapshot(run):
        print(f"ERROR: {run} is not a saved PIT run with an inputs/ snapshot", file=sys.stderr)
        return RC_USAGE

    t0 = time.time()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    pit_run, inputs, hist, at = load_saved_run(run, None)
    fx = getattr(inputs, "fx_exposed", None)
    scores, attrs = pit_run.scores, inputs.attrs
    prep = prepare_pit_backtest(pit_run, inputs, hist, at, windows=(1,))
    R1m, cash = prep.R[1], prep.cash_ret[1]
    if args.dry_run:
        t1 = time.time()
        W, _c = B.random_selection_weights(scores, attrs, hist, at, args.seed, persistent=True, fx_exposed=fx)
        print(f"load+prepare {t1 - t0:.1f} s; one R1 draw {time.time() - t1:.1f} s; mean funds {W.notna().sum(axis=1).mean():.1f}")
        return RC_OK

    def sw(W):
        return B.benchmark_series(W, R1m, cash, args.bps), B.turnover_parts(W, hist, at)

    cons10 = dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=0.10)
    W10, _c = build_weights(scores, attrs, hist, at, cons10, use_incumbents=True, fx_exposed=fx)
    named = {"champion_band0": prep.W, "champion_band0.10": W10,
             "pool_unconstrained": B.pool_weights(scores, hist, at),
             "pool_matched": B.pool_weights(scores, hist, at, fx_exposed=fx, exposed_budget=MAX_NON_EUR_UNHEDGED_WEIGHT)}
    series, arms = {}, {}
    for name, W in named.items():
        series[name], parts = sw(W)
        arms[name] = _arm(series[name], parts, cash, args.bps)
    r1_series, r1_arms, r2_arms, r2_series = [], [], [], []
    for k in range(args.draws):
        s, p = sw(B.random_selection_weights(scores, attrs, hist, at, args.seed + k, True, fx_exposed=fx)[0])
        r1_series.append(s)
        r1_arms.append(_arm(s, p, cash, args.bps))
    for k in range(args.r2_draws):
        s, p = sw(B.random_selection_weights(scores, attrs, hist, at, args.seed + 10_000 + k, False, fx_exposed=fx)[0])
        r2_series.append(s)
        r2_arms.append(_arm(s, p, cash, args.bps))
    arms["R1_persistent"], arms["R2_monthly_redraw"] = _mean_arm(r1_arms), _mean_arm(r2_arms)

    table = pd.read_csv(run / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
    target = table["target_1m"] if "target_1m" in table.columns else pd.Series(np.nan, index=at)
    mean_r1_net = pd.concat([s["net"] for s in r1_series], axis=1).mean(axis=1)
    mandate_src = {"champion_band0 (net)": series["champion_band0"]["net"], "pool_unconstrained (net)": series["pool_unconstrained"]["net"],
                   "pool_matched (net)": series["pool_matched"]["net"], "R1_persistent (net, mean of draws)": mean_r1_net}
    mandate = {n: {str(w): B.window_summary(B.rolling_excess(s, target, w)) for w in (36, 60)} for n, s in mandate_src.items()}
    gaps = _gaps({"gross": series["champion_band0"]["gross"], "net": series["champion_band0"]["net"]},
                 {"gross": series["pool_unconstrained"]["gross"], "net": series["pool_unconstrained"]["net"]},
                 {"gross": series["pool_matched"]["gross"], "net": series["pool_matched"]["net"]}, r1_series, r2_series, cash, args)
    result = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "run_dir": str(run), "run_git": manifest.get("git_commit"), "n_dates": len(at),
              "survivor_only": bool(manifest.get("args", {}).get("current_universe_only")), "bps": args.bps, "draws": args.draws, "r2_draws": args.r2_draws,
              "seed": args.seed, "elapsed_s": time.time() - t0, "arms": arms, "mandate": mandate, "gaps": gaps}
    result["c1"] = c1_rule(gaps, arms, args)
    out = Path(args.out_dir) if args.out_dir else ROOT / "out" / "reports" / "p3_benchmarks" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "p3_benchmarks.json").write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    (out / "p3_benchmarks.md").write_text(render_md(result), encoding="utf-8")
    print(render_md(result))
    print(f"\nwritten to {out}")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
