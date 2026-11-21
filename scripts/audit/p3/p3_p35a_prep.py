# scripts/audit/p3/p3_p35a_prep.py
# -*- coding: utf-8 -*-
r"""
Package L, session 2 (roadmap v5, plan rev. 3.1): the measurements that prepare PREREG_P3_5A_DRAFT, on the SEALED saved PIT run. READ-ONLY (nothing is written outside the report folder; the
database is only read). The ex-ante rules each stage applies are written, with their time, in section 9 of doc/decisiones/PREREG_P3_5A_DRAFT.md BEFORE the stage is run.

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_p35a_prep.py --run-dir out\reports\pit_backtest\20261119_202509 --stage c1a
    ... --stage c1b | c2 | c34        (one stage per call)

  c1a   minimum detectable effect from the paired monthly differences of the band / cadence grid (standard errors only, no new point estimate) and the full N of the trial ledger.
  c1b   pool drawdown of the locked episodes. Refuses unless the contamination log of the draft, in the working tree AND in git HEAD, holds a freeze line for the CURRENT L3 text.
        Reads the POOL return series only: no score, no selection, no lever output.
  c2    funnel time series and the lock-in gap of the since-inception drawdown filter (baseline only; June of 2006..2025, the locked windows are skipped).
  c34   D6 Brinson split on means of the 2008 design dates (top gap of Equilibrada, bottom gap of the master portfolio) with the attribute split, and the L4 scale check.
Exit codes: 2 seal mismatch, 4 rescored score_base does not reproduce, 5 freeze not recorded.
"""
import argparse
import subprocess
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))          # the sibling scripts are imported by bare module name (scripts/AGENTS.md)

import p3_cost_check as CC  # noqa: E402
import p3_crisis_diagnostic as CD  # noqa: E402
import pit_trial_ledger as TL  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import pit_crisis_decompose as X  # noqa: E402
from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402
from proyecto3.src import pit_p35a_prep as P  # noqa: E402
from proyecto3.src.pit_backtest import prepare_pit_backtest  # noqa: E402
from proyecto3.src.pit_cost_check import fee_cost_series, net_series  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402

DRAFT_REL = "doc/decisiones/PREREG_P3_5A_DRAFT.md"
COLS = ["Fund_Nature", "return_ann_real", "sharpe", "max_dd", "alpha_persistence", "capture_ratio", "momentum_rank", "srri_nav", "Credit_Quality"]
GRID_VARIANTS = ("band_0.05", "band_0.1", "band_0.2", "cadence_3m", "cadence_6m", "cadence_12m")
P35A_TRIALS, AMENDMENT5_GRID, FROZEN_COUNTERFACTUALS = 11, 16, 12        # planned P3-5a runs, the grid of Amendment 5 (conservative), the frozen counterfactuals of FND-0435
ALPHA, POWER = 0.05, 0.80
C2_YEARS = tuple(range(2014, 2022))                                      # the June dates whose 60-month window excludes the 2008-09 trough
H_FIXED_MONTHS = (12, 18, 24, 36)
MIN_CELL = 15


def build_parser():
    p = argparse.ArgumentParser(description="Package L session 2 measurements on the sealed PIT run (read-only).")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--stage", required=True, choices=("c1a", "c1b", "c2", "c34"))
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_p35a_prep"))
    p.add_argument("--cache-dir", default=str(_ROOT / "proyecto3" / "cache" / "pit"))
    return p


