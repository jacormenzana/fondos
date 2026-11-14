# scripts/launch/p3_pit_value.py
# -*- coding: utf-8 -*-
r"""
Model-value study of P3 on a SAVED PIT run (FND-0193, pre-registered in doc/decisiones/PREREG_FND-0193.md, amendments 1 and 2). Read-only;
with a run folder that holds its input snapshot (inputs/) it does not even open the database. Launch it under the memory guard:

    powershell -NoProfile -File C:\desarrollo\fondos\out\audit\guarded_run.ps1 -CommandLine "C:\Users\jacor\.conda\envs\des\python.exe -u -X utf8 scripts\launch\p3_pit_value.py --run-dir <R1> --snapped-run-dir <R4> --ext-run-dir <R2> --survivorship-run-dir <R3>"

Order of the checks (a failure stops or flags the verdict, never silently passes):
  1. the trial ledger on disk equals the one committed at tag prereg-fnd0193 (exit 4 otherwise);
  2. each run reproduces its own series_stats.json (1e-6; exit 3 otherwise);
  3. the verdict is evaluated on R1 (raw dates) and, with --snapped-run-dir, on R4 (month-end snapped): ALIGNMENT GATE (amendment 2):
     identical outcome, Holm decisions and Sharpe rank order, |dSharpe| <= 0.01; otherwise the verdict is printed as INVALID;
  4. with --exclude-isins FILE the verdict is re-evaluated without those funds (static-attribute sensitivity; flagged, not decisive);
  5. every run must have been made under the same P2 bundle flags as R1 (manifest.json; FND-0300 NAV month-end labels), exit 5 otherwise;
     --prior-report-dir shows an earlier evaluation this one supersedes (amendment 4).
Writes, under <R1>/value_FND-0193/: REPORT.md, verdict.json and one CSV per table. Exit codes: 0 ok, 3 reproduction failed, 4 ledger lock,
5 runs under different P2 bundle flags.
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
- Fund_Nature, Credit_Quality, family and manager are today's values (static-attribute look-ahead, FND-0198 log 540); see the attribute-sensitivity section when it was run.
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


def run_flags(run_dir) -> dict:
    """The P2 bundle flags a PIT run was made under (manifest.json); {} when the manifest has none. A flag that did not exist when the run
    was made counts as OFF, so a run made before FND-0300 compares equal to one made with NAV_MONTH_END_ALIGN_ENABLED off."""
    m = Path(run_dir) / "manifest.json"
    flags = json.loads(m.read_text(encoding="utf-8")).get("p2_bundle_flags", {}) if m.exists() else {}
    return {k: bool(v) for k, v in flags.items() if v}


def load_prior(report_dir) -> dict:
    """Outcome, validity and strategy table of an earlier evaluation folder (amendment 4: reported next to the evaluation that supersedes it)."""
    d = Path(report_dir)
    v = json.loads((d / "verdict.json").read_text(encoding="utf-8"))
    return {"outcome": v.get("outcome"), "validity": v.get("validity"), "strategies": pd.read_csv(d / "strategies.csv", index_col=0)}


def _check_reproduction(study: pv.Study, label: str) -> dict:
    rep = reproduce(study)
    print(f"reproduction gate {label}: max |diff| = {rep['max_abs_diff']:.3e} -> {'PASS' if rep['pass'] else 'FAIL'} "
          f"(model Sharpe {rep['model_sharpe']:.4f}, pool {rep['pool_sharpe']:.4f})")
    return rep


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="FND-0193 model-value study on a saved PIT run (read-only).")
    p.add_argument("--run-dir", required=True, help="R1: the PIT run on raw NAV dates (with --keep-detail)")
    p.add_argument("--snapped-run-dir", help="R4: the same run with --snap-nav-month-end; enables the ALIGNMENT GATE (amendment 2)")
    p.add_argument("--ext-run-dir", help="R2: PIT run started 2001-03-31 (adds the 'early' sub-period and the extended-window drawdown)")
    p.add_argument("--survivorship-run-dir", help="R3: PIT run with --current-universe-only, same commit as --run-dir")
    p.add_argument("--exclude-isins", action="append", default=[],
                   help="text file, one ISIN per line ('#' comments allowed): re-evaluate the verdict without those funds (static-attribute "
                        "sensitivity). Repeatable; each file is one sensitivity run, labelled by its file name")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.add_argument("--bps", type=float, default=25.0)
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--skip-pbo", action="store_true", help="skip E4 (the slowest part)")
    p.add_argument("--reproduce-only", action="store_true")
    p.add_argument("--prior-report-dir", help="the value_FND-0193 folder of an earlier evaluation that this one supersedes (amendment 4): its "
                                              "outcome, validity and strategy table are shown next to this one")
    args = p.parse_args(argv)

    t0 = time.time()
    run = Path(args.run_dir)
    out = Path(args.out_dir) if args.out_dir else run / "value_FND-0193"
    try:
        ledger = pv.load_ledger(args.ledger)                      # verifies the lock at tag prereg-fnd0193
    except pv.LedgerLockError as e:
        print(f"STOP: {e}")
        return 4
    flags = run_flags(run)
    mismatch = {Path(d).name: f for d in (args.snapped_run_dir, args.ext_run_dir, args.survivorship_run_dir) if d
                for f in [run_flags(d)] if f != flags}
    if mismatch:                                                  # FND-0300: every run of one evaluation under the same P2 conventions
        print(f"STOP: runs made under different P2 bundle flags than {run.name} ({flags}): {mismatch}")
        return 5
    study = pv.load_study(run)
    rep = _check_reproduction(study, "R1")
    snapped = pv.load_study(args.snapped_run_dir) if args.snapped_run_dir else None
    rep_snap = _check_reproduction(snapped, "R4") if snapped else None
    if not rep["pass"] or (rep_snap and not rep_snap["pass"]):
        print("STOP: a run does not reproduce (pre-registered gate). A run saved before FND-0193 has no input snapshot; use a fresh run.")
        return 3
    if args.reproduce_only:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    bps = args.bps

    # the extended window (R2) supplies the 'early' sub-period and the extended drawdown to every evaluation
    early_ok, ext_mdd, ext_sub = None, None, None
    ext_note = "no extended-window run supplied (verdict is PROVISIONAL on the 'early' sub-period)"
    if args.ext_run_dir:
        print("extended window ...")
        ext = pv.load_study(args.ext_run_dir)
        ext_tabs = pv.tables(ext, pv.build_catalogue(ext, ablations=False, a5=False), bps)
        ext_sub = pv.e1_subperiods(ext_tabs, with_early=True)
        early_ok = bool((ext_sub.loc[list(pv.PRIMARY), "early"] > 0).all())
        ext_mdd = pv.stats_row(ext_tabs[pv.MODEL])["max_drawdown"]
        ext_note = f"extended run {Path(args.ext_run_dir).name}: {ext_tabs[pv.MODEL].index.min().date()} .. {ext_tabs[pv.MODEL].index.max().date()}"

    print("evaluating R1 (raw dates) ...")
    res = pv.evaluate_study(study, ledger, bps, args.n_boot, args.skip_pbo, early_ok, ext_mdd)
    tabs, verdict = res["tabs"], res["verdict"]

    gate = None
    if snapped is not None:
        print("evaluating R4 (month-end snapped) ...")
        res4 = pv.evaluate_study(snapped, ledger, bps, args.n_boot, args.skip_pbo, early_ok, ext_mdd)
        gate = pv.alignment_gate(res["gate_summary"], res4["gate_summary"])
        pd.DataFrame({"raw": res["gate_summary"]["sharpe"], "snapped": res4["gate_summary"]["sharpe"],
                      "delta": gate["deltas"]}).to_csv(out / "alignment_gate_sharpe.csv", float_format="%.6g")
    attrs = []                                          # one entry per --exclude-isins file
    for fpath in args.exclude_isins:
        label = Path(fpath).stem
        print(f"static-attribute sensitivity [{label}]: re-evaluating without the unstable funds ...")
        drop = [x.split("#", 1)[0].strip() for x in Path(fpath).read_text(encoding="utf-8").splitlines()]
        drop = [x for x in drop if x]
        res_x = pv.evaluate_study(pv.load_study(run, exclude_isins=drop), ledger, bps, args.n_boot, args.skip_pbo, early_ok, ext_mdd)
        a = {"label": label, "n_excluded": len(drop), "outcome_all": verdict["outcome"], "outcome_without": res_x["verdict"]["outcome"],
             "sensitive": verdict["outcome"] != res_x["verdict"]["outcome"],
             "sharpe_all": res["gate_summary"]["sharpe"], "sharpe_without": res_x["gate_summary"]["sharpe"]}
        pd.DataFrame({"all": a["sharpe_all"], "without_unstable": a["sharpe_without"]}).to_csv(out / f"attribute_sensitivity_{label}.csv", float_format="%.6g")
        attrs.append(a)

    surv = None
    if args.survivorship_run_dir:
        print("survivorship A/B ...")
        s3 = pv.load_study(args.survivorship_run_dir)
        t3 = pv.tables(s3, pv.build_catalogue(s3, ablations=False, a5=False), bps)
        surv = pd.DataFrame({n: {**{f"{k}_all": pv.stats_row(tabs[n])[k] for k in ("sharpe", "max_drawdown")},
                                 **{f"{k}_current_only": pv.stats_row(t3[n])[k] for k in ("sharpe", "max_drawdown")}}
                             for n in (pv.MODEL, "B-POOL", "B-STATIC", "B-LOWVOL", "B-6040")}).T

    if gate is None:
        validity = "ALIGNMENT GATE NOT RUN (no --snapped-run-dir): the verdict is PROVISIONAL"
    elif gate["pass"]:
        validity = f"VALID: alignment gate passed (max |dSharpe| {gate['max_abs_delta_core']:.4f} <= {gate['tol']}; strict tier 0.001 {'met' if gate['strict_ok'] else 'not met'})"
    else:
        validity = (f"INVALID — alignment-sensitive: failed {gate['failed']} (outcome raw {gate['raw_outcome']} vs snapped {gate['snap_outcome']}; "
                    f"max |dSharpe| {gate['max_abs_delta_core']:.4f}). Fix the pipeline alignment before any go-live.")
    verdict["validity"] = validity
    verdict["alignment_gate"] = gate
    verdict["attribute_sensitivity"] = [{k: v for k, v in a.items() if not k.startswith("sharpe")} for a in attrs]
    if any(a["sensitive"] for a in attrs):
        verdict["validity"] += " | ATTRIBUTE-SENSITIVE (the owner decides): " + ", ".join(a["label"] for a in attrs if a["sensitive"])
    verdict["inputs"] = {"run": run.name, "snapped_run": Path(args.snapped_run_dir).name if args.snapped_run_dir else None, "bps": bps,
                         "ledger_N_scan": ledger["N_scan_upper"], "ledger_locked": True, "ext": ext_note, "reproduction": rep,
                         "p2_bundle_flags": flags}
    prior = load_prior(args.prior_report_dir) if args.prior_report_dir else None
    if prior is not None:
        verdict["supersedes"] = {"report": str(args.prior_report_dir), "outcome": prior["outcome"], "validity": prior["validity"]}

    for name, df in (("e1_full", res["e1"]), ("e1_subperiods", res["sub"]), ("e1_loeo", res["loeo"]), ("e1_regimes", res["reg"]),
                     ("strategies", res["overview"]), ("ablations", res["layers"]), ("e2_walk_forward_vs_primary", res["wf_vs"]),
                     ("e2_band_choices", res["wf"]["choices"].to_frame())):
        df.to_csv(out / f"{name}.csv", float_format="%.6g")
    if res["pbo_matrix"] is not None:
        res["pbo_matrix"].to_csv(out / "e4_pbo_matrix.csv", float_format="%.6g")
    if surv is not None:
        surv.to_csv(out / "survivorship_ab.csv", float_format="%.6g")
    if ext_sub is not None:
        ext_sub.to_csv(out / "extended_subperiods.csv", float_format="%.6g")
    (out / "verdict.json").write_text(json.dumps(verdict, indent=2, default=str), encoding="utf-8")

    lines = [f"# FND-0193 model-value study -- run {run.name}", "",
             f"Pre-registration: doc/decisiones/PREREG_FND-0193.md (tag prereg-fnd0193, amendments 1-4). {bps:g} bp per unit of turnover on every strategy.",
             f"Ledger locked at the tag. Reproduction gate: PASS (max |diff| {rep['max_abs_diff']:.2e}). {ext_note}.",
             f"NAV dates: {'month-end labels (NAV_MONTH_END_ALIGN_ENABLED, FND-0300)' if flags.get('NAV_MONTH_END_ALIGN_ENABLED') else 'raw stamps'}; "
             f"all runs under the same P2 bundle flags.", "",
             f"## VERDICT: {verdict['outcome']}", f"**Validity: {validity}**", ""]
    for k, v in verdict["criteria"].items():
        lines.append(f"- {'PASS' if v['pass'] else 'FAIL'}  {k}")
    if prior is not None:
        both = pd.DataFrame({"sharpe_prior": prior["strategies"]["sharpe"], "sharpe_now": res["overview"]["sharpe"],
                             "max_dd_prior": prior["strategies"]["max_drawdown"], "max_dd_now": res["overview"]["max_drawdown"]})
        both["sharpe_delta"] = both["sharpe_now"] - both["sharpe_prior"]
        both.to_csv(out / "supersedes_prior_evaluation.csv", float_format="%.6g")
        lines += ["", f"## Supersedes the earlier evaluation ({args.prior_report_dir})", "",
                  f"Earlier: {prior['outcome']} -- {prior['validity']}", f"This one (of record, amendment 4): {verdict['outcome']} -- {validity}", "",
                  _fmt(both)]
    if gate is not None:
        lines += ["", "## Alignment gate (R1 raw dates vs R4 month-end snapped)", "",
                  json.dumps({k: gate[k] for k in ("pass", "failed", "checks", "max_abs_delta_core", "strict_ok", "rank_raw", "rank_snap")}, indent=2, default=str),
                  "", _fmt(pd.DataFrame({"raw": res["gate_summary"]["sharpe"], "snapped": res4["gate_summary"]["sharpe"], "delta": gate["deltas"]}))]
    for a in attrs:
        lines += ["", f"## Static-attribute sensitivity [{a['label']}] ({a['n_excluded']} unstable funds excluded)", "",
                  f"Outcome with all funds: {a['outcome_all']} | without the unstable funds: {a['outcome_without']} | "
                  f"{'ATTRIBUTE-SENSITIVE (the owner decides)' if a['sensitive'] else 'not sensitive'}", "",
                  _fmt(pd.DataFrame({"all": a["sharpe_all"], "without_unstable": a["sharpe_without"]}))]
    lines += ["", "## E1 -- model (M_h10) vs baselines, full window", "", _fmt(res["e1"]), "",
              "## E1 -- Sharpe difference by sub-period (M minus baseline)", "", _fmt(res["sub"]), "",
              "## E1 -- leave one episode out (M minus baseline)", "", _fmt(res["loeo"]), "",
              "## E1 -- by regime (mean monthly net return)", "", _fmt(res["reg"], 5), "",
              "## All strategies (full window)", "", _fmt(res["overview"]), "",
              f"## E2 -- walk-forward band (from {pv.WF_FIRST_YEAR}); chosen bands", "", res["wf"]["choices"].to_string(), "",
              f"WF series: {res['wf_stats']}", "", _fmt(res["wf_vs"]), "",
              "## E3 -- deflated Sharpe with the trial ledger", "", json.dumps(res["dsr"], indent=2), "",
              "## E4 -- probability of backtest overfitting (CSCV)", "", json.dumps(res["pbo"], indent=2), "",
              "## Drawdown breaker", "", json.dumps(res["dd"], indent=2, default=str), "",
              "## Ablations: does each layer earn its place?", "", _fmt(res["layers"]), ""]
    if surv is not None:
        lines += ["## Survivorship A/B (all funds vs current universe only)", "", _fmt(surv), ""]
    if ext_sub is not None:
        lines += ["## Extended window sub-periods", "", _fmt(ext_sub), ""]
    lines += [BIAS_BLOCK]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:18]))
    print(f"done in {time.time() - t0:.0f} s; artifacts in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
