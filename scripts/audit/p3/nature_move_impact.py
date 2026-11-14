# -*- coding: utf-8 -*-
"""Simulated impact of reclassifying funds on the P3 selection engine, BEFORE anything is changed in production (FND-0397; owner order 2026-11-08: "run a simulated impact analysis ... present the results before
authorizing the move to production").

    python scripts/audit/p3/nature_move_impact.py --census <json>            # moves from a census file: [[isin, old_subtype, new_subtype, in_universe, name], ...]
    python scripts/audit/p3/nature_move_impact.py --moves IE00008TZ2E8=Renta\\ Fija\\ Flexible ...

READ-ONLY: a read-only database session (the server refuses any write); nothing is persisted, no scenario is built, no classification changes. It runs the SAME code the engine runs:
  1. the scoring frame the live scorer loads (`fund_scorer.load_fund_metrics_for_scoring`), for the regime of the PRODUCTION scenario;
  2. `fund_scorer.score_funds_from_df` (the pure scoring core, normalised PER Fund_Nature, with the per-nature profile bonus and the hard filters) on the stored natures (baseline) and on the simulated ones;
  3. the builder's own selection (`score_candidates` filters, `portfolio_engine.select_within_exposure` with the builder's constraints, family dedup, manager cap, hysteresis towards the PRODUCTION incumbents and the
     shared exclusions across sub-portfolios in the regime's order) and `PortfolioBuilder._assign_weights`.
For a moved fund the simulation also re-derives `Credit_Quality` from the new nature, as a P1 refresh would (`classify_utils.derive_credit_quality`), because the Defensiva hard filter reads it. Duration_Profile is
not an input of the scoring frame and is not touched.
What it does NOT model (stated, not hidden): P2 metrics that depend on the peer group (momentum minus peers' mean, capture, persistence by category) keep their stored values: a real move also changes them at the next
P2 pass; the family-level nature consensus of P1 is applied as given by the census; a new P1 pass could move other funds.
Fidelity: the baseline selection is compared with the PRODUCTION and newest DRAFT portfolios, so the reader can see how closely the replica reproduces what the engine produced.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto3"):                      # proyecto3's `src` package (not proyecto2's)
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import config as _cfg                                                             # noqa: E402
from shared.db import get_connection                                                          # noqa: E402
from proyecto1.core.classify_utils import derive_credit_quality                               # noqa: E402
from src.fund_scorer import load_fund_metrics_for_scoring, score_funds_from_df                # noqa: E402
from src.portfolio_builder import (HYSTERESIS_BAND, MAX_FUNDS_PER_MGR, MAX_SAME_NATURE, MAX_WEIGHT_PER_FUND,   # noqa: E402
                                   MIN_WEIGHT_PER_FUND, WEIGHT_METHOD, _assign_weights)
from src.portfolio_engine import PortfolioConstraints, select_within_exposure                 # noqa: E402
from src.score_candidates import exposure_constrained, flag_fx_exposed                        # noqa: E402

NATURE_OF_SUBTYPE = {"RF_Corto": "Renta Fija Corto Plazo", "RF_Flexible": "Renta Fija Flexible"}
MAX_FUNDS_PER_SUB = 10


def moves_from_census(rows) -> dict:
    """{isin: new Fund_Nature} from census rows [isin, old_subtype, new_subtype, in_universe, name]; only in-universe funds count."""
    return {r[0]: NATURE_OF_SUBTYPE[r[2]] for r in rows if r[3] == 1 and r[2] in NATURE_OF_SUBTYPE}


def apply_moves(df: pd.DataFrame, moves: dict) -> pd.DataFrame:
    """A copy of the scoring frame with the new natures and the Credit_Quality a refresh would derive from them. Pure."""
    out = df.copy()
    for isin, nature in moves.items():
        if isin not in out.index:
            continue
        out.at[isin, "Fund_Nature"] = nature
        cq = derive_credit_quality(nature, str(out.at[isin, "Fund_Name"] if "Fund_Name" in out.columns else "").lower())
        if cq is not None and "Credit_Quality" in out.columns:
            out.at[isin, "Credit_Quality"] = cq
    return out


def candidates_from_scores(scores: pd.DataFrame, sub: str, master: pd.DataFrame) -> pd.DataFrame:
    """The builder's candidate frame (eligible, score > 0, in the universe), from simulated scores. Columns of score_candidates.CANDIDATE_COLUMNS without `block`."""
    s = scores[(scores["subportfolio"] == sub) & (scores["eligible"]) & (scores["score_final"] > 0)]
    c = pd.DataFrame({"isin": s["isin"].to_numpy(), "score_total": s["score_final"].to_numpy(), "fund_name": s["fund_name"].to_numpy(),
                      "fund_nature": s["fund_nature"].to_numpy(), "fund_family_id": s["fund_family_id"].to_numpy()})
    c["management_company"] = c["isin"].map(master["management_company"])
    return c.sort_values("score_total", ascending=False).reset_index(drop=True)


