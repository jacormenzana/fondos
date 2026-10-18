# scripts/launch/p3_pit_value.py
# -*- coding: utf-8 -*-
r"""
Model-value study of P3 on a SAVED PIT run (FND-0193, pre-registered in doc/decisiones/PREREG_FND-0193.md). Read-only; with a run folder
that holds its input snapshot (inputs/) it does not even open the database.

    C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_value.py --run-dir out\reports\pit_backtest\<STAMP> \
        [--ext-run-dir <run started 2001-03-31>] [--survivorship-run-dir <run --current-universe-only>]

It first reproduces the run's own series_stats.json (gate of the pre-registration: 1e-6; exit 3 on failure, nothing else is computed),
then builds the model, the baselines and the ablations, evaluates E1-E4, applies the pre-registered decision rules and writes, under
<run>/value_FND-0193/: REPORT.md, verdict.json and one CSV per table. Exit codes: 0 ok, 3 reproduction failed.
"""

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_value as pv
from proyecto3.src.pit_backtest import table_stats

REPRODUCTION_TOL = 1e-6
DEFAULT_LEDGER = _ROOT / "doc" / "decisiones" / "LEDGER_FND-0193.json"

BIAS_BLOCK = """\
## Biases and limits (fixed block, PREREG section 7)
- Universe: retired funds are INCLUDED (FND-0198); the retired share per date is in the run's universe_by_date.csv. Survivorship A/B below when a run was supplied.
- Fund_Nature, Credit_Quality, family and manager are today's values (static-attribute look-ahead, FND-0198 log 540).
- The PIT replicates P2 including its frozen defects; regime inputs use 2-month publication lags.
- Costs: 25 bp per unit of turnover on every strategy's own weights; ongoing charges are assumed to be inside the Morningstar total-return NAV.
- B-CHEAP uses today's Ongoing_Charge (look-ahead) and is secondary. Accepted audit residuals are not modelled (look-ahead). Cash = ECB deposit rate.
- Baselines inherit the share class the model's family de-duplication kept (chosen by its own score): slightly favourable to the baselines.
- About two independent crises fall in the sample; a single-episode benefit is not evidence (FND-0225).
"""


def _fmt(df: pd.DataFrame, digits: int = 4) -> str:
    return df.round(digits).to_string()