def _out(args) -> Path:
    out = Path(args.out_root) / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + args.stage)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _ret12(nav: pd.DataFrame, t, isins) -> pd.Series:
    """HINDSIGHT 12-month return of each fund from month-end `t` (NaN when either NAV is missing: never filled)."""
    pos = nav.index.get_indexer([pd.Timestamp(t)])[0]
    cols = [i for i in isins if i in nav.columns]
    if pos < 0 or pos + 12 >= len(nav.index):
        return pd.Series(np.nan, index=list(isins), dtype=float)
    return (nav.iloc[pos + 12][cols] / nav.iloc[pos][cols] - 1.0).reindex(list(isins))


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------
def stage_c1a(args, scores, inputs, hist, at, pit_run):
    from shared.db import get_connection
    conn = get_connection()
    try:
        entry, exit_ = CC._load_fees(conn)
    finally:
        conn.close()
    var = CC._variants(pit_run, inputs, hist, at, (0.0, 0.05, 0.10, 0.20), (1, 3, 6, 12))
    g0, t0, _cash0, W0 = var["band_0"]
    ever = pd.Index(scores.loc[scores["eligible"], "isin"].unique())
    de, dx = float(entry.reindex(ever).dropna().median()), float(exit_.reindex(ever).dropna().median())          # the cost-check convention for an unknown fee
    cost0, _ = fee_cost_series(W0, entry, exit_, de, dx)

    rows = []
    for label in GRID_VARIANTS:
        g, tu, _c, W = var[label]
        regimes = [("flat_25bp", net_series(g, tu, 25.0) - net_series(g0, t0, 25.0))]
        if W is not None:
            cost, _ = fee_cost_series(W, entry, exit_, de, dx)
            regimes.append(("stated_fees", (g - cost) - (g0 - cost0)))
        for regime, diff in regimes:
            r = P.paired_se_annual_pp(diff.to_numpy(dtype=float), return_n=True)
            rows.append({"variant": label, "regime": regime, **r})
    t = pd.DataFrame(rows)

    ledger_rows, ledger_info = TL.scan(_ROOT, TL.DEFAULT_ROOTS, _ROOT / TL.DEFAULT_LOG_DIR)
    ledger = TL.summarise(ledger_rows, ledger_info)
    n_scan = int(ledger["N_scan_upper"])
    n_full = n_scan + AMENDMENT5_GRID + FROZEN_COUNTERFACTUALS + P35A_TRIALS
    levels = {"alpha_0.05": ALPHA, "holm_11_trials": ALPHA / P35A_TRIALS, "holm_full_N": ALPHA / n_full}
    for name, a in levels.items():
        t[f"mde_{name}"] = [P.mde_pp(se, a, POWER) for se in t["se_pp"]]
    med = t.groupby("regime")[["se_pp"] + [f"mde_{k}" for k in levels]].median()
    lines = ["## C1a. Minimum detectable effect from the paired monthly differences of the band / cadence grid", "",
             "Standard errors of the MEAN monthly difference (variant minus baseline), annualised as 12 x the mean, stationary block bootstrap (mean block 6, 4,000 resamples, seed 12345). No point "
             "estimate is computed. MDE = (z(1 - alpha) + z(0.80)) x SE, one-sided. The median over the variants is the feasibility figure of the draft.", "",
             "```", t.round(4).to_string(index=False), "```", "", "Median over the variants (pp a year):", "", "```", med.round(3).to_string(), "```", "",
             f"Full N: ledger scan N_scan_upper = {n_scan} (every grid row, run folder and run log under the PIT roots; counts the runs made after the lock of the ledger) + {AMENDMENT5_GRID} "
             f"(grid of Amendment 5, conservative) + {FROZEN_COUNTERFACTUALS} (frozen counterfactuals of FND-0435, not in the scan) + {P35A_TRIALS} (planned P3-5a runs) = **{n_full}**. "
             f"Holm levels: 0.05 / {P35A_TRIALS} = {ALPHA / P35A_TRIALS:.4f}; 0.05 / {n_full} = {ALPHA / n_full:.6f}.", ""]
    return lines, {"c1a_mde.csv": t}, 0, {"n_scan": n_scan, "n_full": n_full, "median": med}


