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
            last_ols_calc_version text,
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
        f"SELECT last_ols_quarter, last_ols_nav_count, last_ols_calc_version FROM fund_metric_state "
        f"WHERE isin='ES0001' AND metric_version='{rp.METRIC_VERSION}'"
    ).fetchone()
    assert row == ("2026Q1", 120, rp.CALC_VERSION)

    rp._update_ols_state(conn, "ES0001", "2026Q2", 999, dry_run=True)
    row2 = conn.execute(
        f"SELECT last_ols_quarter FROM fund_metric_state WHERE isin='ES0001' AND metric_version='{rp.METRIC_VERSION}'"
    ).fetchone()
    assert row2[0] == "2026Q1"


def _seed_ols_state(conn, calc_version, quarter="2026-4", nav_count=100):
    conn.execute(
        f"INSERT INTO fund_metric_state (isin, metric_version, input_hash, calculated_at, "
        f"last_ols_quarter, last_ols_nav_count, last_ols_calc_version) "
        f"VALUES ('ES0001', '{rp.METRIC_VERSION}', 'h1', '2026-01-01', %s, %s, %s)",
        (quarter, nav_count, calc_version),
    )
    conn.commit()


def test_ols_is_fresh_requires_current_calc_version(pg_session_conn, pg_conn_module_schema):
    """2026-10-05 RCA: a CALC_VERSION bump inside the same quarter must NOT leave OLS 'fresh'
    (it did: ols_funds=0, macro betas stayed on the old version, the beta gate failed)."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)
    _seed_ols_state(conn, calc_version="19990101")           # same quarter, older version
    assert rp._ols_is_fresh(conn, "ES0001", 100, "2026-4") is False


def test_ols_is_fresh_true_when_quarter_version_and_nav_match(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)
    _seed_ols_state(conn, calc_version=rp.CALC_VERSION)
    assert rp._ols_is_fresh(conn, "ES0001", 102, "2026-4") is True      # NAV grew < 3 rows
    assert rp._ols_is_fresh(conn, "ES0001", 103, "2026-4") is False     # NAV grew >= 3 rows
    assert rp._ols_is_fresh(conn, "ES0001", 100, "2027-1") is False     # new quarter


def test_ols_is_fresh_null_version_is_stale(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)
    _seed_ols_state(conn, calc_version=None)                  # row written before the column existed
    assert rp._ols_is_fresh(conn, "ES0001", 100, "2026-4") is False


def test_ols_is_fresh_force_overrides_everything(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)
    _seed_ols_state(conn, calc_version=rp.CALC_VERSION)
    assert rp._ols_is_fresh(conn, "ES0001", 100, "2026-4", force=True) is False


def test_schema_alignment_fails_when_ols_calc_version_column_missing(
        pg_session_conn, pg_conn_module_schema, monkeypatch):
    """Order guard: code deployed before the migration must abort at startup, not per fund.

    schema_checks._table_columns looks tables up by name across ALL schemas, and the hermetic container
    also holds the real control.fund_metric_state, so the lookup is pointed at this test's schema."""
    import pytest
    import shared.schema_checks as sc
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("""CREATE TABLE fund_metric_state (isin text NOT NULL, metric_version text NOT NULL,
                    input_hash text NOT NULL, calculated_at date NOT NULL, last_ols_quarter text,
                    last_ols_nav_count integer, PRIMARY KEY (isin, metric_version))""")
    monkeypatch.setattr(sc, "_table_columns", lambda c, table: {r[0] for r in c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (pg_conn_module_schema, table.lower())).fetchall()})

    assert sc.verify_db_schema(conn, tables=("fund_metric_state",)) == {
        "fund_metric_state": ["last_ols_calc_version"]}
    with pytest.raises(AssertionError, match="last_ols_calc_version"):
        sc.assert_schema_alignment(conn, tables=("fund_metric_state",))
    conn.execute("ALTER TABLE fund_metric_state ADD COLUMN last_ols_calc_version text")
    sc.assert_schema_alignment(conn, tables=("fund_metric_state",))         # now aligned
    assert sc.verify_db_schema(conn, tables=("no_such_table_in_the_check_list",)) == {}   # filter honoured


