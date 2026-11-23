#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Feasibility study F of the drawdown-management RFC (Package L, PREREG_P3_10_FEAS, FND-0430). READ-ONLY on the sealed PIT run (hash-verified) and the P3-7 frozen snapshot: no database, no
production change, no P2 recompute. The pure pieces and their tests are in proyecto3/src/pit_cdar.py.

  --stage design   the DESIGN-POWER count only (stress months and episodes). Allowed before the preregistration is committed.
  --stage run      F1 census -> F1b predictive gate -> F2 hindsight upper bound, for the four cells of the sensitivity grid; the verdict is the PRIMARY cell (beta cap 0.3, stress 3 %).
                   REFUSES to run unless PREREG_P3_10_FEAS.md is in git HEAD (the preregistration is committed before any outcome is read).

Every number the verdict uses is fixed in the preregistration; nothing is tuned here.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from proyecto3.src import pit_cdar as C  # noqa: E402
from proyecto3.src import pit_crisis_decompose as X  # noqa: E402
from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402
from proyecto3.src import pit_p37_snapshot as SNAP  # noqa: E402
from proyecto3.src import pit_selection_test as ST  # noqa: E402
from proyecto3.src.pit_backtest import cash_index, forward_returns  # noqa: E402
from proyecto3.src.pit_cache import ParquetCache  # noqa: E402
from proyecto3.src.pit_candidates import DEFAULT_MAX_STALE_DAYS  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_run import compute_pit_scores  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402

PREREG_NAME = "PREREG_P3_10_FEAS.md"
DEFAULT_RUN = ROOT / "out" / "reports" / "pit_backtest" / RUN_STAMP
DEFAULT_SNAPSHOT = ROOT / "out" / "reports" / "p3_p37_trials" / "20261121_p37" / "snapshot"
DEFAULT_OUT = ROOT / "out" / "reports" / "p3_cdar_feasibility"

# ---- the preregistration's fixed values (sections 3-4, answers of section 10) ----------------------------------------------------------------------------------------------------
GRID = ((0.3, 0.03), (0.5, 0.03), (0.3, 0.05), (0.5, 0.05))          # (beta cap, stress threshold); the FIRST is the primary cell
GAP = 2                                                              # D1: runs of stress months separated by at most 2 months are one episode
SUB_ORDER = ("Defensiva", "Equilibrada", "Dinamica")
DD_CAP = -0.15
ACTIVE_SHARE_MIN = 0.25
GROSS_MIN, NET_MIN = 0.005, 0.0035
HAIRCUT_A = 0.10                                                     # a; k = 2 (the P3-5A value) decides, k = 3 is reported
TAIL_SHARE = 0.10
N_NULL, NULL_SEED, NULL_Q = 500, 20261123, 0.95
HOLM_N = 676 + 2
CADENCES = ("monthly", "quarterly")
SCRATCH_CEILING = 0.0414
CEILING_TOL = 6e-5


def prereg_in_git_head() -> bool:
    try:
        out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=True).stdout
    except Exception:                                                  # noqa: BLE001 -- unreadable git means refuse
        return False
    return any(Path(line).name == PREREG_NAME for line in out.splitlines())


