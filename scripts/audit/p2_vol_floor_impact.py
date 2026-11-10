# -*- coding: utf-8 -*-
"""Read-only impact simulation of a VOLATILITY FLOOR AT THE SOURCE for the P2 ratio metrics (owner order 2026-11-10; FND-0404, FND-0394).

    python scripts/audit/p2_vol_floor_impact.py [--floors 0.001 0.003 0.005] [--out-dir DIR]

The question: P2 publishes a Sharpe of -66 on a 0.37% volatility. If P2 stopped publishing a Sharpe / Sortino / return-to-volatility ratio whose own volatility denominator is below a floor (as FND-0075 already does for the
Sortino's DOWNSIDE deviation, 0.1%), what would change? This script answers it WITHOUT running P2 and WITHOUT writing anything (the session is read-only; no pipeline process starts):
  A. STORED METRICS: how many rows per ratio family / horizon / nature / fund would no longer be published, per floor; how many are outliers the statistical audit alarms on today.
  B. P3: the scorer's own frame of today, with the affected inputs set to missing exactly as the loader would see them after P2 stops publishing them (`sharpe`, `sharpe_<regime>`; variant 2 adds `sortino_<regime>`),
     re-scored with the scorer's own code and re-selected with the builder's own constraints (the same machinery as nature_move_impact.py): scores, ranks, eligible set, the selected funds, weights and the portfolio's metrics
     (evaluated on the ORIGINAL metrics of the funds, so before and after are comparable).
  C. WHAT IT DOES NOT MODEL: P2 peer-dependent metrics (percentiles / z-scores by category), the rolling slope (the P3 rolling signals are switched OFF: ROLLING_PCTILE_P3_ENABLED), the PIT backtest (needs the same floor
     for parity), a recompute (the floor would reach the stored rows only through a P2 recompute).
Outputs: out/audit/P2/p2_vol_floor_impact.json and .md.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "proyecto3"):                      # proyecto3's `src` package (not proyecto2's)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import config as _cfg                                                       # noqa: E402
from shared.data_suspect import load_panel, quantity_of, score_panel, volatility_quantity_of   # noqa: E402
from shared.db import get_connection                                                    # noqa: E402
from src.fund_scorer import load_fund_metrics_for_scoring, score_funds_from_df          # noqa: E402

_spec = importlib.util.spec_from_file_location("nature_move_impact", _ROOT / "scripts" / "audit" / "nature_move_impact.py")
nm = importlib.util.module_from_spec(_spec)
sys.modules["nature_move_impact"] = nm
_spec.loader.exec_module(nm)

DEFAULT_FLOORS = (0.001, 0.003, 0.005)
FAMILY = {"sharpe": "sharpe", "sortino": "sortino", "ret_vol_simple": "ret_vol_simple"}


def ratio_rows(panel: pd.DataFrame) -> pd.DataFrame:
    """Every ratio row of the panel with the smallest volatility of its own denominator (same fund, horizon or regime; across nominal and real variants). Columns: isin, nature, metric, value, quantity, family, kind, vol.
    `kind` = since_inception scalar / regime / crisis window / rolling. Rows with no denominator in the panel keep vol NaN. Pure."""
    p = panel.assign(quantity=panel["metric"].map(quantity_of))
    vols = p[p["metric"].str.startswith("vol_ann")]
    vol_min = vols.groupby(["isin", "quantity"])["value"].min()
    r = p[p["quantity"].map(lambda q: volatility_quantity_of(q) is not None)].copy()
    r["vq"] = r["quantity"].map(volatility_quantity_of)
    r["vol"] = [vol_min.get((i, v), np.nan) for i, v in zip(r["isin"], r["vq"])]
    name = r["quantity"].str.split("|").str[0]
    hor = r["quantity"].str.split("|").str[1]
    r["family"] = np.where(name.str.startswith("sharpe"), "sharpe", np.where(name.str.startswith("sortino"), "sortino", "ret_vol_simple"))
    r["kind"] = np.where(hor.str.startswith("crisis"), "crisis_window", np.where(hor.str.startswith("rolling"), "rolling",
                np.where(name.str.contains("_"), "regime", "since_inception")))
    return r.drop(columns=["vq"])


def stored_impact(rows: pd.DataFrame, scored: pd.DataFrame, floors) -> dict:
    """Part A: per floor, the rows that would not be published, by family / kind / nature, and how many are rows the audit alarms on (adequate sample, |z| >= 5). Pure."""
    z = scored.set_index(["isin", "metric"])[["z", "adequate"]]
    rows = rows.assign(alarm=[bool(abs(z.at[(i, m), "z"]) >= 5 and z.at[(i, m), "adequate"]) if (i, m) in z.index and pd.notna(z.at[(i, m), "z"]) else False
                              for i, m in zip(rows["isin"], rows["metric"])])
    out = {"ratio_rows": int(len(rows)), "ratio_funds": int(rows["isin"].nunique()), "alarm_rows_total": int(rows["alarm"].sum()), "by_floor": {}}
    for f in floors:
        hit = rows[rows["vol"] < f]
        out["by_floor"][str(f)] = {
            "rows": int(len(hit)), "share_of_ratio_rows": round(len(hit) / max(len(rows), 1), 4), "funds": int(hit["isin"].nunique()),
            "by_family": {k: int(v) for k, v in hit.groupby("family").size().items()},
            "by_kind": {k: int(v) for k, v in hit.groupby("kind").size().items()},
            "funds_by_nature": {k: int(v) for k, v in hit.drop_duplicates("isin").groupby("nature").size().items()},
            "alarm_rows_withdrawn": int(hit["alarm"].sum()), "alarm_funds": int(hit[hit["alarm"]]["isin"].nunique()),
            "alarm_rows_left": int(rows["alarm"].sum() - hit["alarm"].sum())}
    return out


def apply_floor(df: pd.DataFrame, vol: pd.DataFrame, regime_suffix: str, floor: float, with_sortino: bool, neutral: bool = False) -> tuple:
    """(frame, affected): the scorer frame with the inputs a P2 floor would not publish set to NaN. `vol` has columns `si` (vol_ann since_inception) and `reg` (vol_ann of the active regime), index isin.
    `neutral` = the MITIGATION variant: P3 fills the withdrawn Sharpe with the median of its Fund_Nature (computed over the funds that kept theirs) instead of leaving it missing, which the scorer reads as 0 of that
    metric's weight (a missing metric contributes nothing to the base score). Pure."""
    sim = df.copy()
    v = vol.reindex(sim.index)
    m_si, m_reg = (v["si"] < floor).fillna(False), (v["reg"] < floor).fillna(False)
    sim.loc[m_si, "sharpe"] = np.nan
    if neutral:
        med = sim.loc[~m_si].groupby("Fund_Nature")["sharpe"].median()
        sim.loc[m_si, "sharpe"] = sim.loc[m_si, "Fund_Nature"].map(med)
    if f"sharpe_{regime_suffix}" in sim.columns:
        sim.loc[m_reg, f"sharpe_{regime_suffix}"] = np.nan
        if with_sortino and f"sortino_{regime_suffix}" in sim.columns:
            sim.loc[m_reg, f"sortino_{regime_suffix}"] = np.nan
    affected = set(sim.index[m_si | m_reg])
    return sim, affected


