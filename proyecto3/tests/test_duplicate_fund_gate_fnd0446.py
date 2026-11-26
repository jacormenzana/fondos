# proyecto3/tests/test_duplicate_fund_gate_fnd0446.py
# -*- coding: utf-8 -*-
"""FND-0446: the HARD promotion gate GATE:DUPLICATE_FUND. A scenario may hold at most one share class of one sub-fund. Two holdings are the same
fund when they share a fund_family_id, OR when they come from the same manager and their monthly returns are near-identical in the EUR view (an
unhedged pair) or in each class's own currency (a hedged / base pair). The second rule does not rest on fund_family_id, the key that split the
ABN US ESG pair (FND-0444). Calibration (2026-11-26, read-only, 4,573 same-family and 59,227 same-manager-and-nature pairs, last 36 months): the ABN
pair has EUR-view correlation 0.998 and a mean monthly difference of 0.18 pp. R-7: no database, synthetic returns only."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import duplicate_holdings as dh  # noqa: E402
from proyecto3.src import scenario_lifecycle as lc  # noqa: E402

MONTHS = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 13)]
BASE = [0.012, -0.034, 0.021, 0.008, -0.015, 0.027, 0.004, -0.022, 0.031, 0.011, -0.009, 0.018] * 3
FX = [0.006, -0.011, 0.004, -0.007, 0.010, -0.003, 0.008, -0.012, 0.005, 0.002, -0.006, 0.009] * 3


def _series(values, months=MONTHS):
    return dict(zip(months, values))


def _shift(values, by):
    return [v + by for v in values]


def _h(isin, family=None, manager="ACME AM"):
    return {"isin": isin, "family": family, "manager": manager}


KW = dict(min_corr=0.995, max_mad=0.002, min_months=24)


# ---- similarity ----------------------------------------------------------------------------------------------------------------------------------------------------

def test_identical_series_have_correlation_1_and_no_difference():
    corr, mad, n = dh.similarity(_series(BASE), _series(BASE))
    assert n == 36 and mad == 0.0 and corr == pytest.approx(1.0)


def test_only_overlapping_months_count():
    corr, mad, n = dh.similarity(_series(BASE[:30], MONTHS[:30]), _series(BASE[10:], MONTHS[10:]))
    assert n == 20


def test_a_constant_series_has_no_correlation():
    corr, mad, n = dh.similarity(_series([0.01] * 36), _series(BASE))
    assert corr is None


# ---- the two rules ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_two_classes_of_one_family_are_a_duplicate_whatever_their_returns():
    returns = {"eur": {"A": _series(BASE), "B": _series(FX)}, "loc": {}}
    pairs = dh.duplicate_pairs([_h("A", "FAM_1"), _h("B", "FAM_1")], returns, **KW)
    assert [(a, b) for a, b, _ in pairs] == [("A", "B")] and "FAM_1" in pairs[0][2]


def test_an_unhedged_pair_with_split_families_is_caught_in_the_eur_view():
    """The ABN case: EUR and USD classes of one sub-fund, two fund_family_id. In EUR they earn the same; in their own currencies they differ by the FX move."""
    eur_a, eur_b = BASE, _shift(BASE, 0.0005)                                    # fees differ by a few bp a month
    loc_a, loc_b = BASE, [b - f for b, f in zip(BASE, FX)]
    returns = {"eur": {"A": _series(eur_a), "B": _series(eur_b)}, "loc": {"A": _series(loc_a), "B": _series(loc_b)}}
    pairs = dh.duplicate_pairs([_h("A", "FAM_5"), _h("B", "FAM_6")], returns, **KW)
    assert len(pairs) == 1 and pairs[0][2].startswith("returns eur")


def test_a_hedged_pair_with_split_families_is_caught_in_the_local_view():
    eur_a, eur_b = BASE, [b + f for b, f in zip(BASE, FX)]                      # the hedged class does not carry the FX move into EUR
    loc_a, loc_b = BASE, _shift(BASE, 0.001)                                     # in their own currencies they match (carry difference)
    returns = {"eur": {"A": _series(eur_a), "B": _series(eur_b)}, "loc": {"A": _series(loc_a), "B": _series(loc_b)}}
    pairs = dh.duplicate_pairs([_h("A", "FAM_7"), _h("B", "FAM_8")], returns, **KW)
    assert len(pairs) == 1 and pairs[0][2].startswith("returns loc")


def test_two_different_funds_are_not_a_duplicate():
    other = [x * 0.6 + f for x, f in zip(reversed(BASE), FX)]
    returns = {"eur": {"A": _series(BASE), "B": _series(other)}, "loc": {"A": _series(BASE), "B": _series(other)}}
    assert dh.duplicate_pairs([_h("A", "F1"), _h("B", "F2")], returns, **KW) == []


def test_near_identical_returns_of_two_managers_are_not_a_duplicate():
    returns = {"eur": {"A": _series(BASE), "B": _series(BASE)}, "loc": {}}
    assert dh.duplicate_pairs([_h("A", "F1", "ACME AM"), _h("B", "F2", "OTHER AM")], returns, **KW) == []


def test_the_manager_comparison_ignores_case_and_spaces():
    returns = {"eur": {"A": _series(BASE), "B": _series(BASE)}, "loc": {}}
    assert len(dh.duplicate_pairs([_h("A", "F1", "Acme AM "), _h("B", "F2", "ACME AM")], returns, **KW)) == 1


def test_an_unknown_manager_never_matches_by_returns():
    returns = {"eur": {"A": _series(BASE), "B": _series(BASE)}, "loc": {}}
    assert dh.duplicate_pairs([_h("A", "F1", None), _h("B", "F2", None)], returns, **KW) == []


def test_too_short_a_common_history_is_not_judged_by_returns():
    returns = {"eur": {"A": _series(BASE[:20], MONTHS[:20]), "B": _series(BASE[:20], MONTHS[:20])}, "loc": {}}
    assert dh.duplicate_pairs([_h("A", "F1"), _h("B", "F2")], returns, **KW) == []


def test_each_threshold_bites():
    close = _shift(BASE, 0.0025)                                                  # 0.25 pp a month: over the 0.20 pp limit
    returns = {"eur": {"A": _series(BASE), "B": _series(close)}, "loc": {}}
    assert dh.duplicate_pairs([_h("A", "F1"), _h("B", "F2")], returns, **KW) == []
    assert len(dh.duplicate_pairs([_h("A", "F1"), _h("B", "F2")], returns, **dict(KW, max_mad=0.003))) == 1


def test_pairs_are_reported_once_in_a_stable_order():
    returns = {"eur": {i: _series(BASE) for i in "CAB"}, "loc": {}}
    pairs = dh.duplicate_pairs([_h("C", "F3"), _h("A", "F1"), _h("B", "F2")], returns, **KW)
    assert [(a, b) for a, b, _ in pairs] == [("A", "B"), ("A", "C"), ("B", "C")]


# ---- the promotion gate ----------------------------------------------------------------------------------------------------------------------------------------------

def _scope():
    from datetime import datetime
    from shared.statistical_audit.gating import AuditScope
    return AuditScope(run_id="audit_x", run_ts=datetime(2026, 11, 9, 10, 0))


def _gate(**over):
    base = dict(status="DRAFT", n_funds=30, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.2, budget=0.3, audit=_scope(), built_at=None)
    base.update(over)
    return lc.evaluate_promotion(**base)


def test_the_gate_is_hard_and_known_to_the_catalogue():
    assert lc.GATE_DUPLICATE_FUND == "DUPLICATE_FUND" and lc.GATE_DUPLICATE_FUND not in lc.OVERRIDABLE_GATES
    from shared.governance import controls as gc
    assert any(c.source == gc.SOURCE_GATE + "DUPLICATE_FUND" for c in gc.CONTROLS)


def test_not_evaluated_blocks_nothing():
    assert not [b for b in _gate().blockers if "DUPLICATE_FUND" in b]


def test_a_duplicate_pair_blocks_the_promotion():
    v = _gate(duplicate_holdings=[("LU1670606760", "LU1670606927", "returns eur: corr 0.998, mean |diff| 0.18 pp over 38 months")])
    lines = [b for b in v.blockers if b.startswith("[GATE:DUPLICATE_FUND]")]
    assert len(lines) == 1 and "LU1670606760" in lines[0] and "LU1670606927" in lines[0]


def test_a_reasoned_override_does_not_lift_it():
    v = _gate(duplicate_holdings=[("A", "B", "same fund_family_id FAM_1")], accept_alarms="x" * 50, accept_ap="FND-0446", ap_exists=True)
    assert [b for b in v.blockers if b.startswith("[GATE:DUPLICATE_FUND]")]
    assert not [o for o in v.overridden if o[0] == "DUPLICATE_FUND"]


def test_no_duplicate_passes():
    assert not [b for b in _gate(duplicate_holdings=[]).blockers if "DUPLICATE_FUND" in b]


def test_an_unreadable_check_fails_closed():
    v = _gate(duplicate_holdings=None, duplicate_problem="OperationalError: connection lost")
    assert [b for b in v.blockers if b.startswith("[GATE:DUPLICATE_FUND]") and "connection lost" in b]


# ---- the database wrapper fails closed -------------------------------------------------------------------------------------------------------------------------------

class _BrokenConn:
    def __init__(self): self.rolled_back = False
    def execute(self, *a, **k): raise RuntimeError("relation does not exist")
    def rollback(self): self.rolled_back = True


def test_check_scenario_returns_the_problem_instead_of_raising():
    conn = _BrokenConn()
    pairs, problem = dh.check_scenario(conn, ["A", "B"])
    assert pairs is None and "relation does not exist" in problem and conn.rolled_back


def test_a_scenario_with_one_fund_has_nothing_to_compare():
    assert dh.check_scenario(_BrokenConn(), ["A"]) == ([], None)


def test_the_switch_and_its_parameters():
    assert cfg.DUPLICATE_FUND_GATE_ENABLED is True
    assert (cfg.DUPLICATE_FUND_MIN_CORR, cfg.DUPLICATE_FUND_MAX_MAD, cfg.DUPLICATE_FUND_MIN_MONTHS, cfg.DUPLICATE_FUND_WINDOW_MONTHS) == (0.995, 0.002, 24, 36)
