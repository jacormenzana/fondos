# proyecto3/tests/test_portfolio_constraints.py
# -*- coding: utf-8 -*-
"""
Tests for the Phase 2 portfolio-construction integrity fixes
(proyecto3/src/portfolio_builder.py) -- P3 optimization plan, 2026-09-18.

Two groups:
  1. _clamp_and_renormalize -- pure-function property tests. This is the
     highest-risk new code in Phase 2 (a water-filling projection replacing
     the old clamp-then-renormalize, which could violate both the floor
     and the cap it was supposed to enforce). No DB needed.
  2. _select_funds_for_subportfolio -- the manager-count cap (MAX_FUNDS_PER_MGR,
     Fase 2a) and hysteresis bonus (HYSTERESIS_BAND, Fase 2c), against a
     minimal self-contained in-memory schema (just the two columns/tables
     this function actually reads -- not db/schema_fondos.sql).

R-7: imports ONLY portfolio_builder -- no pipeline.py, no core.io.
Run from repo root:
    python -m pytest proyecto3/tests/test_portfolio_constraints.py -v
"""

import random
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.portfolio_builder import (
    _clamp_and_renormalize,
    _select_funds_for_subportfolio,
    MAX_FUNDS_PER_MGR,
    MAX_WEIGHT_PER_FUND,
    MIN_WEIGHT_PER_FUND,
    HYSTERESIS_BAND,
)


# ============================================================
# 1. _clamp_and_renormalize -- water-filling property tests
# ============================================================

LO, HI = MIN_WEIGHT_PER_FUND, MAX_WEIGHT_PER_FUND  # 0.03, 0.20


def _random_weights(n: int, seed: int) -> pd.Series:
    rng = random.Random(seed)
    raw = [rng.uniform(0.01, 100.0) for _ in range(n)]
    total = sum(raw)
    return pd.Series([v / total for v in raw])


@pytest.mark.parametrize("n", range(5, 11))
@pytest.mark.parametrize("seed", range(5))
def test_feasible_n_sums_to_one_and_respects_bounds(n, seed):
    # n in [5, 10]: n*LO <= 1.0 <= n*HI always holds (5*0.03=0.15 <= 1.0,
    # 5*0.20=1.0 <= 1.0; 10*0.03=0.30 <= 1.0 <= 10*0.20=2.0) -- feasible
    # across the whole range, so both bounds must be respected exactly.
    # Tolerance matches the function's own 4-decimal rounding contract
    # (weights are meant for DB/report storage at 0.01% precision, same as
    # the pre-Phase-2 code's .round(4)) -- not a correctness bar in itself.
    w = _clamp_and_renormalize(_random_weights(n, seed), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-3)
    assert (w >= LO - 1e-9).all()
    assert (w <= HI + 1e-9).all()


