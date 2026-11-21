# scripts/audit/p3/p3_p35a_trials.py
# -*- coding: utf-8 -*-
r"""
Package L step F: the 11 preregistered trials of PREREG_P3_5A (doc/decisiones/PREREG_P3_5A.md, section 4, with the operational definitions of its Amendment 1) and the baseline re-run in the SAME
build, on the sealed PIT run's input snapshot. READ-ONLY on the database (NAV, fees, attributes, IPC, daily NAV for the short gates), nothing written outside the output folder.

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_p35a_trials.py --run-dir out\reports\pit_backtest\20261119_202509 --stage build --trial T0_baseline
    ... --stage build --trial all                 every trial, one after the other (resumable: a finished trial folder is skipped)
    ... --stage report                            the adoption table, the design / full-sample / confirmation reports

`--sample-per-nature N` runs the same code on N funds per nature (a smoke test of the plumbing, never a result). Every trial uses the levers through `compute_pit_scores(levers=...)`, i.e. the function
production runs (R3); no lever reads the global configuration here.
"""
import argparse
import dataclasses
import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import p3_cost_check as CC  # noqa: E402
import p3_crisis_diagnostic as CD  # noqa: E402
from proyecto3.src import pit_benchmarks as B  # noqa: E402
from proyecto3.src import pit_crisis_decompose as X  # noqa: E402
from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402
from proyecto3.src import pit_p35a_eval as E  # noqa: E402
from proyecto3.src import pit_p35a_prep as P  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402
from proyecto3.src.pit_backtest import monthly_turnover, portfolio_returns, prepare_pit_backtest, series_stats  # noqa: E402
from proyecto3.src.pit_cache import ParquetCache  # noqa: E402
from proyecto3.src.pit_cost_check import fee_cost_series  # noqa: E402
from proyecto3.src.pit_inputs import iter_daily_chunks  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_run import compute_pit_scores  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402
from shared.config import MAX_NON_EUR_UNHEDGED_WEIGHT  # noqa: E402
from shared.db import get_connection  # noqa: E402

LC = RL.LeverConfig
# The 11 preregistered trials (section 4) and the baseline. Values are listed less intrusive first; "first" values = l1 0.20, l2 2.0, l3 0.5, l4 H 36 months.
TRIALS = {
    "T0_baseline": LC(),
    "S1_L1_q20": LC(l1_q=0.20), "S2_L1_q35": LC(l1_q=0.35),
    "S3_L2_z2.0": LC(l2_z0=2.0), "S4_L2_z1.5": LC(l2_z0=1.5),
    "S5_L3_k0.5": LC(l3_k=0.5), "S6_L3_k0": LC(l3_k=0.0),
    "S7_L4_H36m": LC(l4_h_years=3.0), "S8_L4_H24m": LC(l4_h_years=2.0),
    "I1_L1q20_L3k0.5": LC(l1_q=0.20, l3_k=0.5), "I2_L1q35_L3k0": LC(l1_q=0.35, l3_k=0.0),
    "C1_all_first": LC(l1_q=0.20, l2_z0=2.0, l3_k=0.5, l4_h_years=3.0),
}
# INFORMATIVE runs (owner decision 2026-11-21, item 5; PREREG section 4: the R10 check): L4 again at H = the REALISED mean holding period of S7 / S8 (read from their folder). Outside the 11 trials,
# outside the Holm family and outside the verdict; they only show how much of L4's effect the 24-36 month amortisation leaves out. No re-fit.
INFORMATIVE = {"X7_L4_H_realised": "S7_L4_H36m", "X8_L4_H_realised": "S8_L4_H24m"}
RETURN_TRACK = ("S7_L4_H36m", "S8_L4_H24m") + tuple(INFORMATIVE)   # the KPI of L4 is the net annual return; every other trial is on the risk track (the ceiling)
HOLM_LEVEL = 0.05 / 11                                           # the 11 trials of this study (owner decision 2026-11-21)
DD_CAP, NONINF_MARGIN_PP, DEGRADE_MAX_PP = -0.15, -0.10, 0.35
STRESS_SHARE, STRESS_SHARE_HI = 0.10, 0.20                       # a x k = 5 % x 2 (primary) and 10 % x 2 (sensitivity)
BPS = 25.0                                                       # the PIT's flat cost per side: the cross-check regime and the regime of condition 1 (Amendment 1)
EPISODES = {"2015-07/08": ("2015-07-01", "2015-08-31"), "2015-11..2016-01": ("2015-11-01", "2016-01-31"), "2022-01..2023-01": ("2022-01-01", "2023-01-31")}
DESIGN = ("2007-11-01", "2009-07-31")
CRISIS_WINDOWS = [(lo, hi, 12) for lo, hi in D.LOCKED_EPISODES.values()] + [(DESIGN[0], DESIGN[1], 0), ("2020-02-01", "2020-06-30", 0)]