# ------------------------------------------------------------------------------------------------------------------ the sealed world
def load_world(args) -> dict:
    run = Path(args.run_dir)
    verified = verify_seal(run)
    pit_run, inputs, hist, at = load_saved_run(run, None)                    # the run's own inputs/ snapshot: no database
    at = pd.DatetimeIndex(at)
    scores = pit_run.scores
    ipc, fees, _geo = SNAP.load_snapshot(Path(args.snapshot_dir))
    R1 = forward_returns(inputs.nav, at, 1, DEFAULT_MAX_STALE_DAYS)         # dates x isin: the return t -> t+1 (the convention of the sealed pools and of the 4.14 % ceiling)
    ret = R1.copy()
    ret.index = R1.index + pd.offsets.MonthEnd(1)                             # the same returns indexed by the month they are EARNED
    grid = pd.date_range(at.min(), at.max() + pd.offsets.MonthEnd(1), freq=pd.offsets.MonthEnd())
    cidx = cash_index(inputs.rate, grid)
    cash_fwd = pd.Series(cidx.reindex(at + pd.offsets.MonthEnd(1)).to_numpy() / cidx.reindex(at).to_numpy() - 1.0, index=at)
    cash_real = cash_fwd.copy()
    cash_real.index = at + pd.offsets.MonthEnd(1)
    elig = scores[scores["eligible"].astype(bool)].groupby("as_of")["isin"].agg(lambda s: sorted(set(s)))
    pool = {}
    for prev, t in zip(at[:-1], at[1:]):                                      # the pool actually available at the previous month-end earns month t
        cols = [i for i in elig.get(prev, []) if i in ret.columns]
        if cols and t in ret.index:
            pool[t] = float(ret.loc[t, cols].mean())
    pool = pd.Series(pool, dtype=float)
    first_full = pool.dropna().index[C.WINDOW - 1]
    fee_known = fees.reindex(ret.columns).apply(pd.to_numeric, errors="coerce")
    med = fee_known.median()
    entry = fee_known["Entry_Fee_Pct"].fillna(med["Entry_Fee_Pct"]) / 100.0       # the unknown fee is the median of the known ones, said here and reported, never filled inside the module
    exit_ = fee_known["Exit_Fee_Pct"].fillna(med["Exit_Fee_Pct"]) / 100.0
    unknown = fee_known["Entry_Fee_Pct"].isna() | fee_known["Exit_Fee_Pct"].isna()
    return dict(verified=verified, run=run, inputs=inputs, hist=hist, at=at, scores=scores, ipc=ipc, R1=R1, ret=ret, cash_real=cash_real, pool=pool,
                eval_dates=[t for t in at if t >= first_full], entry=entry, exit_=exit_, unknown_fee=unknown, fee_median=med.to_dict(),
                manager_of=inputs.attrs["Management_Company"] if "Management_Company" in inputs.attrs.columns else pd.Series(dtype=object))


def classify(world: dict, cache_dir: Path, store: Path, reuse: bool) -> pd.DataFrame:
    """One row per (date, sub-portfolio, isin): the sealed eligibility and `classify_exclusion` from the independently evaluated metric filters (needs the metric frames of every date: a rescore of the
    sealed run on its own inputs plus the frozen IPC, validated against the sealed `score_base` exactly as the turning-point diagnostic does). Stored, so a rerun does not rescore."""
    if reuse and store.exists():
        print(f"[CLASSIFY] reusing {store}")
        return pd.read_parquet(store)
    import p3_crisis_diagnostic as DIAG                                         # constants of the validated rescore (same folder)
    at, scores, inputs = world["at"], world["scores"], world["inputs"]
    frames: dict = {}
    t0 = time.time()
    run2 = compute_pit_scores(dataclasses.replace(inputs, ipc=world["ipc"]), pd.DatetimeIndex(at), world["hist"]["regime"], ParquetCache(cache_dir), max_stale_days=60, frames_out=frames)
    print(f"[RESCORE] {len(at)} dates in {time.time() - t0:.0f}s")
    key = ["as_of", "subportfolio", "isin"]
    m = scores[key + ["score_base"]].merge(run2.scores[key + ["score_base"]], on=key, how="left", suffixes=("_sealed", "_rescored"))
    coverage = float(m["score_base_rescored"].notna().mean())
    diff = float((m["score_base_sealed"] - m["score_base_rescored"]).abs().max())
    ok = coverage >= DIAG.MIN_COVERAGE and diff <= DIAG.SCORE_BASE_TOL
    print(f"[VALIDATE] score_base rescored vs sealed: coverage {coverage:.4f}, max |diff| {diff:.6f} -> {'VALIDATED' if ok else 'REFUSED'}")
    if not ok:
        raise RuntimeError("the rescore does not reproduce the sealed scores: the classification would not be the sealed run's")
    why = D.first_reason(scores)
    parts, dd_first, dd_first_ok = [], 0, 0
    for (t, sub), g in scores.groupby(["as_of", "subportfolio"]):
        frame = frames[pd.Timestamp(t)]
        fl = X.independent_filters(frame, sub)
        f_dd = fl["f_max_drawdown"]
        f_other = fl.drop(columns="f_max_drawdown").any(axis=1)
        isins = g["isin"].to_numpy()
        assert len(set(isins)) == len(isins), "duplicate isin in one (date, sub-portfolio)"
        fr = pd.Series(why.loc[g.index].to_numpy(), index=isins)
        cl = C.classify_exclusion(fr, f_dd, f_other)
        was_dd = (fr == "max_drawdown")
        dd_first += int(was_dd.sum())
        dd_first_ok += int((was_dd & f_dd.reindex(isins, fill_value=False)).sum())
        parts.append(pd.DataFrame({"as_of": t, "sub": sub, "isin": isins, "nature": g["fund_nature"].to_numpy(), "eligible": g["eligible"].to_numpy(bool), "cls": cl.reindex(isins).to_numpy()}))
    cls = pd.concat(parts, ignore_index=True)
    print(f"[CLASSIFY] rows with the sealed first reason 'max_drawdown': {dd_first}, of which the independent drawdown filter also fails: {dd_first_ok / max(dd_first, 1):.2%}")
    store.parent.mkdir(parents=True, exist_ok=True)
    cls.to_parquet(store)
    return cls


