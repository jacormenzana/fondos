# proyecto2/tests/writers/test_metrics_writer_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
src/writers/metrics_writer.py's three DB-writing functions (write_metrics, write_timeseries,
replace_beta_set), ported alongside the production module per the plan's "red->green per site"
discipline (the SQLite-only sibling, test_timeseries_writer.py, is unchanged and still covers the
:memory: path).

All three functions manage their own explicit transaction (BEGIN/COMMIT/ROLLBACK, via
shared.db.begin_immediate/in_transaction) when the caller hasn't already opened one, so every test
here uses pg_conn_module_schema/pg_session_conn -- never bare pg_conn/SAVEPOINT -- same discipline
as every other commit-owning function in this migration. Unlike preserve_and_write()
(shared/statistical_audit/persistence.py), these functions issue their own raw BEGIN/COMMIT SQL
rather than the Python conn.commit()/rollback() API, which works correctly even while
pg_conn_module_schema leaves the connection in autocommit=True: a manual SQL BEGIN suspends
autocommit's per-statement behavior until the matching COMMIT/ROLLBACK, so no autocommit toggling
is needed in these tests (contrast with test_statistical_audit_persistence_pg.py's
preserve_and_write test, which DOES need it because that function uses the Python API instead).

gold.fund_metric_timeseries / gold.fund_metric_alerts rename `window` -> `window_label` in the
target schema (PG reserved word, db/pg/rename_map.yaml) -- the production port branches on this;
these tests use the renamed column directly, matching what the ported SQL actually targets.
"""
from __future__ import annotations

import math

from src.writers.metrics_writer import replace_beta_set, write_metrics, write_timeseries


def _make_fund_metrics(conn):
    conn.execute("""
        CREATE TABLE fund_metrics (
            isin              text NOT NULL,
            metric            text NOT NULL,
            horizon           text NOT NULL,
            value             double precision,
            real_flag         smallint NOT NULL DEFAULT 0,
            calculation_date  date,
            metric_version    text NOT NULL DEFAULT 'v1',
            benchmark_id      text,
            source_rows       integer,
            algorithm_version text,
            batch_id          text,
            load_ts           timestamptz DEFAULT now(),
            PRIMARY KEY (isin, metric, horizon, real_flag, metric_version)
        )
    """)


def _make_fund_metric_timeseries(conn):
    conn.execute("""
        CREATE TABLE fund_metric_timeseries (
            isin              text NOT NULL,
            metric            text NOT NULL,
            window_label      text NOT NULL,
            date              date NOT NULL,
            value             double precision,
            real_flag         smallint NOT NULL DEFAULT 0,
            ref_type          text,
            ref_value         double precision,
            source_rows       integer,
            algorithm_version text,
            batch_id          text,
            PRIMARY KEY (isin, metric, window_label, date, real_flag)
        )
    """)


def _ts_row(value, source_rows=12, date="2026-06-30"):
    return {
        "isin": "ES0001", "metric": "sortino", "window": "rolling_1y",
        "date": date, "value": value, "real_flag": 0,
        "ref_type": None, "ref_value": None, "source_rows": source_rows,
    }


def _fetch_ts_row(conn, date="2026-06-30"):
    return conn.execute(
        "SELECT value, algorithm_version, batch_id FROM fund_metric_timeseries "
        "WHERE isin='ES0001' AND metric='sortino' AND window_label='rolling_1y' "
        f"AND date='{date}' AND real_flag=0"
    ).fetchone()


def test_write_timeseries_upsert_semantics(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_timeseries(conn)

    n = write_timeseries(conn, [_ts_row(0.10)], dry_run=False,
                          algorithm_version="V1", batch_id="B1")
    assert n == 1
    row = _fetch_ts_row(conn)
    assert row[0] == 0.10 and row[1] == "V1" and row[2] == "B1"

    # Root-cause regression guard (same invariant as the SQLite sibling test): a formula fix
    # (new value + new algorithm_version) on an existing key must overwrite, not no-op.
    write_timeseries(conn, [_ts_row(0.25)], dry_run=False,
                      algorithm_version="V2", batch_id="B2")
    row = _fetch_ts_row(conn)
    assert row[0] == 0.25 and row[1] == "V2" and row[2] == "B2"

    # Identical rewrite is a no-op (verifies the IS DISTINCT FROM guard survived the
    # SQLite `IS NOT` -> Postgres `IS DISTINCT FROM` translation).
    write_timeseries(conn, [_ts_row(0.25)], dry_run=False,
                      algorithm_version="V2", batch_id="B2")
    row = _fetch_ts_row(conn)
    assert row[0] == 0.25 and row[1] == "V2" and row[2] == "B2"

    # Different dates coexist under the (isin, metric, window_label, date, real_flag) PK.
    write_timeseries(conn, [_ts_row(0.30, date="2026-07-31")], dry_run=False,
                      algorithm_version="V2", batch_id="B2")
    count = conn.execute(
        "SELECT COUNT(*) FROM fund_metric_timeseries WHERE isin='ES0001'"
    ).fetchone()[0]
    assert count == 2

    assert write_timeseries(conn, [], dry_run=False, algorithm_version="V1", batch_id="B1") == 0
    assert write_timeseries(conn, [_ts_row(0.99)], dry_run=True,
                             algorithm_version="V1", batch_id="B1") == 0


def test_write_timeseries_batches_inside_caller_transaction(pg_session_conn, pg_conn_module_schema):
    """EFF-2 path: when the caller already opened a transaction, write_timeseries must NOT issue
    its own COMMIT -- the row must only become visible after the caller commits."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_timeseries(conn)

    conn.execute("BEGIN")
    write_timeseries(conn, [_ts_row(0.42)], dry_run=False,
                      algorithm_version="V1", batch_id="B1")
    conn.execute("COMMIT")

    row = _fetch_ts_row(conn)
    assert row[0] == 0.42


