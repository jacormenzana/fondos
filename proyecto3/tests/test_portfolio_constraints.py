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
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.portfolio_engine import cash_weight
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


def test_infeasible_n_below_5_keeps_cap_strict_and_leaves_residue_as_cash():
    # FND-0187: n=4 -> n*HI = 0.80 < 1.0. The cap is NOT breached (the old behaviour inflated every
    # fund to 25%); the 0.20 residue is an explicit cash line, not spread over the funds.
    w = _clamp_and_renormalize(pd.Series([1.0, 1.0, 1.0, 1.0]), LO, HI)
    assert (w <= HI + 1e-9).all(), f"cap breached: {w.tolist()}"
    assert w.sum() == pytest.approx(4 * HI, abs=1e-9)
    assert cash_weight(w.tolist()) == pytest.approx(1.0 - 4 * HI, abs=1e-9)


@pytest.mark.parametrize("n,expected_cash", [(1, 0.80), (2, 0.60), (3, 0.40), (4, 0.20)])
def test_infeasible_cash_residue_matches_the_shortfall(n, expected_cash):
    # Only 3 funds pass the filters -> 60% invested, 40% cash (never a silent 60% total).
    w = _clamp_and_renormalize(pd.Series([float(i + 1) for i in range(n)]), LO, HI)
    assert (w <= HI + 1e-9).all()
    assert cash_weight(w.tolist()) == pytest.approx(expected_cash, abs=1e-9)
    assert w.sum() + cash_weight(w.tolist()) == pytest.approx(1.0, abs=1e-9)


def test_infeasible_logs_a_warning_instead_of_printing(caplog):
    with caplog.at_level("WARNING", logger="proyecto3.src.portfolio_engine"):
        _clamp_and_renormalize(pd.Series([1.0, 2.0, 3.0]), LO, HI)
    assert any("liquidez" in r.getMessage() for r in caplog.records)


def test_feasible_n_has_no_cash_residue():
    w = _clamp_and_renormalize(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), LO, HI)
    assert cash_weight(w.tolist()) == pytest.approx(0.0, abs=1e-3)


