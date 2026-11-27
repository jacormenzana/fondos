# proyecto3/tests/test_builder_dedup_fnd0444.py
# -*- coding: utf-8 -*-
"""FND-0444 fallback (owner order 2026-11-27): the family key splits share classes of one sub-fund (478 pairs in the census of that day, five different causes) and could not be
repaired and validated before the first counted R6 month, so the BUILDER applies the duplicate rule of the promotion gate (proyecto3/src/duplicate_holdings.py) itself: the shared
selection function skips a candidate whose twin is already chosen. The rule also gets a volatility floor: below DUPLICATE_FUND_MIN_VOL returns cannot identify a sub-fund (two
different money-market funds of one manager match). R-7: no database, synthetic frames only."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import duplicate_holdings as dh  # noqa: E402
from proyecto3.src import portfolio_builder as pb  # noqa: E402
from proyecto3.src.portfolio_engine import PortfolioConstraints, select_candidates, select_within_exposure  # noqa: E402

MONTHS = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)]
HIGH = [0.012, -0.034, 0.021, 0.008, -0.015, 0.027, 0.004, -0.022, 0.031, 0.011, -0.009, 0.018] * 3          # about 7 % a year
LOW = [0.0021, 0.0023, 0.0022, 0.0024, 0.0020, 0.0022, 0.0023, 0.0021, 0.0022, 0.0024, 0.0023, 0.0022] * 3   # money market: about 0.04 % a year
KW = dict(min_corr=0.995, max_mad=0.002, min_months=24)


def _s(values):
    return dict(zip(MONTHS, values))


def _h(isin, family=None, manager="ACME AM"):
    return {"isin": isin, "family": family or isin, "manager": manager}


# ---- the volatility floor --------------------------------------------------------------------------------------------------------------------------------------------

def test_without_a_floor_two_money_market_funds_match_by_returns():
    returns = {"loc": {"A": _s(LOW), "B": _s(LOW)}}
    assert len(dh.duplicate_pairs([_h("A"), _h("B")], returns, **KW)) == 1


def test_below_the_floor_returns_do_not_identify_a_sub_fund():
    returns = {"loc": {"A": _s(LOW), "B": _s(LOW)}, "eur": {"A": _s(LOW), "B": _s(LOW)}}
    assert dh.duplicate_pairs([_h("A"), _h("B")], returns, min_vol=0.005, **KW) == []


def test_the_family_rule_still_applies_below_the_floor():
    returns = {"loc": {"A": _s(LOW), "B": _s(LOW)}}
    assert len(dh.duplicate_pairs([_h("A", "FAM_1"), _h("B", "FAM_1")], returns, min_vol=0.005, **KW)) == 1


def test_above_the_floor_nothing_changes():
    returns = {"loc": {"A": _s(HIGH), "B": _s(HIGH)}}
    assert len(dh.duplicate_pairs([_h("A"), _h("B")], returns, min_vol=0.005, **KW)) == 1


def test_an_unhedged_pair_is_still_found_in_the_eur_view_when_one_view_is_flat():
    returns = {"eur": {"A": _s(HIGH), "B": _s(HIGH)}, "loc": {"A": _s(LOW), "B": _s(HIGH)}}
    assert len(dh.duplicate_pairs([_h("A"), _h("B")], returns, min_vol=0.005, **KW)) == 1


def test_twins_are_symmetric_and_not_transitive():
    twins = dh.twins_from_pairs([("A", "B", "x"), ("B", "C", "y")])
    assert twins == {"A": frozenset({"B"}), "B": frozenset({"A", "C"}), "C": frozenset({"B"})}


# ---- the shared selection function -----------------------------------------------------------------------------------------------------------------------------------

def _cands(*isins, exposed=()):
    df = pd.DataFrame([{"isin": i, "score_total": 1.0 - n * 0.01, "fund_name": f"Fund {i}", "fund_nature": "Mixtos",
                        "management_company": f"M{n}", "fund_family_id": f"F_{i}"} for n, i in enumerate(isins)])
    df["fx_exposed"] = df["isin"].isin(exposed)
    return df


CONS = PortfolioConstraints(max_funds_per_sub=3, max_same_nature=10, max_funds_per_mgr=10)


def test_the_twin_of_a_chosen_fund_is_skipped_and_the_next_candidate_enters():
    got = select_candidates(_cands("A", "B", "C", "D"), CONS, twins={"A": frozenset({"B"}), "B": frozenset({"A"})})
    assert list(got["isin"]) == ["A", "C", "D"]


def test_the_twin_rule_holds_across_sub_portfolios():
    used = set()
    first = select_candidates(_cands("A"), CONS, exclude_isins=used, twins={"A": frozenset({"B"}), "B": frozenset({"A"})})
    second = select_candidates(_cands("B", "C"), CONS, exclude_isins=used, twins={"A": frozenset({"B"}), "B": frozenset({"A"})})
    assert list(first["isin"]) == ["A"] and list(second["isin"]) == ["C"]


def test_no_twins_is_exactly_the_selection_as_before():
    a = select_candidates(_cands("A", "B", "C", "D"), CONS)
    for none in (None, {}):
        b = select_candidates(_cands("A", "B", "C", "D"), CONS, twins=none)
        pd.testing.assert_frame_equal(a, b)


def test_a_fund_dropped_for_exposure_does_not_take_its_twin_with_it():
    cons = PortfolioConstraints(max_funds_per_sub=2, max_same_nature=10, max_funds_per_mgr=10, max_exposed_weight=0.0, weight_method="equal",
                                max_weight_per_fund=0.5)
    twins = {"A": frozenset({"B"}), "B": frozenset({"A"})}
    got = select_within_exposure(_cands("A", "B", "C", exposed=("A",)), cons, twins=twins)
    assert list(got["isin"]) == ["B", "C"]


def test_the_rotation_gate_never_reinstates_a_twin_of_a_held_fund():
    slot = {"isin": "NEW", "weight": 0.1, "fund_family_id": "F_NEW", "fund_name": "n", "gestora": "g", "fund_nature": "Mixtos"}
    held = {"isin": "A", "weight": 0.1, "fund_family_id": "F_A", "fund_name": "a", "gestora": "h", "fund_nature": "Mixtos"}
    sp = pb.SubPortfolioAllocation(name="Defensiva", regime_weight=1.0, funds=[held, slot])
    port = pb.Portfolio(scenario_id="s", regime="Expansion", profile="Equilibrada", sub_portfolios=[sp], macro_context={})
    inc = {"fund_family_id": "F_B", "fund_name": "b", "gestora": "k", "fund_nature": "Mixtos"}
    assert pb._reinstatement_blocker(port, sp, slot, "B", inc, None, twins={"B": frozenset({"A"})}) is not None
    assert pb._reinstatement_blocker(port, sp, slot, "B", inc, None) is None


# ---- what the builder reads ------------------------------------------------------------------------------------------------------------------------------------------

class _Conn:
    def __init__(self, holdings): self.holdings = holdings
    def execute(self, sql, params=()):
        rows = [(h["isin"], h["family"], h["manager"]) for h in self.holdings if h["isin"] in params[0]]
        return type("C", (), {"fetchall": lambda _s: rows})()


def test_returns_are_read_only_for_managers_with_two_or_more_candidates(monkeypatch):
    asked = []
    monkeypatch.setattr(dh, "load_returns", lambda conn, isins, window: asked.append(list(isins)) or {"loc": {i: _s(HIGH) for i in isins}})
    conn = _Conn([_h("A", manager="ONE"), _h("B", manager="ONE"), _h("C", manager="TWO"), _h("D", manager=None), _h("E", manager=None)])
    twins = dh.twins_of(conn, ["A", "B", "C", "D", "E"])
    assert asked == [["A", "B"]] and twins == {"A": frozenset({"B"}), "B": frozenset({"A"})}


def test_nothing_is_read_when_no_manager_repeats(monkeypatch):
    monkeypatch.setattr(dh, "load_returns", lambda *a: (_ for _ in ()).throw(AssertionError("no NAV read expected")))
    conn = _Conn([_h("A", manager="ONE"), _h("C", manager="TWO")])
    assert dh.twins_of(conn, ["A", "C"]) == {}


def test_an_unreadable_input_fails_the_build_instead_of_building_without_the_rule():
    class _Broken:
        def execute(self, *a, **k): raise RuntimeError("relation does not exist")
    with pytest.raises(RuntimeError):
        dh.twins_of(_Broken(), ["A", "B"])


# ---- the switch ------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_builder_computes_twins_only_with_the_switch_on(monkeypatch):
    seen = []
    monkeypatch.setattr(dh, "twins_of", lambda conn, isins: seen.append(sorted(isins)) or {"A": frozenset({"B"})})
    candidates = pd.DataFrame({"isin": ["A", "B", "A"], "block": ["Defensiva", "Dinamica", "Dinamica"]})
    previous = pb.Portfolio(scenario_id="p", regime="r", profile="x", macro_context={},
                            sub_portfolios=[pb.SubPortfolioAllocation(name="Defensiva", regime_weight=1.0, funds=[{"isin": "P1", "weight": 1.0}])])
    monkeypatch.setattr(cfg, "P3_BUILDER_DUPLICATE_FUND_ENABLED", True)
    assert pb._duplicate_twins(None, "v1", "r", {"Defensiva": 1.0}, candidates, previous) == {"A": frozenset({"B"})}
    assert seen == [["A", "B", "P1"]]
    monkeypatch.setattr(cfg, "P3_BUILDER_DUPLICATE_FUND_ENABLED", False)
    assert pb._duplicate_twins(None, "v1", "r", {"Defensiva": 1.0}, candidates, previous) == {} and len(seen) == 1


def test_without_a_candidate_frame_the_builder_reads_the_isins_with_one_cheap_query(monkeypatch):
    seen, sql = [], []
    monkeypatch.setattr(dh, "twins_of", lambda conn, isins: seen.append(sorted(isins)) or {})
    monkeypatch.setattr(cfg, "P3_BUILDER_DUPLICATE_FUND_ENABLED", True)
    monkeypatch.setattr(pb, "load_current_candidates", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the slow candidate query must not be repeated")))

    class _Q:
        def execute(self, q, params=()):
            sql.append((q, params))
            return type("C", (), {"fetchall": lambda _s: [("A",), ("B",)]})()
    assert pb._duplicate_twins(_Q(), "v1", "Expansion", {"Defensiva": 1.0}, None, None) == {}
    assert seen == [["A", "B"]] and len(sql) == 1 and sql[0][1] == ("v1", "Expansion") and "DISTINCT isin" in sql[0][0]


def test_the_parameters_and_the_switch():
    assert cfg.P3_BUILDER_DUPLICATE_FUND_ENABLED is True
    assert cfg.DUPLICATE_FUND_MIN_VOL == 0.005
