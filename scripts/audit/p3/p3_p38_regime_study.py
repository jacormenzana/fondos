# scripts/audit/p3/p3_p38_regime_study.py
# -*- coding: utf-8 -*-
r"""
Package L, P3-8: the CPI input of the regime classifier (doc/decisiones/PREREG_P3_8_CPI_REGIME_INPUT.md). READ-ONLY on the database (the CPI index levels and the classifier's inputs at the snapshot, the daily NAV
of the short gates at the build) and nothing written outside the output folder. NOTHING in production is touched: the regime histories of the four arms are built from a private copy of the classifier's macro frame.

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_p38_regime_study.py --run-dir out\reports\pit_backtest\20261119_202509 --stage snapshot --stamp <S>
    ... --stage build --trial all --stamp <S>      R0 (today) first and its IDENTITY GATE against the sealed run, then R1-R3 (refused until the gate passed)
    ... --stage report --stamp <S>                 the classification of the three trials (PREREG section 3)

The four arms differ ONLY in the regime history: the scores do not depend on the regime (every multiplier is 1.0), so the build re-runs the whole PIT chain with each history and checks that the score rows are
unchanged. The IPC + M3 target is not recomputed (decoupled, rule 2 of the owner's approval).
"""
import argparse
import dataclasses
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p3_p37_trials as P37  # noqa: E402  (the identity gate, the score integrity check and the gate file: one definition)
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402
from proyecto3.src import pit_p38_regime as R  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402
from proyecto3.src.pit_backtest import monthly_turnover, portfolio_returns, prepare_pit_backtest  # noqa: E402
from proyecto3.src.pit_cache import ParquetCache  # noqa: E402
from proyecto3.src.pit_cost_check import fee_cost_series  # noqa: E402
from proyecto3.src.pit_inputs import iter_daily_chunks  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_run import compute_pit_scores  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402
from shared.db import get_connection  # noqa: E402

ARMS = {"R0": R.REFERENCE, **R.TRIALS}                                           # arm -> (aggregator, lag)
P37_SNAPSHOT = _ROOT / "out" / "reports" / "p3_p37_trials" / "20261121_p37" / "snapshot"      # IPC / fees of the P3-7 study, frozen and hash-verified: the same inputs as the baseline it re-runs
BPS = 25.0
SPEC = {"band": 0.0, "cls": False, "pen": False}                                  # no cost lever in this study: the integrity check of an arm without a score lever


def build_parser():
    p = argparse.ArgumentParser(description="Package L, P3-8: the CPI input of the regime classifier (read-only).")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--stage", required=True, choices=("snapshot", "build", "report"))
    p.add_argument("--trial", default="all")
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_p38_regime"))
    p.add_argument("--cache-dir", default=str(_ROOT / "proyecto3" / "cache" / "pit"))
    p.add_argument("--stamp", required=True)
    return p


