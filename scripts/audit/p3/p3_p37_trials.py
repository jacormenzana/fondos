# scripts/audit/p3/p3_p37_trials.py
# -*- coding: utf-8 -*-
r"""
Package L, P3-7: the cost study of doc/decisiones/PREREG_P3_7.md (the eight trials, the baseline re-run in the SAME build, the matched pools at the same band, the adoption table). READ-ONLY on the
database (daily NAV for the short gates only) and nothing written outside the output folder. The IPC series, the entry / exit fees and the geography come ONLY from a frozen, hash-verified snapshot.

    py -X utf8 scripts\audit\p3\p3_p37_trials.py --run-dir out\reports\pit_backtest\20261119_202509 --stage snapshot --stamp <S>
    ... --stage build --trial T0_baseline --stamp <S>       the baseline, then the IDENTITY GATE against the sealed run (identity.json)
    ... --stage build --trial all --stamp <S>               the eight trials: REFUSED until identity.json says the baseline reproduced the sealed run exactly
    ... --stage pools --stamp <S>                           the 20 + 20 draws of the persistent (per band) and redraw matched pools
    ... --stage report --stamp <S>                          the adoption table (PREREG_P3_7 section 5) and the Amendment 5 side verdict on A1 / A2

`--sample-per-nature N` runs the same code on N funds per nature (a smoke test of the plumbing, never a result; it never writes identity.json).
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

import p3_p35a_trials as T  # noqa: E402  (the sample helper and the constants of the previous study, one definition)
from proyecto3.src import pit_benchmarks as B  # noqa: E402
from proyecto3.src import pit_p35a_eval as E  # noqa: E402
from proyecto3.src import pit_p35a_prep as P  # noqa: E402
from proyecto3.src import pit_p37_eval as V  # noqa: E402
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402
from proyecto3.src import ranking_levers as RL  # noqa: E402
from proyecto3.src.pit_backtest import monthly_turnover, portfolio_returns, prepare_pit_backtest  # noqa: E402
from proyecto3.src.pit_cache import ParquetCache  # noqa: E402
from proyecto3.src.pit_cost_check import fee_cost_series  # noqa: E402
from proyecto3.src.pit_inputs import iter_daily_chunks  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_run import compute_pit_scores  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS  # noqa: E402
from shared.config import MAX_NON_EUR_UNHEDGED_WEIGHT  # noqa: E402
from shared.db import get_connection  # noqa: E402

PENALTY_H_YEARS = 0.58            # PREREG_P3_7 section 4: H of the fee penalty, 7 months (0.58 years)
HOLM_TRIALS = 8                   # PREREG_P3_7 section 9: the Holm family of the adoption test
HOLM_LEVEL = 0.05 / HOLM_TRIALS
MATERIALITY_PP, DEGRADE_MAX_PP = 0.5, 0.35
STRESS_REL_PP = 0.5               # D3: the stressed worst window may be at most 0.5 pp deeper than the baseline's
N_DRAWS = 20
SEALED_GROSS_TOL, SEALED_TURNOVER_TOL = 1e-9, 0.005 + 1e-9      # the sealed monthly table prints the turnover with two decimals

# The arms (PREREG_P3_7 sections 4 and 11, item 3). band = hysteresis band on the incumbents (0 = none); cls = the fee-aware class choice (tolerance from shared.config); pen = the fee penalty (L4, H 0.58 y).
ARMS = {
    "T0_baseline": dict(band=0.0, cls=False, pen=False),
    "A1": dict(band=0.10, cls=False, pen=False), "A2": dict(band=0.20, cls=False, pen=False),
    "B": dict(band=0.0, cls=True, pen=False), "C": dict(band=0.0, cls=False, pen=True),
    "AB": dict(band=0.10, cls=True, pen=False), "AC": dict(band=0.10, cls=False, pen=True),
    "ABC": dict(band=0.10, cls=True, pen=True), "D": dict(band=0.20, cls=False, pen=True),
}
TRIALS = [n for n in ARMS if n != "T0_baseline"]
assert len(TRIALS) == HOLM_TRIALS


def levers_of(spec: dict) -> "RL.LeverConfig":
    return RL.LeverConfig(l4_h_years=PENALTY_H_YEARS) if spec["pen"] else RL.LeverConfig()


def default_switches() -> dict:
    """The global switches that must be at their shipped value (OFF) for a baseline to mean what it says: the levers, the class choice and the hysteresis."""
    import shared.config as C
    names = ("P3_L1_PEER_FILTER_ENABLED", "P3_L2_OVEREXTENSION_ENABLED", "P3_L3_RECOVERY_MODULATION_ENABLED", "P3_L4_FEE_PENALTY_ENABLED", "P3_FEE_AWARE_CLASS_ENABLED", "PORTFOLIO_HYSTERESIS_ENABLED")
    return {n: bool(getattr(C, n)) for n in names}


def build_parser():
    p = argparse.ArgumentParser(description="Package L, P3-7: the cost study (read-only).")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--stage", required=True, choices=("snapshot", "build", "identity", "pools", "report"))
    p.add_argument("--trial", default="all")
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_p37_trials"))
    p.add_argument("--cache-dir", default=str(_ROOT / "proyecto3" / "cache" / "pit"))
    p.add_argument("--sample-per-nature", type=int, default=0, help="smoke test of the plumbing on N funds per nature (NEVER a result)")
    p.add_argument("--no-short-gates", action="store_true", help="skip the daily-NAV short gates (smoke tests only)")
    p.add_argument("--pool", default="all", help="pools stage: one of persistent_b0, persistent_b10, persistent_b20, redraw (default all, one after the other; run several in parallel to go faster)")
    p.add_argument("--draws", type=int, default=N_DRAWS, help="draws per pool (the preregistered value is 20; fewer only for smoke tests)")
    p.add_argument("--stamp", required=True, help="output folder name (one folder holds the snapshot, the identity gate, the arms, the pools and the report)")
    return p


# ============================================================================================================================================================================
# the identity gate (step 2): the baseline of THIS build against the sealed run
# ============================================================================================================================================================================
def identity_gate(meta: dict, series: pd.DataFrame, sealed_table: pd.DataFrame, switches: dict) -> dict:
    """The exact-reproduction test of PREREG_P3_7 section 8 / the owner's gate before the trials: scores (coverage, max |score_base| difference, eligible agreement) and the portfolio (monthly gross
    return and turnover against the sealed monthly table), with every shipped switch at its default. `ok` is the conjunction; nothing is rounded to make it pass."""
    vs = meta["baseline_vs_sealed"]
    t = sealed_table.reindex(series.index)
    # the SAME months: every re-run date is in the sealed table and a month has a value in both or in neither (the last date has no forward return: NaN in both is a match, one-sided NaN is not)
    same_dates = bool(series.index.isin(sealed_table.index).all()
                      and (series["gross"].isna().to_numpy() == t["gross_1m"].isna().to_numpy()).all()
                      and (series["turnover"].isna().to_numpy() == t["turnover"].isna().to_numpy()).all()
                      and series["gross"].notna().any())
    gross_d, turn_d = (series["gross"] - t["gross_1m"]).abs(), (series["turnover"] - t["turnover"]).abs()
    gross_diff = float(gross_d.max()) if gross_d.notna().any() else float("inf")
    turn_diff = float(turn_d.max()) if turn_d.notna().any() else float("inf")
    checks = {"coverage_is_1": vs["coverage"] == 1.0, "score_base_identical": vs["max_abs_score_base_diff"] == 0.0, "eligible_identical": vs["eligible_agreement"] == 1.0,
              "gross_identical": bool(gross_diff <= SEALED_GROSS_TOL), "turnover_identical": bool(turn_diff <= SEALED_TURNOVER_TOL), "switches_default_off": not any(switches.values()),
              "same_dates": same_dates}
    return {"ok": bool(all(checks.values())), "checks": checks, "max_abs_gross_diff": gross_diff, "max_abs_turnover_diff": turn_diff, "baseline_vs_sealed": vs, "switches": switches}


def reevaluate_identity(base_dir: Path, run_dir: Path) -> dict:
    """The identity gate again from the SAVED baseline (its series and its meta) and the sealed table, with the switches as they are now; rewrites identity.json. Deterministic and read-only on the database:
    used when a check of the gate itself was wrong (the first version required a forward return on the last date), never to relax one: every check keeps its definition in `identity_gate`."""
    series = pd.read_csv(base_dir / "T0_baseline" / "series.csv", parse_dates=["date"]).set_index("date")
    meta = json.loads((base_dir / "T0_baseline" / "meta.json").read_text(encoding="utf-8"))
    table = pd.read_csv(Path(run_dir) / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
    gate = identity_gate(meta, series, table, default_switches())
    gate["evaluated_from_saved_baseline"] = True
    meta["identity"] = gate
    (base_dir / "identity.json").write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
    (base_dir / "T0_baseline" / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return gate


def class_swap_integrity(sealed: pd.DataFrame, rerun: pd.DataFrame) -> dict:
    """What an arm may change in the score rows against the sealed run: NOTHING (A1, A2) or WHICH CLASS of a family represents it (the fee penalty re-ranks the classes of a family; the fee-aware choice picks
    one by its fees): the number of rows per (date, sub-portfolio, family) is the same, a fund with no family is its own family, and the matched rows keep their eligibility. `n_swapped_rows` = sealed rows
    whose key is absent from the re-run (a swap makes one in each direction)."""
    key = ["as_of", "subportfolio", "isin"]
    m = sealed[key + ["eligible"]].merge(rerun[key + ["eligible"]], on=key, how="outer", indicator=True, suffixes=("_s", "_r"))
    both = m[m["_merge"] == "both"]

    def family_counts(d: pd.DataFrame) -> pd.Series:
        f = d["fund_family_id"]
        own = f.isna() | (f.astype(str).str.strip() == "")
        fam = f.astype(object).where(~own, "isin:" + d["isin"].astype(str))
        return d.assign(_fam=fam).groupby(["as_of", "subportfolio", "_fam"]).size().sort_index()
    return {"rows_equal": bool(len(sealed) == len(rerun)), "family_counts_equal": bool(family_counts(sealed).equals(family_counts(rerun))),
            "n_swapped_rows": int((m["_merge"] == "left_only").sum()),
            "eligible_agreement_matched": float((both["eligible_s"] == both["eligible_r"]).mean()) if len(both) else float("nan")}


def arm_integrity_ok(r: dict, spec: dict) -> bool:
    """An arm without a score lever (A1, A2) reproduces the sealed score rows exactly; an arm with the fee penalty or the class choice may only swap classes inside families (and keeps every matched row's eligibility)."""
    return bool(r["rows_equal"] and r["family_counts_equal"] and r["eligible_agreement_matched"] == 1.0 and (r["n_swapped_rows"] == 0 or spec["cls"] or spec["pen"]))


def gate_open(base_dir: Path) -> "tuple[bool, str]":
    f = base_dir / "identity.json"
    if not f.exists():
        return False, "identity.json is missing: build and pass the baseline first (--trial T0_baseline)"
    g = json.loads(f.read_text(encoding="utf-8"))
    return bool(g.get("ok")), ("identity gate passed" if g.get("ok") else f"identity gate FAILED: {g.get('checks')}")


# ============================================================================================================================================================================
# build: one arm
# ============================================================================================================================================================================
def build_arm(args, name: str, spec: dict, out: Path, base_dir: Path, levers_fn=None, identity_switches=None) -> None:
    """One arm. `levers_fn(spec)` -> LeverConfig and `identity_switches` (the switches that must be OFF for the baseline gate) default to this study's; PREREG P3-9 passes its own (`p3_p39_trials`).
    The frozen snapshot is `args.snapshot_dir` when the parser has one, else `<base_dir>/snapshot`."""
    t0 = time.perf_counter()
    run = Path(args.run_dir)
    pit0, inputs, hist, at = load_saved_run(run, None)
    scores0 = pit0.scores
    sample_keep = None
    if args.sample_per_nature:
        inputs, scores0, sample_keep = T._sample(inputs, scores0, args.sample_per_nature)
        print(f"[SAMPLE] {len(sample_keep)} funds ({args.sample_per_nature} per nature): a plumbing smoke test, NEVER a result")
    snap_dir = Path(getattr(args, "snapshot_dir", None) or (base_dir / "snapshot"))
    ipc, fees_df, geo = SNAP.load_snapshot(snap_dir)                                    # verified against its manifest: no live read of these three inputs
    snap_id = json.loads((snap_dir / SNAP.MANIFEST).read_text(encoding="utf-8"))["snapshot_id"]
    entry, exit_ = fees_df["Entry_Fee_Pct"], fees_df["Exit_Fee_Pct"]
    if sample_keep is not None:
        fees_df = fees_df.reindex(sample_keep)
        entry, exit_ = fees_df["Entry_Fee_Pct"], fees_df["Exit_Fee_Pct"]
    ever = pd.Index(scores0.loc[scores0["eligible"], "isin"].unique())
    de, dx = float(entry.reindex(ever).dropna().median()), float(exit_.reindex(ever).dropna().median())          # the cost-check convention for an unknown fee
    conn = None if args.no_short_gates else get_connection()
    try:
        daily = None if conn is None else (lambda: iter_daily_chunks(conn, list(inputs.nav.columns)))
        inputs2 = dataclasses.replace(inputs, ipc=ipc, fees=fees_df, daily_chunks=daily)
        cfg = (levers_fn or levers_of)(spec)
        print(f"[{name}] spec {spec} | levers {cfg} | snapshot {snap_id[:16]}")
        import shared.config as C
        run_t = compute_pit_scores(inputs2, at, hist["regime"], ParquetCache(args.cache_dir), max_stale_days=60, levers=cfg, fee_aware=bool(spec["cls"]),
                                   fee_tolerance=float(C.P3_FEE_AWARE_CLASS_SCORE_TOLERANCE) if spec["cls"] else None)       # explicit False: the global switch never leaks into an arm
        print(f"[{name}] scored: {len(run_t.scores)} rows | timings {{{', '.join(f'{k}={v:.0f}s' for k, v in run_t.timings.items())}}} | cache {run_t.cache_hits.get('scoring_blocks')}")
    finally:
        if conn is not None:
            conn.close()
    scores = run_t.scores
    key = ["as_of", "subportfolio", "isin"]
    m = scores0[key + ["score_base", "eligible"]].merge(scores[key + ["score_base", "eligible"]], on=key, how="left", suffixes=("_sealed", "_rerun"))
    meta = {"trial": name, "spec": spec, "levers": dataclasses.asdict(cfg), "snapshot_id": snap_id, "n_dates": int(len(at)), "n_funds": int(inputs.nav.shape[1]), "sample": bool(sample_keep),
            "short_gates": not args.no_short_gates, "default_entry": de, "default_exit": dx, "penalty_h_years": PENALTY_H_YEARS if spec["pen"] else None,
            "baseline_vs_sealed": {"coverage": float(m["score_base_rerun"].notna().mean()), "max_abs_score_base_diff": float((m["score_base_sealed"] - m["score_base_rerun"]).abs().max()),
                                   "eligible_agreement": float((m["eligible_sealed"] == m["eligible_rerun"]).mean())}}
    print(f"[{name}] scores vs the sealed run: {meta['baseline_vs_sealed']}")
    attrs, fx = inputs2.attrs, getattr(inputs2, "fx_exposed", None)
    kw = {}
    if spec["band"] > 0:
        kw.update(constraints=dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=spec["band"]), use_incumbents=True)
    if spec["cls"]:
        meta["class_choice_tolerance"] = float(C.P3_FEE_AWARE_CLASS_SCORE_TOLERANCE)
    prep = prepare_pit_backtest(run_t, inputs2, hist, at, windows=(1,), **kw)
    R1, cash = prep.R[1], prep.cash_ret[1]
    gross, _ = portfolio_returns(prep.W, R1, cash)
    tu = monthly_turnover(prep.W)
    cost, _miss = fee_cost_series(prep.W, entry, exit_, de, dx)
    Wp = B.pool_weights(scores, hist, at, fx_exposed=fx, exposed_budget=MAX_NON_EUR_UNHEDGED_WEIGHT)             # the mandate-matched pool of this variant's own eligible set (reference, as in P3-5A)
    gp, _ = portfolio_returns(Wp, R1, cash)
    tup = monthly_turnover(Wp)
    costp, _ = fee_cost_series(Wp, entry, exit_, de, dx)
    series = pd.DataFrame({"gross": gross, "turnover": tu, "cash": cash, "net25": gross - T.BPS / 1e4 * tu, "netfee": gross - cost,
                           "pool_net25": gp - T.BPS / 1e4 * tup, "pool_netfee": gp - costp}, index=at)
    nature_of = scores.drop_duplicates("isin").set_index("isin")["fund_nature"]
    pool = {t: set(g.loc[g["eligible"] & (g["score_final"] > 0), "isin"]) for t, g in scores.groupby("as_of")}
    arrays = {f"master_{tag}": E.stress_paths(R1, at, nature_of, pool, prep.W, q) for q, tag in ((0.10, "p10"), (0.05, "p05"))}
    out.mkdir(parents=True, exist_ok=True)
    series.to_csv(out / "series.csv", index_label="date")
    np.savez_compressed(out / "stress.npz", **arrays)
    meta["holding"] = P.holding_spells(prep.W)
    meta["elapsed_s"] = round(time.perf_counter() - t0, 1)
    if name == "T0_baseline" and sample_keep is None:
        table = pd.read_csv(Path(args.run_dir) / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
        gate = identity_gate(meta, series, table, identity_switches if identity_switches is not None else default_switches())
        meta["identity"] = gate
        (base_dir / "identity.json").write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
        print(f"[IDENTITY GATE] {'PASSED' if gate['ok'] else 'FAILED'}: {gate['checks']} | max |gross diff| {gate['max_abs_gross_diff']:.3e} | max |turnover diff| {gate['max_abs_turnover_diff']:.3e}")
    if sample_keep is None:
        meta["score_integrity"] = class_swap_integrity(scores0, scores)
        meta["integrity_ok"] = arm_integrity_ok(meta["score_integrity"], spec)
        print(f"[{name}] score rows against the sealed run: {meta['score_integrity']} -> {'OK' if meta['integrity_ok'] else 'NOT OK'}")
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(f"[{name}] done in {meta['elapsed_s']} s -> {out}")


# ============================================================================================================================================================================
# pools: the matched comparators of Q2 (PREREG_P3_7 section 4 and section 11 item 1)
# ============================================================================================================================================================================
POOLS = {"persistent_b0": (True, 0.0), "persistent_b10": (True, 0.10), "persistent_b20": (True, 0.20), "redraw": (False, None)}
POOL_OF_BAND = {0.0: "persistent_b0", 0.10: "persistent_b10", 0.20: "persistent_b20"}
SEED_BASE = {True: 1000, False: 2000}                 # persistent draws share their seeds across the bands (paired); the redraw has its own


def run_pools(args, base_dir: Path) -> None:
    """The draws of the matched pools on the BASELINE's eligible set (no arm of P3-7 changes eligibility; each arm's meta says so), costed on the stated fees fund by fund like the champions and
    built by the production engine. One parquet per pool (long: draw, date, gross, turnover, net25, netfee), resumable."""
    run = Path(args.run_dir)
    pit0, inputs, hist, at = load_saved_run(run, None)
    scores0 = pit0.scores
    if args.sample_per_nature:
        inputs, scores0, _keep = T._sample(inputs, scores0, args.sample_per_nature)
    ipc, fees_df, _geo = SNAP.load_snapshot(base_dir / "snapshot")
    entry, exit_ = fees_df["Entry_Fee_Pct"], fees_df["Exit_Fee_Pct"]
    ever = pd.Index(scores0.loc[scores0["eligible"], "isin"].unique())
    de, dx = float(entry.reindex(ever).dropna().median()), float(exit_.reindex(ever).dropna().median())
    inputs2 = dataclasses.replace(inputs, ipc=ipc, fees=fees_df)
    prep = prepare_pit_backtest(pit0, inputs2, hist, at, windows=(1,))                   # only for R1 and the cash leg: the same return matrices as the champions
    R1, cash = prep.R[1], prep.cash_ret[1]
    fx = getattr(inputs2, "fx_exposed", None)
    out_dir = base_dir / "pools"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = list(POOLS) if args.pool == "all" else [args.pool]
    for nm in names:
        persistent, band = POOLS[nm]
        f = out_dir / f"{nm}.parquet"
        if f.exists():
            print(f"[pool {nm}] already built: skipped")
            continue
        t0 = time.perf_counter()
        frames = []
        for k in range(args.draws):
            W, _c = B.random_selection_weights(scores0, inputs2.attrs, hist, at, SEED_BASE[persistent] + k, persistent, fx_exposed=fx, band=band)
            ps = V.pool_series(W, R1, cash, entry, exit_, de, dx, T.BPS)
            ps.insert(0, "draw", k)
            frames.append(ps.rename_axis("date").reset_index())
            print(f"[pool {nm}] draw {k + 1}/{args.draws} turnover {ps['turnover'].mean():.3f} ann net fee {E.ann_return(ps['netfee'].dropna().to_numpy()):.4f} ({time.perf_counter() - t0:.0f}s)", flush=True)
        pd.concat(frames, ignore_index=True).to_parquet(f)
    print(f"[pools] done in {base_dir / 'pools'}")


# ============================================================================================================================================================================
# report
# ============================================================================================================================================================================
def _load_arm(folder: Path) -> dict:
    z = np.load(folder / "stress.npz")
    return {"series": pd.read_csv(folder / "series.csv", parse_dates=["date"]).set_index("date"), "stress": {k: z[k] for k in z.files}, "meta": json.loads((folder / "meta.json").read_text(encoding="utf-8"))}


def _pool_stats(base_dir: Path, band: float) -> "dict | None":
    out = {}
    for kind, nm in (("persistent", POOL_OF_BAND[band]), ("redraw", "redraw")):
        f = base_dir / "pools" / f"{nm}.parquet"
        if not f.exists():
            return None
        d = pd.read_parquet(f)
        out[kind] = np.array([E.ann_return(g.sort_values("date")["netfee"].dropna().to_numpy()) for _k, g in d.groupby("draw")])      # the last date has no forward return: dropped, as for the champions
        out[f"{kind}_turnover"] = float(d.groupby("draw")["turnover"].mean().mean())
    return out


def run_report(args, base_dir: Path) -> None:
    runs = {n: _load_arm(base_dir / n) for n in ARMS if (base_dir / n / "meta.json").exists()}
    if "T0_baseline" not in runs:
        print("no baseline built: nothing to report")
        return
    base = runs["T0_baseline"]
    table = pd.read_csv(Path(args.run_dir) / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")
    target = table["target_1m"]
    rows, a5 = [], {}
    for n, t in runs.items():
        pools = _pool_stats(base_dir, ARMS[n]["band"]) if n != "T0_baseline" else None
        r = V.evaluate_arm(n, t, base, target, pools, episodes=T.EPISODES, design=T.DESIGN, crisis_windows=T.CRISIS_WINDOWS, holm_level=HOLM_LEVEL)
        r["integrity_ok"] = t["meta"].get("integrity_ok")
        r["n_swapped_rows"] = t["meta"].get("score_integrity", {}).get("n_swapped_rows")
        r["snapshot_id"] = t["meta"].get("snapshot_id", "")[:12]
        rows.append(r)
        if n in ("A1", "A2"):
            a5[n] = V.amendment5_verdict(t["series"], base["series"])
    res = pd.DataFrame(rows).set_index("trial")
    res.to_csv(base_dir / "adoption_table.csv")
    pd.DataFrame(a5).T.to_csv(base_dir / "amendment5_side_verdict.csv")
    ident = json.loads((base_dir / "identity.json").read_text(encoding="utf-8")) if (base_dir / "identity.json").exists() else {}

    def fmt(cols, nd=4):
        return res[[c for c in cols if c in res.columns]].round(nd).to_string()
    lines = ["# P3-7 trials: adoption table (PREREG_P3_7 section 5; Holm family 8, level %.5f)" % HOLM_LEVEL, "",
             f"Identity gate (the baseline of this build against the sealed run): {'PASSED' if ident.get('ok') else 'NOT PASSED / missing'}; checks {ident.get('checks')}. Snapshot ids of the arms: {sorted(set(res['snapshot_id']))}. "
             "History yields a CANDIDATE for the forward shadow, never an adoption (R7).", "",
             "## 1. Returns, drawdowns and turnover (annualised; `fee` = the owner's stated fees fund by fund, `25bp` = the flat cross-check)", "", "```",
             fmt(["ann_fee", "ann_25bp", "maxdd_fee", "maxdd_25bp", "stressed_worst_25bp", "stressed_worst_25bp_baseline", "turnover", "cost_drag_pp", "holding_months"]), "```", "",
             "## 2. Condition 1: drawdown (unstressed within 15 % at 25 bp; stressed relative to the baseline, 0.5 pp)", "", "```",
             fmt(["dd_cap_ok", "stress_rel_ok", "c1"] + [f"{p}_{k}_25bp" for k in T.EPISODES for p in ("dd", "dds")]), "```", "",
             "## 3. Condition 2: paired net return against the baseline at the stated fees (lower bound at the Holm level; floor 0.5 pp)", "", "```",
             fmt(["ret_mean_pp", "ret_se_pp", "ret_lb_pp", "ret_pass", "c2", "ret25_mean_pp", "ret25_lb_pp"]), "```", "",
             "## 4. Conditions 3 and 4 (both pools, draw average) and the verdict", "", "```",
             fmt(["noncrisis_degradation_pp", "share36_variant", "share36_baseline", "c3", "persistent_pool_mean_ann", "persistent_share_draws_beaten", "redraw_pool_mean_ann", "redraw_share_draws_beaten",
                  "persistent_turnover", "redraw_turnover", "beat_persistent", "beat_redraw", "c4", "candidate"]), "```", "",
             "## 5. Amendment 5 of PREREG_FND-0193 applied as written to the band-only arms (a SEPARATE verdict; the values are not re-synchronised)", "", "```",
             pd.DataFrame(a5).T.round(4).to_string() if a5 else "(A1 / A2 not built)", "```", ""]
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
        conn = get_connection()
        try:
            ipc, fees, geo = SNAP.read_live_inputs(conn)
        finally:
            conn.close()
        manifest = SNAP.freeze_inputs(base_dir / "snapshot", ipc, fees, geo)
        print(f"[SNAPSHOT] frozen in {base_dir / 'snapshot'}: id {manifest['snapshot_id']} at {manifest['frozen_utc']} | "
              + ", ".join(f"{n} {v['rows']} rows" for n, v in manifest["files"].items()))
        return 0
    if not (base_dir / "snapshot" / SNAP.MANIFEST).exists():
        print(f"no frozen snapshot in {base_dir}: run --stage snapshot first")
        return 3
    if args.stage == "build":
        names = list(ARMS) if args.trial == "all" else [args.trial]
        for n in names:
            if n not in ARMS:
                print(f"unknown trial {n!r}; choose from {list(ARMS)}")
                return 4
            if n != "T0_baseline" and not args.sample_per_nature:
                ok, why = gate_open(base_dir)
                if not ok:
                    print(f"[GATE] {n} refused: {why}")
                    return 5
            if (base_dir / n / "meta.json").exists():
                print(f"[{n}] already built in {base_dir / n}: skipped")
                continue
            build_arm(args, n, ARMS[n], base_dir / n, base_dir)
        return 0
    if args.stage == "identity":
        g = reevaluate_identity(base_dir, run)
        print(f"[IDENTITY GATE] {'PASSED' if g['ok'] else 'FAILED'}: {g['checks']} | max |gross diff| {g['max_abs_gross_diff']:.3e} | max |turnover diff| {g['max_abs_turnover_diff']:.3e}")
        return 0 if g["ok"] else 7
    if args.stage == "pools":
        ok, why = gate_open(base_dir)
        if not ok and not args.sample_per_nature:
            print(f"[GATE] pools refused: {why}")
            return 5
        run_pools(args, base_dir)
        return 0
    run_report(args, base_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