def test_write_metrics_upsert_and_nan_to_null(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)

    m = {"metric": "sharpe", "value": 0.5, "real_flag": 0, "source_rows": 12}
    write_metrics(conn, "ES0001", [m], "since_inception", dry_run=False,
                  algorithm_version="V1", batch_id="B1", metric_version="v1")
    m2 = {"metric": "sharpe", "value": 0.9, "real_flag": 0, "source_rows": 12}
    write_metrics(conn, "ES0001", [m2], "since_inception", dry_run=False,
                  algorithm_version="V2", batch_id="B2", metric_version="v1")
    row = conn.execute(
        "SELECT value, algorithm_version, batch_id FROM fund_metrics "
        "WHERE isin='ES0001' AND metric='sharpe' AND horizon='since_inception'"
    ).fetchone()
    assert row == (0.9, "V2", "B2")

    m_nan = {"metric": "vol_ann", "value": float("nan"), "real_flag": 0, "source_rows": 12}
    write_metrics(conn, "ES0001", [m_nan], "since_inception", dry_run=False,
                  algorithm_version="V1", batch_id="B1", metric_version="v1")
    value = conn.execute(
        "SELECT value FROM fund_metrics WHERE isin='ES0001' AND metric='vol_ann'"
    ).fetchone()[0]
    assert value is None

    assert write_metrics(conn, "ES0001", [m], "since_inception", dry_run=True,
                          algorithm_version="V1", batch_id="B1", metric_version="v1") == 0


def test_replace_beta_set_drops_orphans_and_spares_other_metrics(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)

    write_metrics(conn, "ES0001",
                  [{"metric": "sharpe", "value": 0.5, "real_flag": 0, "source_rows": 12}],
                  "since_inception", dry_run=False, algorithm_version="V1", batch_id="B1",
                  metric_version="v1")

    run1 = [
        {"metric": "beta_rate_eu", "value": 0.1, "real_flag": 0, "source_rows": 60},
        {"metric": "beta_oil",     "value": 0.2, "real_flag": 0, "source_rows": 60},
    ]
    replace_beta_set(conn, "ES0001", run1, "since_inception", dry_run=False,
                      algorithm_version="V1", batch_id="B1", metric_version="v1")

    run2 = [{"metric": "beta_rate_eu", "value": 0.15, "real_flag": 0, "source_rows": 60}]
    replace_beta_set(conn, "ES0001", run2, "since_inception", dry_run=False,
                      algorithm_version="V2", batch_id="B2", metric_version="v1")

    rows = conn.execute(
        "SELECT metric, value FROM fund_metrics WHERE isin='ES0001' AND metric LIKE 'beta_%'"
    ).fetchall()
    metrics = {r[0]: r[1] for r in rows}
    assert metrics == {"beta_rate_eu": 0.15}

    # Non-beta metric untouched by the delete+insert.
    sharpe = conn.execute(
        "SELECT value FROM fund_metrics WHERE isin='ES0001' AND metric='sharpe'"
    ).fetchone()[0]
    assert sharpe == 0.5
