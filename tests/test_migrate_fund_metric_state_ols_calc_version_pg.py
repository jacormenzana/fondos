"""
tests/test_migrate_fund_metric_state_ols_calc_version_pg.py — scripts/ops/migrate_fund_metric_state_ols_calc_version.py.

The backfill must mark a fund only when its WHOLE macro set (macro_r2 + every beta_*/macro_* row) is on the
live CALC_VERSION; the ALTER + UPDATE are one idempotent transaction. Pure parts run anywhere; the database
parts run on the hermetic container (python scripts/ops/run_pg_tests.py). The script's table names are
parameters, so the tests use unqualified tables in a throwaway schema.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "migrate_ols", _ROOT / "scripts" / "ops" / "migrate_fund_metric_state_ols_calc_version.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

NEW, OLD = "20261004", "20261002"


# The script's column_exists() looks tables up by name WITHOUT a schema, and the hermetic container also holds the
# real control.fund_metric_state (loaded from db/pg/35_control.sql). So every call below passes names qualified
# with the per-test schema; the helpers here use the same qualified names.
def _setup(conn):
    conn.execute("""CREATE TABLE fund_metric_state (isin text NOT NULL, metric_version text NOT NULL DEFAULT 'v1',
                    input_hash text NOT NULL, calculated_at date NOT NULL, last_ols_quarter text,
                    last_ols_nav_count integer, PRIMARY KEY (isin, metric_version))""")
    conn.execute("""CREATE TABLE fund_metrics (isin text, metric text, horizon text, algorithm_version text)""")


def _fund(conn, isin, quarter, rows):
    conn.execute("INSERT INTO fund_metric_state (isin, input_hash, calculated_at, last_ols_quarter) "
                 "VALUES (%s, 'h', '2026-10-05', %s)", (isin, quarter))
    for metric, ver in rows:
        conn.execute("INSERT INTO fund_metrics VALUES (%s, %s, 'since_inception', %s)", (isin, metric, ver))


def _versions(conn):
    return dict(conn.execute("SELECT isin, last_ols_calc_version FROM fund_metric_state").fetchall())


def test_backfill_marks_only_funds_whose_whole_macro_set_is_current(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    ST, MT = f"{pg_conn_module_schema}.fund_metric_state", f"{pg_conn_module_schema}.fund_metrics"
    _setup(conn)
    _fund(conn, "FULL", "2026-4", [("macro_r2", NEW), ("beta_oil", NEW), ("beta_vix", NEW), ("macro_alpha", NEW)])
    _fund(conn, "SPARSE", "2026-4", [("macro_r2", NEW), ("beta_oil", NEW)])        # VIF dropped factors: still current
    _fund(conn, "PARTIAL", "2026-4", [("macro_r2", NEW), ("beta_oil", NEW), ("beta_vix", OLD)])   # mixed versions
    _fund(conn, "STALE", "2026-4", [("macro_r2", OLD), ("beta_oil", OLD)])
    _fund(conn, "NOOLS", None, [("macro_r2", NEW)])                                 # never ran OLS
    _fund(conn, "NOROWS", "2026-4", [])                                             # OLS state but no macro rows
    conn.commit()

    marked = mod.migrate(conn, NEW, state_table=ST, metrics_table=MT)
    conn.commit()

    assert marked == 2
    assert _versions(conn) == {"FULL": NEW, "SPARSE": NEW, "PARTIAL": None, "STALE": None, "NOOLS": None,
                               "NOROWS": None}


def test_non_macro_rows_on_other_versions_do_not_block_the_mark(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    ST, MT = f"{pg_conn_module_schema}.fund_metric_state", f"{pg_conn_module_schema}.fund_metrics"
    _setup(conn)
    _fund(conn, "F1", "2026-4", [("macro_r2", NEW), ("beta_oil", NEW), ("sharpe", OLD), ("max_drawdown", OLD)])
    conn.commit()
    assert mod.migrate(conn, NEW, ST, MT) == 1


def test_migration_is_idempotent_and_adds_the_column_once(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    ST, MT = f"{pg_conn_module_schema}.fund_metric_state", f"{pg_conn_module_schema}.fund_metrics"
    _setup(conn)
    _fund(conn, "F1", "2026-4", [("macro_r2", NEW), ("beta_oil", NEW)])
    conn.commit()
    assert mod.column_exists(conn, ST) is False

    assert mod.migrate(conn, NEW, ST, MT) == 1
    conn.commit()
    assert mod.column_exists(conn, ST) is True
    assert mod.migrate(conn, NEW, ST, MT) == 1      # re-run: same value, no error
    conn.commit()
    assert _versions(conn) == {"F1": NEW}


def test_report_is_read_only_and_matches_what_migrate_marks(pg_conn_module_schema, pg_session_conn):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    ST, MT = f"{pg_conn_module_schema}.fund_metric_state", f"{pg_conn_module_schema}.fund_metrics"
    _setup(conn)
    _fund(conn, "A", "2026-4", [("macro_r2", NEW), ("beta_oil", NEW)])
    _fund(conn, "B", "2026-4", [("macro_r2", OLD)])
    _fund(conn, "C", None, [])
    conn.commit()

    rep = mod.report(conn, NEW, ST, MT)      # works BEFORE the column exists
    assert rep == {"state_rows": 3, "with_ols_quarter": 2, "markable": 1, "left_null": 1}
    assert mod.column_exists(conn, ST) is False           # report did not alter anything


def test_a_failed_migration_rolls_back_the_alter_too(pg_conn_module_schema, pg_session_conn):
    """ALTER and UPDATE share one transaction: a failing UPDATE must leave the column absent."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    ST, MT = f"{pg_conn_module_schema}.fund_metric_state", f"{pg_conn_module_schema}.fund_metrics"
    _setup(conn)
    conn.execute("DROP TABLE fund_metrics")                                # makes the UPDATE fail after the ALTER
    # pg_conn_module_schema leaves the connection in autocommit, where the ALTER would commit by itself. The
    # script runs on a normal transactional connection (psycopg default), so model that here.
    conn.autocommit = False
    raised = None
    try:
        mod.migrate(conn, NEW, ST, MT)
    except Exception as exc:
        raised = exc
        conn.rollback()
    finally:
        conn.autocommit = True                                             # what the fixture's teardown expects
    assert type(raised).__name__ == "UndefinedTable", f"migrate raised {raised!r}"
    assert mod.column_exists(conn, ST) is False


def test_lock_file_held_is_false_when_no_cycle_runs(tmp_path):
    assert mod.lock_file_held(tmp_path / "absent.lock") is False
    free = tmp_path / "free.lock"
    free.write_text("")
    assert mod.lock_file_held(free) is False


def test_dry_run_changes_nothing_and_exits_0(capsys):
    assert mod.main([]) in (0, 2)      # 2 only when no owner DSN is configured in this environment
    assert "APPLIED" not in capsys.readouterr().out
