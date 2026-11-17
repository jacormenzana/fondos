# scripts/audit/p3/p3_pit_selection_test.py
# -*- coding: utf-8 -*-
"""FND-0429 / WP5 -- the pre-verdict checks of the ranking-layer test: WP5.2 feasibility, then WP5.1 controls (doc/decisiones/PREREG_FND-0429_WP5.md, signed off 2026-11-17).

Read-only on the database (it reads a saved PIT run, never the database) and BLIND: it never computes the point estimate of the real scores, so it cannot produce a verdict. The verdict
(M1 / M2) is a further order. Order of work, as the pre-registration fixes it:

  1. feasibility (WP5.2): independent periods, the minimum detectable selection spread (real-score bootstrap SE) and the minimum detectable Sharpe difference (paired bootstrap of the PIT
     table). If the minimum detectable Sharpe difference exceeds `--min-delta` (locked 0.10) the question is UNTESTABLE: report and stop, no control runs.
  2. controls (WP5.1): permuted labels (PASS rate must be at most alpha) and planted alpha (detection rate must reach the target). A failure blocks the test.

    python scripts/audit/p3/p3_pit_selection_test.py --run-dir out/reports/pit_backtest/<STAMP> [--perms 1000] [--reps 500]

Output: out/reports/wp5/<stamp>/wp5_checks.json and wp5_checks.md. With `--m2` it runs the M2 VERDICT instead (Amendment 1, owner order 2026-11-17; the only mode that reads the point estimate of the real scores): M2 at 12 months, the 36-month sign check, the
nature-free peer sensitivity, the drawdown condition, the survivorship adjustment and the outcome rule of the signed pre-registration; M1 is reported, never decisive. Output: wp5_m2_verdict.json / .md.
Exit codes: 0 both checks done and passed, 1 a control failed, 3 untestable (with `--diagnostic` the controls still run), 2 usage or missing input.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from proyecto3.src import pit_selection_test as T  # noqa: E402

RC_OK, RC_CONTROL_FAILED, RC_USAGE, RC_UNTESTABLE = 0, 1, 2, 3


def load_run(run_dir: Path) -> dict:
    need = ["scores_long.parquet", "inputs/nav.parquet", "inputs/attrs.parquet", "inputs/fx_exposed.parquet", "pit_backtest_table.csv", "manifest.json"]
    missing = [n for n in need if not (run_dir / n).exists()]
    if missing:
        raise FileNotFoundError(f"{run_dir}: missing {missing}")
    return {"scores": pd.read_parquet(run_dir / "scores_long.parquet"), "nav": pd.read_parquet(run_dir / "inputs" / "nav.parquet"), "attrs": pd.read_parquet(run_dir / "inputs" / "attrs.parquet"),
            "fx": pd.read_parquet(run_dir / "inputs" / "fx_exposed.parquet")["fx_exposed"], "table": pd.read_csv(run_dir / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date"),
            "manifest": json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))}


def build(run: dict, n_boot: int, seed: int, fwd_months: int = T.FWD_MONTHS, peers: str = "nature", with_dd: bool = False) -> tuple:
    from shared.period_key import period_ordinals
    nav_m = T.monthly_nav(run["nav"])
    units = T.pick_units(run["scores"], pd.to_numeric(run["attrs"]["Ongoing_Charge"], errors="coerce"))
    last_ord, first_ord = int(nav_m.index.max()), int(nav_m.index.min())
    as_ord = np.asarray(period_ordinals(units["as_of"]))
    units = units[as_ord <= last_ord - fwd_months].reset_index(drop=True)                # only formation months whose forward outcome can exist
    outcomes = T.outcomes_frame(nav_m, np.unique(np.asarray(period_ordinals(units["as_of"]))), fwd_months=fwd_months, with_dd=with_dd)
    panel = T.build_panel(units, outcomes, run["fx"], peers=peers)
    boot = T.make_boot(panel.n_dates, panel.n_families, n_boot=n_boot, mean_block=6.0, seed=seed)
    return panel, boot, first_ord, last_ord


def render_md(r: dict) -> str:
    f, m1 = r["feasibility"], r["feasibility_m1"]
    lines = ["# WP5.1 / WP5.2 checks - FND-0429", "",
             f"Run `{r['run_dir']}` (git {r['git']}), generated {r['generated']}. Pre-registration: `doc/decisiones/PREREG_FND-0429_WP5.md`. BLIND: no point estimate of the real scores was computed.", "",
             "## WP5.2 feasibility", "",
             f"- Formation months with a 12-month outcome: {f['n_dates_12m']} (about {f['independent_12m_windows']:.1f} independent 12-month windows); 36-month: {f.get('n_dates_36m', 'n/a')} months (about {f.get('independent_36m_windows', float('nan')):.1f} windows).",
             f"- Standard error of the real-score top-quintile mean: date block {f['se_date']:.4f}, family cluster {f['se_family']:.4f}, used (the larger) {f['se_used']:.4f}.",
             f"- **Minimum detectable selection spread** (alpha {f['alpha']}, power {f['power']:.0%}): {f['mde_spread']:.4f} outcome units (12-month return per unit of trailing volatility).",
             f"- Peer cells used (share of valid unit-months): level 1 nature x tercile x FX {f['cell_levels'][1]:.1%}; level 2 nature x FX {f['cell_levels'][2]:.1%}; level 3 nature {f['cell_levels'][3]:.1%}.",
             f"- **Minimum detectable Sharpe difference** (M1, paired block bootstrap of the PIT table, frictionless pool as a proxy of the net pool): {m1['mde_delta_sharpe']:.4f} (90 % interval half-width {m1['ci90_half_width']:.4f}, {m1['n_months']} months).",
             f"- Block-length sensitivity of that minimum (mean block 3 / 12): {r['feasibility_m1_block_sensitivity']['3']:.4f} / {r['feasibility_m1_block_sensitivity']['12']:.4f}.",
             f"- Locked `MIN_MEANINGFUL_DELTA_SHARPE` {r['min_delta']:.2f}: **{'TESTABLE' if r['testable'] else 'UNTESTABLE - stop'}**.", ""]
    if r.get("controls"):
        p, q = r["controls"]["permutation"], r["controls"]["planted"]
        lines += ["## WP5.1 controls" + (" - DIAGNOSTIC (the WP5.2 stop rule had fired; not part of a verdict)" if r["controls"].get("diagnostic") else ""), "",
                  f"- **Permuted labels** ({p['n_perm']} runs): PASS rate {p['pass_rate']:.3f} ({p['pass_count']} of {p['n_perm']}); Wilson 95 % upper limit {p['pass_rate_wilson95_upper']:.3f}; rule PASS rate <= alpha {p['alpha']}: **{'MET' if p['meets_rule_rate_le_alpha'] else 'NOT MET'}**. SD of the null estimate {p['null_estimate_sd']:.4f}, mean bootstrap SE {p['mean_bootstrap_se']:.4f}.",
                  f"- **Planted alpha** ({q['n_rep']} replications, {q['plant_share']:.0%} of families, outcome shift {q['delta_on_outcome']:.4f}, expected spread {q['delta_on_outcome'] * q['plant_share'] / T.TOP_FRAC:.4f}): detection rate {q['detection_rate']:.3f} against the target {q['detection_target']:.2f}: **{'MET' if q['meets_target'] else 'NOT MET'}**; recovered mean spread {q['mean_recovered_spread']:.4f}.", ""]
    else:
        lines += ["## WP5.1 controls", "", "Not run: the question is untestable at the locked delta.", ""]
    lines += ["## Reading", "", "These are the pre-verdict checks only. The M1 / M2 verdict needs a further order; the P3 refactor stays paused behind FND-0429.", ""]
    return "\n".join(lines)


def render_m2_md(r: dict) -> str:
    m, v = r["m2_12m"], r["verdict"]
    row = lambda name, x: f"| {name} | {x['estimate']:+.4f} | {x['se_date']:.4f} / {x['se_family']:.4f} | {x['lower_bound']:+.4f} | {x['n_selected_valid']:,} | {x['n_dates']} |"
    lines = [f"# WP5 M2 verdict - FND-0429 - **{v['outcome']}**", "",
             f"Run `{r['run_dir']}` (git {r['git']}), harness commit {r['harness_commit']}, generated {r['generated']}, seed {r['seed']}. Pre-registration `PREREG_FND-0429_WP5.md` + `PREREG_FND-0429_WP5_AMENDMENT1.md`. "
             "alpha 0.05 one-sided; the three locked values and the adopted rules apply unchanged.", "",
             "## Outcome", "", f"- **{v['outcome']}** (main reading: {v['main_klass']}; nature-free sensitivity: {v['nonature_klass']}; 36-month sign consistent: {v['consistent_36m_sign']}; drawdown condition met: {v['dd_condition_met']}).",
             *[f"- Reason: {x}" for x in v["reasons"]], *([f"- Note: {v['note']}"] if v["note"] else []), "",
             "## M2 (selection level): top quintile by score_final minus its peer-cell median, outcome = forward return / trailing 36-month volatility", "",
             "| Run | Estimate | SE date / family | One-sided lower bound | Selected unit-months | Months |", "|---|---|---|---|---|---|",
             row("12 months, signed peers (PRIMARY)", m), row("36 months, signed peers (sign check)", r["m2_36m"]), row("12 months, nature-free peers (sensitivity)", r["m2_nonature"]), "",
             f"- Drawdown condition (forward 12-month max drawdown of the top quintile minus its peer median, must be >= -{r['dd_margin']:.0%}): {m['dd_excess_mean']:+.4f} over {m['n_dd']:,} unit-months.",
             f"- Peer cells (share of valid unit-months, level 1 / 2 / 3): {m['cell_levels'][1]:.1%} / {m['cell_levels'][2]:.1%} / {m['cell_levels'][3]:.1%}.", "",
             "## Survivorship adjustment (attrition base 5 % a year, bounds 2.5 % and 10 %; loss given death = the worst-decile gap of the cell)", "",
             "| Rate | Adjusted estimate | Adjusted lower bound | Flips the sign |", "|---|---|---|---|",
             *[f"| {a['rate']:.1%} | {a['estimate_adjusted']:+.4f} | {a['lower_bound_adjusted']:+.4f} | {a['flips_sign']} |" for a in v["attrition"]], "",
             "## M1 (portfolio level), DESCRIPTIVE ONLY (Amendment 1)", "",
             f"- Champion at band 0.10: Sharpe {r['m1']['champion_band010_sharpe']:.3f}, max drawdown {r['m1']['champion_band010_maxdd']:.2%}; pool (frictionless): Sharpe {r['m1']['pool_sharpe']:.3f}, max drawdown {r['m1']['pool_maxdd']:.2%}; "
             f"champion drawdown worse than the pool by {r['m1']['dd_worse_by']:.2%} (PIVOT margin {r['dd_margin']:.0%}; at band 0 it would be {r['m1']['dd_worse_by_band0']:.2%}).",
             f"- Paired Sharpe difference, champion at band 0 net vs frictionless pool: {r['m1']['paired']['diff']:+.3f} (90 % interval {r['m1']['paired']['ci_low']:+.3f} .. {r['m1']['paired']['ci_high']:+.3f}, {r['m1']['paired']['n']} months): "
             "not testable on this history (minimum detectable 0.233), shown for the record only.", "",
             "## Not computed in this pass", "", "- The forward Sortino secondary outcome of plan 5.4 (secondary by the plan, never decisive).", "- The 1,000 simulated attrition paths: the pooled expectation replaces them (the pooled mean is deterministic in the rate).", "",
             "## Reading", "", "This page records the outcome of the signed rule; the P3 refactor scope is the owner's decision after reading it.", ""]
    return "\n".join(lines)


def run_m2(run: dict, a, run_dir: Path) -> int:
    import subprocess
    from proyecto3.src.backtest_inference import paired_sharpe_difference
    t0 = time.time()
    p12, b12, _f, _l = build(run, a.boot, a.seed, fwd_months=12, peers="nature", with_dd=True)
    p36, b36, _f, _l = build(run, a.boot, a.seed, fwd_months=36, peers="nature", with_dd=False)
    pnn, bnn, _f, _l = build(run, a.boot, a.seed, fwd_months=12, peers="no_nature", with_dd=True)
    m12, m36, mnn = T.m2_estimate(p12, b12, a.alpha), T.m2_estimate(p36, b36, a.alpha), T.m2_estimate(pnn, bnn, a.alpha)
    exp = pd.read_csv(run_dir / "hysteresis_experiment.csv").set_index("hysteresis_band")
    stats = json.loads((run_dir / "series_stats.json").read_text(encoding="utf-8"))
    ch = exp.loc[0.1]
    pool_dd, pool_sharpe = float(stats["benchmark"]["max_drawdown"]), float(stats["benchmark"]["sharpe"])
    dd_worse, dd_worse_0 = pool_dd - float(ch["max_drawdown"]), pool_dd - float(stats["portfolio"]["max_drawdown"])
    tab = run["table"]
    paired = paired_sharpe_difference(tab["net_turnover_1m"], tab["bench_1m"], cash=tab["cash_ret_1m"], n_boot=2000, mean_block=6.0, ci=0.90, seed=a.seed)
    m1_diff = float(ch["sharpe"]) - pool_sharpe
    ver = T.verdict(m12, m36, mnn, dd_worse, m1_diff=m1_diff)
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    res = {"run_dir": str(run_dir), "git": run["manifest"].get("git_commit"), "harness_commit": commit, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "seed": a.seed, "alpha": a.alpha,
           "dd_margin": T.DD_MARGIN, "m2_12m": m12, "m2_36m": m36, "m2_nonature": mnn, "verdict": ver,
           "m1": {"champion_band010_sharpe": float(ch["sharpe"]), "champion_band010_maxdd": float(ch["max_drawdown"]), "pool_sharpe": pool_sharpe, "pool_maxdd": pool_dd, "dd_worse_by": dd_worse,
                  "dd_worse_by_band0": dd_worse_0, "paired": {k: float(paired[k]) for k in ("n", "diff", "ci_low", "ci_high")}}, "seconds": round(time.time() - t0, 1)}
    out = Path(a.out_dir) if a.out_dir else ROOT / "out" / "reports" / "wp5" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "wp5_m2_verdict.json").write_text(json.dumps(res, indent=2, default=float), encoding="utf-8")
    md = render_m2_md(res)
    (out / "wp5_m2_verdict.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return RC_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--perms", type=int, default=1000)
    ap.add_argument("--reps", type=int, default=500)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--power", type=float, default=0.80)
    ap.add_argument("--min-delta", type=float, default=0.10)
    ap.add_argument("--detect-target", type=float, default=0.80)
    ap.add_argument("--m2", action="store_true", help="run the M2 verdict (Amendment 1); reads the point estimate of the real scores")
    ap.add_argument("--diagnostic", action="store_true", help="run the WP5.1 controls even when the WP5.2 stop rule fired: the result is a DIAGNOSTIC of the M2 machinery, never part of a verdict")
    a = ap.parse_args(argv)
    run_dir = Path(a.run_dir)
    try:
        run = load_run(run_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    if a.m2:
        return run_m2(run, a, run_dir)
    t0 = time.time()
    panel, boot, first_ord, last_ord = build(run, a.boot, a.seed)
    print(f"panel: {len(panel.score):,} unit-months, {panel.n_dates} formation months, {panel.n_families} families, valid outcomes {panel.valid.mean():.1%}; built in {time.time() - t0:.0f}s")
    feas = T.feasibility(panel, boot, alpha=a.alpha, power=a.power, last_nav_ord=last_ord, first_ord=first_ord)
    tab = run["table"]
    m1 = T.sharpe_difference_feasibility(tab["net_turnover_1m"], tab["bench_1m"], tab["cash_ret_1m"], alpha=a.alpha, power=a.power, n_boot=2000, seed=a.seed)
    sens = {str(b): T.sharpe_difference_feasibility(tab["net_turnover_1m"], tab["bench_1m"], tab["cash_ret_1m"], alpha=a.alpha, power=a.power, n_boot=2000, mean_block=float(b),
                                                    seed=a.seed)["mde_delta_sharpe"] for b in (3, 12)}      # the pre-registered block-length sensitivities (FND-0193 precedent)
    testable = bool(m1["mde_delta_sharpe"] <= a.min_delta)
    result = {"feasibility_m1_block_sensitivity": sens, "run_dir": str(run_dir), "git": run["manifest"].get("git_commit"), "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "seed": a.seed, "min_delta": a.min_delta,
              "feasibility": feas, "feasibility_m1": m1, "testable": testable, "controls": None}
    rc = RC_OK
    if testable or a.diagnostic:
        delta_outcome = feas["mde_spread"] * T.TOP_FRAC / T.PLANT_SHARE            # planted funds are half of the top quintile, so the shift is twice the target spread
        t1 = time.time()
        perm = T.permutation_control(panel, boot, a.perms, alpha=a.alpha, seed=a.seed)
        plant = T.planted_control(panel, boot, delta_outcome, a.reps, alpha=a.alpha, target=a.detect_target, seed=a.seed)
        result["controls"] = {"permutation": perm, "planted": plant, "seconds": round(time.time() - t1, 1), "diagnostic": not testable}
        if not (perm["meets_rule_rate_le_alpha"] and plant["meets_target"]):
            rc = RC_CONTROL_FAILED
        elif not testable:
            rc = RC_UNTESTABLE
    else:
        rc = RC_UNTESTABLE
    out = Path(a.out_dir) if a.out_dir else ROOT / "out" / "reports" / "wp5" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "wp5_checks.json").write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    md = render_md(result)
    (out / "wp5_checks.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
