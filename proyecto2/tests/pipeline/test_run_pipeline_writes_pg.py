# proyecto2/tests/pipeline/test_run_pipeline_writes_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
src/pipeline/run_pipeline.py's write-path helpers that own their own transaction:
_upsert_metric_state, _write_metric_alerts, _log, _update_ols_state. All either commit internally
or issue raw BEGIN/COMMIT/ROLLBACK SQL (via shared.db.begin_immediate/in_transaction), so every
test here uses pg_conn_module_schema/pg_session_conn -- never bare pg_conn/SAVEPOINT -- same
discipline as every other commit-owning function in this migration.

This module (run_pipeline.py) had zero test coverage before this port -- its write helpers were
extracted into src/writers/metrics_writer.py specifically to be independently testable (R-7); these
four functions stayed behind because they read module-level state (METRIC_VERSION, RUN_BATCH_ID)
rather than taking it as parameters, so RUN_BATCH_ID is set on the module directly before each test
that needs a non-empty batch_id.

fund_metric_alerts renames `window` -> `window_label` in the target schema (same PG-reserved-word
rename as fund_metric_timeseries, db/pg/rename_map.yaml) -- the ported SQL targets that name.
"""
from __future__ import annotations

import src.pipeline.run_pipeline as rp


def _make_fund_metric_state(conn):
    conn.execute("""
        CREATE TABLE fund_metric_state (
            isin                 text NOT NULL,
            metric_version       text NOT NULL DEFAULT 'v1',
            input_hash           text NOT NULL,
            calculated_at        date NOT NULL,
            last_ols_quarter     text,
            last_ols_nav_count   integer,
            PRIMARY KEY (isin, metric_version)
        )
    """)


def _make_fund_metric_alerts(conn):
    conn.execute("""
        CREATE TABLE fund_metric_alerts (
            isin            text NOT NULL,
            metric          text NOT NULL,
            window_label    text NOT NULL,
            level           text NOT NULL,
            rule_code       text NOT NULL,
            value           double precision,
            reference_value double precision,
            ref_type        text,
            detected_at     timestamptz DEFAULT now(),
            PRIMARY KEY (isin, metric, window_label)
        )
    """)


def _make_p2_pipeline_log(conn):
    conn.execute("""
        CREATE TABLE p2_pipeline_log (
            id              serial PRIMARY KEY,
            isin            text,
            step            text,
            status          text,
            horizon         text,
            metric_version  text,
            message         text,
            created_at      timestamptz DEFAULT now(),
            batch_id        text
        )
    """)


def test_upsert_metric_state_replace_semantics(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)

    rp._upsert_metric_state(conn, "ES0001", "hash-v1", dry_run=False)
    rp._upsert_metric_state(conn, "ES0001", "hash-v2", dry_run=False)

    row = conn.execute(
        f"SELECT input_hash FROM fund_metric_state WHERE isin='ES0001' AND metric_version='{rp.METRIC_VERSION}'"
    ).fetchone()
    assert row[0] == "hash-v2"

    # dry_run writes nothing
    rp._upsert_metric_state(conn, "ES0002", "hash-x", dry_run=True)
    count = conn.execute("SELECT COUNT(*) FROM fund_metric_state WHERE isin='ES0002'").fetchone()[0]
    assert count == 0


def test_upsert_metric_state_batches_inside_caller_transaction(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)

    conn.execute("BEGIN")
    rp._upsert_metric_state(conn, "ES0003", "hash-batched", dry_run=False)
    conn.execute("COMMIT")

    row = conn.execute(
        f"SELECT input_hash FROM fund_metric_state WHERE isin='ES0003' AND metric_version='{rp.METRIC_VERSION}'"
    ).fetchone()
    assert row[0] == "hash-batched"


def test_write_metric_alerts_upsert(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_alerts(conn)

    rows = [{
        "isin": "ES0001", "metric": "vol_ann", "window": "rolling_1y",
        "level": "WARN", "rule_code": "R1", "value": 0.30,
        "reference_value": 0.20, "ref_type": "peer_p75",
    }]
    n = rp._write_metric_alerts(conn, rows, dry_run=False)
    assert n == 1

    rows2 = [dict(rows[0], level="ALARM", value=0.40)]
    rp._write_metric_alerts(conn, rows2, dry_run=False)
    row = conn.execute(
        "SELECT level, value FROM fund_metric_alerts "
        "WHERE isin='ES0001' AND metric='vol_ann' AND window_label='rolling_1y'"
    ).fetchone()
    assert row == ("ALARM", 0.40)

    assert rp._write_metric_alerts(conn, [], dry_run=False) == 0
    assert rp._write_metric_alerts(conn, rows, dry_run=True) == 0


def test_log_writes_row_and_batches_inside_caller_transaction(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_p2_pipeline_log(conn)
    rp.RUN_BATCH_ID = "TESTBATCH1"
    try:
        rp._log(conn, "ES0001", "CALC_METRICS", "OK", "since_inception", "ok", dry_run=False)
        row = conn.execute(
            "SELECT isin, step, status, batch_id FROM p2_pipeline_log WHERE isin='ES0001'"
        ).fetchone()
        assert row == ("ES0001", "CALC_METRICS", "OK", "TESTBATCH1")

        # dry_run writes nothing
        rp._log(conn, "ES0002", "CALC_METRICS", "OK", None, None, dry_run=True)
        count = conn.execute("SELECT COUNT(*) FROM p2_pipeline_log WHERE isin='ES0002'").fetchone()[0]
        assert count == 0

        # EFF-2 batching: caller-owned transaction, _log must not issue its own COMMIT
        conn.execute("BEGIN")
        rp._log(conn, "ES0003", "CALC_METRICS", "OK", None, "batched", dry_run=False)
        conn.execute("COMMIT")
        row3 = conn.execute("SELECT isin FROM p2_pipeline_log WHERE isin='ES0003'").fetchone()
        assert row3 is not None
    finally:
        rp.RUN_BATCH_ID = ""


def test_update_ols_state(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)
    conn.execute(
        f"INSERT INTO fund_metric_state (isin, metric_version, input_hash, calculated_at) "
        f"VALUES ('ES0001', '{rp.METRIC_VERSION}', 'h1', '2026-01-01')"
    )
    conn.commit()

    rp._update_ols_state(conn, "ES0001", "2026Q1", 120, dry_run=False)
    row = conn.execute(
        f"SELECT last_ols_quarter, last_ols_nav_count FROM fund_metric_state "
        f"WHERE isin='ES0001' AND metric_version='{rp.METRIC_VERSION}'"
    ).fetchone()
    assert row == ("2026Q1", 120)

    rp._update_ols_state(conn, "ES0001", "2026Q2", 999, dry_run=True)
    row2 = conn.execute(
        f"SELECT last_ols_quarter FROM fund_metric_state WHERE isin='ES0001' AND metric_version='{rp.METRIC_VERSION}'"
    ).fetchone()
    assert row2[0] == "2026Q1"