def test_pathological_dominant_score_converges_to_uniform_cap():
    # The scenario that broke the old clamp-then-renormalize: one fund's
    # raw proportional weight massively exceeds the cap. n=5, HI=0.20 means
    # n*HI == 1.0 exactly -- the only feasible solution is all five at
    # exactly the cap.
    w = _clamp_and_renormalize(pd.Series([10.0, 1.0, 1.0, 1.0, 1.0]), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    for v in w:
        assert v == pytest.approx(HI, abs=1e-4)


def test_infeasible_n_below_5_does_not_crash_and_still_sums_to_one():
    # n=4: n*HI = 0.80 < 1.0 -- no assignment can respect both the cap and
    # sum-to-1.0. Must not raise, must not silently produce a distribution
    # that doesn't sum to 1.0 (a valid portfolio always allocates 100% of
    # capital, even in this unsolvable-bounds edge case).
    w = _clamp_and_renormalize(pd.Series([1.0, 1.0, 1.0, 1.0]), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-3)
    # Infeasibility is unavoidable here, but the residual must be SHARED
    # (proportional/equal spread), not dumped entirely on one fund -- every
    # weight should land near 0.25, not one fund at 0.40+ and the rest at 0.20.
    assert w.max() < 0.30, f"residual concentrated on one fund: {w.tolist()}"


def test_infeasible_no_headroom_still_preserves_relative_score_order():
    # n=2 under hi=0.20 is maximally infeasible (2*0.20=0.40 << 1.0): both
    # funds get clamped to hi in the very first iteration, leaving zero
    # headroom for either. The naive fallback (split the 0.60 residual
    # equally) would erase the original 49.26%/50.74% score-proportional
    # signal entirely, landing both at exactly 0.50 -- this is the defect
    # caught by test_hysteresis_uses_score_total_not_effective_score_for_weight_input
    # failing before the original-proportions fallback was added. The
    # higher-scored fund must still end up with the larger final weight.
    w = _clamp_and_renormalize(pd.Series([1.00, 1.03]), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-3)
    assert w.iloc[1] > w.iloc[0], f"score ordering erased by infeasibility fallback: {w.tolist()}"


def test_infeasible_n_above_33_does_not_crash():
    # n=40: n*LO = 1.20 > 1.0 -- the floor alone already exceeds 100%.
    w = _clamp_and_renormalize(pd.Series([1.0] * 40), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-3)


def test_all_zero_scores_falls_back_to_equal_weight():
    w = _clamp_and_renormalize(pd.Series([0.0, 0.0, 0.0, 0.0, 0.0]), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert all(v == pytest.approx(0.20, abs=1e-6) for v in w)


def test_empty_series_returns_empty():
    w = _clamp_and_renormalize(pd.Series([], dtype=float), LO, HI)
    assert len(w) == 0


def test_preserves_index_labels():
    # _assign_weights relies on the returned Series aligning back onto the
    # caller's (possibly non-contiguous) DataFrame index.
    s = pd.Series([5.0, 3.0, 1.0], index=[7, 2, 9])
    w = _clamp_and_renormalize(s, LO, HI)
    assert list(w.index) == [7, 2, 9]


def test_no_bound_violation_silently_masked_by_rounding():
    # A weight already exactly on a bound should stay there, not drift out
    # by more than rounding tolerance after the residual-correction step.
    w = _clamp_and_renormalize(pd.Series([1.0] * 5), LO, HI)  # equal split, no clamping needed
    assert w.sum() == pytest.approx(1.0, abs=1e-3)
    assert (w >= LO - 1e-4).all() and (w <= HI + 1e-4).all()


# ============================================================
# 2. _select_funds_for_subportfolio -- manager cap + hysteresis
# ============================================================

_SCHEMA = """
CREATE TABLE fund_master (
    ISIN TEXT PRIMARY KEY,
    Fund_Name TEXT,
    Fund_Nature TEXT,
    Management_Company TEXT,
    fund_family_id TEXT,
    In_Current_Universe INTEGER DEFAULT 1
);
CREATE TABLE fund_scores (
    isin TEXT, block TEXT, score_version TEXT, score_total REAL,
    score_detail TEXT, eligible INTEGER
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    yield c
    c.close()


def _insert_fund(conn, isin, score, mgr="MgrA", nature="Renta Variable"):
    conn.execute(
        "INSERT INTO fund_master (ISIN, Fund_Name, Fund_Nature, Management_Company, "
        "fund_family_id, In_Current_Universe) VALUES (?, ?, ?, ?, NULL, 1)",
        (isin, f"Fund {isin}", nature, mgr),
    )
    conn.execute(
        "INSERT INTO fund_scores (isin, block, score_version, score_total, "
        "score_detail, eligible) VALUES (?, 'Equilibrada', 'v1', ?, '{}', 1)",
        (isin, score),
    )


def test_manager_cap_stops_at_max_funds_per_mgr(conn):
    # 3 funds from the same manager, all top-scoring -- only
    # MAX_FUNDS_PER_MGR (2) should be selected from it.
    for i in range(3):
        _insert_fund(conn, f"MGRA{i}", score=0.9 - i * 0.01, mgr="SameManager")
    _insert_fund(conn, "OTHER1", score=0.5, mgr="OtherManager")
    conn.commit()

    selected = _select_funds_for_subportfolio(conn, "Equilibrada", "v1")
    same_mgr_count = (selected["management_company"] == "SameManager").sum()
    assert same_mgr_count == MAX_FUNDS_PER_MGR
    assert "OTHER1" in selected["isin"].values  # the manager cap made room for it


def test_hysteresis_band_retains_incumbent_within_band(conn):
    # Incumbent scores lower than the challenger, but within HYSTERESIS_BAND
    # (5%) after the bonus -- should still rank above the challenger.
    _insert_fund(conn, "INCUMBENT", score=1.00, mgr="MgrA")
    _insert_fund(conn, "CHALLENGER", score=1.03, mgr="MgrB")  # +3%, inside the 5% band
    conn.commit()

    selected = _select_funds_for_subportfolio(
        conn, "Equilibrada", "v1", incumbent_isins=frozenset({"INCUMBENT"}))
    ranked = selected["isin"].tolist()
    assert ranked.index("INCUMBENT") < ranked.index("CHALLENGER")


def test_hysteresis_band_displaced_by_challenger_beyond_band(conn):
    # Challenger beats the incumbent by MORE than HYSTERESIS_BAND -- must
    # displace it in ranking (the bonus doesn't insulate forever).
    _insert_fund(conn, "INCUMBENT", score=1.00, mgr="MgrA")
    _insert_fund(conn, "CHALLENGER", score=1.10, mgr="MgrB")  # +10%, beyond the 5% band
    conn.commit()

    selected = _select_funds_for_subportfolio(
        conn, "Equilibrada", "v1", incumbent_isins=frozenset({"INCUMBENT"}))
    ranked = selected["isin"].tolist()
    assert ranked.index("CHALLENGER") < ranked.index("INCUMBENT")


def test_hysteresis_uses_score_total_not_effective_score_for_weight_input():
    # Documents the existing (correct) invariant this phase preserves: the
    # hysteresis bonus affects selection ORDER only. _assign_weights (and
    # therefore final portfolio weights) uses score_total, never the
    # bonus-inflated effective_score.
    from proyecto3.src.portfolio_builder import _assign_weights
    df = pd.DataFrame({
        "isin": ["A", "B"],
        "score_total": [1.00, 1.03],
        "effective_score": [1.05, 1.03],  # A has the hysteresis bonus applied
    })
    weighted = _assign_weights(df)
    # B has the higher score_total (1.03 > 1.00) -> B must get the larger weight,
    # even though A ranked first in selection due to effective_score.
    w_a = weighted.loc[weighted["isin"] == "A", "weight"].iloc[0]
    w_b = weighted.loc[weighted["isin"] == "B", "weight"].iloc[0]
    assert w_b > w_a