def build_selection(conn, scores: pd.DataFrame, regime_weights: dict, incumbents: dict, master: pd.DataFrame) -> dict:
    """{sub: DataFrame(isin, fund_nature, score_total, weight)} built the way PortfolioBuilder.build() builds it: sub-portfolios in the regime's order, shared exclusions, the builder's constraints."""
    constraints = exposure_constrained(PortfolioConstraints(max_funds_per_sub=MAX_FUNDS_PER_SUB, max_weight_per_fund=MAX_WEIGHT_PER_FUND, min_weight_per_fund=MIN_WEIGHT_PER_FUND,
                                                           max_same_nature=MAX_SAME_NATURE, max_funds_per_mgr=MAX_FUNDS_PER_MGR, hysteresis_band=HYSTERESIS_BAND, weight_method=WEIGHT_METHOD))
    isins_used, families_used, mgr_used, out = set(), set(), {}, {}
    for sub, w in regime_weights.items():
        if w == 0:
            continue
        cand = flag_fx_exposed(conn, candidates_from_scores(scores, sub, master))
        sel = select_within_exposure(cand, constraints, exclude_isins=isins_used, exclude_families=families_used, mgr_global=mgr_used, incumbent_isins=frozenset(incumbents.get(sub, ())))
        if sel.empty:
            out[sub] = pd.DataFrame(columns=["isin", "fund_nature", "score_total", "weight"])
            continue
        sel = _assign_weights(sel)
        for _, r in sel.iterrows():
            isins_used.add(r["isin"])
            fam = r.get("fund_family_id")
            families_used.add(fam if (fam and str(fam).strip()) else " ".join((r["fund_name"] or "").strip().split()[:3]).upper())
            mgr_used[r["management_company"] or "Desconocida"] = mgr_used.get(r["management_company"] or "Desconocida", 0) + 1
        out[sub] = sel[["isin", "fund_nature", "score_total", "weight"]].reset_index(drop=True)
    return out


def master_weights(selection: dict, regime_weights: dict) -> dict:
    """{isin: master weight} = regime weight of the sub-portfolio x internal weight."""
    return {r["isin"]: float(regime_weights[sub]) * float(r["weight"]) for sub, df in selection.items() for _, r in df.iterrows()}


def compare_selection(base: dict, sim: dict, regime_weights: dict) -> dict:
    mb, ms = master_weights(base, regime_weights), master_weights(sim, regime_weights)
    keys = sorted(set(mb) | set(ms))
    delta = {k: round(ms.get(k, 0.0) - mb.get(k, 0.0), 4) for k in keys if abs(ms.get(k, 0.0) - mb.get(k, 0.0)) > 1e-9}
    per_sub = {}
    for sub in regime_weights:
        b = set(base.get(sub, pd.DataFrame(columns=["isin"]))["isin"]); s = set(sim.get(sub, pd.DataFrame(columns=["isin"]))["isin"])
        per_sub[sub] = {"n_base": len(b), "n_sim": len(s), "left": sorted(b - s), "joined": sorted(s - b)}
    return {"turnover": round(0.5 * sum(abs(v) for v in delta.values()), 4), "weight_changes": delta, "per_subportfolio": per_sub, "n_funds_changed": len(delta)}


