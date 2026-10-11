# proyecto3/tests/test_exposure_constraint.py
# -*- coding: utf-8 -*-
"""
Construction-time currency-exposure constraint (owner decision 2026-10-11): portfolio_engine.select_within_exposure.

The engine is pure (R-7: no DB). What these tests pin:
  * with no limit, or without the `fx_exposed` column, it is exactly select_candidates (parity with the Backtester);
  * over budget, it drops the LOWEST-scored exposed fund of the selection and re-selects until the exposed weight is within the budget;
  * rejected trial selections never leak into the shared exclusion state;
  * a pool that cannot absorb the weight yields fewer funds (cash residue), never a breach of the budget.
"""
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.portfolio_engine import (  # noqa: E402
    PortfolioConstraints,
    assign_weights,
    select_and_weight,
    select_candidates,
    select_within_exposure,
)

BUDGET = 0.30
CONS = PortfolioConstraints(max_funds_per_sub=10, max_weight_per_fund=0.20, min_weight_per_fund=0.03,
                            max_same_nature=5, max_funds_per_mgr=2, hysteresis_band=0.0, max_exposed_weight=BUDGET)


def _cands(n_exposed, n_eur, exposed_top=0.95, eur_top=0.80, prefix=""):
    rows = []
    for k in range(n_exposed):
        rows.append(dict(isin=f"{prefix}USD{k:02d}", score_total=exposed_top - 0.01 * k, fund_name=f"US fund {prefix}{k}",
                         fund_nature=f"N{k % 4}", management_company=f"{prefix}MGR_U{k}", fund_family_id=f"{prefix}FAM_U{k}", fx_exposed=True))
    for k in range(n_eur):
        rows.append(dict(isin=f"{prefix}EUR{k:02d}", score_total=eur_top - 0.01 * k, fund_name=f"EU fund {prefix}{k}",
                         fund_nature=f"N{k % 4}", management_company=f"{prefix}MGR_E{k}", fund_family_id=f"{prefix}FAM_E{k}", fx_exposed=False))
    return pd.DataFrame(rows)


def _exposed_weight(selected, constraints=CONS):
    w = assign_weights(selected, constraints)["weight"]
    return float(w[selected["fx_exposed"].astype(bool)].sum())


def test_without_a_limit_or_the_column_it_is_exactly_select_candidates():
    c = _cands(8, 8)
    plain = PortfolioConstraints(hysteresis_band=0.0)                      # max_exposed_weight=None
    a = select_within_exposure(c, plain)
    b = select_candidates(c, plain)
    assert a["isin"].tolist() == b["isin"].tolist()
    no_col = c.drop(columns="fx_exposed")
    assert select_within_exposure(no_col, CONS)["isin"].tolist() == select_candidates(no_col, CONS)["isin"].tolist()


def test_over_budget_selection_drops_the_lowest_scored_exposed_funds_until_compliant():
    c = _cands(8, 8)
    unconstrained = select_candidates(c, CONS)
    assert _exposed_weight(unconstrained) > BUDGET                         # the failure being fixed: 8 exposed + 2 EUR
    sel = select_within_exposure(c, CONS)
    assert _exposed_weight(sel) <= BUDGET + 1e-9
    kept_exposed = sorted(sel.loc[sel["fx_exposed"].astype(bool), "isin"])
    # the survivors are the HIGHEST-scored exposed funds: USD00..USD(k-1), a prefix of the score ordering
    assert kept_exposed == [f"USD{k:02d}" for k in range(len(kept_exposed))] and 0 < len(kept_exposed) < 8
    assert len(sel) == 10                                                  # replaced by EUR funds, the sub-portfolio stays full


def test_a_selection_already_within_budget_is_left_untouched():
    c = _cands(2, 12)                                                      # two exposed funds among ten: well below 30%
    assert select_within_exposure(c, CONS)["isin"].tolist() == select_candidates(c, CONS)["isin"].tolist()


def test_rejected_trials_do_not_leak_into_the_shared_exclusion_state():
    c = _cands(8, 8)
    isins, fams, mgrs = set(), set(), {}
    sel = select_within_exposure(c, CONS, exclude_isins=isins, exclude_families=fams, mgr_global=mgrs)
    assert isins == set(sel["isin"])                                       # only the final selection is recorded
    assert fams == set(sel["fund_family_id"])
    assert sum(mgrs.values()) == len(sel)


