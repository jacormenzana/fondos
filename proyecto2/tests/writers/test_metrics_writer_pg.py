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

v27 pivot (2026-09-27): fund_metric_timeseries dropped real_flag from its key (pivoted into
value_nominal/value_real/has_real, see db/pg/30_gold.sql and the pivot plan). write_timeseries()
still ACCEPTS the long format (one dict per real_flag) -- that contract is unchanged, callers
(rolling_stats.compute_rolling_rows) are untouched -- but now groups pairs internally
(_group_timeseries_pairs) and writes one pivoted row per (isin, metric, window, date).
"""
from __future__ import annotations

import math

from src.writers.metrics_writer import (
    replace_beta_set, write_metrics, write_metrics_batch, write_timeseries,
)


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
            value_nominal     double precision,
            value_real        double precision,
            has_real          boolean NOT NULL DEFAULT false,
            source_rows       integer,
            algorithm_version text,
            batch_id          text,
            PRIMARY KEY (isin, metric, window_label, date)
        )
    """)


def _ts_row(value, source_rows=12, date="2026-06-30"):
    """A nominal-only (real_flag=0) long-format row -- write_timeseries()'s input contract is
    unchanged by the pivot, only what it does with it internally."""
    return {
        "isin": "ES0001", "metric": "sortino", "window": "rolling_1y",
        "date": date, "value": value, "real_flag": 0, "source_rows": source_rows,
    }


def _ts_row_real(value, source_rows=12, date="2026-06-30"):
    return {
        "isin": "ES0001", "metric": "sortino", "window": "rolling_1y",
        "date": date, "value": value, "real_flag": 1, "source_rows": source_rows,
    }


def _fetch_ts_row(conn, date="2026-06-30"):
    return conn.execute(
        "SELECT value_nominal, algorithm_version, batch_id FROM fund_metric_timeseries "
        "WHERE isin='ES0001' AND metric='sortino' AND window_label='rolling_1y' "
        f"AND date='{date}'"
    ).fetchone()


def _fetch_ts_pivoted(conn, date="2026-06-30"):
    return conn.execute(
        "SELECT value_nominal, value_real, has_real FROM fund_metric_timeseries "
        "WHERE isin='ES0001' AND metric='sortino' AND window_label='rolling_1y' "
        f"AND date='{date}'"
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

    # Different dates coexist under the (isin, metric, window_label, date) PK.
    write_timeseries(conn, [_ts_row(0.30, date="2026-07-31")], dry_run=False,
                      algorithm_version="V2", batch_id="B2")
    count = conn.execute(
        "SELECT COUNT(*) FROM fund_metric_timeseries WHERE isin='ES0001'"
    ).fetchone()[0]
    assert count == 2

    assert write_timeseries(conn, [], dry_run=False, algorithm_version="V1", batch_id="B1") == 0
    assert write_timeseries(conn, [_ts_row(0.99)], dry_run=True,
                             algorithm_version="V1", batch_id="B1") == 0


def test_write_timeseries_real_without_nominal_raises(pg_session_conn, pg_conn_module_schema):
    """Structurally impossible per rolling_stats.compute_rolling_rows (always emits real_flag=0
    first) -- write_timeseries must fail loud rather than guess, and run_pipeline.py confines the
    failure to the one fund being processed (run_pipeline.py:1322-1340: per-fund ROLLBACK + continue)."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_timeseries(conn)
    try:
        write_timeseries(conn, [_ts_row_real(0.05)], dry_run=False,
                          algorithm_version="V1", batch_id="B1")
        raise AssertionError("expected ValueError")
    except AssertionError:
        raise
    except ValueError:
        pass


def test_write_timeseries_pivot_truth_table(pg_session_conn, pg_conn_module_schema):
    """Table-driven coverage of every stored x incoming state the pivot upsert must handle (§pivot
    plan "Verification" — review round 3 flagged the dense WHERE clause as fragile, so this pins
    every combination the SET/WHERE expressions are built from)."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")

    def write(rows, alg="V1", batch="B1"):
        return write_timeseries(conn, rows, dry_run=False, algorithm_version=alg, batch_id=batch)

    # 1. Insert, nominal-only -> has_real=false, value_real stays NULL.
    _make_fund_metric_timeseries(conn)
    n = write([_ts_row(1.0)])
    assert n == 1
    assert _fetch_ts_pivoted(conn) == (1.0, None, False)

    # 2. Insert, pair -> has_real=true, value_real set.
    conn.execute("TRUNCATE fund_metric_timeseries")
    n = write([_ts_row(1.0), _ts_row_real(0.8)])
    assert n == 1
    assert _fetch_ts_pivoted(conn) == (1.0, 0.8, True)

    # 3. Insert, pair with a NULL real value (a NaN result) -> has_real=true anyway (NOT gated on
    # value_real IS NOT NULL -- that's the whole point of has_real existing, §pivot plan "v1 defect").
    conn.execute("TRUNCATE fund_metric_timeseries")
    n = write([_ts_row(1.0), _ts_row_real(None)])
    assert n == 1
    assert _fetch_ts_pivoted(conn) == (1.0, None, True)

    # 4. Update: nominal-only write over an existing has_real=true row NEVER touches the real side.
    conn.execute("TRUNCATE fund_metric_timeseries")
    write([_ts_row(1.0), _ts_row_real(0.8)])
    n = write([_ts_row(2.0)])
    assert n == 1
    assert _fetch_ts_pivoted(conn) == (2.0, 0.8, True)

    # 5. Update: nominal-only write, unchanged value -> no-op (rowcount 0).
    n = write([_ts_row(2.0)])
    assert n == 0
    assert _fetch_ts_pivoted(conn) == (2.0, 0.8, True)

    # 6. Update: pair over an existing has_real=false row -> has_real flips true, value_real set.
    conn.execute("TRUNCATE fund_metric_timeseries")
    write([_ts_row(1.0)])
    n = write([_ts_row(1.0), _ts_row_real(0.5)])
    assert n == 1
    assert _fetch_ts_pivoted(conn) == (1.0, 0.5, True)

    # 7. Update: identical pair rewrite -> no-op (rowcount 0) -- the WHERE clause's row comparison
    # must agree with the SET clause's own expressions, or this would spuriously "change" every run.
    n = write([_ts_row(1.0), _ts_row_real(0.5)])
    assert n == 0
    assert _fetch_ts_pivoted(conn) == (1.0, 0.5, True)

    # 8. Update: algorithm_version alone changes -> not a no-op, even with identical values.
    n = write([_ts_row(1.0), _ts_row_real(0.5)], alg="V2", batch="B2")
    assert n == 1
    row = _fetch_ts_row(conn)
    assert row[1] == "V2" and row[2] == "B2"


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


