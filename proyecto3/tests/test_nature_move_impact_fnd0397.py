# proyecto3/tests/test_nature_move_impact_fnd0397.py
# -*- coding: utf-8 -*-
"""FND-0397: the pure parts of scripts/audit/nature_move_impact.py (moves from a census, master weights, selection comparison, score impact).

R-7: no DB; synthetic frames only. The script runs the engine's own code on the live store (read-only) when the simulation is produced; these tests pin the arithmetic around it.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "proyecto3"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("nature_move_impact", _ROOT / "scripts" / "audit" / "nature_move_impact.py")
nm = importlib.util.module_from_spec(spec)
sys.modules["nature_move_impact"] = nm
spec.loader.exec_module(nm)


def test_moves_from_a_census_keep_only_universe_funds_and_known_subtypes():
    rows = [["A", "RF_Corto", "RF_Flexible", 1, "a"], ["B", "RF_Flexible", "RF_Corto", 1, "b"], ["C", "RF_Corto", "RF_Flexible", 0, "c"], ["D", "RF_Corto", "Mixtos", 1, "d"]]
    assert nm.moves_from_census(rows) == {"A": "Renta Fija Flexible", "B": "Renta Fija Corto Plazo"}


def _sel(sub_isins):
    return {sub: pd.DataFrame({"isin": list(w), "fund_nature": "N", "score_total": 1.0, "weight": list(w.values())}) for sub, w in sub_isins.items()}


def test_master_weights_are_the_regime_weight_times_the_internal_weight():
    sel = _sel({"Defensiva": {"A": 0.5, "B": 0.5}, "Dinamica": {"C": 1.0}})
    assert nm.master_weights(sel, {"Defensiva": 0.6, "Dinamica": 0.4}) == pytest.approx({"A": 0.3, "B": 0.3, "C": 0.4})


def test_the_comparison_reports_who_left_who_joined_and_the_turnover():
    rw = {"Defensiva": 0.6, "Dinamica": 0.4}
    base = _sel({"Defensiva": {"A": 0.5, "B": 0.5}, "Dinamica": {"C": 1.0}})
    sim = _sel({"Defensiva": {"A": 0.5, "X": 0.5}, "Dinamica": {"C": 1.0}})
    out = nm.compare_selection(base, sim, rw)
    assert out["per_subportfolio"]["Defensiva"] == {"n_base": 2, "n_sim": 2, "left": ["B"], "joined": ["X"]}
    assert out["per_subportfolio"]["Dinamica"]["left"] == [] and out["weight_changes"] == {"B": -0.3, "X": 0.3}
    assert out["turnover"] == pytest.approx(0.3) and out["n_funds_changed"] == 2                              # half the sum of |changes|
    same = nm.compare_selection(base, base, rw)
    assert same["turnover"] == 0 and same["weight_changes"] == {}


def _scores(rows):
    return pd.DataFrame(rows, columns=["isin", "subportfolio", "score_final", "eligible"])


def test_score_impact_separates_moved_funds_from_the_peers_whose_normalisation_shifts():
    base = _scores([("M", "Defensiva", 0.9, True), ("P1", "Defensiva", 0.8, True), ("P2", "Defensiva", 0.7, True), ("Q", "Equilibrada", 0.4, True)])
    sim = _scores([("M", "Defensiva", 0.6, True), ("P1", "Defensiva", 0.85, True), ("P2", "Defensiva", 0.7, True), ("Q", "Equilibrada", 0.4, True), ("M", "Equilibrada", 0.5, True)])
    out = nm.score_impact(base, sim, {"M"})
    d = out["Defensiva"]
    assert d["n_base"] == d["n_sim"] == 3 and d["unmoved_max_abs_score_change"] == pytest.approx(0.05) and d["unmoved_top30_rank_changes"] == 2    # P1 and P2 each change rank once M falls
    assert out["Equilibrada"]["new_candidacies"] == ["M"]                                                       # a moved fund gains a candidacy in a sub-portfolio that now lists its nature


def test_candidates_follow_the_builders_filters_eligible_positive_score_with_a_manager():
    scores = pd.DataFrame({"isin": ["A", "B", "C"], "subportfolio": ["Defensiva"] * 3, "score_final": [0.9, 0.0, 0.7], "eligible": [True, True, False],
                           "fund_name": ["a", "b", "c"], "fund_nature": ["N"] * 3, "fund_family_id": ["F1", "F2", "F3"]})
    master = pd.DataFrame({"management_company": ["M1", "M2", "M3"]}, index=["A", "B", "C"])
    c = nm.candidates_from_scores(scores, "Defensiva", master)
    assert c["isin"].tolist() == ["A"] and c["management_company"].tolist() == ["M1"]                           # B has no score, C is not eligible