def test_quarantine_invalid_nav_clears_derived_rows_and_flags_source(pg_session_conn, pg_conn_module_schema):
    """FND-0164: a validate_nav() failure must remove the fund's stale metrics, not just skip it."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_metric_state(conn)
    _make_fund_metric_alerts(conn)
    conn.execute("CREATE TABLE fund_metrics (isin text, metric text, horizon text, value double precision)")
    conn.execute("CREATE TABLE fund_metric_timeseries (isin text, metric text)")
    conn.execute("CREATE TABLE nav_sources (isin text PRIMARY KEY, data_status text DEFAULT 'OK')")
    for isin in ("BAD", "GOOD"):
        conn.execute("INSERT INTO fund_metrics VALUES (%s,'max_drawdown','since_inception',-0.97)", (isin,))
        conn.execute("INSERT INTO fund_metrics VALUES (%s,'sharpe','since_inception',0.5)", (isin,))
        conn.execute("INSERT INTO fund_metric_timeseries VALUES (%s,'sharpe')", (isin,))
        conn.execute("INSERT INTO nav_sources (isin) VALUES (%s)", (isin,))
        rp._upsert_metric_state(conn, isin, "h", dry_run=False)
    conn.execute("INSERT INTO fund_metric_alerts (isin,metric,window_label,level,rule_code) VALUES ('BAD','sharpe','w','WARN','R')")

    assert rp._quarantine_invalid_nav(conn, "BAD", dry_run=True) == 0
    assert conn.execute("SELECT COUNT(*) FROM fund_metrics WHERE isin='BAD'").fetchone()[0] == 2

    assert rp._quarantine_invalid_nav(conn, "BAD", dry_run=False) == 2
    for t in ("fund_metrics", "fund_metric_timeseries", "fund_metric_alerts", "fund_metric_state"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE isin='BAD'").fetchone()[0] == 0, t
        if t != "fund_metric_alerts":
            assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE isin='GOOD'").fetchone()[0] >= 1, t
    assert conn.execute("SELECT data_status FROM nav_sources WHERE isin='BAD'").fetchone()[0] == "INVALID_NAV"
    assert conn.execute("SELECT data_status FROM nav_sources WHERE isin='GOOD'").fetchone()[0] == "OK"


def test_clear_short_horizon_rows_touches_only_that_horizon(pg_session_conn, pg_conn_module_schema):
    """FND-0177: a bad daily tail window clears ONE short horizon of ONE fund, nothing else."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("CREATE TABLE fund_metrics (isin text, metric text, horizon text, value double precision)")
    for isin in ("BAD", "GOOD"):
        for hz in ("rolling_1m", "rolling_3m", "since_inception"):
            conn.execute("INSERT INTO fund_metrics VALUES (%s,'sharpe',%s,0.5)", (isin, hz))

    assert rp._clear_short_horizon_rows(conn, "BAD", "rolling_1m", dry_run=True) == 0
    assert conn.execute("SELECT COUNT(*) FROM fund_metrics").fetchone()[0] == 6

    assert rp._clear_short_horizon_rows(conn, "BAD", "rolling_1m", dry_run=False) == 1
    left = {(r[0], r[1]) for r in conn.execute("SELECT isin, horizon FROM fund_metrics").fetchall()}
    assert ("BAD", "rolling_1m") not in left
    assert {("BAD", "rolling_3m"), ("BAD", "since_inception"), ("GOOD", "rolling_1m")} <= left
    assert len(left) == 5


def test_validate_nav_rejects_scale_glitch_in_daily_tail_window():
    """FND-0177: validate_nav() works on the daily tail window exactly as on the monthly series."""
    import pandas as pd
    from src.utils.validators import validate_nav

    dates = pd.date_range("2026-01-01", periods=63, freq="B")
    clean = pd.DataFrame({"date": dates, "nav": [100.0 + i * 0.01 for i in range(63)]})
    assert validate_nav(clean.tail(21).reset_index(drop=True))[0] is True

    glitch = clean.copy()
    glitch.loc[40:, "nav"] = glitch.loc[40:, "nav"] * 100       # 1 -> 100 scale seam inside the window
    ok, err = validate_nav(glitch.tail(63).reset_index(drop=True))
    assert ok is False and "saltos" in err
    assert validate_nav(glitch.tail(10).reset_index(drop=True))[0] is True   # seam outside a shorter tail
