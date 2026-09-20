# proyecto3/tests/test_portfolio_builder_persist_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression test for
src/portfolio_builder.py::PortfolioBuilder._persist(). Commits internally, so this uses
pg_conn_module_schema/pg_session_conn -- never bare pg_conn/SAVEPOINT -- same discipline as every
other commit-owning function in this migration. Zero prior test coverage for this method.

gold.portfolio_weights renames `role` -> `position_role` in the target schema (SQL:2003 reserved
word, db/pg/rename_map.yaml) -- the ported SQL branches the column name, not just the placeholder.
"""
from __future__ import annotations

import json

from proyecto3.src.portfolio_builder import Portfolio, PortfolioBuilder, SubPortfolioAllocation


def _make_tables(conn):
    conn.execute("""
        CREATE TABLE portfolio_scenarios (
            scenario_id   text PRIMARY KEY,
            profile       text NOT NULL,
            macro_regime  text,
            created_at    date NOT NULL,
            notes         text
        )
    """)
    conn.execute("""
        CREATE TABLE portfolio_weights (
            scenario_id    text NOT NULL,
            isin           varchar(12) NOT NULL,
            block          text NOT NULL,
            weight         double precision NOT NULL,
            position_role  text,
            notes          text,
            PRIMARY KEY (scenario_id, isin)
        )
    """)


def _portfolio(scenario_id="test_scn", weight=0.15):
    sub = SubPortfolioAllocation(
        name="Defensiva", regime_weight=0.5,
        funds=[{
            "isin": "ES0001", "fund_nature": "Renta Fija Corto Plazo",
            "weight": weight, "role": "core", "score": 0.82,
        }],
    )
    return Portfolio(
        scenario_id=scenario_id, regime="Expansion", profile="Defensiva",
        sub_portfolios=[sub], macro_context={"oil_yoy": 0.02},
    )


def test_persist_scenario_and_weights_upsert(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    builder = PortfolioBuilder(conn)

    builder._persist(_portfolio(weight=0.15), score_version="v1")

    scenario = conn.execute(
        "SELECT profile, macro_regime, notes FROM portfolio_scenarios WHERE scenario_id='test_scn'"
    ).fetchone()
    assert scenario[0] == "Defensiva"
    assert scenario[1] == "Expansion"
    assert json.loads(scenario[2]) == {"oil_yoy": 0.02}

    weight_row = conn.execute(
        "SELECT weight, position_role FROM portfolio_weights "
        "WHERE scenario_id='test_scn' AND isin='ES0001'"
    ).fetchone()
    assert weight_row[0] == 0.075   # 0.15 * regime_weight 0.5
    assert weight_row[1] == "core"

    # Rerun with the same scenario_id and a revised weight — scenario upserts (ON CONFLICT DO
    # UPDATE), and the weights table is fully replaced (DELETE-then-reinsert), not appended.
    builder._persist(_portfolio(weight=0.20), score_version="v1")
    count = conn.execute(
        "SELECT COUNT(*) FROM portfolio_weights WHERE scenario_id='test_scn'"
    ).fetchone()[0]
    assert count == 1
    new_weight = conn.execute(
        "SELECT weight FROM portfolio_weights WHERE scenario_id='test_scn' AND isin='ES0001'"
    ).fetchone()[0]
    assert new_weight == 0.10   # 0.20 * 0.5

    scenario_count = conn.execute(
        "SELECT COUNT(*) FROM portfolio_scenarios WHERE scenario_id='test_scn'"
    ).fetchone()[0]
    assert scenario_count == 1