def test_infeasible_n_above_33_does_not_crash(caplog):
    # n=40: n*LO = 1.20 > 1.0 -- the floor alone already exceeds 100%.
    with caplog.at_level("ERROR", logger="proyecto3.src.portfolio_engine"):
        w = _clamp_and_renormalize(pd.Series([1.0] * 40), LO, HI)
    assert w.sum() == pytest.approx(1.0, abs=1e-3)
    assert any("suelo infactible" in r.getMessage() for r in caplog.records)


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
    In_Current_Universe SMALLINT DEFAULT 1
);
CREATE TABLE fund_scores (
    isin TEXT, block TEXT, score_version TEXT, regime TEXT, as_of_date DATE,
    score_total DOUBLE PRECISION, score_detail TEXT, eligible SMALLINT, exclusion_reason TEXT
);
"""
# Fase 3a (P3 optimization plan, migracion SQLite 2026-09-19): fund_scores'
# PK se extendio a (isin, block, score_version, regime, as_of_date) -- ver
# db/pg/30_gold.sql. _select_funds_for_subportfolio ahora filtra
# por regimen; el esquema/fixture de test refleja la forma real de la
# tabla, y todas las filas sembradas usan el mismo regimen de prueba para
# que el filtro las encuentre.
_TEST_REGIME = "Shock_Energetico"


@pytest.fixture
def conn(pg_conn):
    """Tablas toy en Postgres (esquema public, SAVEPOINT revertido al acabar el test)."""
    pg_conn.execute(_SCHEMA)
    return pg_conn


def _insert_fund(conn, isin, score, mgr="MgrA", nature="Renta Variable"):
    conn.execute(
        "INSERT INTO fund_master (ISIN, Fund_Name, Fund_Nature, Management_Company, "
        "fund_family_id, In_Current_Universe) VALUES (%s, %s, %s, %s, NULL, 1)",
        (isin, f"Fund {isin}", nature, mgr),
    )
    conn.execute(
        "INSERT INTO fund_scores (isin, block, score_version, regime, as_of_date, "
        "score_total, score_detail, eligible) "
        "VALUES (%s, 'Equilibrada', 'v1', %s, '2026-09-19', %s, '{}', 1)",
        (isin, _TEST_REGIME, score),
    )


def test_manager_cap_stops_at_max_funds_per_mgr(conn):
    # 3 funds from the same manager, all top-scoring -- only
    # MAX_FUNDS_PER_MGR (2) should be selected from it.
    for i in range(3):
        _insert_fund(conn, f"MGRA{i}", score=0.9 - i * 0.01, mgr="SameManager")
    _insert_fund(conn, "OTHER1", score=0.5, mgr="OtherManager")

    selected = _select_funds_for_subportfolio(conn, "Equilibrada", "v1", _TEST_REGIME)
    same_mgr_count = (selected["management_company"] == "SameManager").sum()
    assert same_mgr_count == MAX_FUNDS_PER_MGR
    assert "OTHER1" in selected["isin"].values  # the manager cap made room for it


def test_hysteresis_band_retains_incumbent_within_band(conn):
    # Incumbent scores lower than the challenger, but within HYSTERESIS_BAND
    # (5%) after the bonus -- should still rank above the challenger.
    _insert_fund(conn, "INCUMBENT", score=1.00, mgr="MgrA")
    _insert_fund(conn, "CHALLENGER", score=1.03, mgr="MgrB")  # +3%, inside the 5% band

    selected = _select_funds_for_subportfolio(
        conn, "Equilibrada", "v1", _TEST_REGIME, incumbent_isins=frozenset({"INCUMBENT"}))
    ranked = selected["isin"].tolist()
    assert ranked.index("INCUMBENT") < ranked.index("CHALLENGER")


def test_hysteresis_band_displaced_by_challenger_beyond_band(conn):
    # Challenger beats the incumbent by MORE than HYSTERESIS_BAND -- must
    # displace it in ranking (the bonus doesn't insulate forever).
    _insert_fund(conn, "INCUMBENT", score=1.00, mgr="MgrA")
    _insert_fund(conn, "CHALLENGER", score=1.10, mgr="MgrB")  # +10%, beyond the 5% band

    selected = _select_funds_for_subportfolio(
        conn, "Equilibrada", "v1", _TEST_REGIME, incumbent_isins=frozenset({"INCUMBENT"}))
    ranked = selected["isin"].tolist()
    assert ranked.index("CHALLENGER") < ranked.index("INCUMBENT")


def test_hysteresis_uses_score_total_not_effective_score_for_weight_input():
    # Documents the existing (correct) invariant this phase preserves: the
    # hysteresis bonus affects selection ORDER only. _assign_weights (and
    # therefore final portfolio weights) uses score_total, never the
    # bonus-inflated effective_score.
    from proyecto3.src.portfolio_builder import _assign_weights
    # n=6 keeps the sub-portfolio feasible (n*HI >= 1, FND-0187) and unclamped, so the weights still
    # reflect the score ratio (with n<5 every fund sits at the cap and the signal is flat by design).
    df = pd.DataFrame({
        "isin": ["A", "B", "C", "D", "E", "F"],
        "score_total": [1.00, 1.03, 1.00, 1.00, 1.00, 1.00],
        "effective_score": [1.05, 1.03, 1.00, 1.00, 1.00, 1.00],  # A has the hysteresis bonus applied
    })
    weighted = _assign_weights(df)
    # B has the higher score_total (1.03 > 1.00) -> B must get the larger weight,
    # even though A ranked first in selection due to effective_score.
    w_a = weighted.loc[weighted["isin"] == "A", "weight"].iloc[0]
    w_b = weighted.loc[weighted["isin"] == "B", "weight"].iloc[0]
    assert w_b > w_a


def test_stale_eligible_row_does_not_resurrect_a_now_ineligible_fund(conn):
    # Regression test for a real bug found in the Phase 3a live smoke test
    # (2026-09-19): a fund with an OLDER eligible row and a NEWER
    # ineligible row was still being selected, using the STALE eligible
    # score -- because the SQL filtered eligible=1 INSIDE the ROW_NUMBER()
    # CTE, before windowing. That discards the true latest row whenever it
    # happens to be ineligible, so ROW_NUMBER() promotes an older eligible
    # row to rn=1 instead of correctly excluding the fund. Fill enough
    # OTHER eligible funds that DEMOTED wouldn't be selected purely on
    # count, so this test fails cleanly if the bug regresses.
    for i in range(9):
        _insert_fund(conn, f"FILLER{i:02d}", score=0.80 - i * 0.01, mgr=f"FillerMgr{i}")

    conn.execute(
        "INSERT INTO fund_master (ISIN, Fund_Name, Fund_Nature, Management_Company, "
        "fund_family_id, In_Current_Universe) VALUES (%s, %s, %s, %s, NULL, 1)",
        ("DEMOTED", "Fund DEMOTED", "Renta Variable", "DemotedMgr"),
    )
    # Older row: eligible, high score -- would rank #1 by score alone.
    conn.execute(
        "INSERT INTO fund_scores (isin, block, score_version, regime, as_of_date, "
        "score_total, score_detail, eligible, exclusion_reason) "
        "VALUES ('DEMOTED', 'Equilibrada', 'v1', %s, '2026-03-21', 0.99, '{}', 1, NULL)",
        (_TEST_REGIME,),
    )
    # Newer row: same fund, now ineligible -- this is the row that should govern.
    conn.execute(
        "INSERT INTO fund_scores (isin, block, score_version, regime, as_of_date, "
        "score_total, score_detail, eligible, exclusion_reason) "
        "VALUES ('DEMOTED', 'Equilibrada', 'v1', %s, '2026-09-19', 0.0, '{}', 0, "
        "'Credit_Quality=High Yield excluido de Defensiva')",
        (_TEST_REGIME,),
    )

    selected = _select_funds_for_subportfolio(conn, "Equilibrada", "v1", _TEST_REGIME)
    assert "DEMOTED" not in selected["isin"].values, (
        "a fund whose LATEST fund_scores row is ineligible must not be "
        "selected using a stale eligible row from an earlier as_of_date"
    )


# ============================================================
# 3. FND-0171 -- candidates come only from the LATEST scoring run
# ============================================================

def _score_row(conn, isin, as_of, score, block="Equilibrada", regime=_TEST_REGIME, eligible=1):
    conn.execute(
        "INSERT INTO fund_scores (isin, block, score_version, regime, as_of_date, "
        "score_total, score_detail, eligible) VALUES (%s, %s, 'v1', %s, %s, %s, '{}', %s)",
        (isin, block, regime, as_of, score, eligible),
    )


def _master(conn, isin, mgr=None):
    conn.execute(
        "INSERT INTO fund_master (ISIN, Fund_Name, Fund_Nature, Management_Company, "
        "fund_family_id, In_Current_Universe) VALUES (%s, %s, 'Renta Variable', %s, NULL, 1)",
        (isin, f"Fund {isin}", mgr or f"Mgr{isin}"),
    )


def test_fund_not_rescored_in_the_latest_run_is_not_a_candidate(conn):
    # LIVE CASE 2026-10-01: two Defensiva funds in the 30-fund portfolio were chosen from
    # 2026-03-21 rows because they were simply absent from the 2026-10-01 run (family
    # de-duplication / lost metrics). Absent from the latest run => not a candidate, no matter
    # how high the old score was.
    for i in range(3):
        _master(conn, f"CURRENT{i}")
        _score_row(conn, f"CURRENT{i}", "2026-10-01", 0.80 - i * 0.01)
    _master(conn, "ORPHAN")
    _score_row(conn, "ORPHAN", "2026-03-21", 1.50)           # eligible, top score, but old run

    selected = _select_funds_for_subportfolio(conn, "Equilibrada", "v1", _TEST_REGIME)
    assert set(selected["isin"]) == {"CURRENT0", "CURRENT1", "CURRENT2"}


def test_backtester_candidate_loader_applies_the_same_rule(conn):
    from proyecto3.src.backtesting import _load_candidates
    for i in range(2):
        _master(conn, f"CURRENT{i}")
        _score_row(conn, f"CURRENT{i}", "2026-10-01", 0.80 - i * 0.01)
    _master(conn, "ORPHAN")
    _score_row(conn, "ORPHAN", "2026-03-21", 1.50)

    by_block = _load_candidates(conn, "v1", _TEST_REGIME)
    assert set(by_block["Equilibrada"]["isin"]) == {"CURRENT0", "CURRENT1"}
    assert "block" not in by_block["Equilibrada"].columns


def test_latest_run_is_resolved_per_block(conn):
    # Each block has its own latest run: a run on a later date for another block must not
    # wipe out this block's candidates.
    _master(conn, "EQ")
    _score_row(conn, "EQ", "2026-09-26", 0.9, block="Equilibrada")
    _master(conn, "DEF")
    _score_row(conn, "DEF", "2026-10-01", 0.9, block="Defensiva")

    assert set(_select_funds_for_subportfolio(conn, "Equilibrada", "v1", _TEST_REGIME)["isin"]) == {"EQ"}
    assert set(_select_funds_for_subportfolio(conn, "Defensiva", "v1", _TEST_REGIME)["isin"]) == {"DEF"}


def test_latest_run_is_resolved_per_regime(conn):
    # A run under another regime on a later date does not make this regime's run stale.
    _master(conn, "SHOCK")
    _score_row(conn, "SHOCK", "2026-09-26", 0.9, regime=_TEST_REGIME)
    _master(conn, "CRISIS")
    _score_row(conn, "CRISIS", "2026-10-01", 0.9, regime="Crisis_Financiera")

    assert set(_select_funds_for_subportfolio(conn, "Equilibrada", "v1", _TEST_REGIME)["isin"]) == {"SHOCK"}


def test_inactive_funds_and_ineligible_latest_rows_are_still_excluded(conn):
    _master(conn, "OK")
    _score_row(conn, "OK", "2026-10-01", 0.9)
    _master(conn, "INELIGIBLE")
    _score_row(conn, "INELIGIBLE", "2026-10-01", 0.0, eligible=0)
    _master(conn, "RETIRED")
    _score_row(conn, "RETIRED", "2026-10-01", 0.95)
    conn.execute("UPDATE fund_master SET In_Current_Universe = 0 WHERE ISIN = 'RETIRED'")

    assert set(_select_funds_for_subportfolio(conn, "Equilibrada", "v1", _TEST_REGIME)["isin"]) == {"OK"}


# ============================================================
# FND-0169: master weights must sum to 1.0000 (largest-remainder rounding)
# ============================================================

def test_round_master_weights_sums_to_one_and_moves_at_most_one_unit():
    from proyecto3.src.portfolio_engine import round_master_weights
    rng = random.Random(169)
    for _ in range(300):
        n = rng.randint(3, 30)
        raw_w = [rng.random() + 0.05 for _ in range(n)]
        tot = sum(raw_w)
        raw = [(f"ISIN{i:03d}", w / tot) for i, w in enumerate(raw_w)]
        out = round_master_weights(raw)
        assert round(sum(out), 4) == 1.0
        assert all(abs(o - r) <= 1.0001e-4 for o, (_, r) in zip(out, raw))      # one grid unit at most
        assert all(abs(o * 1e4 - round(o * 1e4)) < 1e-6 for o in out)             # on the 4-dp grid


def test_round_master_weights_is_order_independent_and_keeps_a_short_total():
    from proyecto3.src.portfolio_engine import round_master_weights
    raw = [("A", 0.33335), ("B", 0.33335), ("C", 0.33330)]
    fwd = dict(zip("ABC", round_master_weights(raw)))
    rev = dict(zip("CBA", round_master_weights(list(reversed(raw)))))
    assert fwd == rev and round(sum(fwd.values()), 4) == 1.0
    short = round_master_weights([("A", 0.30), ("B", 0.2)])                       # an unfilled portfolio keeps its total
    assert short == [0.3, 0.2]


def test_portfolio_all_funds_master_weights_sum_to_one():
    """The live case (cartera_shock_energetico_202609): independent rounding gave blocks 0.55 / 0.1001 / 0.3498 = 0.9999."""
    from proyecto3.src.portfolio_builder import Portfolio, SubPortfolioAllocation
    third = [0.0576, 0.0576, 0.0576, 0.0576, 0.0576, 0.0576, 0.0576, 0.0576, 0.0576, 0.1824]
    funds = lambda p, k: [{"isin": f"{p}{i}", "weight": w / sum(third)} for i, w in enumerate(third[:k])]
    pf = Portfolio(scenario_id="t", regime="Shock_Energetico", profile="p", sub_portfolios=[
        SubPortfolioAllocation("Defensiva", 0.55, funds("D", 10)),
        SubPortfolioAllocation("Equilibrada", 0.10, funds("E", 9)),
        SubPortfolioAllocation("Dinamica", 0.35, funds("Y", 10)),
    ])
    for sp in pf.sub_portfolios:                                                   # internal weights sum to 1 per block
        tot = sum(f["weight"] for f in sp.funds)
        for f in sp.funds:
            f["weight"] /= tot
    mw = [f["master_weight"] for f in pf.all_funds]
    assert round(sum(mw), 4) == 1.0
