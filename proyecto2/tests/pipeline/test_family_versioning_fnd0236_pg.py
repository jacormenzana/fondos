# -*- coding: utf-8 -*-
"""FND-0236: the per-family state helpers and the DDL, on the hermetic Postgres container
(python scripts/ops/run_pg_tests.py). Same discipline as test_run_pipeline_writes_pg.py: helpers that own their
transaction are tested on pg_session_conn + a throwaway schema, never on the SAVEPOINT connection.
"""
from __future__ import annotations

import src.pipeline.run_pipeline as rp
from src.utils import family_versions as fv
from src.utils.fingerprint import data_fingerprint

import pandas as pd


def _make_tables(conn):
    conn.execute("""CREATE TABLE fund_metric_state (isin text NOT NULL, metric_version text NOT NULL DEFAULT 'v1',
                    input_hash text NOT NULL, calculated_at date NOT NULL, last_ols_quarter text,
                    last_ols_nav_count integer, last_ols_calc_version text, PRIMARY KEY (isin, metric_version))""")
    conn.execute("""CREATE TABLE fund_metric_family_state (isin text NOT NULL, metric_version text NOT NULL DEFAULT 'v1',
                    family text NOT NULL, input_hash text NOT NULL, family_token text NOT NULL,
                    calculated_at date NOT NULL, batch_id text, PRIMARY KEY (isin, metric_version, family))""")


def _hashes(cv="20261004", overrides=None):
    nav = pd.DataFrame({"date": pd.date_range("2020-01-31", periods=40, freq="ME"), "nav": range(100, 140)})
    return fv.current_family_hashes(data_fingerprint(nav, None), rp.METRIC_VERSION, cv, (), overrides or {})


def test_family_state_roundtrip_replace_and_dry_run(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    assert rp._get_family_hashes(conn, "ES0001") == {}

    rp.RUN_BATCH_ID = "B1"
    rp._upsert_family_state(conn, "ES0001", {"risk": "h1", "macro": "h2"}, dry_run=False)
    assert rp._get_family_hashes(conn, "ES0001") == {"risk": "h1", "macro": "h2"}

    rp._upsert_family_state(conn, "ES0001", {"macro": "h3"}, dry_run=False)           # replaces one family only
    assert rp._get_family_hashes(conn, "ES0001") == {"risk": "h1", "macro": "h3"}

    rp._upsert_family_state(conn, "ES0002", {"risk": "x"}, dry_run=True)              # dry run writes nothing
    rp._upsert_family_state(conn, "ES0002", {}, dry_run=False)                        # nothing to stamp
    assert rp._get_family_hashes(conn, "ES0002") == {}
    token = conn.execute("SELECT family_token, batch_id FROM fund_metric_family_state "
                         "WHERE isin='ES0001' AND family='risk'").fetchone()
    assert token == (rp.CALC_VERSION, "B1")


def test_family_state_joins_the_callers_transaction(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    conn.execute("BEGIN")
    rp._upsert_family_state(conn, "ES0003", {"fx": "h"}, dry_run=False)
    conn.execute("ROLLBACK")                                       # the helper must not have committed on its own
    assert rp._get_family_hashes(conn, "ES0003") == {}


def test_scenario_seed_then_macro_only_bump_runs_only_macro(pg_session_conn, pg_conn_module_schema):
    """The whole point of FND-0236, end to end on real rows: adopt from the legacy hash (nothing recomputed), then a
    macro-only version bump selects exactly one family and stamping it leaves the fund fully current."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_tables(conn)
    cur = _hashes()
    legacy = "legacy-hash"
    rp._upsert_metric_state(conn, "ES0004", legacy, dry_run=False)

    stored = rp._get_family_hashes(conn, "ES0004")                                      # no family rows yet
    first = fv.decide_fund(fv.ALL_FAMILIES, stored, cur,
                           legacy_stored=rp._get_stored_hash(conn, "ES0004"), legacy_current=legacy)
    assert first.seed and first.cache_hit
    rp._upsert_family_state(conn, "ES0004", cur, dry_run=False)

    bumped = _hashes(overrides={"macro": "20261101"})
    second = fv.decide_fund(fv.ALL_FAMILIES, rp._get_family_hashes(conn, "ES0004"), bumped)
    assert second.run == frozenset({"macro"})
    rp._upsert_family_state(conn, "ES0004", {f: bumped[f] for f in second.stamp}, dry_run=False)

    third = fv.decide_fund(fv.ALL_FAMILIES, rp._get_family_hashes(conn, "ES0004"), bumped)
    assert third.cache_hit and not third.run


def test_ddl_exists_with_the_columns_schema_checks_expects(pg_session_conn):
    """The real DDL (db/pg/35_control.sql, loaded into the container) matches shared.schema_checks."""
    from shared.db import _named_row_factory           # verify_db_schema reads rows by name, like the app connection
    from shared.schema_checks import FUND_METRIC_FAMILY_STATE_COLUMNS, verify_db_schema
    conn = pg_session_conn
    previous = conn.row_factory
    conn.row_factory = _named_row_factory
    try:
        assert verify_db_schema(conn, tables=("fund_metric_family_state",)) == {}
    finally:
        conn.row_factory = previous
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='control' "
        "AND table_name='fund_metric_family_state'").fetchall()}
    assert cols == set(FUND_METRIC_FAMILY_STATE_COLUMNS)


def test_all_tables_check_skips_the_optional_table(pg_session_conn, monkeypatch):
    """With the switch off nobody asks for the table, so the all-tables check must not require it."""
    from shared import schema_checks
    conn = pg_session_conn
    seen = []
    monkeypatch.setattr(schema_checks, "_table_columns", lambda c, t: (seen.append(t) or set()))
    schema_checks.verify_db_schema(conn)
    assert "fund_metric_family_state" not in seen
    seen.clear()
    schema_checks.verify_db_schema(conn, tables=("fund_metric_family_state",))
    assert seen == ["fund_metric_family_state"]
