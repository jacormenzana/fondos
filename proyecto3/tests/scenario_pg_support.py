# proyecto3/tests/scenario_pg_support.py
# -*- coding: utf-8 -*-
"""
Shared fixtures of the scenario-lifecycle Postgres tests (not collected: no `test_` prefix).

`db` runs the migration's OWN DDL block (scripts/ops/migrate_portfolio_scenario_status.py::ddl_block, schema qualifier removed) on toy tables
in a per-test schema, over three legacy scenarios, and hands a connection with a real transaction (the functions under test commit).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import migrate_portfolio_scenario_status as mig  # noqa: E402


def toy(conn):
    conn.execute("""CREATE TABLE portfolio_scenarios (scenario_id text PRIMARY KEY, profile text NOT NULL, macro_regime text,
                    created_at date NOT NULL, notes text)""")
    conn.execute("""CREATE TABLE portfolio_weights (scenario_id text NOT NULL, isin varchar(12) NOT NULL, block text NOT NULL,
                    weight double precision NOT NULL, position_role text, notes text, PRIMARY KEY (scenario_id, isin))""")
    conn.execute("CREATE TABLE fund_master (isin varchar(12) PRIMARY KEY, fund_nature text)")
    conn.execute("CREATE TABLE ingestion_log (isin text, step text, status text, message text, created_at timestamptz)")


def apply_block(conn):
    block = (mig.ddl_block().replace("gold.portfolio_scenarios", "portfolio_scenarios")
             .replace("table_schema = 'gold'", "table_schema = current_schema()"))
    conn.execute(block)


def statuses(conn):
    return dict(conn.execute("SELECT scenario_id, status FROM portfolio_scenarios ORDER BY scenario_id").fetchall())


@pytest.fixture
def db(pg_session_conn, pg_conn_module_schema, monkeypatch):
    # These tests exercise the promotion CLI on a toy world (no pushed tree, no audit header): they pin the LEGACY gate. The two-pillar
    # verdict has its own opt-in cases (test_promotion_gate_negative_pg._orchestrator), whatever the production switches say.
    import shared.config as cfg
    monkeypatch.setattr(cfg, "GOVERNANCE_ORCHESTRATOR_ENABLED", False)
    monkeypatch.setattr(cfg, "CODE_INTEGRITY_GATE_ENABLED", False)
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    toy(conn)
    conn.execute("""INSERT INTO portfolio_scenarios (scenario_id, profile, created_at) VALUES
                    ('legacy_a', 'Equilibrada', '2026-03-20'), ('legacy_b', 'Equilibrada', '2026-10-03'),
                    ('legacy_c', 'Equilibrada', '2026-10-05')""")
    apply_block(conn)
    conn.autocommit = False                         # the functions under test commit: give them a real transaction
    yield conn
    conn.rollback()
    conn.autocommit = True