def portfolio_metrics(weights: dict, df: pd.DataFrame) -> dict:
    """Weighted metrics of a master-weight dict on the ORIGINAL metrics of the funds (weights renormalised over the funds that have the metric). Pure."""
    out = {}
    for col, key in (("return_ann_real", "return_ann_real"), ("sharpe", "sharpe"), ("max_dd", "max_dd"), ("srri_nav", "srri")):
        w = pd.Series(weights)
        x = df[col].reindex(w.index) if col in df.columns else pd.Series(np.nan, index=w.index)
        ok = x.notna()
        out[key] = round(float((w[ok] * x[ok]).sum() / w[ok].sum()), 4) if ok.any() and w[ok].sum() > 0 else None
    return out


def rank_in(scores: pd.DataFrame, sub: str) -> pd.Series:
    s = scores[(scores["subportfolio"] == sub) & scores["eligible"] & (scores["score_final"] > 0)].sort_values("score_final", ascending=False)
    return pd.Series(range(1, len(s) + 1), index=s["isin"].to_numpy())


def scenario(conn, df, base_sc, base_sel, vol, regime, regime_suffix, regime_weights, incumbents, master, floor, with_sortino, neutral=False) -> dict:
    sim, affected = apply_floor(df, vol, regime_suffix, floor, with_sortino, neutral)
    sim_sc = score_funds_from_df(sim, regime, verbose=False)
    sim_sel = nm.build_selection(conn, sim_sc, regime_weights, incumbents, master)
    cmp = nm.compare_selection(base_sel, sim_sel, regime_weights)
    mb, ms = nm.master_weights(base_sel, regime_weights), nm.master_weights(sim_sel, regime_weights)
    b = base_sc.set_index(["isin", "subportfolio"])["score_final"]
    s = sim_sc.set_index(["isin", "subportfolio"])["score_final"]
    both = b.index.intersection(s.index)
    d = (s.loc[both] - b.loc[both])
    changed = d[d.abs() > 1e-6]
    per_sub = {}
    for sub in regime_weights:
        rb, rs = rank_in(base_sc, sub), rank_in(sim_sc, sub)
        common = rb.index.intersection(rs.index)
        top_b = [i for i in rb.index[:30] if i in rs.index]
        per_sub[sub] = {"n_candidates_base": int(len(rb)), "n_candidates_sim": int(len(rs)),
                        "funds_with_a_score_change": int(sum(1 for (i, sb) in changed.index if sb == sub)),
                        "top30_rank_changes": int(sum(1 for i in top_b if rb[i] != rs[i])), "top30_base_still_in_top30": int(sum(1 for i in rb.index[:30] if i in rs.index and rs[i] <= 30)),
                        "max_abs_score_change": round(float(d[[ix for ix in d.index if ix[1] == sub]].abs().max()), 4) if any(ix[1] == sub for ix in d.index) else 0.0}
    nature_mix = {sub: {k: int(v) for k, v in sel["fund_nature"].value_counts().items()} for sub, sel in sim_sel.items()}
    nature_mix_base = {sub: {k: int(v) for k, v in sel["fund_nature"].value_counts().items()} for sub, sel in base_sel.items()}
    aff_nat = df.loc[sorted(affected), "Fund_Nature"].value_counts().to_dict() if affected else {}
    held = {i: {"production_weight": round(mb[i], 4), "now": round(ms.get(i, 0.0), 4)} for i in affected if i in mb}
    moves = {}
    for i in sorted(set(mb) | set(ms)):
        if abs(ms.get(i, 0.0) - mb.get(i, 0.0)) > 1e-9 and i in df.index:
            r = df.loc[i]
            moves[i] = {"name": str(r.get("Fund_Name"))[:34], "nature": str(r.get("Fund_Nature")), "weight_before": round(mb.get(i, 0.0), 4), "weight_after": round(ms.get(i, 0.0), 4),
                        "sharpe_raw": None if pd.isna(r.get("sharpe")) else round(float(r["sharpe"]), 2), "return_ann_real": None if pd.isna(r.get("return_ann_real")) else round(float(r["return_ann_real"]), 4),
                        "max_dd": None if pd.isna(r.get("max_dd")) else round(float(r["max_dd"]), 3), "input_withdrawn": i in affected}
    return {"floor": floor, "with_sortino": with_sortino, "neutral": neutral, "moves": moves, "funds_with_an_input_withdrawn": len(affected), "by_nature": {k: int(v) for k, v in aff_nat.items()},
            "score_changes": int(len(changed)), "score_change_median_abs": round(float(changed.abs().median()), 4) if len(changed) else 0.0,
            "score_change_max_abs": round(float(changed.abs().max()), 4) if len(changed) else 0.0,
            "eligible_base": int(base_sc["eligible"].sum()), "eligible_sim": int(sim_sc["eligible"].sum()),
            "per_subportfolio": per_sub, "turnover": cmp["turnover"], "n_funds_changed": cmp["n_funds_changed"], "selection": cmp["per_subportfolio"],
            "portfolio_before": portfolio_metrics(mb, df), "portfolio_after": portfolio_metrics(ms, df),
            "nature_mix_before": nature_mix_base, "nature_mix_after": nature_mix, "held_with_a_withdrawn_input": held,
            "weight_changes": cmp["weight_changes"]}