def score_impact(base: pd.DataFrame, sim: pd.DataFrame, moved: set) -> dict:
    """Per sub-portfolio: how the scores and ranks of MOVED and of UNMOVED funds change (the normalisation is per nature, so peers move too)."""
    out = {}
    for sub in sorted(set(base["subportfolio"])):
        b = base[base["subportfolio"] == sub].set_index("isin"); s = sim[sim["subportfolio"] == sub].set_index("isin")
        both = b.index.intersection(s.index)
        rb = b["score_final"].rank(ascending=False, method="first"); rs = s["score_final"].rank(ascending=False, method="first")
        unmoved = [i for i in both if i not in moved]
        d = (s.loc[unmoved, "score_final"] - b.loc[unmoved, "score_final"]).abs() if unmoved else pd.Series(dtype=float)
        top_b = set(rb[rb <= 30].index) - moved
        out[sub] = {"n_base": int(len(b)), "n_sim": int(len(s)), "eligible_base": int(b["eligible"].sum()), "eligible_sim": int(s["eligible"].sum()),
                    "new_candidacies": sorted(set(s.index) - set(b.index) & moved), "lost_candidacies": sorted(set(b.index) - set(s.index) & moved),
                    "unmoved_max_abs_score_change": None if d.empty else round(float(d.max()), 4), "unmoved_median_abs_score_change": None if d.empty else round(float(d.median()), 5),
                    "unmoved_top30_rank_changes": int(sum(1 for i in top_b if i in rs.index and abs(int(rs[i]) - int(rb[i])) >= 1))}
    return out