def test_an_all_exposed_pool_shrinks_to_what_the_per_fund_cap_allows_and_the_rest_is_cash():
    sel = select_within_exposure(_cands(6, 0), CONS)
    # one fund at the 20% per-fund cap is 20% of the sub-portfolio (<= 30%); the other 80% is held as cash (cash_weight), the budget holds
    assert len(sel) == 1 and sel.iloc[0]["isin"] == "USD00"
    assert _exposed_weight(sel) == 0.20


def test_too_few_non_exposed_funds_leaves_a_smaller_portfolio_not_a_breach():
    c = _cands(8, 2)                                                       # 2 EUR funds can carry at most 2 x 20% = 40% of the sub-portfolio
    sel = select_within_exposure(c, CONS)
    w = assign_weights(sel, CONS)["weight"]
    assert _exposed_weight(sel) <= BUDGET + 1e-9
    assert float(w.sum()) <= 1.0 + 1e-9


def test_select_and_weight_applies_it_per_sub_portfolio_with_cross_exclusion():
    by_sub = {"Defensiva": _cands(8, 8, prefix="D"), "Equilibrada": _cands(8, 8, prefix="E")}
    out = select_and_weight(by_sub, {"Defensiva": 0.6, "Equilibrada": 0.4}, CONS)
    for name, df in out.items():
        assert not df.empty
        exposed = df["fx_exposed"].astype(bool)
        assert float(df.loc[exposed, "weight"].sum()) <= BUDGET + 1e-4     # internal weights are rounded to 4 dp
    master_exposed = sum(
        float(df.loc[df["fx_exposed"].astype(bool), "weight"].sum()) * w
        for (name, df), w in zip(out.items(), (0.6, 0.4)))
    assert master_exposed <= BUDGET + 1e-4
    assert not (set(out["Defensiva"]["isin"]) & set(out["Equilibrada"]["isin"]))


def test_dropping_is_deterministic_regardless_of_row_order():
    c = _cands(8, 8)
    a = select_within_exposure(c, CONS)["isin"].tolist()
    b = select_within_exposure(c.sample(frac=1.0, random_state=7).reset_index(drop=True), CONS)["isin"].tolist()
    assert a == b


# ---------------- the shared helpers (builder and backtester call these) ----------------

class _FakeConn:
    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        rows = self.rows
        return type("Cur", (), {"fetchall": lambda s: rows, "fetchone": lambda s: None})()


def test_flag_fx_exposed_marks_candidates_with_the_gate_rule_and_one_query():
    import proyecto3.src.score_candidates as sc
    conn = _FakeConn([("A", "USD", "Unhedged", "USD"), ("B", "EUR", "Hedged", "USD"), ("C", "EUR", "Unhedged", "MCY")])
    c = pd.DataFrame({"isin": ["A", "B", "C", "D"], "score_total": [0.9, 0.8, 0.7, 0.6]})
    out = sc.flag_fx_exposed(conn, c)
    assert out["fx_exposed"].tolist() == [True, False, False, False]      # hedged EUR, MCY (reported apart) and unknown are not budgeted
    assert len(conn.queries) == 1 and "fx_exposed" not in c.columns       # input untouched


def test_flag_and_constraint_are_no_ops_when_the_switch_is_off(monkeypatch):
    import proyecto3.src.score_candidates as sc
    monkeypatch.setattr(sc, "FX_EXPOSURE_CONSTRAINT_ENABLED", False)
    c = pd.DataFrame({"isin": ["A"], "score_total": [0.9]})
    conn = _FakeConn([("A", "USD", "Unhedged", "USD")])
    assert sc.flag_fx_exposed(conn, c) is c and not conn.queries
    base = PortfolioConstraints()
    assert sc.exposure_constrained(base) is base and base.max_exposed_weight is None


def test_exposure_constrained_sets_the_configured_budget(monkeypatch):
    import proyecto3.src.score_candidates as sc
    monkeypatch.setattr(sc, "FX_EXPOSURE_CONSTRAINT_ENABLED", True)
    monkeypatch.setattr(sc, "MAX_NON_EUR_UNHEDGED_WEIGHT", 0.25)
    assert sc.exposure_constrained(PortfolioConstraints()).max_exposed_weight == 0.25
