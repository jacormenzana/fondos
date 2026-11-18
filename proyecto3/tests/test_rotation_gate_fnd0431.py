# proyecto3/tests/test_rotation_gate_fnd0431.py
# -*- coding: utf-8 -*-
"""FND-0431 (P3-1.1, P3-1.2): the rotation gate's incumbent score, pairing and ROT-0.

Defects pinned here (verified by reading portfolio_builder.py on 2026-11-18):
  1. rotation_plan read the incumbent's score from a previous portfolio that load_previous builds WITHOUT one, so score_out was always 0
     and the cost hurdle (cost / max(score, 0.01)) was about 100x the cost.
  2. The plan only carried the paired entrant when a rotation WAS recommended, while _apply_rotation_gate reverts entries that are NOT
     recommended AND have an entrant: the condition could never be true, so the gate never reverted anything.
  3. Entrants were taken first-match over a set (arbitrary order, reusable).
  4. A reverted incumbent was restored without eligibility, regime, date, family, manager, nature or FX checks (ROT-0 was not explicit).
No Postgres: the three database helpers are replaced (R-7).
"""
from __future__ import annotations

import pytest

from proyecto3.src import portfolio_builder as pb

COST = 0.01


def fund(isin, nature="Renta Variable", mgr=None, fam=None, score=None, weight=0.2):
    f = {"isin": isin, "fund_name": f"name {isin}", "fund_nature": nature, "gestora": mgr or f"M-{isin}",
         "fund_family_id": fam or f"F-{isin}", "weight": weight, "role": "slot"}
    if score is not None:
        f["score"] = score
    return f


def portfolio(**subs):
    return pb.Portfolio("s", "Expansion", "Equilibrada", [pb.SubPortfolioAllocation(n, 0.5, list(fs)) for n, fs in subs.items()])


def run_row(score=0.5, eligible=True, in_universe=True):
    return {"score": score, "eligible": eligible, "in_universe": in_universe}


@pytest.fixture
def stub(monkeypatch):
    """Patch the database helpers; the returned dict is filled by each test."""
    state = {"run": {}, "attrs": {}, "fx": None}
    monkeypatch.setattr(pb, "estimate_rotation_cost", lambda conn, a, b: COST)
    monkeypatch.setattr(pb, "_incumbent_run_scores", lambda conn, version, regime, isins: dict(state["run"]))
    monkeypatch.setattr(pb, "_incumbent_attrs", lambda conn, isins: {i: state["attrs"][i] for i in isins if i in state["attrs"]})
    monkeypatch.setattr(pb, "_fx_flags", lambda conn, isins: state["fx"])
    return state


def attrs(isin, nature="Renta Variable", mgr=None, fam=None):
    return {"fund_name": f"name {isin}", "fund_nature": nature, "gestora": mgr or f"M-{isin}", "fund_family_id": fam or f"F-{isin}"}


# ---------------------------------------------------------------- 1. the incumbent score comes from the run
def test_the_hurdle_uses_the_incumbent_score_of_the_run_not_zero(stub):
    previous = portfolio(Defensiva=[fund("A")])                       # load_previous builds funds without a score
    new = portfolio(Defensiva=[fund("B", score=0.70)])
    scores = {("Defensiva", "A"): run_row(0.50)}
    plan = pb.rotation_plan(None, previous, new, "v1", incumbent_scores=scores)
    (op,) = plan
    # improvement 0.20 > cost 0.01 / 0.50 + 0.05 = 0.07: the rotation is recommended. With score_out = 0 the hurdle was 1.05 and it never was.
    assert op["recomendar"] is True and op["isin_out"] == "A" and op["isin_in"] == "B"


def test_a_not_recommended_rotation_still_carries_its_entrant(stub):
    previous = portfolio(Defensiva=[fund("A")])
    new = portfolio(Defensiva=[fund("B", score=0.52)])
    plan = pb.rotation_plan(None, previous, new, "v1", incumbent_scores={("Defensiva", "A"): run_row(0.50)})
    (op,) = plan
    assert op["recomendar"] is False and op["isin_in"] == "B"       # the revert step needs the pair; before, isin_in was None here


# ---------------------------------------------------------------- 2. one-to-one, deterministic pairing
def test_pairing_is_one_to_one_and_independent_of_input_order(stub):
    scores = {("Defensiva", "A1"): run_row(0.50), ("Defensiva", "A2"): run_row(0.40)}
    results = []
    for order in (("A1", "A2"), ("A2", "A1")):
        previous = portfolio(Defensiva=[fund(i) for i in order])
        new = portfolio(Defensiva=[fund("B2", score=0.60), fund("B1", score=0.90)])
        plan = pb.rotation_plan(None, previous, new, "v1", incumbent_scores=scores)
        results.append(sorted((op["isin_out"], op["isin_in"]) for op in plan))
    assert results[0] == results[1] == [("A1", "B1"), ("A2", "B2")]    # best incumbent against best entrant, each entrant used once


def test_an_exit_with_no_entrant_is_a_plain_exit(stub):
    previous = portfolio(Defensiva=[fund("A1"), fund("A2")])
    new = portfolio(Defensiva=[fund("B1", score=0.9)])
    scores = {("Defensiva", "A1"): run_row(0.5), ("Defensiva", "A2"): run_row(0.4)}
    plan = pb.rotation_plan(None, previous, new, "v1", incumbent_scores=scores)
    unpaired = [op for op in plan if op["isin_in"] is None]
    assert len(unpaired) == 1 and unpaired[0]["recomendar"] is True


