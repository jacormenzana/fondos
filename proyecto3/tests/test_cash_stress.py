# proyecto3/tests/test_cash_stress.py
# -*- coding: utf-8 -*-
"""
Cash-cap stress test (owner decision 2026-10-11): a sub-portfolio with deliberately too few eligible NON-exposed funds produces structural cash
under the construction constraint; the promotion gate then accepts it only when the DECLARED cash is within MAX_PORTFOLIO_CASH_WEIGHT.
Engine + gate end to end, pure (R-7: no DB).
"""
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import scenario_lifecycle as lc  # noqa: E402
from proyecto3.src.portfolio_builder import Portfolio, SubPortfolioAllocation  # noqa: E402
from proyecto3.src.portfolio_engine import PortfolioConstraints, select_and_weight  # noqa: E402

CONS = PortfolioConstraints(max_funds_per_sub=10, max_weight_per_fund=0.20, min_weight_per_fund=0.03, max_same_nature=5,
                            max_funds_per_mgr=2, hysteresis_band=0.0, max_exposed_weight=0.30)
CAP = 0.10


def _pool(prefix, n_exposed, n_eur):
    rows = []
    for k in range(n_exposed):
        rows.append(dict(isin=f"{prefix}U{k:02d}", score_total=0.95 - 0.01 * k, fund_name=f"US {prefix}{k}", fund_nature=f"N{k % 4}",
                         management_company=f"{prefix}MU{k}", fund_family_id=f"{prefix}FU{k}", fx_exposed=True))
    for k in range(n_eur):
        rows.append(dict(isin=f"{prefix}E{k:02d}", score_total=0.80 - 0.01 * k, fund_name=f"EU {prefix}{k}", fund_nature=f"N{k % 4}",
                         management_company=f"{prefix}ME{k}", fund_family_id=f"{prefix}FE{k}", fx_exposed=False))
    return pd.DataFrame(rows)


def _portfolio(sub_weights, scarce_sub):
    by_sub = {name: _pool(name[:3], 8, 2 if name == scarce_sub else 12) for name in sub_weights}
    selection = select_and_weight(by_sub, sub_weights, CONS)
    subs = []
    for name, df in selection.items():
        funds = [{"isin": r["isin"], "weight": round(float(r["weight"]), 4), "fund_nature": r["fund_nature"], "role": "r",
                  "score": float(r["score_total"])} for _, r in df.iterrows()]
        subs.append(SubPortfolioAllocation(name=name, regime_weight=sub_weights[name], funds=funds))
    return Portfolio(scenario_id="stress", regime="Expansion", profile="Equilibrada", sub_portfolios=subs)


def _gate(portfolio, declared):
    weights = [f["master_weight"] for f in portfolio.all_funds]
    return lc.promotion_blockers(status="DRAFT", n_funds=len(weights), weight_sum=sum(weights), stale_checks=[],
                                 exposure_non_eur=0.0, budget=0.30, alarms=0, declared_cash=declared, max_cash=CAP)


def test_a_small_scarce_sub_portfolio_yields_declared_cash_inside_the_cap_and_promotes():
    p = _portfolio({"Defensiva": 0.55, "Equilibrada": 0.35, "Dinamica": 0.10}, scarce_sub="Dinamica")
    cash = p.cash_weight
    assert 0 < cash < CAP                                          # structural cash exists, bounded by the scarce sub's 10% regime weight
    assert _gate(p, declared=cash) == []                           # declared and within the cap: promotable
    out = _gate(p, declared=0.0)                                   # the same portfolio WITHOUT the declaration is an error
    assert len(out) == 1 and "suman" in out[0]


def test_a_large_scarce_sub_portfolio_is_refused_with_the_exact_blocker_and_an_audit_trail():
    p = _portfolio({"Defensiva": 0.30, "Equilibrada": 0.15, "Dinamica": 0.55}, scarce_sub="Dinamica")
    cash = p.cash_weight
    assert cash > CAP                                              # the scarce sub carries 55% of the portfolio
    out = _gate(p, declared=cash)
    assert len(out) == 1 and "liquidez estructural declarada" in out[0] and "tope 10%" in out[0] and "sin override" in out[0]
    assert "no expuestos" in out[0]                                # tells the owner what unblocks it


def test_the_exposure_budget_still_holds_in_the_scarce_pool():
    p = _portfolio({"Defensiva": 0.30, "Equilibrada": 0.15, "Dinamica": 0.55}, scarce_sub="Dinamica")
    exposed = sum(f["master_weight"] for f in p.all_funds if "U" in f["isin"][3:4])      # isins are <sub><U|E><nn>
    assert exposed <= 0.30 + 1e-4