# ------------------------------------------------------------------------------------------------------------------ one cell of the grid
def run_f1_f1b(world: dict, sets: dict, cells: tuple, beta_max: float, thr: float, out: Path, tag: str) -> dict:
    ret, pool, dates = world["ret"], world["pool"], world["eval_dates"]
    cell_of, sub_of = cells
    stress = C.stress_months(pool, thr)
    ep = C.stress_episodes(stress, GAP)
    power = C.design_power(stress, gap=GAP)
    set_a, set_b = sets["a"], sets["b"]
    cands = {t: C.convex_candidates(ret, pool, stress, t, beta_max=beta_max, pool=set_b.get(t, [])) for t in dates}
    thin = C.thin_window(cands)
    active = float(np.mean([len(thin[t]) > 0 for t in dates]))
    per_sub = {s: [sum(1 for i in cands[t] if sub_of.get(i) == s) for t in dates] for s in SUB_ORDER}
    med = {s: float(np.median(v)) for s, v in per_sub.items()}
    f1_stop = bool(active < ACTIVE_SHARE_MIN or all(m < C.MIN_CANDIDATES for m in med.values()))
    census = pd.DataFrame({"date": dates, "n_set_a": [len(set_a.get(t, [])) for t in dates], "n_set_b": [len(set_b.get(t, [])) for t in dates],
                           "n_candidates": [len(cands[t]) for t in dates], "n_candidates_thin": [len(thin[t]) for t in dates],
                           **{f"cand_{s}": per_sub[s] for s in SUB_ORDER}})
    census.to_csv(out / f"f1_census_{tag}.csv", index=False)
    obs, n_cells, no_fwd = C.persistence_observations(ret, pool, stress, ep, cands, set_b, cell_of, sub_of, dates)
    obs.to_csv(out / f"f1b_observations_{tag}.csv", index=False)
    verdict = C.persistence_verdict(obs, n_cells)
    return dict(stress=stress, episodes=ep, power=power, cands=cands, thin=thin, active_share=active, median_per_sub=med, f1_stop=f1_stop, census=census,
                f1b=verdict, n_cand_cells=n_cells, no_forward=no_fwd, level_counts=obs["level"].value_counts().to_dict() if len(obs) else {})