# ---------------------------------------------------------------- 3. the gate reverts a not recommended rotation
def test_the_gate_restores_the_incumbent_in_the_entrants_slot(stub):
    stub["run"] = {("Defensiva", "A"): run_row(0.50)}
    stub["attrs"] = {"A": attrs("A")}
    previous = portfolio(Defensiva=[fund("A")])
    # A pairs with the BEST entrant (B, 0.52); C (0.40) is the extra new position, not a rotation
    tentative = portfolio(Defensiva=[fund("B", score=0.52), fund("C", score=0.40)])
    result, plan = pb._apply_rotation_gate(None, tentative, previous, "v1")
    isins = [f["isin"] for f in result.sub_portfolios[0].funds]
    assert isins == ["A", "C"]                                          # A takes B's slot (same weight)
    assert result.sub_portfolios[0].funds[0]["weight"] == 0.2
    assert result.sub_portfolios[0].funds[0]["score"] == 0.5          # the score of the run, not a stale one
    assert [op["revertida"] for op in plan] == [True]


# ---------------------------------------------------------------- 4. ROT-0: a fund that cannot be held is never retained
@pytest.mark.parametrize("row", [
    run_row(0.50, eligible=False),
    run_row(0.50, in_universe=False),
    run_row(0.0),
    None,                                                              # no row in the latest run at all: never fall back to an older one
], ids=["ineligible", "out-of-universe", "score-zero", "not-in-the-run"])
def test_rot_0_forces_the_exit_and_never_reinstates(stub, row):
    stub["run"] = {} if row is None else {("Defensiva", "A"): row}
    stub["attrs"] = {"A": attrs("A")}
    previous = portfolio(Defensiva=[fund("A")])
    tentative = portfolio(Defensiva=[fund("B", score=0.52)])
    result, plan = pb._apply_rotation_gate(None, tentative, previous, "v1")
    assert [f["isin"] for f in result.sub_portfolios[0].funds] == ["B"]
    (op,) = plan
    assert op["recomendar"] is True and "ROT-0" in op["razon"] and not op.get("revertida")


# ---------------------------------------------------------------- 5. the reinstated incumbent must still satisfy the construction rules
def _gate_with(stub, tentative_funds, incumbent_attrs, fx=None):
    stub["run"] = {("Defensiva", "A"): run_row(0.50)}
    stub["attrs"] = {"A": incumbent_attrs}
    stub["fx"] = fx
    previous = portfolio(Defensiva=[fund("A")])
    tentative = portfolio(Defensiva=tentative_funds)
    return pb._apply_rotation_gate(None, tentative, previous, "v1")


def _refused(result, plan):
    (op,) = plan
    assert "A" not in {f["isin"] for sp in result.sub_portfolios for f in sp.funds}
    assert op["recomendar"] is True and op.get("revertida") is False and "reversion refused" in op["razon"]
    return op


def test_a_family_already_in_the_portfolio_blocks_the_reversion(stub):
    result, plan = _gate_with(stub, [fund("B", score=0.52), fund("C", fam="SHARED")], attrs("A", fam="SHARED"))
    _refused(result, plan)


def test_the_manager_cap_blocks_the_reversion_even_across_sub_portfolios(stub):
    stub["run"] = {("Defensiva", "A"): run_row(0.50)}
    stub["attrs"] = {"A": attrs("A", mgr="BIG")}
    previous = portfolio(Defensiva=[fund("A")])
    tentative = portfolio(Defensiva=[fund("B", score=0.52), fund("C", mgr="BIG")], Equilibrada=[fund("D", mgr="BIG")])
    result, plan = pb._apply_rotation_gate(None, tentative, previous, "v1")
    _refused(result, plan)                                              # MAX_FUNDS_PER_MGR = 2, already 2 elsewhere


def test_the_nature_cap_blocks_the_reversion(stub):
    others = [fund(f"N{i}", nature="Mixtos") for i in range(pb.MAX_SAME_NATURE)]
    result, plan = _gate_with(stub, [fund("B", score=0.52, nature="Renta Variable")] + others, attrs("A", nature="Mixtos"))
    _refused(result, plan)


def test_the_isin_already_held_elsewhere_blocks_the_reversion(stub):
    stub["run"] = {("Defensiva", "A"): run_row(0.50)}
    stub["attrs"] = {"A": attrs("A")}
    previous = portfolio(Defensiva=[fund("A")])
    tentative = portfolio(Defensiva=[fund("B", score=0.52)], Equilibrada=[fund("A")])
    result, plan = pb._apply_rotation_gate(None, tentative, previous, "v1")
    op = next(o for o in plan if o["subportfolio"] == "Defensiva")
    assert op["recomendar"] is True and "reversion refused" in op["razon"]


def test_the_fx_budget_blocks_a_reversion_that_would_exceed_it(stub):
    funds = [fund("B", score=0.52), fund("X", weight=0.2), fund("Y", weight=0.2), fund("Z", weight=0.2), fund("W", weight=0.2)]
    flags = {"A": True, "X": True, "B": False, "Y": False, "Z": False, "W": False}
    # exposed weight now 0.2 (X); A exposed in B's slot gives 0.4 > MAX_NON_EUR_UNHEDGED_WEIGHT (0.30)
    result, plan = _gate_with(stub, funds, attrs("A"), fx=flags)
    _refused(result, plan)


def test_an_incumbent_that_satisfies_every_rule_is_restored(stub):
    funds = [fund("B", score=0.52), fund("X"), fund("Y")]
    flags = {"A": False, "B": False, "X": False, "Y": False}
    result, plan = _gate_with(stub, funds, attrs("A"), fx=flags)
    assert [f["isin"] for f in result.sub_portfolios[0].funds][0] == "A"
    assert plan[0]["revertida"] is True