def reproduce(study: pv.Study) -> dict:
    saved = json.loads((study.run_dir / "series_stats.json").read_text(encoding="utf-8"))
    W, cash = pv.model_weights(study, band=0.0)
    st = table_stats(pv.table_for(study, W, cash, 25.0))
    worst = 0.0
    for who in ("portfolio", "benchmark"):
        for k in ("n_months", "ann_return", "ann_vol", "sharpe", "max_drawdown"):
            worst = max(worst, abs(st[who][k] - saved[who][k]))
    return {"max_abs_diff": worst, "pass": worst < REPRODUCTION_TOL, "model_sharpe": st["portfolio"]["sharpe"], "pool_sharpe": st["benchmark"]["sharpe"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="FND-0193 model-value study on a saved PIT run (read-only).")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--ext-run-dir", help="PIT run started 2001-03-31 (adds the 'early' sub-period and the extended-window drawdown)")
    p.add_argument("--survivorship-run-dir", help="PIT run with --current-universe-only, same commit as --run-dir")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.add_argument("--bps", type=float, default=25.0)
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--skip-pbo", action="store_true", help="skip E4 (the slowest part)")
    p.add_argument("--reproduce-only", action="store_true")
    args = p.parse_args(argv)

    t0 = time.time()
    run = Path(args.run_dir)
    out = Path(args.out_dir) if args.out_dir else run / "value_FND-0193"
    study = pv.load_study(run)
    rep = reproduce(study)
    print(f"reproduction gate: max |diff| = {rep['max_abs_diff']:.3e} -> {'PASS' if rep['pass'] else 'FAIL'} "
          f"(model Sharpe {rep['model_sharpe']:.4f}, pool {rep['pool_sharpe']:.4f})")
    if not rep["pass"]:
        print("STOP: the run does not reproduce (pre-registered gate). A run saved before FND-0193 has no input snapshot; use a fresh run.")
        return 3
    if args.reproduce_only:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    ledger = pv.load_ledger(args.ledger)
    bps = args.bps

    print("building the model, baselines and ablations ...")
    cat = pv.build_catalogue(study)
    tabs = pv.tables(study, cat, bps)
    print(f"  {len(cat)} strategies built in {time.time() - t0:.0f} s")

    e1 = pv.e1_full(tabs, args.n_boot)
    sub = pv.e1_subperiods(tabs)
    loeo = pv.e1_loeo(tabs)
    reg = pv.e1_regimes(tabs)
    overview = pv.strategy_overview(tabs)
    layers = pv.layer_verdicts(tabs, args.n_boot)

    print("E2 walk-forward band ...")
    wf = pv.walk_forward_band(study, bps=bps)
    wf_tab = wf["table"].loc[f"{pv.WF_FIRST_YEAR}-01-01":]
    wf_vs = {}
    for n in pv.PRIMARY:
        r = pv.paired(pv.series_of(wf_tab), pv.series_of(tabs[n]).loc[f"{pv.WF_FIRST_YEAR}-01-01":], wf_tab["cash_ret_1m"], args.n_boot)
        wf_vs[n] = {"sharpe_WF": r["sharpe_a"], "sharpe_B": r["sharpe_b"], "diff": r["diff"], "ci_low": r["ci_low"], "ci_high": r["ci_high"]}
    wf_vs = pd.DataFrame(wf_vs).T
    wf_stats = pv.stats_row(wf_tab)

    dsr = pv.e3_dsr(tabs[pv.MODEL], ledger)

    pbo = {"pbo": float("nan"), "n_configs": 0}
    if not args.skip_pbo:
        print("E4 PBO (CSCV) ...")
        mat = pv.pbo_matrix(study, cat, bps)
        from proyecto3.src.backtest_inference import cscv_pbo
        pbo = cscv_pbo(mat, 16, cash=tabs[pv.MODEL]["cash_ret_1m"])
        mat.to_csv(out / "e4_pbo_matrix.csv", float_format="%.6g")

    early_ok, ext_mdd, ext_note = None, None, "no extended-window run supplied (verdict is PROVISIONAL on the 'early' sub-period)"
    ext_sub = None
    if args.ext_run_dir:
        print("extended window ...")
        ext = pv.load_study(args.ext_run_dir)
        ext_cat = pv.build_catalogue(ext, ablations=False, a5=False)
        ext_tabs = pv.tables(ext, ext_cat, bps)
        ext_sub = pv.e1_subperiods(ext_tabs, with_early=True)
        early_ok = bool((ext_sub.loc[list(pv.PRIMARY), "early"] > 0).all())
        ext_mdd = pv.stats_row(ext_tabs[pv.MODEL])["max_drawdown"]
        ext_note = f"extended run {Path(args.ext_run_dir).name}: {ext_tabs[pv.MODEL].index.min().date()} .. {ext_tabs[pv.MODEL].index.max().date()}"

    surv = None
    if args.survivorship_run_dir:
        print("survivorship A/B ...")
        s3 = pv.load_study(args.survivorship_run_dir)
        c3 = pv.build_catalogue(s3, ablations=False, a5=False)
        t3 = pv.tables(s3, c3, bps)
        surv = pd.DataFrame({n: {**{f"{k}_all": pv.stats_row(tabs[n])[k] for k in ("sharpe", "max_drawdown")},
                                 **{f"{k}_current_only": pv.stats_row(t3[n])[k] for k in ("sharpe", "max_drawdown")}}
                             for n in (pv.MODEL, "B-POOL", "B-STATIC", "B-LOWVOL", "B-6040")}).T

    worst = min([pv.stats_row(tabs[pv.MODEL])["max_drawdown"], wf_stats["max_drawdown"]] + ([ext_mdd] if ext_mdd is not None else []))
    dd = {"model_max_dd": pv.stats_row(tabs[pv.MODEL])["max_drawdown"], "wf_max_dd": wf_stats["max_drawdown"], "ext_max_dd": ext_mdd,
          "worst_max_dd": worst, "model_prob_36m": float(overview.loc[pv.MODEL, "dd_prob_36m_15pct"]),
          "pool_prob_36m": float(overview.loc["B-POOL", "dd_prob_36m_15pct"])}
    verdict = pv.verdict(e1, sub, loeo, wf_vs, dsr, pbo, dd, early_ok)
    verdict["provisional"] = early_ok is None
    verdict["inputs"] = {"run": run.name, "bps": bps, "ledger_N_scan": ledger["N_scan_upper"], "ext": ext_note, "reproduction": rep}

    for name, df in (("e1_full", e1), ("e1_subperiods", sub), ("e1_loeo", loeo), ("e1_regimes", reg), ("strategies", overview),
                     ("ablations", layers), ("e2_walk_forward_vs_primary", wf_vs), ("e2_band_choices", wf["choices"].to_frame())):
        df.to_csv(out / f"{name}.csv", float_format="%.6g")
    if surv is not None:
        surv.to_csv(out / "survivorship_ab.csv", float_format="%.6g")
    if ext_sub is not None:
        ext_sub.to_csv(out / "extended_subperiods.csv", float_format="%.6g")
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2, default=str), encoding="utf-8")

    lines = [f"# FND-0193 model-value study -- run {run.name}", "",
             f"Pre-registration: doc/decisiones/PREREG_FND-0193.md (tag prereg-fnd0193). {bps:g} bp per unit of turnover on every strategy.",
             f"Reproduction gate: PASS (max |diff| {rep['max_abs_diff']:.2e}). {ext_note}.", "",
             f"## VERDICT: {verdict['outcome']}" + ("  (PROVISIONAL: no extended-window run)" if verdict["provisional"] else ""), ""]
    for k, v in verdict["criteria"].items():
        lines.append(f"- {'PASS' if v['pass'] else 'FAIL'}  {k}")
    lines += ["", "## E1 -- model (M_h10) vs baselines, full window", "", _fmt(e1), "",
              "## E1 -- Sharpe difference by sub-period (M minus baseline)", "", _fmt(sub), "",
              "## E1 -- leave one episode out (M minus baseline)", "", _fmt(loeo), "",
              "## E1 -- by regime (mean monthly net return)", "", _fmt(reg, 5), "",
              "## All strategies (full window)", "", _fmt(overview), "",
              f"## E2 -- walk-forward band (from {pv.WF_FIRST_YEAR}); chosen bands", "", wf["choices"].to_string(), "",
              f"WF series: {wf_stats}", "", _fmt(wf_vs), "",
              "## E3 -- deflated Sharpe with the trial ledger", "", json.dumps(dsr, indent=2), "",
              "## E4 -- probability of backtest overfitting (CSCV)", "", json.dumps(pbo, indent=2), "",
              "## Drawdown breaker", "", json.dumps(dd, indent=2, default=str), "",
              "## Ablations: does each layer earn its place?", "", _fmt(layers), ""]
    if surv is not None:
        lines += ["## Survivorship A/B (all funds vs current universe only)", "", _fmt(surv), ""]
    if ext_sub is not None:
        lines += ["## Extended window sub-periods", "", _fmt(ext_sub), ""]
    lines += [BIAS_BLOCK]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:16]))
    print(f"done in {time.time() - t0:.0f} s; artifacts in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