def build_parser():
    p = argparse.ArgumentParser(description="Package L step F: the preregistered trials (read-only).")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--stage", required=True, choices=("build", "report"))
    p.add_argument("--trial", default="all")
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_p35a_trials"))
    p.add_argument("--cache-dir", default=str(_ROOT / "proyecto3" / "cache" / "pit"))
    p.add_argument("--sample-per-nature", type=int, default=0, help="smoke test of the plumbing on N funds per nature (NEVER a result)")
    p.add_argument("--no-short-gates", action="store_true", help="skip the daily-NAV short gates (smoke tests only)")
    p.add_argument("--stamp", default="", help="output folder name (default: a time stamp, or the latest one for --stage report)")
    return p


# ============================================================================================================================================================================
# build: one trial
# ============================================================================================================================================================================
def _sample(inputs, scores0, per_nature):
    nav = inputs.nav
    nat = inputs.attrs["Fund_Nature"].reindex(nav.columns)
    n_obs = nav.notna().sum()
    keep = []
    for _n, cols in nat.groupby(nat).groups.items():
        keep += list(n_obs.reindex(cols).sort_values(ascending=False, kind="mergesort").head(per_nature).index)
    keep = sorted(set(keep))
    attrs = inputs.attrs.loc[inputs.attrs.index.intersection(keep)]
    fx = inputs.fx_exposed.reindex(keep) if inputs.fx_exposed is not None else None
    return dataclasses.replace(inputs, nav=nav[keep], attrs=attrs, fx_exposed=fx), scores0[scores0["isin"].isin(keep)], keep


def _sub_weight_frames(scores, attrs, fx, hist, at):
    """Per sub-portfolio weights (dates x isin, the sub-portfolio's INTERNAL weights, NaN = not held) built by the engine exactly as `build_weights` does (no incumbents, no cadence)."""
    by_date = dict(tuple(scores.groupby("as_of")))
    rows = {s: {} for s in D.SUBPORTFOLIOS}
    for t in at:
        sub_w = B._sub_weights(hist, t)
        if t not in by_date or sub_w is None:
            continue
        selection, _master = X.select_master(by_date[t], attrs, fx, sub_w)
        for s in D.SUBPORTFOLIOS:
            df = selection.get(s)
            rows[s][t] = dict(zip(df["isin"], df["weight"])) if df is not None and len(df) else {}
    return {s: pd.DataFrame.from_dict(rows[s], orient="index").reindex(at) for s in D.SUBPORTFOLIOS}


