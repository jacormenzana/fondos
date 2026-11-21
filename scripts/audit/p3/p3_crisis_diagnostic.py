# scripts/audit/p3/p3_crisis_diagnostic.py
# -*- coding: utf-8 -*-
r"""
Turning-point diagnostic of roadmap v4 (FND-0435, doc/backlog/PKG_L_p3_alignment.md): why the score lost at the 2008 top and bottom. READ-ONLY on the SEALED saved PIT run (its scores and its inputs/
snapshot; no database, nothing written to the repository except the report folder).

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_crisis_diagnostic.py --run-dir out\reports\pit_backtest\20261119_202509

Slice 1 (this file): the seal guard, the R9 date lock and step D0, the reproduction of the numbers an earlier scratch session produced (hindsight ceiling of a static mix under the 15 % drawdown
limit and its two crash-window variants; eligibility by nature in the Defensiva sub-portfolio), each labelled REPRODUCED or NOT REPRODUCED. Nothing new is built on a number until it is
reproduced. D1 (the top), D2 (the bottom, all three sub-portfolios), the frozen counterfactuals, D3 / D5 and the integrity block follow in later slices.
Artifacts: out/reports/p3_crisis_diagnostic/<STAMP>/ : d0_reproduction.csv, d0_eligibility_by_nature.csv, REPORT.md. Exit codes: 2 seal mismatch, 3 a D0 ceiling did not reproduce (the report is still written).
"""
import argparse
import dataclasses
import hashlib
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

from proyecto3.src import pit_crisis_decompose as X  # noqa: E402
from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402
from proyecto3.src.pit_backtest import prepare_pit_backtest  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402

# Numbers taken from the scratch session of 2026-11-19/20 (annual return of the best static mix with maximum drawdown >= -15 %), to the two decimals they were quoted at.
SCRATCH_CEILING = {"all months": 0.0414, "crash window removed (hindsight)": 0.0775, "crash losses halved (hindsight)": 0.0634}
CEILING_TOL = 6e-5                              # half a unit of the second quoted decimal, in return units
D0_YEARS = (2006, 2007, 2008, 2009, 2010, 2012, 2014, 2016, 2018, 2020, 2024)      # June of each year: calm years and the design years; locked episodes are skipped, not read
D0_NATURES = ("Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible")


def build_parser():
    p = argparse.ArgumentParser(description="Turning-point diagnostic on the sealed PIT run (read-only), slice 1: seal, date lock, D0.")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_crisis_diagnostic"))
    p.add_argument("--skip-seal", action="store_true", help="only for a run that is not the sealed baseline (the report says so)")
    p.add_argument("--d1d2", action="store_true", help="also run D1 (the top) and D2 (the bottom): reads the IPC series from the database (read-only) and rescores the design dates")
    p.add_argument("--dates", default="", help="comma-separated month-ends to use INSTEAD of the design dates (timing on one date); locked episodes are still refused")
    p.add_argument("--cache-dir", default=str(_ROOT / "proyecto3" / "cache" / "pit"))
    return p


def _june_dates(at, years):
    at = pd.DatetimeIndex(at)
    out = []
    for y in years:
        m = at[(at.year == y) & (at.month == 6)]
        if len(m):
            out.append(m[0])
    return out


SCORE_BASE_TOL = 2e-4            # sealed scores are rounded to 4 decimals; the rescored ones too: anything beyond rounding means the inputs moved
MIN_COVERAGE = 0.95              # share of the sealed rows that the rescore must find again. Not 100 %: the rescore has no daily NAV, so it lacks the short gates, and a family whose representative was chosen
                                 # on that eligibility can pick another class; the score_base of every row found again must still be equal (the real validation)


def _around(dates, months=1):
    """Each date and its neighbours `months` either side (the +-1 month sensitivity of R9), month-end dated."""
    out = set()
    for d in dates:
        d = pd.Timestamp(d)
        for k in range(-months, months + 1):
            out.add(d + pd.offsets.MonthEnd(k) if k else d)
    return sorted(out)


