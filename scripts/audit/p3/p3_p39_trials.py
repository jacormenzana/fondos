# scripts/audit/p3/p3_p39_trials.py
# -*- coding: utf-8 -*-
r"""
Package L, P3-9: the layer-3 study of doc/decisiones/PREREG_P3_9_LAYER3.md, the BUILD half (arms and the identity gate). READ-ONLY on the database (daily NAV for the short gates only) and nothing
is written outside the output folder. The IPC series, the entry / exit fees and the geography come ONLY from a frozen, hash-verified snapshot: by default the one of the P3-7 run, so the two
studies read the same data (section 3 of the preregistration).

    py -X utf8 scripts\audit\p3\p3_p39_trials.py --run-dir out\reports\pit_backtest\20261119_202509 --stage build --trial T0_baseline --stamp <S>
    ... --stage build --trial AC_base --stamp <S>            the base arm (band 0.10 + the fee penalty, H 0.58 y) in THIS build
    ... --stage identity --stamp <S>                         the two-part identity gate -> identity_p39.json
    ... --stage build --trial all --stamp <S>                the six trial arms: REFUSED until identity_p39.json says both parts passed
    ... --stage report --stamp <S>                           the adoption table (PREREG_P3_9 section 5) -> adoption_table.csv, REPORT.md

Identity gate (section 8 (2)): (a) the all-off baseline T0 reproduces the sealed run exactly, with the two new switches among those that must be OFF; (b) the base arm AC re-run here reproduces the
AC series of the P3-7 run exactly (gross return and turnover). The arms: T0_baseline, AC_base and N5, N0, E5, E0, NE5, NE0 on top of AC (N = nature-bonus scale k 0.5 / 0, E = chasing control f 0.5 / 0).
The report stage (`pit_p39_eval`) was written and tested before any trial result was read. `--sample-per-nature N` is a smoke test of the plumbing, never a result.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p3_p37_trials as P  # noqa: E402  (arms machinery, the build of one arm, the identity gate: one definition)
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402
from proyecto3.src import pit_p39_eval as W  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402

HOLM_TRIALS = 6                   # PREREG_P3_9 section 5: the Holm family of rule 2
HOLM_LEVEL = 0.05 / HOLM_TRIALS
PENALTY_H_YEARS = P.PENALTY_H_YEARS
AC_TOL_GROSS, AC_TOL_TURNOVER = 1e-9, 1e-9
P37_DIR = _ROOT / "out" / "reports" / "p3_p37_trials" / "20261121_p37"
IDENTITY_FILE = "identity_p39.json"
NL = chr(10)

_AC = dict(band=0.10, cls=False, pen=True)
ARMS = {
    "T0_baseline": dict(band=0.0, cls=False, pen=False),
    "AC_base": dict(_AC),
    "N5": {**_AC, "l5": 0.5}, "N0": {**_AC, "l5": 0.0},
    "E5": {**_AC, "l6": 0.5}, "E0": {**_AC, "l6": 0.0},
    "NE5": {**_AC, "l5": 0.5, "l6": 0.5}, "NE0": {**_AC, "l5": 0.0, "l6": 0.0},
}
BASE_ARMS = ("T0_baseline", "AC_base")
TRIALS = [n for n in ARMS if n not in BASE_ARMS]
assert len(TRIALS) == HOLM_TRIALS


def levers_of(spec: dict) -> "RL.LeverConfig":
    return RL.LeverConfig(l4_h_years=PENALTY_H_YEARS if spec["pen"] else None, l5_nature_k=spec.get("l5"), l6_chase_f=spec.get("l6"))


def default_switches39() -> dict:
    """The global switches that must be at their shipped value (OFF) for a baseline to mean what it says: those of P3-7 plus the two layer-3 levers."""
    import shared.config as C
    return {**P.default_switches(), **{n: bool(getattr(C, n)) for n in ("P3_L5_NATURE_SCALE_ENABLED", "P3_L6_CHASE_CONTROL_ENABLED")}}


def build_parser():
    p = argparse.ArgumentParser(description="Package L, P3-9: layer 3, build and identity gate (read-only).")
    p.add_argument("--run-dir", required=True, help="the sealed PIT run (20261119_202509)")
    p.add_argument("--stage", required=True, choices=("build", "identity", "report"))
    p.add_argument("--trial", default="all")
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_p39_trials"))
    p.add_argument("--cache-dir", default=str(_ROOT / "proyecto3" / "cache" / "pit"))
    p.add_argument("--snapshot-dir", default=str(P37_DIR / "snapshot"), help="the frozen inputs (default: the P3-7 snapshot, hash-verified on every load)")
    p.add_argument("--p37-dir", default=str(P37_DIR), help="the P3-7 run folder whose AC series the base arm must reproduce")
    p.add_argument("--sample-per-nature", type=int, default=0, help="smoke test of the plumbing on N funds per nature (NEVER a result)")
    p.add_argument("--no-short-gates", action="store_true", help="skip the daily-NAV short gates (smoke tests only)")
    p.add_argument("--stamp", required=True, help="output folder name")
    return p


# ============================================================================================================================================================================
# the identity gate (two parts)
# ============================================================================================================================================================================
def ac_identity(series: pd.DataFrame, ref: pd.DataFrame) -> dict:
    """The base arm re-run here against the AC series of the P3-7 run: the same months, the same NaN pattern, gross return and turnover equal within 1e-9. Nothing is rounded to make it pass."""
    same_dates = bool(series.index.equals(ref.index))
    if not same_dates:
        return {"ok": False, "checks": {"same_dates": False, "gross_identical": False, "turnover_identical": False}, "max_abs_gross_diff": float("inf"), "max_abs_turnover_diff": float("inf")}
    nan_ok = bool((series["gross"].isna().to_numpy() == ref["gross"].isna().to_numpy()).all() and (series["turnover"].isna().to_numpy() == ref["turnover"].isna().to_numpy()).all())
    gd, td = (series["gross"] - ref["gross"]).abs(), (series["turnover"] - ref["turnover"]).abs()
    g = float(gd.max()) if gd.notna().any() else float("inf")
    t = float(td.max()) if td.notna().any() else float("inf")
    checks = {"same_dates": same_dates, "same_nan_pattern": nan_ok, "gross_identical": bool(g <= AC_TOL_GROSS), "turnover_identical": bool(t <= AC_TOL_TURNOVER)}
    return {"ok": bool(all(checks.values())), "checks": checks, "max_abs_gross_diff": g, "max_abs_turnover_diff": t}


def combine_identity(t0: dict, ac: dict) -> dict:
    return {"ok": bool(t0.get("ok") and ac.get("ok")), "t0": t0, "ac": ac}


def gate_open39(base_dir: Path) -> "tuple[bool, str]":
    f = Path(base_dir) / IDENTITY_FILE
    if not f.exists():
        return False, f"{IDENTITY_FILE} is missing: build T0_baseline and AC_base, then run --stage identity"
    g = json.loads(f.read_text(encoding="utf-8"))
    ok = bool(g.get("ok") and g.get("t0", {}).get("ok") and g.get("ac", {}).get("ok"))      # `ok` alone is not trusted: both parts are read
    return ok, ("identity gate passed (T0 against the sealed run, AC against P3-7)" if ok else f"identity gate FAILED: t0 {g.get('t0', {}).get('ok')}, ac {g.get('ac', {}).get('ok')}")


def run_identity(base_dir: Path, run_dir: Path, p37_dir: Path) -> dict:
    """Both parts from the SAVED series of the two base arms; writes identity_p39.json. Deterministic, read-only on the database."""
    t0_dir, ac_dir = base_dir / "T0_baseline", base_dir / "AC_base"
    series0 = pd.read_csv(t0_dir / "series.csv", parse_dates=["date"]).set_index("date")
    meta0 = json.loads((t0_dir / "meta.json").read_text(encoding="utf-8"))
    table = pd.read_csv(Path(run_dir) / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
    t0 = P.identity_gate(meta0, series0, table, default_switches39())
    ac_new = pd.read_csv(ac_dir / "series.csv", parse_dates=["date"]).set_index("date")
    ac_ref = pd.read_csv(Path(p37_dir) / "AC" / "series.csv", parse_dates=["date"]).set_index("date")
    gate = combine_identity(t0, ac_identity(ac_new, ac_ref))
    (base_dir / IDENTITY_FILE).write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
    return gate


def run_report(args, base_dir: Path) -> int:
    """The adoption table: every arm against the base arm AC of the SAME build (rules 1-4 of section 5), written to adoption_table.csv and REPORT.md. Refused (5) before the identity gate passed and (6)
    without the baseline and the base arm. A base arm is a reference row (never a candidate)."""
    ok, why = gate_open39(base_dir)
    if not ok:
        print(f"[GATE] report refused: {why}")
        return 5
    runs = {n: P._load_arm(base_dir / n) for n in ARMS if (base_dir / n / "meta.json").exists()}
    if "AC_base" not in runs or "T0_baseline" not in runs:
        print("report needs T0_baseline and AC_base built in this folder")
        return 6
    base = runs["AC_base"]
    rows = []
    for n, t in runs.items():
        kw = dict(episodes=P.T.EPISODES, design=P.T.DESIGN, crisis_windows=P.T.CRISIS_WINDOWS, holm_level=HOLM_LEVEL)
        if n in BASE_ARMS:
            r = P.V.evaluate_arm("T0_baseline", t, base, W.band_target(t["series"].index), None, **kw)              # the reference rows: drawdowns and returns, no rule
            r["trial"] = n
        else:
            r = W.evaluate_trial(n, t, base, **kw)
        r["integrity_ok"] = t["meta"].get("integrity_ok")
        r["n_swapped_rows"] = t["meta"].get("score_integrity", {}).get("n_swapped_rows")
        r["snapshot_id"] = str(t["meta"].get("snapshot_id", ""))[:12]
        rows.append(r)
    res = pd.DataFrame(rows).set_index("trial")
    res.to_csv(base_dir / "adoption_table.csv")
    ident = json.loads((base_dir / IDENTITY_FILE).read_text(encoding="utf-8"))

    def fmt(cols, nd=4):
        return res[[c for c in cols if c in res.columns]].round(nd).to_string()
    lines = [f"# P3-9 trials: adoption table (PREREG_P3_9 section 5; Holm family {HOLM_TRIALS}, level {HOLM_LEVEL:.5f}; every arm against the base arm AC of this build)", "",
             f"Identity gate: T0 against the sealed run {'PASSED' if ident['t0'].get('ok') else 'FAILED'}; AC against the P3-7 AC series {'PASSED' if ident['ac'].get('ok') else 'FAILED'}. "
             f"Snapshot ids of the arms: {sorted(set(res['snapshot_id']))}. History yields a CANDIDATE for the forward shadow, never an adoption (R7). Rule 3 is read at the 25 bp regime of the cap.", "",
             "## 1. Returns, drawdowns and turnover (annualised; `fee` = the stated fees fund by fund, `25bp` = the flat cross-check)", "", "```",
             fmt(["ann_fee", "ann_25bp", "maxdd_fee", "maxdd_25bp", "stressed_worst_25bp", "stressed_worst_25bp_baseline", "turnover", "cost_drag_pp", "holding_months"]), "```", "",
             "## 2. Rule 1: the 15 % cap (unstressed at 25 bp; stressed relative to the base, 0.5 pp)", "", "```",
             fmt(["dd_cap_ok", "stress_rel_ok", "c1"] + [f"{p}_{k}_25bp" for k in P.T.EPISODES for p in ("dd", "dds")]), "```", "",
             "## 3. Rule 2: non-inferiority of the net return at the stated fees (lower bound at the Holm level above -0.10 pp)", "", "```",
             fmt(["ret_mean_pp", "ret_se_pp", "ret_lb_pp", "ret_pass", "c2", "ret25_mean_pp", "ret25_lb_pp"]), "```", "",
             "## 4. Rule 3: the risk benefit (at least 1.0 pp) and rule 4: collateral (band 3.2 %, 0.35 pp), then the verdict", "", "```",
             fmt(["dd_gain_pp", "stress_gain_pp", "c_benefit", "noncrisis_degradation_pp", "share36_variant", "share36_baseline", "c3", "candidate", "integrity_ok", "n_swapped_rows"]), "```", ""]
    (base_dir / "REPORT.md").write_text(NL.join(lines), encoding="utf-8")
    print(NL.join(lines))
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run = Path(args.run_dir)
    if run.name == RUN_STAMP:
        try:
            sealed = verify_seal(run)
        except SealMismatch as exc:
            print(f"[SEAL] REFUSED: {exc}")
            return 2
        print(f"[SEAL] {len(sealed)} artifacts verified against the baseline seal of {RUN_STAMP}")
    base_dir = Path(args.out_root) / args.stamp
    if not (Path(args.snapshot_dir) / SNAP.MANIFEST).exists():
        print(f"no frozen snapshot in {args.snapshot_dir}: the study reads the P3-7 snapshot (or one frozen by p3_p37_trials --stage snapshot)")
        return 3
    if args.stage == "build":
        names = list(ARMS) if args.trial == "all" else [args.trial]
        for n in names:
            if n not in ARMS:
                print(f"unknown trial {n!r}; choose from {list(ARMS)}")
                return 4
            if n not in BASE_ARMS and not args.sample_per_nature:
                ok, why = gate_open39(base_dir)
                if not ok:
                    print(f"[GATE] {n} refused: {why}")
                    return 5
            if (base_dir / n / "meta.json").exists():
                print(f"[{n}] already built in {base_dir / n}: skipped")
                continue
            P.build_arm(args, n, ARMS[n], base_dir / n, base_dir, levers_fn=levers_of, identity_switches=default_switches39())
        return 0
    if args.stage == "report":
        return run_report(args, base_dir)
    g = run_identity(base_dir, run, Path(args.p37_dir))
    print(f"[IDENTITY GATE] {'PASSED' if g['ok'] else 'FAILED'}: T0 {g['t0']['checks']} | AC {g['ac']['checks']} | AC max |gross diff| {g['ac']['max_abs_gross_diff']:.3e}, max |turnover diff| {g['ac']['max_abs_turnover_diff']:.3e}")
    return 0 if g["ok"] else 7


if __name__ == "__main__":
    sys.exit(main())
