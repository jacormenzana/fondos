# proyecto3/tests/test_p3_reads_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20/21, plan §Addendum Stage 6) — regression tests for the
two highest-value P3 read-path bugs found while porting, both instances of dialect gaps already
seen elsewhere this migration but newly confirmed here:

1. fund_scorer.py::load_fund_metrics_for_scoring() used pd.read_sql(sql, conn).set_index("ISIN")
   with column ALIASES ("SRRI as srri_kiid", "... AS ISIN"). Postgres folds unquoted aliases to
   lowercase too (cursor.description reports 'isin' regardless of the query's own "AS ISIN" text —
   verified live 2026-09-21), so pd.read_sql would have silently built a lowercase-keyed DataFrame,
   breaking .set_index("ISIN") outright. Fixed with fetchall()+explicit DataFrame(columns=...).

2. portfolio_builder.py::PortfolioBuilder.load_previous() reads portfolio_weights.role, a reserved
   word on Postgres (renamed to position_role, db/pg/rename_map.yaml) — same rename already applied
   to _persist() (the write side) but the read side needed its own fix.

Uses pg_conn_module_schema/pg_session_conn per the established rule (both functions are read-only,
no internal commit — pg_conn would normally be preferred — but these tests build throwaway schemas
matching test_portfolio_builder_persist_pg.py's established pattern for this file, for consistency
with the write-side tests already covering the same tables).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.fund_scorer import load_fund_metrics_for_scoring
from proyecto3.src.portfolio_builder import PortfolioBuilder


def test_load_fund_metrics_for_scoring_returns_mixed_case_columns(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("""
        CREATE TABLE fund_master (
            isin text PRIMARY KEY, fund_name text, fund_nature text, srri smallint,
            investment_focus text, credit_quality text, ongoing_charge_recurrent double precision,
            srri_quality_flag text, fund_family_id text, in_current_universe smallint
        )
    """)
    conn.execute("""
        CREATE TABLE fund_metrics (
            isin text NOT NULL, metric text NOT NULL, horizon text NOT NULL,
            value double precision, real_flag smallint NOT NULL DEFAULT 0,
            metric_version text NOT NULL DEFAULT 'v1',
            PRIMARY KEY (isin, metric, horizon, real_flag, metric_version)
        )
    """)
    conn.execute("""
        INSERT INTO fund_master VALUES
        ('X1', 'Fund X', 'Renta Variable', 4, 'Broad', 'Investment Grade', 0.015,
         'HIGH', 'FAM1', 1)
    """)
    conn.execute("""
        INSERT INTO fund_metrics (isin, metric, horizon, value, real_flag) VALUES
        ('X1', 'return_ann', 'since_inception', 0.08, 1),
        ('X1', 'sharpe', 'since_inception', 1.2, 0),
        ('X1', 'max_drawdown', 'since_inception', -0.15, 0)
    """)

    df = load_fund_metrics_for_scoring(conn)

    # The actual regression proof: this would have raised KeyError("ISIN") before the fix,
    # since pd.read_sql would have built a lowercase-keyed frame and .set_index("ISIN") on the
    # metrics-side join would never have found a matching mixed-case column to join against.
    assert "X1" in df.index
    assert df.loc["X1", "Fund_Name"] == "Fund X"
    assert df.loc["X1", "Fund_Nature"] == "Renta Variable"
    assert df.loc["X1", "srri_kiid"] == 4
    assert df.loc["X1", "Ongoing_Charge"] == 0.015


def test_load_previous_uses_position_role_column(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("""
        CREATE TABLE portfolio_scenarios (
            scenario_id text PRIMARY KEY, profile text, macro_regime text,
            created_at date NOT NULL, notes text
        )
    """)
    conn.execute("""
        CREATE TABLE portfolio_weights (
            scenario_id text NOT NULL, isin text NOT NULL, block text NOT NULL,
            weight double precision NOT NULL, position_role text, notes text,
            PRIMARY KEY (scenario_id, isin)
        )
    """)
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, fund_nature text)")
    conn.execute("INSERT INTO fund_master VALUES ('X1', 'Renta Variable')")
    conn.execute("""
        INSERT INTO portfolio_scenarios VALUES
        ('scn_old', 'Defensiva', 'Expansion', '2026-08-01', %s)
    """, (json.dumps({"oil_yoy": 0.01}),))
    conn.execute("""
        INSERT INTO portfolio_weights VALUES
        ('scn_old', 'X1', 'Defensiva', 0.15, 'core', NULL),
        ('scn_old', 'GONE', 'Defensiva', 0.05, 'core', NULL)
    """)

    builder = PortfolioBuilder(conn)
    prev = builder.load_previous()

    # Regression proof: this would have raised UndefinedColumn ("column role does not exist")
    # before the fix — portfolio_weights has no "role" column on Postgres, only position_role.
    assert prev is not None
    assert prev.scenario_id == "scn_old"
    funds = {f["isin"]: f for f in prev.sub_portfolios[0].funds}
    assert funds["X1"]["role"] == "core"
    # FND-0063: fund_nature is loaded (and a fund missing from fund_master keeps its row), so
    # summary() -- which reads f["fund_nature"] for every fund -- no longer raises KeyError.
    assert funds["X1"]["fund_nature"] == "Renta Variable"
    assert funds["GONE"]["fund_nature"] == "Desconocida"
    assert "Renta Variable" in prev.summary()