def stage_c1b(args, scores, prep, at):
    work = (_ROOT / DRAFT_REL).read_text(encoding="utf-8")
    try:
        head = subprocess.run(["git", "show", f"HEAD:{DRAFT_REL}"], cwd=str(_ROOT), capture_output=True, text=True, check=True, timeout=60).stdout
        P.assert_freeze_recorded(work)
        got = P.assert_freeze_recorded(head)
    except (P.FreezeNotRecorded, subprocess.CalledProcessError, OSError) as exc:
        print(f"[FREEZE] REFUSED: {exc}")
        return ["REFUSED: the L3 freeze is not recorded in the working tree and in git HEAD of the draft."], {}, 5, {}
    print(f"[FREEZE] L3 sha256 {got['digest'][:16]}.. recorded at {got['utc']} in the working tree and in git HEAD: the count may run")
    pool = P.master_pool_returns(scores, prep.R[1], at)
    t = P.pool_dd_count(pool, D.LOCKED_EPISODES, gate=0.04, recovery_months=12)
    n_inf, n_rec = int(t["informative"].sum()), int(t["informative_with_recovery"].sum())
    lines = ["## C1b. Pool drawdown of the locked episodes (freeze recorded: sha256 " + got["digest"] + f", {got['utc']})", "",
             "Equal-weight pool of the unique eligible funds, peak-to-trough over the calendar months of each window; only this drawdown and the flags are read.", "",
             "```", t.round(4).to_string(index=False), "```", "",
             f"Informative (pool drawdown >= 4 %, complete window): {n_inf} of {len(t)}; informative WITH 12 months of recovery data: {n_rec}. "
             f"Rule: fewer than 3 informative with recovery data means the confirmation is descriptive and adoption relies on the forward shadow -> {'DESCRIPTIVE' if n_rec < 3 else 'CONFIRMATORY'}.", ""]
    return lines, {"c1b_pool_dd.csv": t}, 0, {"informative": n_inf, "with_recovery": n_rec}


def stage_c2(args, scores, inputs, hist, at, prereg):
    dates = [t for t in at if t.month == 6 and 2006 <= t.year <= 2025 and not D.locked_episode(t)]
    D.assert_dates_allowed(dates, prereg)
    ok, cov, diff, _ipc, ipc_id, frames, _run2 = CD.rescore_validated(args.cache_dir, scores, inputs, hist, dates)
    if not ok:
        return [f"REFUSED: the rescored score_base does not reproduce (coverage {cov:.4f}, max diff {diff:.6f})."], {}, 4, {}
    nav = inputs.nav
    funnel, lock = [], []
    for t in dates:
        st = scores[scores["as_of"] == t]
        frame = frames[t]
        age = P.trough_age_months(nav, t)
        for sub in D.SUBPORTFOLIOS:
            block = st[st["subportfolio"] == sub]
            funnel.append(X.funnel_table(frame[COLS], sub, block.set_index("isin")["eligible"], block.set_index("isin")["exclusion_reason"]).reset_index().assign(date=t, subportfolio=sub))
            if sub == "Defensiva":
                continue
            limit = fs.MAX_DRAWDOWN_BY_SUB[sub]
            funds = X.sub_frame(frame[COLS], sub)
            funds = funds[funds["Fund_Nature"] == "Renta Variable"]
            roll = P.rolling_max_dd(nav, t, 60).reindex(funds.index)
            valid = roll.notna()
            since_fail = funds["max_dd"].lt(limit)
            roll_fail = roll.lt(limit)
            stale = age.reindex(funds.index)[since_fail & valid]
            lock.append({"date": t, "subportfolio": sub, "n_rv": int(len(funds)), "n_valid_roll60": int(valid.sum()), "limit": limit,
                         "since_inception_pass": float((~since_fail[valid]).mean()) if valid.any() else np.nan,
                         "rolling60_pass": float((~roll_fail[valid]).mean()) if valid.any() else np.nan,
                         "n_since_fail": int((since_fail & valid).sum()), "share_fail_trough_over_60m": float((stale > 60).mean()) if len(stale) else np.nan})
    lockin = pd.DataFrame(lock)
    lockin["lockin_gap"] = lockin["rolling60_pass"] - lockin["since_inception_pass"]
    eq = lockin[(lockin["subportfolio"] == "Equilibrada") & lockin["date"].dt.year.isin(C2_YEARS)]
    gap = float(eq["lockin_gap"].mean())
    form = "ROLLING 60-month drawdown" if gap >= 0.10 else "SINCE-INCEPTION drawdown"
    show = lockin.assign(year=lockin["date"].dt.year).pivot_table(index="year", columns="subportfolio", values=["since_inception_pass", "rolling60_pass", "lockin_gap", "share_fail_trough_over_60m"])
    lines = ["## C2. Funnel time series and lock-in gap of the since-inception drawdown filter (baseline only)", "",
             f"June of each year 2006..2025 whose month is not in a locked window ({len(dates)} dates); metric frames rescored on the sealed inputs plus the IPC series ({ipc_id}); score_base reproduced "
             f"(coverage {cov:.4f}, max |diff| {diff:.6f}). Renta Variable funds of the sub-portfolio; the rolling 60-month drawdown is computed on the month-end NAV panel, the since-inception one is the "
             "metric of the scorer, both compared on the SAME funds (those with a full 60-month window) and the sub-portfolio's limit. `share_fail_trough_over_60m` = share of the since-inception "
             "failures whose low is more than 60 months old (reported, not gating).", "", "```", show.round(3).to_string(), "```", "",
             f"**Rule (ex ante): mean lock-in gap of Equilibrada's Renta Variable over the Junes 2014..2021 = {gap:+.3f}** (threshold 0.10) -> L1 is computed on the **{form}**.", ""]
    return lines, {"c2_funnel.csv": pd.concat(funnel, ignore_index=True), "c2_lockin.csv": lockin}, 0, {"mean_gap": gap, "form": form}