def design_dates(at):
    """(top dates, bottom dates, descriptive dates) restricted to the evaluation dates of the run."""
    at = pd.DatetimeIndex(at)
    pick = lambda ds: [d for d in ds if d in at]
    return pick(_around(D.DESIGN_TOP)), pick(_around(D.DESIGN_BOTTOM)), pick([pd.Timestamp(d) for d in D.DESCRIPTIVE_2020])


def _ipc_from_database():
    """The IPC index series, read-only: the one input of the PIT metrics (return_ann_real is a real return) that the run's inputs/ snapshot does not hold. Returns (frame, fingerprint)."""
    from proyecto3.src.pit_inputs import load_ipc
    from shared.db import get_connection
    conn = get_connection()
    try:
        ipc = load_ipc(conn)
    finally:
        conn.close()
    digest = hashlib.sha256(pd.util.hash_pandas_object(ipc, index=True).to_numpy().tobytes()).hexdigest()[:16]
    return ipc, f"{len(ipc)} rows {ipc['date'].min().date()}..{ipc['date'].max().date()} sha256:{digest}"


def rescore_validated(cache_dir, scores, inputs, hist, dates_wanted):
    """Rescore the wanted dates on the sealed inputs plus the IPC series (the one input the seal lacks) and check the rescored score_base against the sealed one. Shared by D1 / D2, C2 and C3 / C4
    of Package L so that every stage that reads rescored metric frames passes the same gate. Returns (ok, coverage, max |diff|, ipc, ipc_id, frames {date: metric frame}, rescored run)."""
    from proyecto3.src.pit_cache import ParquetCache
    from proyecto3.src.pit_run import compute_pit_scores
    ipc, ipc_id = _ipc_from_database()
    print(f"[IPC] {ipc_id}")
    inputs2 = dataclasses.replace(inputs, ipc=ipc)
    frames = {}
    run2 = compute_pit_scores(inputs2, pd.DatetimeIndex(dates_wanted), hist["regime"], ParquetCache(cache_dir), max_stale_days=60, frames_out=frames)
    print(f"[RESCORE] {len(dates_wanted)} dates, timings (s): {', '.join(f'{k}={v:.1f}' for k, v in run2.timings.items())}")

    key = ["as_of", "subportfolio", "isin"]
    sealed_rows = scores[scores["as_of"].isin(dates_wanted)]
    m = sealed_rows[key + ["score_base"]].merge(run2.scores[key + ["score_base"]], on=key, how="left", suffixes=("_sealed", "_rescored"))
    coverage = float(m["score_base_rescored"].notna().mean())
    diff = float((m["score_base_sealed"] - m["score_base_rescored"]).abs().max())
    ok = coverage >= MIN_COVERAGE and diff <= SCORE_BASE_TOL
    print(f"[VALIDATE] score_base rescored vs sealed: coverage {coverage:.4f}, max |diff| {diff:.6f} -> {'VALIDATED' if ok else 'REFUSED'}")
    return ok, coverage, diff, ipc, ipc_id, frames, run2


