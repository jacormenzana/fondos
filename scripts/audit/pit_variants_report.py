#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pit_variants_report.py -- evaluates the FND-0303 variants against PREREG_FND-0303 (read-only; writes REPORT_FND-0303.md + report_data.json).

    C:\\data\\envs\\des\\python.exe scripts\\audit\\pit_variants_report.py --out-root out\\reports\\pit_variants\\FND-0303_<STAMP>

It reads ONLY what the orchestrator left in the out-root: gate.json, variant_status.json per variant, and each run's pit_backtest_table.csv,
series_stats.json and inputs/*.parquet (never the logs, never the database). Every number is computed by the rules written in the
pre-registration; this script decides nothing the PREREG did not decide:

  Rule 1  drawdown breaker (<= 15 % full history and the four sub-periods; bootstrap P(DD over 36 m < -15 %) not above V0's)
  Rule 2  Sharpe non-inferiority (diff >= -0.05 and the 90 % interval's lower bound > -0.15)
  Rule 3  objective not worse (annual return not below V0's by more than 0.5 pp: full window and >= 3 of the 4 sub-periods)
  Rule 4  turnover not higher than V0's by more than 5 pp of capital
  Rule 5  the live dry-run is promotable -> NOT computable here: PENDING (needs the live implementation of the variant)
  Rule 6  cash timing on VARIANT-INDUCED cash max(0, cash_variant - cash_V0): C1 mean <= 5 %, C2 max <= 20 %, C3 mean over each stress
          [trough, recovery end] <= 10 %, C4 return over each [trough, recovery end] not below V0's by more than 0.33 pp per month
  Rule 7  fallback when V0 itself fails Rule 1

Stress episodes are dated on a variant-independent reference: the B-STATIC mix of equal-weight indices of ALL funds per sub-portfolio.
A variant that did not succeed is INCOMPLETE; one whose saved inputs differ from V0's is INCOMPARABLE: neither is evaluated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from proyecto3.src.backtest_inference import drawdown_probability, paired_sharpe_difference   # noqa: E402
from proyecto3.src.pit_backtest import series_stats   # noqa: E402

# ---- the pre-registered constants (PREREG_FND-0303 sections 3-5); a change here is a change of the pre-registration ----------------
DD_LIMIT = -0.15
SHARPE_DIFF_MIN, SHARPE_CI_LOW_MIN = -0.05, -0.15
RETURN_DIFF_MIN = -0.005
TURNOVER_DIFF_MAX = 0.05
CASH_MEAN_MAX, CASH_PEAK_MAX, CASH_RECOVERY_MAX = 0.05, 0.20, 0.10
REBOUND_TOL_PER_MONTH = 0.0033
RECOVERY_CAP_MONTHS = 36
BSTATIC = {"Defensiva": 0.464286, "Equilibrada": 0.364286, "Dinamica": 0.171429}
SUBPERIODS = (("2005-2010", "2005-01-01", "2010-12-31"), ("2011-2016", "2011-01-01", "2016-12-31"),
              ("2017-2021", "2017-01-01", "2021-12-31"), ("2022-2026", "2022-01-01", "2026-12-31"))
EPISODES = (("2008-09..2009-03", "2008-09-01", "2009-03-31"), ("2020-02..2020-03", "2020-02-01", "2020-03-31"),
            ("2022", "2022-01-01", "2022-12-31"))
SIMPLICITY_ORDER = ("V1", "V3", "V2", "V123")          # fewest changes first
GATE_MIN_FUNDS, GATE_MIN_COMPANIES, GATE_TOP1_MAX, DATA_STARVED_SHARE = 30, 10, 0.20, 0.10
SUB_NATURES = {"Defensiva": ("Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible"),
               "Equilibrada": ("Mixtos", "Renta Variable", "Renta Fija Flexible"),
               "Dinamica": ("Renta Variable", "Mixtos", "Alternativo")}


def load_table(run_dir) -> pd.DataFrame:
    t = pd.read_csv(Path(run_dir) / "pit_backtest_table.csv", index_col=0, parse_dates=True)
    t.index = pd.DatetimeIndex(t.index)
    return t


def chained(t: pd.DataFrame) -> pd.Series:
    return (t["net_turnover_1m"] if "net_turnover_1m" in t.columns else t["ret_1m"]).dropna()


def window_stats(t: pd.DataFrame, lo=None, hi=None) -> dict:
    r = chained(t).loc[lo:hi]
    return series_stats(r, t["cash_ret_1m"].reindex(r.index) if "cash_ret_1m" in t.columns else None)


# ---------------------------------------------------------------------------------------------------- exogenous stress reference
def stress_reference(nav: pd.DataFrame, nature: pd.Series, weights: dict = BSTATIC, sub_natures: dict = SUB_NATURES) -> pd.Series:
    """Cumulative index of the B-STATIC blend of equal-weight indices of ALL funds (no eligibility, no selection, variant independent)."""
    rets = nav.pct_change(fill_method=None)
    parts = {}
    for sub, natures in sub_natures.items():
        cols = [c for c in rets.columns if nature.get(c) in natures]
        parts[sub] = rets[cols].mean(axis=1, skipna=True) if cols else pd.Series(np.nan, index=rets.index)
    blend = sum(weights[s] * parts[s] for s in weights)
    blend = blend.dropna()
    return (1.0 + blend).cumprod()


def episode_windows(index: pd.Series, episodes=EPISODES, cap: int = RECOVERY_CAP_MONTHS) -> list:
    """For each episode: trough (minimum of the reference inside the window), recovery end (first month it regains its pre-episode
    peak, capped at trough + `cap` months; `censored` when not reached) -- all dated on the reference, never on a variant."""
    out = []
    for name, lo, hi in episodes:
        win = index.loc[lo:hi]
        if win.empty:
            out.append({"episode": name, "available": False})
            continue
        trough = win.idxmin()
        before = index.loc[:lo]
        peak = before.max() if len(before) else win.max()
        after = index.loc[trough:]
        rec = after[after >= peak]
        cap_date = trough + pd.DateOffset(months=cap)
        if len(rec) and rec.index[0] <= cap_date:
            end, censored = rec.index[0], False
        else:
            end, censored = min(cap_date, index.index[-1]), True
        out.append({"episode": name, "available": True, "start": pd.Timestamp(lo), "trough": trough, "recovery_end": end,
                    "censored": censored, "months": int(((end.year - trough.year) * 12 + end.month - trough.month))})
    return out


# ---------------------------------------------------------------------------------------------------- V2 input diagnostics
def v2_eligibility(nav: pd.DataFrame, attrs: pd.DataFrame, window: int = 60, first: str = "2005-01-31", dates=None) -> dict:
    """Per month and sub-portfolio: funds with a full `window` of observations (and a NAV in the last 3 months), management companies,
    largest company share. Reports the minimum and the months failing the pre-declared gates."""
    cnt = nav.notna().cumsum()
    recent = nav.notna().rolling(3, min_periods=1).max().astype(bool)
    nat = attrs["Fund_Nature"].reindex(nav.columns)
    mc = attrs["Management_Company"].reindex(nav.columns)
    rows = []
    # only the PIT evaluation months (the month-ends of the run); the NAV panel also holds mid-month rows of funds that stopped pricing
    months = nav.index[nav.index >= pd.Timestamp(first)]
    if dates is not None:
        months = months.intersection(pd.DatetimeIndex(dates))
    for d in months:
        ok = (cnt.loc[d] >= window) & recent.loc[d]
        for sub, natures in SUB_NATURES.items():
            m = ok & nat.isin(natures)
            vc = mc[m].value_counts()
            n = int(m.sum())
            rows.append({"date": d, "sub": sub, "funds": n, "companies": int(len(vc)),
                         "top1": float(vc.iloc[0] / vc.sum()) if n else np.nan})
    df = pd.DataFrame(rows)
    df["fail"] = (df["funds"] < GATE_MIN_FUNDS) | (df["companies"] < GATE_MIN_COMPANIES) | (df["top1"] > GATE_TOP1_MAX)
    months = df["date"].nunique()
    bad_months = df.loc[df["fail"], "date"].nunique()
    return {"min_funds": df.groupby("sub")["funds"].min().to_dict(), "min_companies": df.groupby("sub")["companies"].min().to_dict(),
            "max_top1": df.groupby("sub")["top1"].max().round(4).to_dict(), "shortfall_months": int(bad_months), "months": int(months),
            "data_starved": bool(bad_months / months > DATA_STARVED_SHARE) if months else False,
            "worst": df[df["fail"]].sort_values("date").head(10).astype({"date": str}).to_dict("records")}


# ---------------------------------------------------------------------------------------------------- one variant against V0
def compound(r: pd.Series) -> float:
    return float((1.0 + r).prod() - 1.0) if len(r) else float("nan")


def evaluate_variant(name: str, tv: pd.DataFrame, t0: pd.DataFrame, episodes: list, n_boot_sharpe: int = 2000, n_boot_dd: int = 5000) -> dict:
    sv, s0 = chained(tv), chained(t0)
    cash = t0["cash_ret_1m"] if "cash_ret_1m" in t0.columns else None
    full_v, full_0 = window_stats(tv), window_stats(t0)
    subs = {}
    for label, lo, hi in SUBPERIODS:
        a, b = window_stats(tv, lo, hi), window_stats(t0, lo, hi)
        subs[label] = {"variant": a, "v0": b}
    dd_variant_ok = full_v["max_drawdown"] >= DD_LIMIT and all(
        v["variant"]["max_drawdown"] >= DD_LIMIT for v in subs.values() if v["variant"]["n_months"] >= 12)
    p_v, p_0 = drawdown_probability(sv, n_boot=n_boot_dd), drawdown_probability(s0, n_boot=n_boot_dd)
    r1 = bool(dd_variant_ok and (np.isnan(p_v) or np.isnan(p_0) or p_v <= p_0 + 1e-12))

    sh = paired_sharpe_difference(sv, s0, cash, n_boot=n_boot_sharpe)
    r2 = bool(sh["diff"] >= SHARPE_DIFF_MIN and sh["ci_low"] > SHARPE_CI_LOW_MIN)

    ret_full = full_v["ann_return"] - full_0["ann_return"]
    ok_subs = sum(1 for v in subs.values() if v["variant"]["n_months"] >= 12 and
                  v["variant"]["ann_return"] - v["v0"]["ann_return"] >= RETURN_DIFF_MIN)
    r3 = bool(ret_full >= RETURN_DIFF_MIN and ok_subs >= 3)

    turn_v = float(tv["turnover"].mean()) if "turnover" in tv.columns else float("nan")
    turn_0 = float(t0["turnover"].mean()) if "turnover" in t0.columns else float("nan")
    r4 = bool(turn_v - turn_0 <= TURNOVER_DIFF_MAX) if not (np.isnan(turn_v) or np.isnan(turn_0)) else False

    cw_v = tv["cash_weight"].reindex(t0.index).fillna(0.0)
    cw_0 = t0["cash_weight"].fillna(0.0)
    induced = (cw_v - cw_0).clip(lower=0.0)
    c1, c2 = float(induced.mean()), float(induced.max())
    eps = []
    c3_ok = c4_ok = True
    for e in episodes:
        if not e.get("available") or e["months"] <= 0:
            continue
        lo, hi = e["trough"], e["recovery_end"]
        after = slice(lo + pd.offsets.MonthEnd(1), hi)
        r_v, r_0 = sv.loc[after], s0.loc[after]
        m = len(r_v)
        cash_tw = float(induced.loc[lo:hi].mean())
        diff = compound(r_v) - compound(r_0) if m else float("nan")
        cum = (1.0 + sv.loc[e["start"]:hi]).cumprod()
        eps.append({"episode": e["episode"], "trough": str(lo.date()), "recovery_end": str(hi.date()), "censored": e["censored"], "months": m,
                    "variant_max_dd": float((cum / cum.cummax() - 1).min()) if len(cum) else float("nan"),
                    "induced_cash_tw": cash_tw, "total_cash_tw": float(cw_v.loc[lo:hi].mean()),
                    "return_variant": compound(r_v), "return_v0": compound(r_0), "return_diff": diff,
                    "c3_ok": bool(cash_tw <= CASH_RECOVERY_MAX), "c4_ok": bool(np.isnan(diff) or diff >= -REBOUND_TOL_PER_MONTH * m)})
        c3_ok &= eps[-1]["c3_ok"]
        c4_ok &= eps[-1]["c4_ok"]
    r6 = bool(c1 <= CASH_MEAN_MAX and c2 <= CASH_PEAK_MAX and c3_ok and c4_ok)

    tgt = (1.0 + t0["target_1m"].reindex(sv.index).dropna()).prod() ** (12.0 / max(1, len(sv))) - 1.0 if "target_1m" in t0.columns else float("nan")
    return {"variant": name, "status": "evaluated", "full": {"variant": full_v, "v0": full_0}, "subperiods": subs,
            "rules": {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": None, "R6": r6},
            "details": {"dd_prob_variant": p_v, "dd_prob_v0": p_0, "sharpe": sh, "return_diff_full": ret_full, "subperiods_ok_r3": ok_subs,
                        "turnover_variant": turn_v, "turnover_v0": turn_0,
                        "cash": {"induced_mean": c1, "induced_max": c2, "total_mean": float(cw_v.mean()), "total_p95": float(cw_v.quantile(0.95)),
                                 "total_share_over_10pct": float((cw_v > 0.10).mean())},
                        "objective": {"target_ann": float(tgt), "aspiration_ann": float(tgt + 0.02) if not np.isnan(tgt) else float("nan"),
                                      "variant_ann": full_v["ann_return"], "v0_ann": full_0["ann_return"]},
                        "episodes": eps}}


def decide(results: dict, v0_rule1: bool) -> dict:
    """The pre-registered decision: adopt the simplest variant passing Rules 1-4 and 6 (Rule 5 is checked afterwards on the live path)."""
    if not any(r.get("status") == "evaluated" for r in results.values()):
        return {"outcome": "NOT_EVALUATED", "variant": None, "note": "No variant has been evaluated yet (not run, incomplete or incomparable)."}
    passing = [n for n in SIMPLICITY_ORDER if n in results and results[n].get("status") == "evaluated" and
               all(results[n]["rules"][k] for k in ("R1", "R2", "R3", "R4", "R6"))]
    if passing:
        return {"outcome": "CANDIDATE", "variant": passing[0], "passing_1_4_6": passing,      # the simplest first (SIMPLICITY_ORDER)
                "note": "Rule 5 (the live dry-run is promotable) is still PENDING: it needs the live implementation of the variant."}
    if not v0_rule1:
        near = [n for n in SIMPLICITY_ORDER if n in results and results[n].get("status") == "evaluated" and
                all(results[n]["rules"][k] for k in ("R2", "R3", "R4", "R6"))]
        shallowest = max(near, key=lambda n: results[n]["full"]["variant"]["max_drawdown"]) if near else None
        return {"outcome": "RULE_7_FALLBACK", "variant": shallowest, "note": "V0 fails Rule 1 and no variant passes Rules 1-6: nothing is promoted "
                "automatically; the variant above (lowest drawdown among those passing Rules 2-4 and 6) is proposed for the FND-0223 waiver "
                "review; execution halts for the owner."}
    return {"outcome": "V0_STAYS", "variant": None, "note": "No variant passes Rules 1-4 and 6; V0 stays in production and the ticket closes with the evidence."}


# ---------------------------------------------------------------------------------------------------- the report
def pct(x, d=2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.{d}f}%"


def build(out_root: Path, v0_run: "Path | None" = None, n_boot_sharpe: int = 2000, n_boot_dd: int = 5000) -> dict:
    out_root = Path(out_root)
    gate = json.loads((out_root / "gate.json").read_text(encoding="utf-8"))
    v0_dir = Path(v0_run) if v0_run else Path(gate.get("v0_run_dir") or (out_root / "V0_gate_A" / "run"))
    t0 = load_table(v0_dir)
    inputs = v0_dir / "inputs"
    nav, attrs = pd.read_parquet(inputs / "nav.parquet"), pd.read_parquet(inputs / "attrs.parquet")
    ref = stress_reference(nav, attrs["Fund_Nature"].reindex(nav.columns))
    episodes = episode_windows(ref)
    results, notes = {}, []
    for name in SIMPLICITY_ORDER:
        sf = out_root / name / "variant_status.json"
        if not sf.is_file():
            continue
        st = json.loads(sf.read_text(encoding="utf-8"))
        if st["status"] == "not_available":
            results[name] = {"variant": name, "status": "not_available"}
        elif st["status"] != "success":
            results[name] = {"variant": name, "status": "INCOMPLETE", "reason": f"run status {st['status']}"}
        elif st.get("inputs_sha256") != gate.get("v0_inputs_sha256"):
            results[name] = {"variant": name, "status": "INCOMPARABLE", "reason": "saved inputs differ from V0's (the database moved)"}
        else:
            results[name] = evaluate_variant(name, load_table(Path(st["out_dir"])), t0, episodes, n_boot_sharpe, n_boot_dd)
            results[name]["overlay_sha256"] = st.get("overlay_sha256")
    full0 = window_stats(t0)
    v0 = {"full": full0, "subperiods": {l: window_stats(t0, lo, hi) for l, lo, hi in SUBPERIODS},
          "dd_prob": drawdown_probability(chained(t0), n_boot=n_boot_dd), "cash_mean": float(t0["cash_weight"].mean()),
          "turnover": float(t0["turnover"].mean()) if "turnover" in t0.columns else float("nan")}
    v0_rule1 = bool(full0["max_drawdown"] >= DD_LIMIT and all(v["max_drawdown"] >= DD_LIMIT for v in v0["subperiods"].values() if v["n_months"] >= 12))
    v2 = v2_eligibility(nav, attrs, dates=t0.index) if any(n in results for n in ("V2", "V123")) else None
    return {"gate": gate, "v0": v0, "v0_rule1": v0_rule1, "episodes": [{**e, **({k: str(e[k].date()) for k in ("start", "trough", "recovery_end")}
            if e.get("available") else {})} for e in episodes], "variants": results, "v2_eligibility": v2,
            "decision": decide(results, v0_rule1)}


def render(data: dict) -> str:
    g, v0, L = data["gate"], data["v0"], []
    L += ["# REPORT FND-0303 -- selection variants against PREREG_FND-0303", "",
          f"Generated by `scripts/audit/pit_variants_report.py` from the orchestrator's artifacts (code `{(g.get('git') or {}).get('commit')}`; "
          f"V0 series_stats sha256 `{g.get('v0_series_stats_sha256')}`). Every verdict below applies the pre-registered rules; nothing else decides.", "",
          "## V0 (live configuration)", "",
          "| window | months | ann. return | vol | Sharpe | max DD |", "|---|---|---|---|---|---|"]
    for label, s in [("full", v0["full"]), *v0["subperiods"].items()]:
        L.append(f"| {label} | {s['n_months']} | {pct(s['ann_return'])} | {pct(s['ann_vol'])} | {s['sharpe']:.3f} | {pct(s['max_drawdown'])} |")
    L += ["", f"Bootstrap P(max DD over 36 m < -15 %) = {pct(v0['dd_prob'], 1)}; mean cash {pct(v0['cash_mean'], 1)}; mean turnover {pct(v0['turnover'], 1)}. "
          f"V0 passes the 15 % drawdown rule: **{'yes' if data['v0_rule1'] else 'NO (Rule 7 applies)'}**.", ""]
    L += ["## Stress episodes (dated on the variant-independent B-STATIC reference)", "", "| episode | trough | recovery end | months | censored |", "|---|---|---|---|---|"]
    for e in data["episodes"]:
        L.append(f"| {e['episode']} | {e.get('trough', 'n/a')} | {e.get('recovery_end', 'n/a')} | {e.get('months', 'n/a')} | {e.get('censored', 'n/a')} |")
    L += ["", "## Rules matrix", "", "| variant | status | R1 DD | R2 Sharpe | R3 return | R4 turnover | R5 live | R6 cash |", "|---|---|---|---|---|---|---|---|"]
    mark = lambda v: "PENDING" if v is None else ("PASS" if v else "FAIL")
    for n in SIMPLICITY_ORDER:
        r = data["variants"].get(n)
        if r is None:
            continue
        if r["status"] != "evaluated":
            L.append(f"| {n} | {r['status']} {r.get('reason', '')} | | | | | | |")
        else:
            k = r["rules"]
            L.append(f"| {n} | evaluated | {mark(k['R1'])} | {mark(k['R2'])} | {mark(k['R3'])} | {mark(k['R4'])} | {mark(k['R5'])} | {mark(k['R6'])} |")
    for n in SIMPLICITY_ORDER:
        r = data["variants"].get(n)
        if not r or r["status"] != "evaluated":
            continue
        d, f = r["details"], r["full"]["variant"]
        L += ["", f"### {n}", "",
              f"- full window: ann. return {pct(f['ann_return'])} (V0 {pct(r['full']['v0']['ann_return'])}, diff {pct(d['return_diff_full'])}), "
              f"Sharpe {f['sharpe']:.3f} (diff {d['sharpe']['diff']:+.3f}, 90 % CI {d['sharpe']['ci_low']:+.3f}..{d['sharpe']['ci_high']:+.3f}), "
              f"max DD {pct(f['max_drawdown'])}; bootstrap P(DD36 < -15 %) {pct(d['dd_prob_variant'], 1)} vs V0 {pct(d['dd_prob_v0'], 1)}.",
              f"- sub-periods with return not below V0's by more than 0.5 pp: {d['subperiods_ok_r3']} of 4 (need 3); mean turnover {pct(d['turnover_variant'], 1)} vs {pct(d['turnover_v0'], 1)}.",
              f"- cash: variant-induced mean {pct(d['cash']['induced_mean'], 2)}, max {pct(d['cash']['induced_max'], 1)}; total mean {pct(d['cash']['total_mean'], 1)}, "
              f"p95 {pct(d['cash']['total_p95'], 1)}, months above 10 % {pct(d['cash']['total_share_over_10pct'], 1)}.",
              f"- objective: variant {pct(d['objective']['variant_ann'])} vs IPC+M3 {pct(d['objective']['target_ann'])} (aspiration {pct(d['objective']['aspiration_ann'])}); V0 {pct(d['objective']['v0_ann'])}.", "",
              "| episode | trough | recovery end | months | variant max DD | induced cash (tw) | return diff vs V0 | C3 | C4 |", "|---|---|---|---|---|---|---|---|---|"]
        for e in d["episodes"]:
            L.append(f"| {e['episode']} | {e['trough']} | {e['recovery_end']}{' (censored)' if e['censored'] else ''} | {e['months']} | {pct(e['variant_max_dd'])} | "
                     f"{pct(e['induced_cash_tw'])} | {pct(e['return_diff'])} | {mark(e['c3_ok'])} | {mark(e['c4_ok'])} |")
    if data["v2_eligibility"]:
        q = data["v2_eligibility"]
        L += ["", "## V2 input diagnostics (gates: >= 30 funds, >= 10 companies, largest company <= 20 %)", "",
              f"- minimum eligible funds {q['min_funds']}; minimum companies {q['min_companies']}; maximum top-1 share {q['max_top1']}.",
              f"- shortfall months: {q['shortfall_months']} of {q['months']} ({'DATA-STARVED' if q['data_starved'] else 'not data-starved'}; threshold 10 % of months)."]
    dcs = data["decision"]
    L += ["", "## Pre-registered outcome", "", f"**{dcs['outcome']}**" + (f" -- {dcs['variant']}" if dcs.get("variant") else ""), "", dcs["note"], "",
          "Mandate-compliance metric (PREREG section 4.4) is not computed here: the orchestrator runs with `--no-scores`, so the selected funds are not stored. "
          "It is reported-only and never decides.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--v0-run", default=None, help="folder of the V0 run (default: gate.json v0_run_dir, else <out-root>/V0_gate_A/run)")
    ap.add_argument("--n-boot", type=int, default=2000)
    args = ap.parse_args(argv)
    out_root = Path(args.out_root)
    data = build(out_root, args.v0_run, n_boot_sharpe=args.n_boot)
    (out_root / "report_data.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    md = out_root / "REPORT_FND-0303.md"
    md.write_text(render(data), encoding="utf-8")
    print(f"report written: {md}\noutcome: {data['decision']['outcome']} {data['decision'].get('variant') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