def build_trial(args, name: str, cfg: "RL.LeverConfig", out: Path) -> None:
    t0 = time.perf_counter()
    run = Path(args.run_dir)
    pit0, inputs, hist, at = load_saved_run(run, None)
    scores0 = pit0.scores
    sample_keep = None
    if args.sample_per_nature:
        inputs, scores0, sample_keep = _sample(inputs, scores0, args.sample_per_nature)
        print(f"[SAMPLE] {len(sample_keep)} funds ({args.sample_per_nature} per nature): a plumbing smoke test, NEVER a result")
    conn = get_connection()
    try:
        ipc, ipc_id = CD._ipc_from_database()
        entry, exit_ = CC._load_fees(conn)
        geo = P.load_d6_attributes(conn)["Geography"]
        if sample_keep is not None:
            entry, exit_ = entry.reindex(sample_keep), exit_.reindex(sample_keep)
        ever = pd.Index(scores0.loc[scores0["eligible"], "isin"].unique())
        de, dx = float(entry.reindex(ever).dropna().median()), float(exit_.reindex(ever).dropna().median())      # the cost-check convention for an unknown fee
        fees_df = pd.DataFrame({"Entry_Fee_Pct": entry, "Exit_Fee_Pct": exit_})
        daily = None if args.no_short_gates else (lambda: iter_daily_chunks(conn, list(inputs.nav.columns)))
        inputs2 = dataclasses.replace(inputs, ipc=ipc, fees=fees_df, daily_chunks=daily)
        print(f"[{name}] levers {cfg} | IPC {ipc_id}")
        run_t = compute_pit_scores(inputs2, at, hist["regime"], ParquetCache(args.cache_dir), max_stale_days=60, levers=cfg)
        print(f"[{name}] scored: {len(run_t.scores)} rows | timings {{{', '.join(f'{k}={v:.0f}s' for k, v in run_t.timings.items())}}} | cache {run_t.cache_hits.get('scoring_blocks')}")
    finally:
        conn.close()
    scores = run_t.scores
    meta = {"trial": name, "levers": dataclasses.asdict(cfg), "ipc": ipc_id, "n_dates": int(len(at)), "n_funds": int(inputs.nav.shape[1]), "sample": bool(sample_keep),
            "short_gates": not args.no_short_gates, "default_entry": de, "default_exit": dx}
    if name == "T0_baseline" and sample_keep is None:       # the re-run of the baseline in this build against the sealed one
        key = ["as_of", "subportfolio", "isin"]
        m = scores0[key + ["score_base", "eligible"]].merge(scores[key + ["score_base", "eligible"]], on=key, how="left", suffixes=("_sealed", "_rerun"))
        meta["baseline_vs_sealed"] = {"coverage": float(m["score_base_rerun"].notna().mean()), "max_abs_score_base_diff": float((m["score_base_sealed"] - m["score_base_rerun"]).abs().max()),
                                      "eligible_agreement": float((m["eligible_sealed"] == m["eligible_rerun"]).mean())}
        print(f"[{name}] baseline re-run vs the sealed run: {meta['baseline_vs_sealed']}")
    attrs, fx = inputs2.attrs, getattr(inputs2, "fx_exposed", None)
    prep = prepare_pit_backtest(run_t, inputs2, hist, at, windows=(1,))
    R1, cash = prep.R[1], prep.cash_ret[1]
    gross, _ = portfolio_returns(prep.W, R1, cash)
    tu = monthly_turnover(prep.W)
    cost, _miss = fee_cost_series(prep.W, entry, exit_, de, dx)
    Wp = B.pool_weights(scores, hist, at, fx_exposed=fx, exposed_budget=MAX_NON_EUR_UNHEDGED_WEIGHT)           # the COSTED MATCHED POOL (R11) of this variant's own eligible set
    gp, _ = portfolio_returns(Wp, R1, cash)
    tup = monthly_turnover(Wp)
    costp, _ = fee_cost_series(Wp, entry, exit_, de, dx)
    series = pd.DataFrame({"gross": gross, "turnover": tu, "cash": cash, "net25": gross - BPS / 1e4 * tu, "netfee": gross - cost,
                           "pool_net25": gp - BPS / 1e4 * tup, "pool_netfee": gp - costp}, index=at)
    Wsub = _sub_weight_frames(scores, attrs, fx, hist, at)
    for s in D.SUBPORTFOLIOS:
        gs, _ = portfolio_returns(Wsub[s], R1, cash)
        cs, _ = fee_cost_series(Wsub[s], entry, exit_, de, dx)
        series[f"sub_{s}"] = gs - cs                                                    # the sub-portfolio's own net return at the stated fees (the ceiling's input)
    nature_of = scores.drop_duplicates("isin").set_index("isin")["fund_nature"]
    pool = {t: set(g.loc[g["eligible"] & (g["score_final"] > 0), "isin"]) for t, g in scores.groupby("as_of")}
    arrays = {}
    for q, tag in ((0.10, "p10"), (0.05, "p05")):
        arrays[f"master_{tag}"] = E.stress_paths(R1, at, nature_of, pool, prep.W, q)
        arrays[f"subs_{tag}"] = np.stack([E.stress_paths(R1, at, nature_of, pool, Wsub[s], q) for s in D.SUBPORTFOLIOS], axis=2)
    # geographic exposure of the selected set (the preregistration requires it for every L2 run; reported for all) and per-date diagnostics
    Wn = prep.W.fillna(0.0)
    labels = geo.reindex(Wn.columns).astype(object).where(geo.reindex(Wn.columns).notna(), "NA")
    share = Wn.T.groupby(labels.to_numpy()).sum().T
    share = share.div(share.sum(axis=1).replace(0.0, np.nan), axis=0)
    top, bottom, _desc = CD.design_dates(at)
    geo_out = pd.DataFrame({"all_dates": share.mean(), "design_top": share.reindex([d for d in top if d in share.index]).mean(),
                            "design_bottom": share.reindex([d for d in bottom if d in share.index]).mean()})
    diag = scores.assign(l1_peer=scores["exclusion_reason"].fillna("").str.startswith("l1_peer"), l1_backstop=scores["exclusion_reason"].fillna("").str.startswith("l1_backstop")) \
        .groupby(["as_of", "subportfolio"]).agg(scored=("isin", "size"), eligible=("eligible", "sum"), l1_peer=("l1_peer", "sum"), l1_backstop=("l1_backstop", "sum")).reset_index()
    out.mkdir(parents=True, exist_ok=True)
    series.to_csv(out / "series.csv", index_label="date")
    np.savez_compressed(out / "stress.npz", **arrays)
    geo_out.to_csv(out / "geo_share.csv", index_label="geography")
    diag.to_csv(out / "scores_diag.csv", index=False)
    if "l3_state" in run_t.universe.columns:
        run_t.universe[["l3_state"]].to_csv(out / "l3_state.csv", index_label="date")
    meta["holding"] = P.holding_spells(prep.W)                                                     # the realised holding period of this variant's master portfolio (months)
    meta["elapsed_s"] = round(time.perf_counter() - t0, 1)
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"[{name}] done in {meta['elapsed_s']} s -> {out}")


