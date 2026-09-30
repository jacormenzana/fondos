# proyecto3/tests/test_regime_history_persist_pg.py
# -*- coding: utf-8 -*-
"""FND-0153: persist_regime_history() writes classify_historical() into gold.regime_history.

Commits internally, so it uses pg_session_conn + pg_conn_module_schema (never bare pg_conn), like
test_fund_scorer_persist_pg.py.
"""
from __future__ import annotations

import pandas as pd

from proyecto3.src.regime_classifier import CLASSIFIER_VERSION, persist_regime_history


def _make_table(conn):
    conn.execute("""
        CREATE TABLE regime_history (
            weight_defensive double precision NOT NULL,
            weight_balanced  double precision NOT NULL,
            weight_dynamic   double precision NOT NULL,
            oil_yoy double precision, ipc_yoy_avg double precision, cli_eu double precision,
            rate_deposit double precision, d_rate_3m double precision, spread_hy double precision,
            vix_yoy double precision, term_spread double precision,
            date date NOT NULL,
            classifier_version text NOT NULL,
            regime text NOT NULL,
            membership_json jsonb,
            PRIMARY KEY (date, classifier_version)
        )
    """)


def _hist(regime="Expansion", cli=101.0, term_spread=None):
    idx = pd.to_datetime(["2026-07-31", "2026-08-31"])
    df = pd.DataFrame({
        "regime": [regime, regime],
        "weight_defensive": 0.2, "weight_balanced": 0.45, "weight_dynamic": 0.35,
        "oil_yoy": 0.05, "ipc_yoy_avg": 0.02, "cli_eu": cli, "rate_deposit": 2.0,
        "d_rate_3m": 0.0, "spread_hy": 3.0, "vix_yoy": float("nan"), "term_spread": term_spread,
    }, index=idx)
    df.index.name = "date"
    return df


def test_persist_upsert_nulls_and_versioning(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_table(conn)

    assert persist_regime_history(conn, _hist()) == 2
    row = conn.execute(
        "SELECT regime, cli_eu, vix_yoy, term_spread, classifier_version FROM regime_history "
        "WHERE date = '2026-08-31'").fetchone()
    assert row[0] == "Expansion" and row[1] == 101.0
    assert row[2] is None and row[3] is None          # NaN / None stay NULL, not 'NaN'
    assert row[4] == CLASSIFIER_VERSION

    # Same (date, version): rerun refreshes the row instead of duplicating or keeping the old one.
    assert persist_regime_history(conn, _hist(regime="Contraccion", cli=98.0)) == 2
    rows = conn.execute("SELECT regime, cli_eu FROM regime_history WHERE date = '2026-08-31'").fetchall()
    assert rows == [("Contraccion", 98.0)]

    # A different classifier_version coexists with the first one.
    persist_regime_history(conn, _hist(), classifier_version="other-version")
    assert conn.execute("SELECT COUNT(*) FROM regime_history").fetchone()[0] == 4


def test_empty_and_dry_run_write_nothing(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_table(conn)

    assert persist_regime_history(conn, pd.DataFrame()) == 0
    assert persist_regime_history(conn, _hist(), dry_run=True) == 2
    assert conn.execute("SELECT COUNT(*) FROM regime_history").fetchone()[0] == 0