# ============================================================================================================================================================================
# snapshot: the four regime histories, built once from ONE read of the classifier's inputs
# ============================================================================================================================================================================
def build_histories(conn, sealed_hist: pd.DataFrame) -> dict:
    from proyecto3.src.regime_classifier import RegimeClassifier
    from shared.config import REGIME_PUBLICATION_LAG_MONTHS
    rows = conn.execute("SELECT date, geography, value FROM series_macro WHERE indicator = 'ipc_index' AND geography IN ('ES','EU') ORDER BY date").fetchall()
    df = pd.DataFrame(rows, columns=["date", "geography", "value"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    levels = df.pivot_table(index="date", columns="geography", values="value", aggfunc="last").astype(float)[["ES", "EU"]]
    clf = RegimeClassifier(conn, publication_lags=REGIME_PUBLICATION_LAG_MONTHS)
    base = clf.classify_historical().sort_index()
    macro0 = clf._macro.copy()
    # the reconstruction of the reference input must equal the classifier's own series, month by month (otherwise the variants would not be comparable with it)
    rec = R.cpi_input(levels, *R.REFERENCE).reindex(macro0.index)
    own = macro0["ipc_yoy_avg"]
    if not (rec.notna().to_numpy() == own.notna().to_numpy()).all() or (rec - own).abs().max() > 1e-12:
        raise RuntimeError("the reconstructed reference CPI input differs from the classifier's ipc_yoy_avg: refusing to build variants")
    hists = {"R0": base}
    for name, (agg, lag) in R.TRIALS.items():
        alt = macro0.copy()
        alt["ipc_yoy_avg"] = R.cpi_input(levels, agg, lag).reindex(alt.index)                # only this column; the target is not built from this copy
        clf._macro = alt
        clf._historical_cache = None                                                          # classify_historical() caches its first result: without this the override is silently ignored
        hists[name] = clf.classify_historical().sort_index()
        if R.label_summary(base, hists[name])["n_changed"] == 0:
            raise RuntimeError(f"{name}: no regime label changed: the override did not reach the classifier")
    idx = sealed_hist.index.intersection(base.index)
    w = ["weight_defensive", "weight_balanced", "weight_dynamic"]
    if len(idx) != len(sealed_hist) or (sealed_hist.loc[idx, "regime"] != base.loc[idx, "regime"]).any() or ((sealed_hist.loc[idx, w] - base.loc[idx, w]).abs() > 1e-12).any().any():
        raise RuntimeError("R0 differs from the regime history sealed in the PIT run: the identity gate could not be exact")
    return {"levels.parquet": levels, **{f"hist_{k}.parquet": v for k, v in hists.items()}}


# ============================================================================================================================================================================
# build: one arm
# ============================================================================================================================================================================
def build_arm(args, name: str, hist_k: pd.DataFrame, out: Path, base_dir: Path) -> None:
    t0 = time.perf_counter()
    run = Path(args.run_dir)
    pit0, inputs, _hist0, at = load_saved_run(run, None)
    scores0 = pit0.scores
    ipc, fees_df, _geo = SNAP.load_snapshot(P37_SNAPSHOT)
    entry, exit_ = fees_df["Entry_Fee_Pct"], fees_df["Exit_Fee_Pct"]
    ever = pd.Index(scores0.loc[scores0["eligible"], "isin"].unique())
    de, dx = float(entry.reindex(ever).dropna().median()), float(exit_.reindex(ever).dropna().median())
    conn = get_connection()
    try:
        inputs2 = dataclasses.replace(inputs, ipc=ipc, fees=fees_df, daily_chunks=lambda: iter_daily_chunks(conn, list(inputs.nav.columns)))
        print(f"[{name}] aggregator/lag {ARMS[name]} | regime changes against R0: see the report")
        run_t = compute_pit_scores(inputs2, at, hist_k["regime"], ParquetCache(args.cache_dir), max_stale_days=60, levers=RL.LeverConfig(), fee_aware=False)
        print(f"[{name}] scored: {len(run_t.scores)} rows | timings {{{', '.join(f'{k}={v:.0f}s' for k, v in run_t.timings.items())}}} | cache {run_t.cache_hits.get('scoring_blocks')}")
    finally:
        conn.close()
    scores = run_t.scores
    key = ["as_of", "subportfolio", "isin"]
    m = scores0[key + ["score_base", "score_final", "eligible"]].merge(scores[key + ["score_base", "score_final", "eligible"]], on=key, how="left", suffixes=("_sealed", "_rerun"))
    meta = {"arm": name, "aggregator_lag": ARMS[name], "snapshot_id": json.loads((base_dir / "snapshot" / SNAP.MANIFEST).read_text(encoding="utf-8"))["snapshot_id"],
            "n_dates": int(len(at)), "baseline_vs_sealed": {"coverage": float(m["score_base_rerun"].notna().mean()), "max_abs_score_base_diff": float((m["score_base_sealed"] - m["score_base_rerun"]).abs().max()),
                                                          "eligible_agreement": float((m["eligible_sealed"] == m["eligible_rerun"]).mean())},
            "score_final_identical": bool((m["score_final_sealed"] == m["score_final_rerun"]).all())}
    meta["score_integrity"] = P37.class_swap_integrity(scores0, scores)
    meta["integrity_ok"] = bool(P37.arm_integrity_ok(meta["score_integrity"], SPEC) and meta["score_final_identical"])        # the regime changes the blend, never a score
    print(f"[{name}] score rows against the sealed run: {meta['score_integrity']} | score_final identical: {meta['score_final_identical']} -> {'OK' if meta['integrity_ok'] else 'NOT OK'}")
    prep = prepare_pit_backtest(run_t, inputs2, hist_k, at, windows=(1,))
    R1, cash = prep.R[1], prep.cash_ret[1]
    gross, _ = portfolio_returns(prep.W, R1, cash)
    tu = monthly_turnover(prep.W)
    cost, _miss = fee_cost_series(prep.W, entry, exit_, de, dx)
    series = pd.DataFrame({"gross": gross, "turnover": tu, "cash": cash, "net25": gross - BPS / 1e4 * tu, "netfee": gross - cost}, index=at)
    out.mkdir(parents=True, exist_ok=True)
    series.to_csv(out / "series.csv", index_label="date")
    meta["elapsed_s"] = round(time.perf_counter() - t0, 1)
    if name == "R0":
        table = pd.read_csv(run / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
        gate = P37.identity_gate(meta, series, table, P37.default_switches())
        meta["identity"] = gate
        (base_dir / "identity.json").write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
        print(f"[IDENTITY GATE] {'PASSED' if gate['ok'] else 'FAILED'}: {gate['checks']} | max |gross diff| {gate['max_abs_gross_diff']:.3e} | max |turnover diff| {gate['max_abs_turnover_diff']:.3e}")
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"[{name}] done in {meta['elapsed_s']} s -> {out}")


# ============================================================================================================================================================================
# report
# ============================================================================================================================================================================
def run_report(args, base_dir: Path) -> None:
    frames = SNAP.load_frames(base_dir / "snapshot")
    arms = {n: pd.read_csv(base_dir / n / "series.csv", index_col=0, parse_dates=True) for n in ARMS if (base_dir / n / "series.csv").exists()}
    metas = {n: json.loads((base_dir / n / "meta.json").read_text(encoding="utf-8")) for n in arms}
    ref = arms["R0"]
    ident = json.loads((base_dir / "identity.json").read_text(encoding="utf-8")) if (base_dir / "identity.json").exists() else {}
    rows, labels = [], {}
    for n in R.TRIALS:
        if n not in arms:
            continue
        r = R.evaluate_trial(n, ref, arms[n], R.HOLM_LEVEL)
        labels[n] = R.label_summary(frames["hist_R0.parquet"], frames[f"hist_{n}.parquet"])
        r.update({"aggregator": ARMS[n][0], "lag": ARMS[n][1], "months_changed": labels[n]["n_changed"], "turnover_ref": float(ref["turnover"].mean()), "turnover_alt": float(arms[n]["turnover"].mean()),
                  "integrity_ok": metas[n]["integrity_ok"]})
        rows.append(r)
    res = pd.DataFrame(rows).set_index("trial")
    res.to_csv(base_dir / "trials_table.csv")

    def f(cols, nd=4):
        return res[[c for c in cols if c in res.columns]].round(nd).to_string()
    lines = ["# P3-8: the CPI input of the regime classifier (PREREG_P3_8; Holm family 3, level %.4f)" % R.HOLM_LEVEL, "",
             f"Identity gate (R0 against the sealed run): {'PASSED' if ident.get('ok') else 'NOT PASSED / missing'}; {ident.get('checks')}. Snapshot {sorted({m['snapshot_id'][:12] for m in metas.values()})}. Nothing in production is touched.", "",
             "## 1. Classification (ACCEPTABLE = drawdown within 0.5 pp at the stated fees AND at 25 bp, and non-inferior net return; BENEFICIAL = also +0.5 pp a year or 1.0 pp of drawdown at the stated fees)", "", "```",
             f(["aggregator", "lag", "months_changed", "label", "acceptable", "net_diff_pp", "net_se_pp", "net_lb_pp", "ni_pass", "mde_pp", "dd_ok_fee", "dd_ok_25", "benefit_net", "benefit_dd", "integrity_ok"]), "```", "",
             "## 2. Net return and maximum drawdown", "", "```",
             f(["ann_ref_fee", "ann_alt_fee", "ann_ref_25", "ann_alt_25", "dd_ref_fee", "dd_alt_fee", "dd_delta_fee", "dd_ref_25", "dd_alt_25", "dd_delta_25", "turnover_ref", "turnover_alt"]), "```", "",
             "## 3. What the change does to the labels (descriptive)", ""]
    for n, s in labels.items():
        lines += [f"- **{n}** ({ARMS[n][0]}, lag {ARMS[n][1]}): {s['n_changed']} of {s['n_dates']} months change label (months: {', '.join(s['changed_months'])}); shares {({k: round(v, 3) for k, v in s['shares_ref'].items()})} -> "
                  f"{({k: round(v, 3) for k, v in s['shares_alt'].items()})}; mean weights D/B/Dyn {s['mean_weight_defensive_ref']:.3f}/{s['mean_weight_balanced_ref']:.3f}/{s['mean_weight_dynamic_ref']:.3f} -> "
                  f"{s['mean_weight_defensive_alt']:.3f}/{s['mean_weight_balanced_alt']:.3f}/{s['mean_weight_dynamic_alt']:.3f}"]
    last = pd.DataFrame({k: v["regime"].tail(6) for k, v in ((n, frames[f"hist_{n}.parquet"]) for n in ARMS)})
    lines += ["", "Last six months of regime labels (formation month-end):", "", "```", last.rename(index=lambda d: d.strftime("%Y%m")).to_string(), "```", ""]
    (base_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


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
    if args.stage == "snapshot":
        sealed_hist = pd.read_parquet(run / "inputs" / "regime_hist.parquet").sort_index()
        conn = get_connection()
        try:
            frames = build_histories(conn, sealed_hist)
        finally:
            conn.close()
        manifest = SNAP.freeze_frames(base_dir / "snapshot", frames)
        print(f"[SNAPSHOT] frozen in {base_dir / 'snapshot'}: id {manifest['snapshot_id']} at {manifest['frozen_utc']}")
        for n in R.TRIALS:
            s = R.label_summary(frames["hist_R0.parquet"], frames[f"hist_{n}.parquet"])
            print(f"[SNAPSHOT] {n} {ARMS[n]}: {s['n_changed']} of {s['n_dates']} months change label")
        return 0
    if not (base_dir / "snapshot" / SNAP.MANIFEST).exists():
        print(f"no frozen snapshot in {base_dir}: run --stage snapshot first")
        return 3
    if args.stage == "build":
        frames = SNAP.load_frames(base_dir / "snapshot")
        names = list(ARMS) if args.trial == "all" else [args.trial]
        for n in names:
            if n not in ARMS:
                print(f"unknown arm {n!r}; choose from {list(ARMS)}")
                return 4
            if n != "R0":
                ok, why = P37.gate_open(base_dir)
                if not ok:
                    print(f"[GATE] {n} refused: {why}")
                    return 5
            if (base_dir / n / "meta.json").exists():
                print(f"[{n}] already built: skipped")
                continue
            build_arm(args, n, frames[f"hist_{n}.parquet"], base_dir / n, base_dir)
            meta = json.loads((base_dir / n / "meta.json").read_text(encoding="utf-8"))
            if not meta["integrity_ok"]:
                print(f"[{n}] INTEGRITY NOT OK: stopping")
                return 6
        return 0
    run_report(args, base_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