# ============================================================================================================================================================================
# report: the adoption rule across the trials
# ============================================================================================================================================================================
def _load(folder: Path) -> dict:
    s = pd.read_csv(folder / "series.csv", parse_dates=["date"]).set_index("date")
    z = np.load(folder / "stress.npz")
    return {"series": s, "stress": {k: z[k] for k in z.files}, "meta": json.loads((folder / "meta.json").read_text(encoding="utf-8")),
            "geo": pd.read_csv(folder / "geo_share.csv").set_index("geography"), "diag": pd.read_csv(folder / "scores_diag.csv", parse_dates=["as_of"]),
            "l3": pd.read_csv(folder / "l3_state.csv", parse_dates=["date"]).set_index("date")["l3_state"] if (folder / "l3_state.csv").exists() else None}


def _dd_blocks(series: pd.Series, dd_stress: np.ndarray, first, last) -> tuple:
    """(unstressed window drawdown, worst stressed window drawdown over windows STARTING in [first, last])."""
    un = E.window_dd(series, first, last)
    sel = (series.index >= first) & (series.index <= last)
    d = dd_stress[sel]
    return un, (float(np.nanmin(d)) if np.isfinite(d).any() else float("nan"))


def evaluate(name: str, t: dict, base: dict, target: pd.Series, base_ceiling: dict) -> dict:
    s, b = t["series"], base["series"]
    at = s.index
    res = {"trial": name, "track": "return" if name in RETURN_TRACK else ("baseline" if name == "T0_baseline" else "risk")}
    for tag, col in (("25bp", "net25"), ("fee", "netfee")):
        r = s[col]
        res[f"ann_{tag}"] = E.ann_return(r.dropna().to_numpy())
        res[f"maxdd_{tag}"] = E.max_dd(r.dropna().to_numpy())
        res[f"maxdd_repo_{tag}"] = series_stats(r, s["cash"])["max_drawdown"]
    res["turnover"] = float(s["turnover"].mean())
    res["pool_ann_fee"], res["pool_ann_25bp"] = E.ann_return(s["pool_netfee"].dropna().to_numpy()), E.ann_return(s["pool_net25"].dropna().to_numpy())
    # ---- stressed windows (primary: the 10th-percentile realised path, a x k = 10 %) ----
    st10 = {tag: E.stressed_window_dds(s[col].to_numpy(), t["stress"]["master_p10"], STRESS_SHARE) for tag, col in (("25bp", "net25"), ("fee", "netfee"))}
    st05 = E.stressed_window_dds(s["net25"].to_numpy(), t["stress"]["master_p05"], STRESS_SHARE)
    st20 = E.stressed_window_dds(s["net25"].to_numpy(), t["stress"]["master_p10"], STRESS_SHARE_HI)
    res["stressed_worst_25bp"], res["stressed_worst_fee"] = float(np.nanmin(st10["25bp"])), float(np.nanmin(st10["fee"]))
    res["stressed_worst_p05_25bp"], res["stressed_worst_s20_25bp"] = float(np.nanmin(st05)), float(np.nanmin(st20))
    for label, (lo, hi, rec) in {"design": (DESIGN[0], DESIGN[1], 0), **{k: (v[0], v[1], 12) for k, v in EPISODES.items()}}.items():
        first, last = E.episode_formation_range(lo, hi, rec)
        for tag, col in (("25bp", "net25"), ("fee", "netfee")):
            un, stz = _dd_blocks(s[col], st10[tag], first, last)
            res[f"dd_{label}_{tag}"], res[f"dds_{label}_{tag}"] = un, stz
    # ---- condition 1 ----
    res["c1_full_25bp"] = bool(res["maxdd_25bp"] >= DD_CAP) and bool(res["stressed_worst_25bp"] >= DD_CAP)
    res["c1_episodes_25bp"] = all(res[f"dd_{k}_25bp"] >= DD_CAP and res[f"dds_{k}_25bp"] >= DD_CAP for k in EPISODES)
    res["c1"] = bool(res["c1_full_25bp"] and res["c1_episodes_25bp"])
    # ---- condition 2: non-inferiority against the baseline at the stated fees (return KPI) and the ceiling KPI of the risk track ----
    if name != "T0_baseline":
        ni = E.noninferiority((s["netfee"] - b["netfee"]).to_numpy(), HOLM_LEVEL, NONINF_MARGIN_PP)
        res.update({f"ret_{k}": v for k, v in ni.items()})
        ni25 = E.noninferiority((s["net25"] - b["net25"]).to_numpy(), HOLM_LEVEL, NONINF_MARGIN_PP)
        res.update({f"ret25_{k}": v for k, v in ni25.items()})
    subs = [f"sub_{x}" for x in D.SUBPORTFOLIOS]
    ok = s[subs].notna().all(axis=1) & b[subs].notna().all(axis=1)
    S = s.loc[ok, subs].to_numpy()
    STR = t["stress"]["subs_p10"][ok.to_numpy()]
    cl = E.ceiling(S, np.nan_to_num(STR, nan=0.0), STRESS_SHARE, DD_CAP)
    res.update({"ceil_ann": cl["ann"], "ceil_mix": cl["mix"], "ceil_dd": cl["dd_unstressed"], "ceil_dd_stressed": cl["dd_stressed"]})
    if base_ceiling is not None:
        at_base = E.ceiling_at_mix(S, np.nan_to_num(STR, nan=0.0), STRESS_SHARE, base_ceiling["mix"])
        res["ceil_at_base_mix_ann"], res["ceil_at_base_mix_dd_stressed"] = at_base["ann"], at_base["dd_stressed"]
        if name != "T0_baseline":
            Sb = b.loc[ok, subs].to_numpy()
            se = E.ceiling_diff_se_pp(S, Sb)
            diff_pp = (cl["ann"] - base_ceiling["ann"]) * 100
            res.update({"ceil_diff_pp": diff_pp, "ceil_se_pp": se})
            from statistics import NormalDist
            res["ceil_lb_pp"] = diff_pp - NormalDist().inv_cdf(1 - HOLM_LEVEL) * se
            res["ceil_pass"] = bool(res["ceil_lb_pp"] > NONINF_MARGIN_PP)
    kpi_pass = res.get("ret_pass") if res["track"] == "return" else res.get("ceil_pass")
    res["c2"] = bool(kpi_pass) if kpi_pass is not None else None
    # ---- condition 3: non-crisis degradation and the share of 36-month windows above IPC+M3 ----
    if name != "T0_baseline":
        mask = ~E.crisis_mask(at, CRISIS_WINDOWS)
        d = (s["netfee"] - b["netfee"])[mask].dropna()
        res["noncrisis_degradation_pp"] = float(-d.mean() * 12 * 100)
        sh_v = B.window_summary(B.rolling_excess(s["netfee"].dropna(), target, 36))["share_above"]
        sh_b = B.window_summary(B.rolling_excess(b["netfee"].dropna(), target, 36))["share_above"]
        res["share36_variant"], res["share36_baseline"] = sh_v, sh_b
        res["c3"] = bool(res["noncrisis_degradation_pp"] <= DEGRADE_MAX_PP and sh_v >= sh_b - 1e-12)
    res["c4"] = bool(res["ann_fee"] > res["pool_ann_fee"])
    if name in INFORMATIVE:
        res["track"] = "informative"
    elif name != "T0_baseline":
        res["candidate"] = bool(res["c1"] and res["c2"] and res["c3"] and res["c4"])
    res["holding_months"] = t["meta"].get("holding", {}).get("mean_months", float("nan"))
    return res