def run_f2(world: dict, sets: dict, cell: dict, out: Path, tag: str, paths: dict, S3: pd.DataFrame) -> dict:
    at, ret, cash = world["at"], world["ret"], world["cash_real"]
    dates = list(at)
    set_b_full = {t: sets["b"].get(t, []) for t in dates}
    thin_full = {t: cell["thin"].get(t, []) for t in dates}
    zero = pd.Series(0.0, index=ret.columns)
    flat = pd.Series(0.0025, index=ret.columns)
    front = C.optimise_mix(S3, DD_CAP)
    res = {"front_ann": front["ann"], "front_weights": front["weights"].to_dict()}
    variants = {"gross": (zero, zero, 0.0), "stated_k2": (world["entry"], world["exit_"], HAIRCUT_A * 2), "stated_k3": (world["entry"], world["exit_"], HAIRCUT_A * 3),
                "flat25_k2": (flat, flat, HAIRCUT_A * 2)}
    rows, observed = [], {}
    for cad in CADENCES:
        for name, (ent, ext, hs) in variants.items():
            basket, state, Wt = C.basket_by_decision_date(thin_full, dates, ret, cash, ent, ext, cad, C.MIN_CANDIDATES, hs, paths["p10_excl"] if hs else None)
            g = C.gain_by_state(S3, basket, state, DD_CAP)
            tail_new = ST.tail_stress_max_dd(g["r_new"], paths["p10_all"].reindex(g["r_new"].index), TAIL_SHARE)
            tail_front = ST.tail_stress_max_dd(g["r_front"], paths["p10_all"].reindex(g["r_front"].index), TAIL_SHARE)
            traded = Wt.diff().abs().fillna(Wt.abs()).sum()
            unk = float(traded[traded.index.isin(world["unknown_fee"][world["unknown_fee"]].index)].sum() / max(traded.sum(), 1e-12))
            rows.append({"cadence": cad, "variant": name, "gain_active": g["ACTIVE"], "gain_cash": g["CASH"], "gain_total": g["total"], "ann_front": g["ann_front"], "ann_new": g["ann_new"],
                         "geo_gain": g["ann_new"] - g["ann_front"], "dd_new": g["dd_new"], "dd_new_stressed": tail_new, "dd_front_stressed": tail_front, "w_convex": float(g["weights"]["Convex"]),
                         "months_active": int((state == "ACTIVE").sum()), "unknown_fee_traded_share": unk})
            observed[(cad, name)] = (basket, state, g)
    table = pd.DataFrame(rows)
    table.to_csv(out / f"f2_variants_{tag}.csv", index=False)
    # the Brinson split of the net monthly variant
    basket, state, g = observed[("monthly", "stated_k2")]
    S4 = S3.copy()
    S4["Convex"] = basket.reindex(S3.index)
    S4 = S4.dropna()
    sleeve, realloc = C.brinson_split(S4, g["weights"], g["weights_front"][list(S3.columns)])
    st = state.reindex(S4.index)
    brinson = {k: {"sleeve": float(12 * sleeve[st == k].sum() / len(S4)), "reallocation": float(12 * realloc[st == k].sum() / len(S4))} for k in ("ACTIVE", "CASH")}
    res.update(table=table, brinson=brinson)
    # the null: random baskets from the same excluded sets, scored on Gain_ACTIVE with the observed state
    nulls = {}
    for cad in CADENCES:
        _, state_obs, g_obs = observed[(cad, "stated_k2")]
        rng = np.random.default_rng(NULL_SEED)
        vals = []
        for _ in range(N_NULL):
            draw = C.random_candidates(thin_full, set_b_full, rng)
            b, _s, _w = C.basket_by_decision_date(draw, dates, ret, cash, world["entry"], world["exit_"], cad, C.MIN_CANDIDATES, HAIRCUT_A * 2, paths["p10_excl"])
            vals.append(C.gain_by_state(S3, b, state_obs, DD_CAP)["ACTIVE"])
        v = np.array(vals)
        nulls[cad] = {"observed": g_obs["ACTIVE"], "p95": float(np.quantile(v, NULL_Q)), "mean": float(v.mean()), "rank_share": float((v >= g_obs["ACTIVE"]).mean())}
    res["null"] = nulls
    res["reach"] = C.reach({t: thin_full[t] for t in world["eval_dates"]}, world["manager_of"])
    gross = table[(table.cadence == "monthly") & (table.variant == "gross")].iloc[0]
    res["gross_ok"] = bool(gross["gain_active"] >= GROSS_MIN)
    res["net_ok"] = {}
    for cad in CADENCES:
        r = table[(table.cadence == cad) & (table.variant == "stated_k2")].iloc[0]
        res["net_ok"][cad] = bool(r["gain_active"] >= NET_MIN and r["gain_active"] > nulls[cad]["p95"] and r["dd_new_stressed"] >= DD_CAP and r["dd_new"] >= DD_CAP)
    res["passed"] = bool(res["gross_ok"] and any(res["net_ok"].values()))
    return res


def paths_for(world: dict, sets: dict) -> dict:
    """The realised P10 paths of the survivorship stress (`realised_quantile_path`): one among the funds ever eligible with a complete path over the whole window (for the tail stress of a mix), one
    among the funds that were ever in an excluded set with a complete path over the evaluation window (for the basket haircut; falls back to the first when none is complete)."""
    R1, scores, at, ev = world["R1"], world["scores"], world["at"], world["eval_dates"]
    rows = [t for t in at if R1.loc[t].notna().any()]
    ever = [c for c in sorted(set(scores.loc[scores["eligible"].astype(bool), "isin"])) if c in R1.columns]
    p10_all = ST.realised_quantile_path(R1.loc[rows, ever], 0.10)
    ex_ever = [c for c in sorted({i for t in ev for i in sets["b"].get(t, [])}) if c in R1.columns]
    note = "excluded-set survivors"
    try:
        p10_excl = ST.realised_quantile_path(R1.loc[[t for t in rows if t in set(ev)], ex_ever], 0.10)
    except ValueError:
        p10_excl, note = p10_all, "FALLBACK: no excluded-set fund has a complete path over the evaluation window; the all-eligible P10 path is used"
    return {"p10_all": p10_all, "p10_excl": p10_excl, "note": note}


