# proyecto2/tests/writers/test_timeseries_writer.py
# -*- coding: utf-8 -*-
"""
Tests para src/writers/metrics_writer.py (P0, 2026-09-15).

Cumple R-7: sin importar pipeline.py ni core.io -- importa directamente
src.writers.metrics_writer, que ahora recibe algorithm_version/batch_id como
parametros explicitos en lugar de leer globals de run_pipeline.py (por eso
es testable sin ese import).

Foco: pinnear la semantica de upsert de write_timeseries() ANTES de usarla en
produccion. Root cause fix: la version anterior usaba INSERT OR IGNORE, por
lo que un --force nunca propagaba un cambio de formula (ej. la
canonicalizacion de sortino/sharpe, 2b45416) a fund_metric_timeseries -- ver
docstring de write_timeseries().
"""

import sqlite3

import pytest

from src.writers.metrics_writer import (
    rows_from_metric_tuples,
    write_metrics,
    write_timeseries,
    replace_beta_set,
)


def _fund_metrics_ddl() -> str:
    return """
    CREATE TABLE fund_metrics (
        isin TEXT NOT NULL,
        metric TEXT NOT NULL,
        horizon TEXT NOT NULL,
        value REAL,
        real_flag INTEGER NOT NULL DEFAULT 0,
        calculation_date TEXT,
        metric_version TEXT NOT NULL DEFAULT 'v1',
        benchmark_id TEXT,
        source_rows INTEGER,
        algorithm_version TEXT,
        batch_id TEXT,
        PRIMARY KEY (isin, metric, horizon, real_flag, metric_version)
    )"""


