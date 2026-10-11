# proyecto3/tests/test_scenario_builder_pg.py
# -*- coding: utf-8 -*-
"""
PortfolioBuilder persistence rules under the scenario lifecycle (owner decision 2026-10-11): the build writes DRAFT only, never overwrites
anything that is not a DRAFT, stamps `built_at`, and the rotation baseline is the PRODUCTION scenario. Toy tables carrying the migration's own
DDL block (scenario_pg_support.py).

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_scenario_builder_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenario_pg_support import db, statuses  # noqa: E402,F401  (db is a fixture)

from proyecto3.src import scenario_lifecycle as lc  # noqa: E402
from proyecto3.src.portfolio_builder import Portfolio, PortfolioBuilder, SubPortfolioAllocation  # noqa: E402


def _portfolio(scenario_id, weight=0.15):
    sub = SubPortfolioAllocation(name="Defensiva", regime_weight=0.5, funds=[
        {"isin": "ES0001", "fund_nature": "Monetario", "weight": weight, "role": "core", "score": 0.8}])
    return Portfolio(scenario_id=scenario_id, regime="Expansion", profile="Defensiva", sub_portfolios=[sub], macro_context={})


def test_builder_inserts_a_draft_and_never_overwrites_anything_else(db):
    b = PortfolioBuilder(db)
    b._persist(_portfolio("new_1", 0.15), "v1")
    assert statuses(db)["new_1"] == "DRAFT"
    with pytest.raises(lc.ScenarioExists, match="DRAFT"):                           # same id again, no replace requested
        b._persist(_portfolio("new_1", 0.20), "v1")
    assert db.execute("SELECT weight FROM portfolio_weights WHERE scenario_id='new_1'").fetchone()[0] == 0.075
    b._persist(_portfolio("new_1", 0.20), "v1", replace_draft=True)                 # an explicit replace of a DRAFT is allowed
    assert db.execute("SELECT weight FROM portfolio_weights WHERE scenario_id='new_1'").fetchone()[0] == 0.10
    lc.promote(db, "new_1", "owner")
    for replace in (False, True):                                                   # PRODUCTION is never replaced
        with pytest.raises(lc.ScenarioExists, match="PRODUCTION"):
            b._persist(_portfolio("new_1", 0.30), "v1", replace_draft=replace)
    with pytest.raises(lc.ScenarioExists, match="ARCHIVED"):
        b._persist(_portfolio("legacy_a"), "v1", replace_draft=True)
    assert db.execute("SELECT weight FROM portfolio_weights WHERE scenario_id='new_1'").fetchone()[0] == 0.10


def test_the_build_stamps_built_at_and_the_newest_build_is_the_newest_draft(db):
    b = PortfolioBuilder(db)
    b._persist(_portfolio("zzz_regime_b1"), "v1")          # ids that sort AGAINST the build order
    b._persist(_portfolio("aaa_regime_b2"), "v1")
    db.execute("UPDATE portfolio_scenarios SET built_at = now() - interval '2 hours' WHERE scenario_id = 'zzz_regime_b1'")
    db.execute("UPDATE portfolio_scenarios SET built_at = now() - interval '1 hour' WHERE scenario_id = 'aaa_regime_b2'")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM portfolio_scenarios WHERE built_at IS NULL").fetchone()[0] == 0
    assert lc.latest_draft_id(db) == "aaa_regime_b2"
    before = db.execute("SELECT built_at FROM portfolio_scenarios WHERE scenario_id='zzz_regime_b1'").fetchone()[0]
    b._persist(_portfolio("zzz_regime_b1", 0.20), "v1", replace_draft=True)         # a replace is a new build: it becomes the newest draft
    after = db.execute("SELECT built_at FROM portfolio_scenarios WHERE scenario_id='zzz_regime_b1'").fetchone()[0]
    assert after > before and lc.latest_draft_id(db) == "zzz_regime_b1"


def test_rotation_baseline_is_the_production_scenario_not_the_newest_row(db):
    b = PortfolioBuilder(db)
    b._persist(_portfolio("prod_1"), "v1")
    lc.promote(db, "prod_1", "owner")
    b._persist(_portfolio("newer_draft", 0.20), "v1")                               # a later draft must not become the incumbent
    prev = b.load_previous(exclude_scenario_id="newer_draft")
    assert prev is not None and prev.scenario_id == "prod_1"
    assert b.load_previous(exclude_scenario_id="prod_1") is None                    # only PRODUCTION counts


def test_the_declared_cash_round_trips_through_the_persisted_notes(db):
    p = _portfolio("with_cash", 0.15)
    p.macro_context["cash_weight"] = 0.04                                           # what build() records from Portfolio.cash_weight
    PortfolioBuilder(db)._persist(p, "v1")
    assert lc.declared_cash(db, "with_cash") == 0.04
    PortfolioBuilder(db)._persist(_portfolio("no_cash", 0.15), "v1")                # scenarios without the key declare nothing
    assert lc.declared_cash(db, "no_cash") == 0.0
    assert lc.declared_cash(db, "does_not_exist") == 0.0