def l3_stability(states: "pd.Series | None") -> dict:
    """The stability limit of L3 (PREREG_P3_5A section 9.4, frozen text): at most 4 complete transitions (state changes between consecutive dates) in the 2008 design window, and at most 2 per year on average over the series."""
    if states is None:
        return {}
    s = states.dropna()
    changes = (s != s.shift()).iloc[1:]
    win = s.loc[pd.Timestamp(DESIGN[0]) + pd.offsets.MonthEnd(-1): pd.Timestamp(DESIGN[1]) + pd.offsets.MonthEnd(0)]
    n_win = int((win != win.shift()).iloc[1:].sum())
    years = max((s.index.max() - s.index.min()).days / 365.25, 1e-9)
    return {"design_window_transitions": n_win, "design_ok": bool(n_win <= 4), "flips_per_year": float(changes.sum() / years), "flips_ok": bool(changes.sum() / years <= 2.0),
            "share_recovery": float((s == "recovery").mean()), "share_stress": float((s == "stress").mean())}


def run_report(args, folders: dict, out: Path) -> None:
    runs = {n: _load(f) for n, f in folders.items()}
    base = runs["T0_baseline"]
    table = pd.read_csv(Path(args.run_dir) / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
    target = table["target_1m"]
    s0 = base["series"]
    subs = [f"sub_{x}" for x in D.SUBPORTFOLIOS]
    ok0 = s0[subs].notna().all(axis=1)
    base_ceiling = E.ceiling(s0.loc[ok0, subs].to_numpy(), np.nan_to_num(base["stress"]["subs_p10"][ok0.to_numpy()], nan=0.0), STRESS_SHARE, DD_CAP)
    rows = [evaluate(n, t, base, target, base_ceiling) for n, t in runs.items()]
    res = pd.DataFrame(rows).set_index("trial")
    res.to_csv(out / "adoption_table.csv")
    stab = {n: l3_stability(t["l3"]) for n, t in runs.items() if t["l3"] is not None}
    pd.DataFrame(stab).T.to_csv(out / "l3_stability.csv")

    def fmt(cols, **kw):
        return res[[c for c in cols if c in res.columns]].round(kw.get("nd", 4)).to_string()
    lines = ["# P3-5A trials: adoption table (PREREG_P3_5A, Amendment 1 definitions)", "",
             f"Baseline re-run in the same build: {base['meta'].get('baseline_vs_sealed', 'sample run: not compared')}. Holm family = 11 trials (level {HOLM_LEVEL:.4f}); the historical test of condition 2 is non-inferiority "
             f"(lower bound > {NONINF_MARGIN_PP} pp a year); adoption is left to the forward shadow. Condition 1 is evaluated at the PIT's flat {BPS:g} bp per side (Amendment 1: at the stated fees the "
             "baseline itself is far deeper, see `maxdd_fee`), both unstressed and under the primary tail stress (realised P10 path, a x k = 10 %).", "",
             "## 1. DESIGN (2008, 2007-11..2009-07): drawdown of the master portfolio, unstressed and under the primary stress (worst 12-month window starting in the design window)", "", "```",
             fmt(["dd_design_25bp", "dds_design_25bp", "dd_design_fee", "dds_design_fee"]), "```", "",
             "## 2. FULL SAMPLE: returns, drawdowns, stress, turnover", "", "```",
             fmt(["ann_25bp", "ann_fee", "pool_ann_fee", "maxdd_25bp", "maxdd_repo_25bp", "maxdd_fee", "stressed_worst_25bp", "stressed_worst_p05_25bp", "stressed_worst_s20_25bp", "turnover", "holding_months"]), "```", "",
             "## 3. CONFIRMATION EPISODES (episode + 12 months; unstressed `dd_`, stressed `dds_`, flat 25 bp)", "", "```",
             fmt([f"{p}_{k}_25bp" for k in EPISODES for p in ("dd", "dds")]), "```", "",
             "## 4. CONDITION 2: non-inferiority at the stated fees (return KPI) and the ceiling KPI (risk track)", "", "```",
             fmt(["track", "ret_mean_pp", "ret_se_pp", "ret_lb_pp", "ret_pass", "ceil_ann", "ceil_dd", "ceil_dd_stressed", "ceil_diff_pp", "ceil_se_pp", "ceil_lb_pp", "ceil_pass", "ceil_at_base_mix_ann"]), "```", "",
             "## 5. CONDITIONS 3 AND 4 AND THE VERDICT", "", "```",
             fmt(["noncrisis_degradation_pp", "share36_variant", "share36_baseline", "c1", "c2", "c3", "c4", "candidate"]), "```", ""]
    if stab:
        lines += ["## 6. L3 stability (the frozen limit: at most 4 transitions in the 2008 design window and at most 2 flips a year)", "", "```", pd.DataFrame(stab).T.round(3).to_string(), "```", ""]
    geo = pd.DataFrame({n: t["geo"]["all_dates"] for n, t in runs.items()}).fillna(0.0)
    geo_top = pd.DataFrame({n: t["geo"]["design_top"] for n, t in runs.items()}).fillna(0.0)
    lines += ["## 7. Geographic exposure of the selected set (share of the invested weight; required for every L2 run), all dates and the 2008 top dates", "", "```", geo.round(3).to_string(), "", geo_top.round(3).to_string(), "```", ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
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
    prereg = D.prereg_in_git_head(_ROOT)
    print(f"[LOCK] {D.PREREG_NAME} in git HEAD: {prereg} -> the locked episodes are {'OPEN' if prereg else 'REFUSED'}")
    if not prereg:
        print("[LOCK] the trials read the confirmation episodes: refused until the preregistration is committed")
        return 3
    root = Path(args.out_root)
    stamp = args.stamp or (sorted(p.name for p in root.iterdir() if p.is_dir())[-1] if (args.stage == "report" and root.exists() and any(root.iterdir())) else datetime.now().strftime("%Y%m%d_%H%M%S"))
    base_dir = root / stamp
    base_dir.mkdir(parents=True, exist_ok=True)
    if args.stage == "build":
        names = list(TRIALS) if args.trial == "all" else [args.trial]
        for n in names:
            if n not in TRIALS and n not in INFORMATIVE:
                print(f"unknown trial {n!r}; choose from {list(TRIALS) + list(INFORMATIVE)}")
                return 4
            if (base_dir / n / "meta.json").exists():
                print(f"[{n}] already built in {base_dir / n}: skipped")
                continue
            if n in INFORMATIVE:
                src = base_dir / INFORMATIVE[n] / "meta.json"
                if not src.exists():
                    print(f"[{n}] needs {INFORMATIVE[n]} built first: skipped")
                    continue
                months = json.loads(src.read_text(encoding="utf-8"))["holding"]["mean_months"]
                cfg = LC(l4_h_years=float(months) / 12.0)
                print(f"[{n}] H = the realised mean holding period of {INFORMATIVE[n]}: {months:.2f} months")
            else:
                cfg = TRIALS[n]
            build_trial(args, n, cfg, base_dir / n)
        print(f"[OUT] {base_dir}")
        return 0
    folders = {n: base_dir / n for n in list(TRIALS) + list(INFORMATIVE) if (base_dir / n / "meta.json").exists()}
    if "T0_baseline" not in folders:
        print("the baseline trial is not built in this folder")
        return 5
    run_report(args, folders, base_dir)
    print(f"[OUT] {base_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