def _fund_metric_timeseries_ddl() -> str:
    return """
    CREATE TABLE fund_metric_timeseries (
        isin TEXT NOT NULL,
        metric TEXT NOT NULL,
        window TEXT NOT NULL,
        date TEXT NOT NULL,
        value REAL,
        real_flag INTEGER NOT NULL DEFAULT 0,
        ref_type TEXT,
        ref_value REAL,
        source_rows INTEGER,
        algorithm_version TEXT,
        batch_id TEXT,
        PRIMARY KEY (isin, metric, window, date, real_flag)
    )"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_fund_metrics_ddl())
    conn.execute(_fund_metric_timeseries_ddl())
    return conn


def _ts_row(value: float, source_rows: int = 12) -> dict:
    return {
        "isin": "ES0001", "metric": "sortino", "window": "rolling_1y",
        "date": "2026-06-30", "value": value, "real_flag": 0,
        "ref_type": None, "ref_value": None, "source_rows": source_rows,
    }


def _fetch_ts_row(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM fund_metric_timeseries WHERE isin='ES0001' AND metric='sortino' "
        "AND window='rolling_1y' AND date='2026-06-30' AND real_flag=0"
    ).fetchone()


class TestRowsFromMetricTuples:
    def test_converts_tuples_to_dicts(self):
        result = rows_from_metric_tuples([("sharpe", 0.5, 0), ("sortino", 0.7, 1)], source_rows=24)
        assert result == [
            {"metric": "sharpe",  "value": 0.5, "real_flag": 0, "source_rows": 24},
            {"metric": "sortino", "value": 0.7, "real_flag": 1, "source_rows": 24},
        ]


class TestWriteTimeseriesUpsert:
    def test_first_write_inserts(self):
        conn = _conn()
        n = write_timeseries(conn, [_ts_row(0.10)], dry_run=False,
                              algorithm_version="V1", batch_id="B1")
        assert n == 1
        row = _fetch_ts_row(conn)
        assert row["value"] == pytest.approx(0.10)
        assert row["algorithm_version"] == "V1"
        assert row["batch_id"] == "B1"

    def test_second_write_with_new_value_updates(self):
        """Root-cause regression guard: a formula fix (new value, new
        algorithm_version) on an EXISTING (isin, metric, window, date,
        real_flag) key must overwrite the row, not silently no-op as the
        old INSERT OR IGNORE did -- this is the exact propagation trap that
        let the 2b45416 sortino/sharpe fix never reach this table."""
        conn = _conn()
        write_timeseries(conn, [_ts_row(0.10)], dry_run=False,
                          algorithm_version="V1", batch_id="B1")
        write_timeseries(conn, [_ts_row(0.25)], dry_run=False,
                          algorithm_version="V2", batch_id="B2")
        row = _fetch_ts_row(conn)
        assert row["value"] == pytest.approx(0.25)
        assert row["algorithm_version"] == "V2"
        assert row["batch_id"] == "B2"

    def test_identical_rewrite_is_a_noop(self):
        """Re-running with IDENTICAL value+algorithm_version must not
        disturb the row -- verifies the conditional WHERE guard in the
        upsert (idempotent at the page level), not just that DO UPDATE
        fires unconditionally on every conflict."""
        conn = _conn()
        write_timeseries(conn, [_ts_row(0.10)], dry_run=False,
                          algorithm_version="V1", batch_id="B1")
        write_timeseries(conn, [_ts_row(0.10)], dry_run=False,
                          algorithm_version="V1", batch_id="B1")
        row = _fetch_ts_row(conn)
        assert row["value"] == pytest.approx(0.10)
        assert row["algorithm_version"] == "V1"
        assert row["batch_id"] == "B1"

    def test_dry_run_writes_nothing(self):
        conn = _conn()
        n = write_timeseries(conn, [_ts_row(0.10)], dry_run=True,
                              algorithm_version="V1", batch_id="B1")
        assert n == 0
        assert _fetch_ts_row(conn) is None

    def test_empty_rows_writes_nothing(self):
        conn = _conn()
        n = write_timeseries(conn, [], dry_run=False,
                              algorithm_version="V1", batch_id="B1")
        assert n == 0

    def test_different_dates_do_not_collide(self):
        """Sanity check the PK really is (isin, metric, window, date,
        real_flag) -- two different dates must coexist, not overwrite."""
        conn = _conn()
        row_a = _ts_row(0.10)
        row_b = dict(row_a, date="2026-07-31", value=0.20)
        write_timeseries(conn, [row_a, row_b], dry_run=False,
                          algorithm_version="V1", batch_id="B1")
        count = conn.execute(
            "SELECT COUNT(*) FROM fund_metric_timeseries WHERE isin='ES0001'"
        ).fetchone()[0]
        assert count == 2


class TestWriteMetrics:
    def _metric(self, metric, value, real_flag=0, source_rows=12):
        return {"metric": metric, "value": value, "real_flag": real_flag, "source_rows": source_rows}

    def test_insert_or_replace_semantics(self):
        conn = _conn()
        write_metrics(conn, "ES0001", [self._metric("sharpe", 0.5)], "since_inception",
                      dry_run=False, algorithm_version="V1", batch_id="B1", metric_version="v1")
        write_metrics(conn, "ES0001", [self._metric("sharpe", 0.9)], "since_inception",
                      dry_run=False, algorithm_version="V2", batch_id="B2", metric_version="v1")
        row = conn.execute(
            "SELECT value, algorithm_version, batch_id FROM fund_metrics "
            "WHERE isin='ES0001' AND metric='sharpe' AND horizon='since_inception'"
        ).fetchone()
        assert row["value"] == pytest.approx(0.9)
        assert row["algorithm_version"] == "V2"

    def test_nan_value_stored_as_null(self):
        conn = _conn()
        write_metrics(conn, "ES0001", [self._metric("sharpe", float("nan"))], "since_inception",
                      dry_run=False, algorithm_version="V1", batch_id="B1", metric_version="v1")
        row = conn.execute(
            "SELECT value FROM fund_metrics WHERE isin='ES0001' AND metric='sharpe'"
        ).fetchone()
        assert row["value"] is None

    def test_dry_run_writes_nothing(self):
        conn = _conn()
        n = write_metrics(conn, "ES0001", [self._metric("sharpe", 0.5)], "since_inception",
                           dry_run=True, algorithm_version="V1", batch_id="B1", metric_version="v1")
        assert n == 0


class TestReplaceBetaSet:
    def test_replaces_full_beta_set_dropping_orphans(self):
        """A factor present in run 1 but excluded (e.g. by VIF) in run 2
        must not survive as an orphan row."""
        conn = _conn()
        run1 = [
            {"metric": "beta_rate_eu", "value": 0.1, "real_flag": 0, "source_rows": 60},
            {"metric": "beta_oil",     "value": 0.2, "real_flag": 0, "source_rows": 60},
        ]
        replace_beta_set(conn, "ES0001", run1, "since_inception", dry_run=False,
                          algorithm_version="V1", batch_id="B1", metric_version="v1")

        run2 = [
            {"metric": "beta_rate_eu", "value": 0.15, "real_flag": 0, "source_rows": 60},
        ]
        replace_beta_set(conn, "ES0001", run2, "since_inception", dry_run=False,
                          algorithm_version="V2", batch_id="B2", metric_version="v1")

        rows = conn.execute(
            "SELECT metric, value FROM fund_metrics "
            "WHERE isin='ES0001' AND metric LIKE 'beta_%'"
        ).fetchall()
        metrics = {r["metric"]: r["value"] for r in rows}
        assert metrics == {"beta_rate_eu": pytest.approx(0.15)}

    def test_does_not_touch_non_beta_metrics(self):
        conn = _conn()
        write_metrics(conn, "ES0001",
                      [{"metric": "sharpe", "value": 0.5, "real_flag": 0, "source_rows": 12}],
                      "since_inception", dry_run=False, algorithm_version="V1", batch_id="B1",
                      metric_version="v1")
        replace_beta_set(conn, "ES0001",
                          [{"metric": "beta_oil", "value": 0.2, "real_flag": 0, "source_rows": 60}],
                          "since_inception", dry_run=False, algorithm_version="V1", batch_id="B1",
                          metric_version="v1")
        row = conn.execute(
            "SELECT value FROM fund_metrics WHERE isin='ES0001' AND metric='sharpe'"
        ).fetchone()
        assert row["value"] == pytest.approx(0.5)