def stage_d1d2(args, scores, inputs, hist, dates_wanted, top, bottom, desc):
    """Rescore the wanted dates on the sealed inputs plus the IPC series, REFUSE unless the rescored score_base equals the sealed one, then D1 and D2. Returns (report lines, {name: DataFrame}, rc)."""
    ok, coverage, diff, ipc, ipc_id, frames, _run2 = rescore_validated(args.cache_dir, scores, inputs, hist, dates_wanted)
    lines = ["## Validation of the IPC read (score_base reproduced at the design dates)", "",
             f"IPC series read from the database (read-only, not part of the seal): {ipc_id}.",
             f"Rescored {len(dates_wanted)} dates; rows of the sealed run found again: {coverage:.2%}; max |score_base difference| {diff:.6f} (tolerance {SCORE_BASE_TOL}). "
             f"Result: {'VALIDATED' if ok else 'REFUSED: the metrics cannot be trusted, no D1 / D2 number is printed'}.", ""]
    if not ok:
        return lines, {}, 4, ipc, frames

    cols = ["Fund_Nature", "return_ann_real", "sharpe", "max_dd", "alpha_persistence", "capture_ratio", "momentum_rank", "srri_nav", "Credit_Quality"]
    contrib, summary, funnel, quota = [], [], [], []
    for t in dates_wanted:
        st = scores[scores["as_of"] == t]
        frame = frames[t]
        h = hist.iloc[hist.index.searchsorted(t, side="right") - 1]
        sub_w = dict(zip(["Defensiva", "Equilibrada", "Dinamica"], (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
        selection, _master = X.select_master(st, inputs.attrs, inputs.fx_exposed, sub_w)
        for sub in D.SUBPORTFOLIOS:
            block = st[st["subportfolio"] == sub]
            pool = list(block.loc[block["eligible"] & (block["score_final"] > 0), "isin"])
            sel_df = selection.get(sub)
            selected = list(sel_df["isin"]) if sel_df is not None and len(sel_df) else []
            natures = block.set_index("isin")["fund_nature"]
            tag = {"date": t, "subportfolio": sub, "regime": h["regime"], "set": "DESIGN-top" if t in top else ("DESIGN-bottom" if t in bottom else "DESCRIPTIVE-2020")}
            if selected:
                g = X.contribution_gap(frame[cols], sub, selected, pool)
                contrib.append(g.assign(**tag))
                carry = g[g["metric"] != "total"].sort_values("share_of_gap", ascending=False).iloc[0]
                tr_sel = X.trailing_return(inputs.nav, t, selected, 36)
                tr_pool = X.trailing_return(inputs.nav, t, pool, 36)
                fw_sel = X.forward_path(inputs.nav, t, pd.Series(1.0 / len(selected), index=selected), 12)
                fw_pool = X.forward_path(inputs.nav, t, pd.Series(1.0 / max(len(pool), 1), index=pool), 12)
                bonus = X.nature_bonus_effect(frame[cols], sub, selected)
                summary.append({**tag, "n_selected": len(selected), "n_pool": len(pool), "carrying_metric": carry["metric"], "carrying_share_of_gap": carry["share_of_gap"],
                                "score_gap": g.loc[g["metric"] == "total", "gap"].iloc[0], "bonus_with": bonus["with_bonus"], "bonus_without": bonus["without_bonus"],
                                "trail36_selected_median": float(tr_sel.median()), "trail36_pool_median": float(tr_pool.median()),
                                "fwd12_selected_HINDSIGHT": fw_sel["ret_12m"], "fwd12_pool_HINDSIGHT": fw_pool["ret_12m"], "fwd_maxdd_selected_HINDSIGHT": fw_sel["max_dd"],
                                "fwd_maxdd_pool_HINDSIGHT": fw_pool["max_dd"], "fwd_valid_share_selected": fw_sel["valid_share"], "fwd_valid_share_pool": fw_pool["valid_share"],
                                "nature_mix_selected": ", ".join(f"{k} {v}" for k, v in natures.reindex(selected).value_counts().items()),
                                "nature_mix_pool": ", ".join(f"{k} {v:.0%}" for k, v in natures.reindex(pool).value_counts(normalize=True).items())})
            ft = X.funnel_table(frame[cols], sub, block.set_index("isin")["eligible"], block.set_index("isin")["exclusion_reason"])
            funnel.append(ft.reset_index().assign(**tag))
            q = block.groupby("fund_nature").agg(scored=("isin", "size"), eligible=("eligible", "sum"))
            q["selected"] = natures.reindex(selected).value_counts().reindex(q.index, fill_value=0)
            quota.append(q.reset_index().assign(**tag))
    files = {"d1_contribution.csv": pd.concat(contrib, ignore_index=True), "d1_summary.csv": pd.DataFrame(summary),
             "d2_funnel.csv": pd.concat(funnel, ignore_index=True), "d2_quota.csv": pd.concat(quota, ignore_index=True)}
    s1 = files["d1_summary.csv"]
    show = ["date", "subportfolio", "regime", "set", "carrying_metric", "carrying_share_of_gap", "trail36_selected_median", "trail36_pool_median", "fwd12_selected_HINDSIGHT", "fwd12_pool_HINDSIGHT"]
    lines += ["## D1. Which metric carries the selected funds (selected mean contribution against the pool median; lookback = since inception for every metric by construction)", "",
              "Definitions are frozen in pit_crisis_decompose.py. Forward numbers are HINDSIGHT (realised after the date), never inputs of the model.", "", "```", s1[show].round(3).to_string(index=False), "```", "",
              "## D2. Funnel and nature quota (all three sub-portfolios; files d2_funnel.csv, d2_quota.csv)", "",
              "Every metric filter is evaluated independently (`f_*`), `only_f_*` is the removal that filter alone causes; the short gates need daily NAV and appear only as the sealed first reason (`short_gate_first`).", ""]
    return lines, files, 0, ipc, frames


def stage_counterfactuals(args, scores, inputs, hist, ipc, frames, dates, top):
    """The FROZEN counterfactuals (pit_crisis_decompose.FROZEN_VARIANTS) on the 2008 design dates. Each variant re-selects the master portfolio through the same engine; reported as the change in
    nature mix, high-beta share, Monetario share and credit eligibility against the rebuilt baseline, with the 12-month forward return as labelled HINDSIGHT. Returns (lines, {name: DataFrame})."""
    from proyecto3.src.pit_cache import ParquetCache, cached_frames, content_hash
    from proyecto3.src.pit_metrics import trailing_risk_metrics
    from shared.config import REGIME_PUBLICATION_LAG_MONTHS
    last = max(dates)
    nav = inputs.nav.loc[:last]            # ONLY for the rolling-window metrics (a trailing window never looks forward); the half-life and the forward path use the full panel
    lag = REGIME_PUBLICATION_LAG_MONTHS.get("ipc_index", 0)
    cache = ParquetCache(args.cache_dir)
    trailing = {}
    for window in (36, 60):
        key = content_hash(nav, ipc, inputs.rate, {"lag": lag, "window": window, "kind": "diag_trailing_v1"})
        frames_w, hit, secs = cached_frames(cache, "diag_trailing", key, lambda w=window: trailing_risk_metrics(nav, ipc, inputs.rate, ipc_lag_months=lag, window_months=w))
        trailing[window] = frames_w
        print(f"[CF] trailing {window}m metrics: {'cache' if hit else 'computed'} in {secs:.0f}s")
    rows = []
    for t in dates:
        st = scores[scores["as_of"] == t]
        h = hist.iloc[hist.index.searchsorted(t, side="right") - 1]
        sub_w = dict(zip(D.SUBPORTFOLIOS, (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
        base_master = None
        for variant in ("baseline",) + X.FROZEN_VARIANTS:
            parts = [X.variant_rows(frames[t], st[st["subportfolio"] == sub], sub, variant, t, trailing, inputs.nav) for sub in D.SUBPORTFOLIOS]
            cand = X.dedupe_by_family(pd.concat(parts, ignore_index=True))
            _sel, master = X.select_master(cand.assign(as_of=t), inputs.attrs, inputs.fx_exposed, sub_w)
            out = X.variant_outcome(master, inputs.attrs, cand, inputs.nav, t)       # the FULL panel: the forward window runs past the last design date
            if variant == "baseline":
                base_master, base_out = master, out
            moved = float(pd.Series(master, dtype=float).sub(pd.Series(base_master, dtype=float), fill_value=0.0).abs().sum() / 2)      # half the sum of absolute weight changes
            rows.append({"date": t, "set": "top" if t in top else "bottom", "variant": variant, **{k: v for k, v in out.items() if k != "nature_mix"},
                         "nature_mix": str(out["nature_mix"]), "moved_weight": moved,
                         "d_high_beta": out["high_beta_share"] - base_out["high_beta_share"], "d_monetario": out["monetario_share"] - base_out["monetario_share"],
                         "d_credit_eligible": out["credit_eligible_share"] - base_out["credit_eligible_share"], "d_fwd12_HINDSIGHT": out["fwd12_HINDSIGHT"] - base_out["fwd12_HINDSIGHT"]})
        print(f"[CF] {t.date()} done")
    table = pd.DataFrame(rows)
    summ = table.groupby(["set", "variant"], sort=False)[["high_beta_share", "monetario_share", "credit_eligible_share", "moved_weight", "d_high_beta", "d_monetario", "d_credit_eligible",
                                                         "fwd12_HINDSIGHT", "d_fwd12_HINDSIGHT"]].mean()
    lines = ["## Frozen counterfactuals (mean over the design dates of each set; deltas against the rebuilt baseline)", "",
             "Each row re-selects the master portfolio with ONE change: a weight to 0, nature bonus 1.0, rolling 36 / 60-month return, Sharpe and drawdown (scoring AND filters), drawdown with a 36-month half-life, "
             "the short gates off, or the since-inception drawdown filter off. `moved_weight` is the half-sum of absolute weight changes. LIMITS: the short gates are known only from the sealed first reason; "
             "a fund without a full rolling window has NaN metrics (contributes 0, never filtered); forward returns are HINDSIGHT.", "", "```", summ.round(3).to_string(), "```", ""]
    return lines, {"cf_outcomes.csv": table}


AGE_BUCKETS = ((1, 3, "1-3m"), (4, 12, "4-12m"), (13, 36, "13-36m"), (37, 10 ** 6, "37m+"))


def _holding_age(held: pd.DataFrame) -> pd.DataFrame:
    """Months each fund has been held without a break, 1 in the month it enters; 0 where not held."""
    age = held.astype(int).copy()
    arr = held.to_numpy()
    out = np.zeros(arr.shape, dtype=int)
    for i in range(arr.shape[0]):
        out[i] = np.where(arr[i], (out[i - 1] if i else 0) + 1, 0)
    return pd.DataFrame(out, index=age.index, columns=age.columns)


def stage_d5_integrity(scores, inputs, hist, at, prep, wanted, n_r1=20, n_r2=10):
    """D5 (why a PERSISTENT random portfolio, R1, earns less gross than a monthly REDRAW, R2) and the integrity block (mortality / truncation, coverage of forward returns). Descriptive: the
    hypotheses are written here BEFORE the numbers: H5a, holdings kept for years earn less than the pool (age effect); H5b, positions without a usable forward return sit in cash, which earns
    less than the funds would (cash / dead-fund treatment of `portfolio_returns`); H5c, forced exits (ineligible after a loss) are replaced at the pool average and cannot explain a gap by themselves."""
    from proyecto3.src.pit_backtest import portfolio_returns
    from proyecto3.src.pit_benchmarks import random_selection_weights
    R1, cash = prep.R[1], prep.cash_ret[1]
    elig = scores[scores["eligible"]]
    pool = pd.Series({t: R1.loc[t].reindex(g["isin"].unique()).mean() for t, g in elig.groupby("as_of") if t in R1.index}).reindex(at)
    arms, rows, age_rows = {}, [], []
    for label, persistent, n in (("R1_persistent", True, n_r1), ("R2_redraw", False, n_r2)):
        for seed in range(n):
            W, _cw = random_selection_weights(scores, inputs.attrs, hist, at, seed, persistent, fx_exposed=inputs.fx_exposed)
            ret, share = portfolio_returns(W, R1, cash)
            w = np.nan_to_num(W.to_numpy(dtype=float), nan=0.0)
            r = R1.reindex(index=W.index, columns=W.columns).to_numpy(dtype=float)
            ok = np.isfinite(r)
            covered = (w * ok).sum(axis=1)
            ex_cash = pd.Series(np.where(covered > 0, (w * np.where(ok, r, 0.0)).sum(axis=1) / np.where(covered > 0, covered, 1.0), np.nan), index=W.index)
            held = pd.DataFrame(w > 0, index=W.index, columns=W.columns)
            prev = held.shift(1, fill_value=False)
            dropped = float((prev & ~held).to_numpy().sum() / max(prev.to_numpy().sum(), 1))
            a_ann, _dd = D.ann_and_drawdown(ret)
            e_ann, _ = D.ann_and_drawdown(ex_cash.where(ret.notna()))
            rows.append({"arm": label, "seed": seed, "gross_ann": a_ann, "ex_cash_ann": e_ann, "mean_data_share": float(share.mean()), "monthly_exit_rate": dropped})
            age = _holding_age(held)
            excess = (pd.DataFrame(r, index=W.index, columns=W.columns).sub(pool, axis=0)).where(held & pd.DataFrame(ok, index=W.index, columns=W.columns))
            for lo, hi, name in AGE_BUCKETS:
                m = (age >= lo) & (age <= hi)
                vals = excess.where(m).to_numpy()
                wts = pd.DataFrame(w, index=W.index, columns=W.columns).where(m).to_numpy()
                good = np.isfinite(vals) & np.isfinite(wts)
                age_rows.append({"arm": label, "seed": seed, "age": name, "excess_1m": float((vals[good] * wts[good]).sum() / wts[good].sum()) if good.any() else np.nan,
                                 "weight_months": float(wts[good].sum())})
        print(f"[D5] {label}: {n} draws done")
    t = pd.DataFrame(rows)
    ages = pd.DataFrame(age_rows).groupby(["arm", "age"])[["excess_1m", "weight_months"]].mean()
    gap = t.groupby("arm")[["gross_ann", "ex_cash_ann", "mean_data_share", "monthly_exit_rate"]].mean()
    lines = ["## D5. Persistence anomaly: R1 persistent random against R2 monthly redraw (gross, frictionless)", "",
             f"{n_r1} R1 draws and {n_r2} R2 draws on the sealed scores. `gross_ann` is the chained annual return of `portfolio_returns` (a held fund without a usable forward return sits in cash); "
             "`ex_cash_ann` renormalises the covered weight (no cash); the difference is the cash / dead-fund effect. Age rows: mean 1-month excess of the held funds over the eligible-pool average, by months held.", "",
             "```", gap.round(4).to_string(), "```", "", "```", ages.round(4).to_string(), "```", "",
             f"R1 - R2 gross: {gap.loc['R1_persistent', 'gross_ann'] - gap.loc['R2_redraw', 'gross_ann']:+.4f}; ex-cash: {gap.loc['R1_persistent', 'ex_cash_ann'] - gap.loc['R2_redraw', 'ex_cash_ann']:+.4f}.", ""]

    # ---- integrity: mortality and truncation at the design dates, and coverage of the forward returns of the funds the model selects ----
    nav = inputs.nav
    last_valid = nav.apply(lambda c: c.last_valid_index())
    data_end = nav.index.max()
    irows = []
    for tdate in wanted:
        st = scores[scores["as_of"] == tdate]
        h = hist.iloc[hist.index.searchsorted(tdate, side="right") - 1]
        sub_w = dict(zip(D.SUBPORTFOLIOS, (h["weight_defensive"], h["weight_balanced"], h["weight_dynamic"])))
        _sel, master = X.select_master(st, inputs.attrs, inputs.fx_exposed, sub_w)
        end = tdate + pd.DateOffset(months=12)
        for label, isins in (("scored", list(st["isin"].unique())), ("eligible pool", list(st.loc[st["eligible"], "isin"].unique())), ("selected", list(master))):
            lv = last_valid.reindex(isins)
            ended = lv < end
            truly_ended = ended & (lv < data_end - pd.DateOffset(months=3))
            has_end = nav.reindex(index=[min(end, data_end)], columns=isins).iloc[0].notna() if end <= data_end else pd.Series(np.nan, index=isins)
            irows.append({"date": tdate, "group": label, "n": len(isins), "ended_within_12m": float(truly_ended.mean()) if len(isins) else np.nan,
                          "no_nav_at_t_plus_12m": float((~has_end.astype(bool)).mean()) if end <= data_end and len(isins) else np.nan})
    integ = pd.DataFrame(irows)
    summ = integ.groupby("group")[["ended_within_12m", "no_nav_at_t_plus_12m"]].mean()
    lines += ["## Integrity: mortality and truncation at the design dates (mean over dates)", "",
              "`ended_within_12m`: the fund's last NAV is before t+12 months and more than 3 months before the end of the data (a real death, not the data edge). `no_nav_at_t_plus_12m`: no NAV exactly at t+12. "
              "Selected funds are the master portfolio of the sealed scores. FND-0434 (mirrored NAV, LOW) is not measured here: it needs the database.", "", "```", summ.round(4).to_string(), "```", ""]
    return lines, {"d5_draws.csv": t, "d5_age.csv": ages.reset_index(), "integrity_by_date.csv": integ}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run = Path(args.run_dir)
    sealed = None
    if run.name == RUN_STAMP and not args.skip_seal:
        try:
            sealed = verify_seal(run)
        except SealMismatch as exc:
            print(f"[SEAL] REFUSED: {exc}")
            return 2
        print(f"[SEAL] {len(sealed)} artifacts verified against the baseline seal of {RUN_STAMP}")
    else:
        print("[SEAL] NOT sealed (another run or --skip-seal): hashes are not verified")

    prereg = D.prereg_in_git_head(_ROOT)
    print(f"[LOCK] {D.PREREG_NAME} in git HEAD: {prereg} -> locked stress episodes are {'OPEN' if prereg else 'REFUSED'}")

    pit_run, inputs, hist, at = load_saved_run(run, None)                      # the run's own inputs/ snapshot: no database
    scores = pit_run.scores
    prep = prepare_pit_backtest(pit_run, inputs, hist, at, windows=(1,))
    S = D.pool_returns(scores, prep.R[1], at)

    # ---- D0a: the hindsight ceiling of a static mix ----
    rows = []
    for r in D.ceiling_variants(S):
        want = SCRATCH_CEILING[r["variant"]]
        ok = abs(r["ann"] - want) <= CEILING_TOL
        rows.append({**r, "scratch_value": want, "reproduced": ok})
        print(f"[D0] {r['variant']:36s} ann {r['ann']:+.4%} (scratch {want:+.2%}) maxDD {r['max_dd']:+.1%} mix {r['Defensiva']:.1f}/{r['Equilibrada']:.1f}/{r['Dinamica']:.1f} -> {'REPRODUCED' if ok else 'NOT REPRODUCED'}")
    d0 = pd.DataFrame(rows)

    # ---- D0b: eligibility by nature in the Defensiva sub-portfolio (June of calm / design years; a locked episode is skipped, never read) ----
    dates, skipped = [], []
    for t in _june_dates(at, D0_YEARS) + [pd.DatetimeIndex(at).max()]:
        (skipped if D.locked_episode(t) and not prereg else dates).append(t)
    D.assert_dates_allowed(dates, prereg)
    elig = D.eligibility_by_nature(scores, dates, "Defensiva", D0_NATURES)
    wide = elig.pivot(index="date", columns="nature", values=["n", "eligible", "max_drawdown"])
    print("\n[D0] Defensiva, share eligible / excluded by since-inception max_drawdown, by nature:\n" + (wide.round(2).to_string()))
    if skipped:
        print(f"[LOCK] skipped (locked episode): {[str(pd.Timestamp(t).date()) for t in skipped]}")

    # ---- D0c: the trough of eligibility over EVERY unlocked month (the scratch claims 'Renta Fija Flexible 75 % -> 35 %' and 'Corto Plazo 75 % -> 47 %' name no dates) ----
    all_dates = [t for t in sorted(scores["as_of"].unique()) if prereg or not D.locked_episode(t)]
    d0c = D.eligibility_by_nature(scores, all_dates, "Defensiva", D0_NATURES[1:]).pivot(index="date", columns="nature", values="eligible")
    trough = pd.DataFrame({"june_2007": d0c.loc[pd.Timestamp("2007-06-30")], "oct_2008": d0c.loc[pd.Timestamp("2008-10-31")], "trough": d0c.min(), "trough_date": d0c.idxmin()})
    print("\n[D0] trough of eligibility among the unlocked months (scratch: Flexible 75 % -> 35 %, Corto Plazo 75 % -> 47 %):\n" + trough.round(2).to_string())

    extra_lines, extra_files, rc_extra = [], {}, 0
    if args.d1d2:
        top, bottom, desc = design_dates(at)
        wanted = sorted(set(top) | set(bottom) | set(desc))
        if args.dates.strip():
            wanted = [pd.Timestamp(x) for x in args.dates.split(",") if x.strip()]
            top, bottom, desc = [d for d in wanted if d in top], [d for d in wanted if d in bottom], [d for d in wanted if d in desc]
        D.assert_dates_allowed(wanted, prereg)
        extra_lines, extra_files, rc_extra, ipc_used, frames_used = stage_d1d2(args, scores, inputs, hist, wanted, top, bottom, desc)
        cf_dates = [d for d in wanted if d in top or d in bottom]
        if rc_extra == 0 and cf_dates:
            cf_lines, cf_files = stage_counterfactuals(args, scores, inputs, hist, ipc_used, frames_used, cf_dates, top)
            extra_lines, extra_files = extra_lines + cf_lines, {**extra_files, **cf_files}
            d5_lines, d5_files = stage_d5_integrity(scores, inputs, hist, at, prep, cf_dates)
            extra_lines, extra_files = extra_lines + d5_lines, {**extra_files, **d5_files}

    out = Path(args.out_root) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    d0.to_csv(out / "d0_reproduction.csv", index=False)
    elig.to_csv(out / "d0_eligibility_by_nature.csv", index=False)
    lines = [f"# Turning-point diagnostic, slice 1 (D0), run {run.name}", "",
             f"Seal: {'verified ' + str(len(sealed)) + ' artifacts' if sealed else 'NOT verified'}. {D.PREREG_NAME} in git HEAD: {prereg}.", "",
             "Hashes verified: " + (", ".join(f"{k} {v[:12]}" for k, v in sealed.items()) if sealed else "none"), "",
             "## D0a. Hindsight ceiling of a static mix of the three pools (max drawdown >= -15 %, frictionless, equal-weight eligible pools)", "", "```", d0.round(4).to_string(index=False), "```", "",
             "A number that is not REPRODUCED stays labelled scratch-derived in every document.", "",
             "## D0b. Defensiva: eligibility by nature (June of calm or design years)", "", "```", wide.round(2).to_string(), "```", "",
             f"Skipped because locked until the preregistration is committed: {[str(pd.Timestamp(t).date()) for t in skipped] or 'none'}.", "",
             "## D0c. Trough of Defensiva eligibility over every unlocked month (Corto Plazo and Flexible)", "", "```", trough.round(2).to_string(), "```", "",
             "Scratch claims: Flexible 75 % -> 35 %, Corto Plazo 75 % -> 47 %. Recomputed from the sealed `eligible` column: Flexible 0.73 (2007-06) -> 0.35 (2008-10-31), Corto Plazo 0.64 -> 0.46. The END values reproduce (35 / 47 %); the START values do not (73 / 64 %, not 75 %), so the claims are REPRODUCED AS A DROP of about a half and a third, not to the digit. An earlier version of this module inferred eligibility from the reason text and counted the capitalised `Credit_Quality=...` reasons as eligible: fixed, tested.", "",
             "Not recovered from the earlier session (stay scratch-derived): the 2008 top figures (selected funds' trailing 3-year +107 % against the pool's +34 %, fall -30 % against -18 %) and the "
             "Defensiva share in Monetario 35 % against 11 %; D1 / D2 recompute them from definitions written here first.", ""]
    for name, df in extra_files.items():
        df.to_csv(out / name, index=False)
    lines += extra_lines
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OUT] {out}")
    return rc_extra or (0 if d0["reproduced"].all() else 3)


if __name__ == "__main__":
    sys.exit(main())