def fund_cards(isins, base_sc: pd.DataFrame, sim_sc: pd.DataFrame, base_sel: dict, sim_sel: dict, df: pd.DataFrame, df_sim: pd.DataFrame) -> dict:
    """For each fund: name, nature before/after, and per sub-portfolio its score and rank among the CANDIDATES before/after and whether it is selected before/after. A fund absent from the simulated scores was
    removed by the family dedup (a sibling share class scores higher): `sibling` names that class. Pure."""
    cards = {}
    for i in isins:
        card = {"name": str(df.at[i, "Fund_Name"]) if i in df.index else None, "nature_before": str(df.at[i, "Fund_Nature"]) if i in df.index else None,
                "nature_after": str(df_sim.at[i, "Fund_Nature"]) if i in df_sim.index else None, "family": None, "subportfolios": {}}
        fam = df.at[i, "fund_family_id"] if i in df.index else None
        card["family"] = None if fam is None or (isinstance(fam, float) and np.isnan(fam)) else str(fam)
        for sub in ("Defensiva", "Equilibrada", "Dinamica"):
            row = {}
            for tag, sc, sel in (("before", base_sc, base_sel), ("after", sim_sc, sim_sel)):
                cand = sc[(sc["subportfolio"] == sub) & (sc["eligible"]) & (sc["score_final"] > 0)].sort_values("score_final", ascending=False).reset_index(drop=True)
                hit = cand[cand["isin"] == i]
                row[tag] = {"score": None if hit.empty else float(hit["score_final"].iloc[0]), "rank": None if hit.empty else int(hit.index[0]) + 1, "n_candidates": int(len(cand)),
                            "selected": bool(i in set(sel.get(sub, pd.DataFrame(columns=["isin"]))["isin"]))}
            if any(row[t]["score"] is not None or row[t]["selected"] for t in row):
                card["subportfolios"][sub] = row
        if card["family"] and sim_sc[sim_sc["isin"] == i].empty:
            sib = sim_sc[sim_sc["fund_family_id"] == card["family"]]
            card["removed_by_family_dedup"] = True
            card["sibling"] = sorted(set(sib["isin"]))
        cards[i] = card
    return cards


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--census"); ap.add_argument("--moves", nargs="*", default=[])
    ap.add_argument("--out-dir", default=str(_cfg.AUDIT_DIRS["p1"]))
    args = ap.parse_args(argv)
    moves = {}
    if args.census:
        moves.update(moves_from_census(json.load(open(args.census, encoding="utf-8"))))
    for m in args.moves:
        isin, _, nature = m.partition("=")
        moves[isin] = nature
    if not moves:
        ap.error("no moves: give --census or --moves ISIN=Nature")
    conn = get_connection(); conn.read_only = True
    prod = conn.execute("SELECT scenario_id, macro_regime FROM gold.portfolio_scenarios WHERE status = 'PRODUCTION'").fetchone()
    draft = conn.execute("SELECT scenario_id FROM gold.portfolio_scenarios WHERE status = 'DRAFT' ORDER BY created_at DESC, scenario_id DESC LIMIT 1").fetchone()
    regime = prod[1]
    wrows = conn.execute("SELECT block, sum(weight), array_agg(isin) FROM gold.portfolio_weights WHERE scenario_id = %s GROUP BY block", (prod[0],)).fetchall()
    regime_weights = {r[0]: round(float(r[1]), 4) for r in wrows}
    order = [s for s in ("Defensiva", "Equilibrada", "Dinamica") if s in regime_weights]
    regime_weights = {s: regime_weights[s] for s in order}
    incumbents = {r[0]: list(r[2]) for r in wrows}
    master = pd.DataFrame(conn.execute("SELECT isin, management_company FROM fund_master").fetchall(), columns=["isin", "management_company"]).set_index("isin")
    df = load_fund_metrics_for_scoring(conn, regime=regime)
    df_sim = apply_moves(df, moves)
    moved = {i for i in moves if i in df.index}
    base_sc = score_funds_from_df(df, regime, verbose=False); sim_sc = score_funds_from_df(df_sim, regime, verbose=False)
    base_sel = build_selection(conn, base_sc, regime_weights, incumbents, master); sim_sel = build_selection(conn, sim_sc, regime_weights, incumbents, master)

    prod_funds = {s: set(i) for s, i in incumbents.items()}
    draft_funds = {}
    if draft:
        for r in conn.execute("SELECT block, array_agg(isin) FROM gold.portfolio_weights WHERE scenario_id = %s GROUP BY block", (draft[0],)).fetchall():
            draft_funds[r[0]] = set(r[1])
    fidelity = {s: {"baseline_vs_production": len(set(base_sel[s]["isin"]) & prod_funds.get(s, set())), "baseline_vs_draft": len(set(base_sel[s]["isin"]) & draft_funds.get(s, set())),
                    "n_production": len(prod_funds.get(s, set())), "n_baseline": len(base_sel[s])} for s in order}
    cq_changes = {i: (str(df.at[i, "Credit_Quality"]), str(df_sim.at[i, "Credit_Quality"])) for i in moved if str(df.at[i, "Credit_Quality"]) != str(df_sim.at[i, "Credit_Quality"])}
    held = {}
    for i in (set(prod_funds.get("Defensiva", set())) | set(prod_funds.get("Equilibrada", set())) | set(prod_funds.get("Dinamica", set()))) & moved:
        bs = base_sc[(base_sc["isin"] == i)][["subportfolio", "score_final", "eligible", "exclusion_reason"]].to_dict("records")
        ss = sim_sc[(sim_sc["isin"] == i)][["subportfolio", "score_final", "eligible", "exclusion_reason"]].to_dict("records")
        held[i] = {"production_weight": round(sum(float(r[0]) for r in conn.execute("SELECT weight FROM gold.portfolio_weights WHERE scenario_id = %s AND isin = %s", (prod[0], i)).fetchall()), 4),
                   "baseline": bs, "simulated": ss}
    report = {"regime": regime, "production": prod[0], "draft": draft[0] if draft else None, "regime_weights": regime_weights, "n_moves": len(moves), "n_moved_in_frame": len(moved),
              "fidelity": fidelity, "score_impact": score_impact(base_sc, sim_sc, moved), "selection_impact": compare_selection(base_sel, sim_sel, regime_weights),
              "credit_quality_changes": cq_changes, "held_moved_funds": held,
              "cards": fund_cards(sorted(set(compare_selection(base_sel, sim_sel, regime_weights)["weight_changes"]) | {i for i in moved if i in {x for v in prod_funds.values() for x in v}}),
                                  base_sc, sim_sc, base_sel, sim_sel, df, df_sim),
              "baseline_selection": {s: base_sel[s]["isin"].tolist() for s in order}, "simulated_selection": {s: sim_sel[s]["isin"].tolist() for s in order}}
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / "nature_move_impact.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("baseline_selection", "simulated_selection")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