def label(s: dict) -> str:
    return "Sharpe withdrawn, P3 fills it neutrally (nature median)" if s.get("neutral") else ("Sharpe + Sortino(regime)" if s["with_sortino"] else "Sharpe only")


def render(rep: dict) -> str:
    L = [f"# P2 volatility floor at the source: impact simulation ({rep['generated']})", "",
         "Read-only. No P2 run, nothing written to the database. Regime of the PRODUCTION scenario: **" + rep["regime"] + "**; scenario `" + rep["production"] + "`.", ""]
    a = rep["stored"]
    L += ["## A. Stored metrics (fund_metrics, in-universe funds)", f"- ratio rows (Sharpe / Sortino / return-to-volatility, all horizons and regimes): {a['ratio_rows']:,} on {a['ratio_funds']:,} funds; rows the audit alarms on today (adequate sample, |z| >= 5): {a['alarm_rows_total']:,}", "",
          "| floor | rows no longer published | share | funds | Sharpe / Sortino / ret_vol | alarm rows withdrawn (left) |", "|---|---|---|---|---|---|"]
    for f, x in a["by_floor"].items():
        L.append(f"| {f} | {x['rows']:,} | {x['share_of_ratio_rows']:.1%} | {x['funds']:,} | {x['by_family'].get('sharpe', 0):,} / {x['by_family'].get('sortino', 0):,} / {x['by_family'].get('ret_vol_simple', 0):,} | {x['alarm_rows_withdrawn']:,} ({x['alarm_rows_left']:,}) |")
    L += ["", "Funds by nature (rows withdrawn on at least one ratio): " + "; ".join(f"floor {f}: {x['funds_by_nature']}" for f, x in a["by_floor"].items()), ""]
    L += ["## B. P3 (scorer re-run on the frame of today with the withdrawn inputs missing, builder constraints of production)",
          f"Baseline fidelity: the rebuilt baseline selection equals PRODUCTION on {rep['fidelity']}.", "",
          "| floor | variant | funds with an input withdrawn | funds with a score change | eligible (base -> sim) | selection turnover | funds changed | weighted return_ann_real | weighted sharpe | weighted max_dd | srri |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in rep["scenarios"]:
        pb, pa = s["portfolio_before"], s["portfolio_after"]
        L.append(f"| {s['floor']} | {label(s)} | {s['funds_with_an_input_withdrawn']} | {s['score_changes']} | {s['eligible_base']} -> {s['eligible_sim']} | {s['turnover']:.1%} | {s['n_funds_changed']} | "
                 f"{pb['return_ann_real']} -> {pa['return_ann_real']} | {pb['sharpe']} -> {pa['sharpe']} | {pb['max_dd']} -> {pa['max_dd']} | {pb['srri']} -> {pa['srri']} |")
    L += [""]
    for s in rep["scenarios"]:
        L += [f"### floor {s['floor']}, {label(s)}",
              f"- inputs withdrawn on {s['funds_with_an_input_withdrawn']} funds by nature {s['by_nature']}; score changes {s['score_changes']} (median |change| {s['score_change_median_abs']}, max {s['score_change_max_abs']})"]
        for sub, x in s["per_subportfolio"].items():
            sel = s["selection"][sub]
            L.append(f"- {sub}: candidates {x['n_candidates_base']} -> {x['n_candidates_sim']}; funds with a score change {x['funds_with_a_score_change']}; top-30 rank changes {x['top30_rank_changes']}; "
                     f"top-30 funds that stay in the top 30 {x['top30_base_still_in_top30']}/30; selection left {sel['left']} joined {sel['joined']}; nature mix {s['nature_mix_before'].get(sub)} -> {s['nature_mix_after'].get(sub)}")
        if s["held_with_a_withdrawn_input"]:
            L.append(f"- selected funds with a withdrawn input (master weight before -> after): {s['held_with_a_withdrawn_input']}")
        if s["moves"] and s["turnover"] > 0:
            L += ["", "| fund | name | nature | weight before -> after | raw Sharpe | return_ann_real | max_dd | its input withdrawn |", "|---|---|---|---|---|---|---|---|"]
            for i, m in sorted(s["moves"].items(), key=lambda kv: -abs(kv[1]["weight_after"] - kv[1]["weight_before"])):
                L.append(f"| {i} | {m['name']} | {m['nature']} | {m['weight_before']} -> {m['weight_after']} | {m['sharpe_raw']} | {m['return_ann_real']} | {m['max_dd']} | {'yes' if m['input_withdrawn'] else ''} |")
        L.append("")
    L += ["## C. How P3 reads a withdrawn Sharpe, and where the floor would go",
          "- `compute_base_scores` ranks each metric inside the fund's Fund_Nature and a missing value contributes 0 of that metric's weight (Sharpe is 15-25% of the base score): P3 reads a withdrawn Sharpe as the WORST Sharpe of its nature, not as a neutral one. "
          "That is the whole first-order effect on selection; the variant `P3 fills it neutrally` shows what a mitigation changes.",
          "- Today the regime Sharpe / Sortino multipliers are 1.0 (`MULT_REGIME_SHARPE_BONUS`, `MULT_REGIME_SORTINO_BONUS`) and the rolling signals are OFF (`ROLLING_PCTILE_P3_ENABLED`), so only the since-inception Sharpe reaches P3: the Sortino variant is identical to the Sharpe-only one by construction.",
          "- P2 code sites: `returns.sharpe_ratio` (since inception and the crisis windows, through `risk_metrics`), `returns.sharpe_ratio_from_returns` (the per-regime Sharpe), `returns.ret_vol_simple`, `rolling_stats._roll_sharpe` (rolling series). "
          "The Sortino already has its floor on the DOWNSIDE deviation (`_MIN_DOWNSIDE_DEV_ANN` = 0.001, FND-0075); the Sharpe only guards `vol == 0`. The PIT backtest has its own Sharpe (`proyecto3/src/pit_metrics.py`) and would need the same floor for parity.",
          "- Reaching the stored rows needs a P2 recompute (`FAMILY_CALC_OVERRIDES` or a `CALC_VERSION` bump) and then a new P3 build: nothing changes in PRODUCTION until both, and the owner has authorised neither.", "",
          "## D. Not modelled", "- P2 peer-dependent metrics (percentile / z-score by category) and the rolling Sharpe slope: they would move with the floor, but the P3 rolling signals are OFF (`ROLLING_PCTILE_P3_ENABLED`).",
          "- The point-in-time backtest computes its own Sharpe (`pit_metrics`): it needs the same floor for parity with production.",
          "- Nothing reaches the stored rows until a P2 recompute (the floor is a code change; FAMILY_CALC_OVERRIDES or a CALC_VERSION bump), which the owner has not authorised.", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--floors", nargs="*", type=float, default=list(DEFAULT_FLOORS))
    ap.add_argument("--out-dir", default=str(_cfg.AUDIT_DIRS["p2"]))
    args = ap.parse_args(argv)
    from datetime import date
    conn = get_connection()
    conn.read_only = True
    prod = conn.execute("SELECT scenario_id, macro_regime FROM gold.portfolio_scenarios WHERE status = 'PRODUCTION'").fetchone()
    regime = prod[1]
    from proyecto3.src.regime_classifier import _REGIME_SUFFIX
    suffix = _REGIME_SUFFIX[regime]
    wrows = conn.execute("SELECT block, sum(weight), array_agg(isin) FROM gold.portfolio_weights WHERE scenario_id = %s GROUP BY block", (prod[0],)).fetchall()
    regime_weights = {r[0]: round(float(r[1]), 4) for r in wrows}
    order = [s for s in ("Defensiva", "Equilibrada", "Dinamica") if s in regime_weights]
    regime_weights = {s: regime_weights[s] for s in order}
    incumbents = {r[0]: list(r[2]) for r in wrows}
    master = pd.DataFrame(conn.execute("SELECT isin, management_company FROM fund_master").fetchall(), columns=["isin", "management_company"]).set_index("isin")

    # A. stored metrics
    panel = load_panel(conn)
    scored = score_panel(panel)
    rows = ratio_rows(panel)
    stored = stored_impact(rows, scored, args.floors)

    # B. P3
    df = load_fund_metrics_for_scoring(conn, regime=regime)
    vrows = conn.execute("SELECT isin, metric, value FROM fund_metrics WHERE horizon = 'since_inception' AND real_flag = 0 AND metric_version = 'v1' AND metric IN ('vol_ann', %s)",
                         (f"vol_ann_{suffix}",)).fetchall()
    vol = pd.DataFrame(vrows, columns=["isin", "metric", "value"]).pivot(index="isin", columns="metric", values="value").rename(columns={"vol_ann": "si", f"vol_ann_{suffix}": "reg"})
    for c in ("si", "reg"):
        if c not in vol.columns:
            vol[c] = np.nan
    base_sc = score_funds_from_df(df, regime, verbose=False)
    base_sel = nm.build_selection(conn, base_sc, regime_weights, incumbents, master)
    fidelity = {s: f"{len(set(base_sel[s]['isin']) & set(incumbents.get(s, [])))}/{len(incumbents.get(s, []))} of its funds" for s in order}
    scenarios = []
    for f in args.floors:
        for with_sortino, neutral in ((False, False), (True, False), (False, True)):
            scenarios.append(scenario(conn, df, base_sc, base_sel, vol, regime, suffix, regime_weights, incumbents, master, f, with_sortino, neutral))
    rep = {"generated": date.today().isoformat(), "regime": regime, "production": prod[0], "regime_weights": regime_weights, "floors": args.floors, "fidelity": fidelity,
           "stored": stored, "scenarios": scenarios}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "p2_vol_floor_impact.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    md = render(rep)
    (out / "p2_vol_floor_impact.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