# ---- FND-0092: one-transaction batch writer for the whole-universe rolling category write -----------

_KW = dict(algorithm_version="V1", batch_id="B1", metric_version="v1")


def _items():
    def mk(metric, value):
        return {"metric": metric, "value": value, "real_flag": 0, "source_rows": 12}
    return [
        ("ES0001", "rolling_1y", [mk("vol_ann_pctile_cat", 0.25), mk("max_dd_pctile_cat", 0.75)]),
        ("ES0001", "rolling_3y", [mk("vol_ann_pctile_cat", 0.5)]),
        ("ES0002", "rolling_1y", [mk("vol_ann_pctile_cat", float("nan"))]),   # NaN -> NULL
    ]


def _dump(conn):
    return conn.execute(
        "SELECT isin, metric, horizon, value, real_flag, algorithm_version, batch_id, metric_version "
        "FROM fund_metrics ORDER BY isin, metric, horizon"
    ).fetchall()


def test_write_metrics_batch_matches_per_call_writer(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)
    for isin, horizon, metrics in _items():
        write_metrics(conn, isin, metrics, horizon, dry_run=False, **_KW)
    expected = [tuple(r) for r in _dump(conn)]
    conn.execute("DELETE FROM fund_metrics")

    n = write_metrics_batch(conn, _items(), dry_run=False, **_KW)
    assert n == 4
    assert [tuple(r) for r in _dump(conn)] == expected          # identical rows, NaN -> NULL included
    # idempotent upsert: a second identical batch changes nothing and adds nothing
    write_metrics_batch(conn, _items(), dry_run=False, **_KW)
    assert [tuple(r) for r in _dump(conn)] == expected


def test_write_metrics_batch_chunks_and_counts(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)
    assert write_metrics_batch(conn, _items(), dry_run=False, chunk=1, **_KW) == 4
    assert len(_dump(conn)) == 4


def test_write_metrics_batch_appends_to_caller_transaction(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)
    conn.execute("BEGIN")
    write_metrics_batch(conn, _items(), dry_run=False, **_KW)
    conn.execute("ROLLBACK")                       # the caller owns the transaction: nothing was committed
    assert _dump(conn) == []


def test_write_metrics_batch_is_atomic_when_a_chunk_fails(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)
    items = _items()
    items[2][2][0]["real_flag"] = "not-a-smallint"  # fails in the 4th chunk (chunk=1), after 3 rows went in
    try:
        write_metrics_batch(conn, items, dry_run=False, chunk=1, **_KW)
        raise AssertionError("expected a database error")
    except AssertionError:
        raise
    except Exception:
        pass
    assert _dump(conn) == []                        # the whole batch rolled back, no partial universe


def test_write_metrics_batch_noop_cases(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metrics(conn)
    assert write_metrics_batch(conn, [], dry_run=False, **_KW) == 0
    assert write_metrics_batch(conn, _items(), dry_run=True, **_KW) == 0
    assert write_metrics_batch(conn, [("ES0001", "rolling_1y", [])], dry_run=False, **_KW) == 0
    assert _dump(conn) == []