def stage_c34(args, scores, inputs, hist, at, prep, prereg):
    from shared.db import get_connection
    top, bottom, desc = CD.design_dates(at)
    wanted = sorted(set(top) | set(bottom) | set(desc))
    D.assert_dates_allowed(wanted, prereg)
    ok, cov, diff, _ipc, ipc_id, frames, _run2 = CD.rescore_validated(args.cache_dir, scores, inputs, hist, wanted)
    if not ok:
        return [f"REFUSED: the rescored score_base does not reproduce (coverage {cov:.4f}, max |diff| {diff:.6f})."], {}, 4, {}
    exact = "EXACT (max difference 0 to the sealed 4 decimals)" if diff == 0.0 else f"within rounding (max |diff| {diff:.6f}, tolerance {CD.SCORE_BASE_TOL})"
    conn = get_connection()
    try:
        d6 = P.load_d6_attributes(conn)
        entry, exit_ = CC._load_fees(conn)
    finally:
        conn.close()
    nav = inputs.nav
    rows, attr_rows, cell_inputs, entry_share = [], [], [], []
    for label, dates in (("top_Equilibrada", top), ("bottom_master", bottom)):
        for t in dates:
            st = scores[scores["as_of"] == t]
            h = hist.iloc[hist.index.searchsorted(t, side="right") - 1]
            sub_w = dict(zip(D.SUBPORTFOLIOS, (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
            selection, master = X.select_master(st, inputs.attrs, inputs.fx_exposed, sub_w)
            if label == "top_Equilibrada":
                block = st[(st["subportfolio"] == "Equilibrada")]
                pool_isins = list(block.loc[block["eligible"] & (block["score_final"] > 0), "isin"])
                sel_df = selection.get("Equilibrada")
                sel_isins = list(sel_df["isin"]) if sel_df is not None and len(sel_df) else []
                sel_w = pd.Series(1.0 / len(sel_isins), index=sel_isins) if sel_isins else pd.Series(dtype=float)
                natures = block.drop_duplicates("isin").set_index("isin")["fund_nature"]
            else:
                block = st[st["eligible"] & (st["score_final"] > 0)]
                pool_isins = list(block["isin"].unique())
                sel_w = pd.Series(master, dtype=float)
                sel_w = sel_w / sel_w.sum() if len(sel_w) else sel_w
                natures = st.drop_duplicates("isin").set_index("isin")["fund_nature"]
            if not len(sel_w) or not pool_isins:
                continue
            pool_w = pd.Series(1.0 / len(pool_isins), index=pool_isins)
            ret = _ret12(nav, t, sorted(set(pool_isins) | set(sel_w.index)))
            b = P.brinson(sel_w, pool_w, ret, natures)
            fp = X.forward_path(nav, t, sel_w, 12)["ret_12m"]
            rows.append({"set": label, "date": t, "n_selected": len(sel_w), "n_pool": len(pool_isins), "gap": b["gap"], "allocation": b["allocation"], "selection": b["selection"],
                         "interaction": b["interaction"], "valid_share_selected": b["valid_share_selected"], "valid_share_pool": b["valid_share_pool"],
                         "selected_mean_fwd12_HINDSIGHT": float((sel_w / sel_w.sum() * ret.reindex(sel_w.index)).sum()), "forward_path_ret12_D1": fp})
            for attr in P.D6_ATTRIBUTES:
                a = P.attribute_mix_within_nature(sel_w, pool_w, ret, natures, d6[attr])
                attr_rows.append({"set": label, "date": t, "attribute": attr, **a})
            fees_total = (entry + exit_).reindex(sel_w.index)
            entry_share.append({"set": label, "date": t, "share_selected_with_entry_fee": float((entry.reindex(sel_w.index).dropna() > 0).mean()) if entry.reindex(sel_w.index).notna().any() else np.nan,
                                "share_selected_fee_unknown": float(fees_total.isna().mean())})
            for sub in D.SUBPORTFOLIOS:                  # the cells of the L4 scale check: every (date, sub-portfolio, nature) with enough eligible funds
                blk = st[(st["subportfolio"] == sub) & st["eligible"] & (st["score_final"] > 0)]
                for nat, g in blk.groupby("fund_nature"):
                    isins = list(g["isin"])
                    if len(isins) >= MIN_CELL:
                        cell_inputs.append((t, sub, nat, frames[t].reindex(isins)["return_ann_real"], (entry + exit_).reindex(isins)))
    by_date = pd.DataFrame(rows)
    attr_df = pd.DataFrame(attr_rows)
    summ = by_date.groupby("set")[["gap", "allocation", "selection", "interaction"]].mean()
    summ["within_nature_share"] = (summ["selection"] + summ["interaction"]) / summ["gap"]
    summ["allocation_share"] = summ["allocation"] / summ["gap"]
    gaps = summ["gap"]
    am = attr_df.groupby(["set", "attribute"])[["attribute_mix", "cell_selection", "within_nature_total", "missing_attribute_share_pool"]].mean().reset_index()
    am["share_of_gap"] = [r["attribute_mix"] / gaps[r["set"]] for _, r in am.iterrows()]

    # ---- C4 ----
    h_base = P.holding_spells(prep.W)
    cands = [("baseline", float(h_base["mean_months"]))] + [(f"{m}m", float(m)) for m in H_FIXED_MONTHS]
    c4 = []
    for name, months in cands:
        cells = [P.penalty_scale(r, f, months / 12.0) for (_t, _s, _n, r, f) in cell_inputs]
        c = pd.DataFrame(cells)
        med_ratio = float(c["ratio_median_to_iqr"].median())
        c4.append({"H": name, "H_months": months, "n_cells": int(len(c)), "median_ratio_to_iqr": med_ratio, "median_share_over_half_iqr": float(c["share_over_half_iqr"].median()),
                   "median_rank_change_share": float(c["rank_change_share"].median()), "mean_unknown_fee_share": float((c["n_unknown_fee"] / c["n"]).mean()),
                   "passes": bool(0.05 <= med_ratio <= 1.0)})
    c4 = pd.DataFrame(c4)
    chosen = c4[c4["passes"]].sort_values("H_months").head(2)

    lines = ["## C3. D6 Brinson split on weighted MEANS of the 12-month forward return (HINDSIGHT; 2008 design dates; groups = Fund_Nature of the sealed scores)", "",
             f"Verifications: seal verified before this stage; score_base reproduction: {exact}, coverage {cov:.4f}; IPC {ipc_id}. The identity allocation + selection + interaction = gap is pinned by "
             "tests to 1e-12. D1 compared a mean with a median; `forward_path_ret12_D1` (the D1 code path, complete-path funds only) is shown next to the mean over valid funds for reconciliation.", "",
             "```", by_date.round(4).to_string(index=False), "```", "", "Mean over the dates of each set:", "", "```", summ.round(4).to_string(), "```", "",
             "Within-nature part split by attribute (mean over the dates; `share_of_gap` = attribute_mix / mean gap):", "", "```", am.round(4).to_string(index=False), "```", "",
             "## C4. L4 scale check on the 2008 design dates (today's fees: a documented look-ahead)", "",
             f"Baseline holding spells measured on the sealed weights: mean {h_base['mean_months']:.2f} months over {h_base['n_spells']} spells (median {h_base['median_months']:.1f}; completed-spell mean "
             f"{h_base['completed_mean_months']:.2f}; {h_base['n_censored']} censored). Cells = (date, sub-portfolio, nature) with at least {MIN_CELL} eligible funds: {len(cell_inputs)}.", "",
             "```", c4.round(4).to_string(index=False), "```", "",
             f"**Rule (ex ante): L4's two values of H are the two smallest that pass (ratio in [0.05, 1.0]): {', '.join(chosen['H'])}.**", "",
             "Share of the selected funds with an entry fee (design dates):", "", "```", pd.DataFrame(entry_share).groupby("set")[["share_selected_with_entry_fee", "share_selected_fee_unknown"]].mean().round(3).to_string(), "```", ""]
    files = {"c3_brinson_by_date.csv": by_date, "c3_attribute_split.csv": attr_df, "c3_attribute_summary.csv": am, "c4_scale.csv": c4}
    return lines, files, 0, {"summ": summ, "am": am, "c4": c4, "chosen": list(chosen["H"]), "h_base": h_base}


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
    else:
        print("[SEAL] NOT sealed (another run): hashes are not verified")
    prereg = D.prereg_in_git_head(_ROOT)
    print(f"[LOCK] {D.PREREG_NAME} in git HEAD: {prereg} -> locked stress episodes are {'OPEN' if prereg else 'REFUSED'} (the c1b stage reads the POOL drawdown only, by owner decision 4)")
    pit_run, inputs, hist, at = load_saved_run(run, None)
    scores = pit_run.scores
    if args.stage == "c1a":
        lines, files, rc, extra = stage_c1a(args, scores, inputs, hist, at, pit_run)
    elif args.stage == "c1b":
        prep = prepare_pit_backtest(pit_run, inputs, hist, at, windows=(1,))
        lines, files, rc, extra = stage_c1b(args, scores, prep, at)
    elif args.stage == "c2":
        lines, files, rc, extra = stage_c2(args, scores, inputs, hist, at, prereg)
    else:
        prep = prepare_pit_backtest(pit_run, inputs, hist, at, windows=(1,))
        lines, files, rc, extra = stage_c34(args, scores, inputs, hist, at, prep, prereg)
    out = _out(args)
    for name, df in files.items():
        df.to_csv(out / name, index=False)
    (out / "REPORT.md").write_text("\n".join([f"# Package L session 2, stage {args.stage}, run {run.name}", ""] + lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"[OUT] {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