# ------------------------------------------------------------------------------------------------------------------ stages
def stage_design(run_dir: Path, snapshot_dir: Path) -> int:
    class A:                                                                  # the same loader as the outcome stage, so the design count is on the same returns
        pass
    a = A()
    a.run_dir, a.snapshot_dir = run_dir, snapshot_dir
    w = load_world(a)
    stress = C.stress_months(w["pool"])
    print(f"[SEAL] {len(w['verified'])} artifacts verified for run {Path(run_dir).name}; [POOL] {len(w['pool'])} months {w['pool'].index.min().date()}..{w['pool'].index.max().date()}")
    rc = 0
    for gap in (0, 2):
        p = C.design_power(stress, gap=gap)
        print(f"[DESIGN gap={gap}] stress months {p['n_stress_months']} of {p['n_months']} | episodes {p['n_episodes']} (lengths {p['episode_lengths']}) | enough (>= {C.MIN_EPISODES}): "
              f"{p['enough_episodes']} | dates where a stress beta can exist: {p['share_dates_with_beta']:.1%}")
        rc = rc or (0 if p["enough_episodes"] else 3)
    return rc


def stage_run(args) -> int:
    if not prereg_in_git_head():
        print(f"[REFUSED] {PREREG_NAME} is not in git HEAD: the outcome stages run only after the preregistration is committed")
        return 4
    world = load_world(args)
    out = Path(args.out_root) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    print(f"[SEAL] {len(world['verified'])} artifacts verified | [OUT] {out}")
    cls = classify(world, Path(args.cache_dir), Path(args.out_root) / "classification.parquet", args.reuse)
    sets_a, sets_b = C.excluded_sets(cls)
    sets = {"a": sets_a, "b": sets_b}
    cells = C.primary_cells(cls[["isin", "sub", "nature"]].drop_duplicates(), SUB_ORDER)
    S3 = D.pool_returns(world["scores"], world["R1"], world["at"])
    front = D.best_static_mix(S3)
    mine = C.optimise_mix(S3, DD_CAP)
    reproduced = abs(front["ann"] - SCRATCH_CEILING) <= CEILING_TOL and abs(front["ann"] - mine["ann"]) < 1e-9
    print(f"[FRONT] three-pool ceiling at -15 %: {front['ann']:.4%} (scratch {SCRATCH_CEILING:.2%}); optimise_mix {mine['ann']:.4%} -> {'REPRODUCED' if reproduced else 'NOT REPRODUCED'}")
    if not reproduced:
        print("[STOP] the three-pool frontier is not reproduced: no fourth column is added to an unreproduced baseline")
        return 3
    lines = [f"# Feasibility study F (PREREG P3-10F), run {out.name}", "", f"Sealed run {world['run'].name} ({len(world['verified'])} artifacts verified); the preregistration is in git HEAD. "
             f"Three-pool ceiling at -15 %: {front['ann']:.4%} (REPRODUCED against the 4.14 % of `P3_L3_MANDATE_AND_FEASIBILITY.md`).", ""]
    results = {}
    primary_pass_f1 = False
    for k, (beta_max, thr) in enumerate(GRID):
        tag = f"b{beta_max}_s{int(thr * 100)}"
        cell = run_f1_f1b(world, sets, cells, beta_max, thr, out, tag)
        f1b = cell["f1b"]
        ok1 = not cell["f1_stop"]
        ok1b = bool(f1b["passed"])
        print(f"[F1 {tag}] active share {cell['active_share']:.1%}, median candidates per sub {cell['median_per_sub']} -> {'STOP' if cell['f1_stop'] else 'ok'} | episodes {cell['power']['n_episodes']} | "
              f"[F1b] mean {f1b['mean']:+.4f} lb {f1b['lb']:+.4f} clusters {f1b['n_clusters']} sign {f1b['sign_count']} dropped {f1b['dropped_share']:.0%} evaluable {f1b['evaluable']} -> {'PASS' if ok1b else 'STOP'}")
        cell["f1_ok"], cell["f1b_ok"] = ok1, ok1b
        results[tag] = cell
        if k == 0:
            primary_pass_f1 = ok1 and ok1b
    primary_tag = f"b{GRID[0][0]}_s{int(GRID[0][1] * 100)}"
    paths = None
    for tag, cell in results.items():
        if primary_pass_f1 and cell["f1_ok"] and cell["f1b_ok"]:
            paths = paths or paths_for(world, sets)
            print(f"[F2 {tag}] building baskets and the null ({N_NULL} draws x {len(CADENCES)} cadences)")
            cell["f2"] = run_f2(world, sets, cell, out, tag, paths, S3)
            f = cell["f2"]
            print(f"[F2 {tag}] gross ok {f['gross_ok']} | net ok {f['net_ok']} | passed {f['passed']}")
    # ---- verdict and report
    prim = results[primary_tag]
    if not prim["f1_ok"]:
        verdict, why = "STOP", "F1: the Convex column is too often empty or too thin"
    elif not prim["f1b_ok"]:
        verdict, why = "STOP", "F1b: ex-ante convexity does not predict later stress behaviour (or the gate is not evaluable)"
    elif prim["f2"]["passed"]:
        others = [t for t, c in results.items() if t != primary_tag and "f2" in c and not c["f2"]["passed"]]
        others += [t for t, c in results.items() if t != primary_tag and "f2" not in c]
        verdict = "PASS" + (" (FRAGILE)" if len(others) >= 2 else "")
        why = "F2 passes in the primary cell" + (f"; fails or stops in {len(others)} of the other three cells: {others}" if others else "")
    else:
        verdict, why = "STOP", "F2: the gain does not clear the gross, friction, haircut, null or drawdown conditions in the primary cell"
    print(f"[VERDICT] {verdict}: {why}")
    lines += [f"## Verdict: **{verdict}**", "", why, "", f"Holm reference (printed, not applied): 0.05 / {HOLM_N} = {0.05 / HOLM_N:.2e}.", ""]
    for tag, cell in results.items():
        f1b = cell["f1b"]
        lines += [f"## Cell {tag}" + (" (PRIMARY)" if tag == primary_tag else ""), "",
                  f"- Stress: {cell['power']['n_stress_months']} months, {cell['power']['n_episodes']} episodes (gap {GAP}), lengths {cell['power']['episode_lengths']}.",
                  f"- F1: active share of the Convex column {cell['active_share']:.1%}; median candidates per sub-portfolio {cell['median_per_sub']} -> {'STOP' if cell['f1_stop'] else 'ok'}.",
                  f"- F1b: mean {f1b['mean']:+.4f}, one-sided 95 % bound {f1b['lb']:+.4f}, {f1b['n_obs']} observations in {f1b['n_clusters']} episodes, sign count {f1b['sign_count']}, "
                  f"dropped {f1b['dropped_share']:.0%} (levels {cell['level_counts']}), dates with no stress ahead {cell['no_forward']} -> evaluable {f1b['evaluable']}, "
                  f"{'PASS' if cell['f1b_ok'] else 'STOP'}."]
        if "f2" in cell:
            f = cell["f2"]
            lines += ["", f"- F2: three-pool ceiling {f['front_ann']:.4%}; gross ok {f['gross_ok']}; net ok {f['net_ok']}; passed {f['passed']}; construction reach {f['reach']:.1%}.",
                      f"- Null (500 random baskets, `Gain_ACTIVE` with the observed state): {json.dumps({k: {a: round(b, 5) for a, b in v.items()} for k, v in f['null'].items()})}.",
                      f"- Brinson split of the net monthly variant (annual, by state): {json.dumps({k: {a: round(b, 5) for a, b in v.items()} for k, v in f['brinson'].items()})}.", "", "```",
                      f["table"].round(5).to_string(index=False), "```"]
        lines.append("")
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "verdict.json").write_text(json.dumps({"verdict": verdict, "why": why, "primary": primary_tag}, indent=2), encoding="utf-8")
    print(f"[OUT] {out}")
    return 0 if verdict.startswith("PASS") else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["design", "run"], default="design")
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "proyecto3" / "cache" / "pit")
    ap.add_argument("--reuse", action="store_true", help="reuse the stored classification instead of rescoring")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return stage_design(args.run_dir, args.snapshot_dir) if args.stage == "design" else stage_run(args)
    except (SealMismatch, SNAP.SnapshotMismatch) as exc:
        print(f"[REFUSED] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
