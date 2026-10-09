"""FND-0168: a fund under MIN_NAV_ROWS must lose its stale metrics instead of keeping them."""
from __future__ import annotations

from pathlib import Path

import src.pipeline.run_pipeline as rp


def test_clear_insufficient_history_deletes_derived_rows_but_not_the_nav_flag(
        pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("""CREATE TABLE fund_metric_state (isin text NOT NULL, metric_version text NOT NULL DEFAULT 'v1',
                    input_hash text NOT NULL, calculated_at date NOT NULL, last_ols_quarter text,
                    last_ols_nav_count integer, last_ols_calc_version text,
                    PRIMARY KEY (isin, metric_version))""")
    conn.execute("CREATE TABLE fund_metrics (isin text, metric text, horizon text, value double precision)")
    conn.execute("CREATE TABLE fund_metric_timeseries (isin text, metric text)")
    conn.execute("CREATE TABLE fund_metric_alerts (isin text, metric text)")
    conn.execute("CREATE TABLE nav_sources (isin text PRIMARY KEY, data_status text DEFAULT 'OK')")
    for isin in ("SHORT", "LONG"):
        conn.execute("INSERT INTO fund_metrics VALUES (%s,'sharpe','since_inception',0.5)", (isin,))
        conn.execute("INSERT INTO fund_metrics VALUES (%s,'return_ann','since_inception',0.1)", (isin,))
        conn.execute("INSERT INTO fund_metric_timeseries VALUES (%s,'sharpe')", (isin,))
        conn.execute("INSERT INTO fund_metric_alerts VALUES (%s,'sharpe')", (isin,))
        conn.execute("INSERT INTO nav_sources (isin) VALUES (%s)", (isin,))
        rp._upsert_metric_state(conn, isin, "h", dry_run=False)

    assert rp._clear_insufficient_history(conn, "SHORT", dry_run=True) == 0
    assert conn.execute("SELECT COUNT(*) FROM fund_metrics WHERE isin='SHORT'").fetchone()[0] == 2

    assert rp._clear_insufficient_history(conn, "SHORT", dry_run=False) == 2
    for t in ("fund_metrics", "fund_metric_timeseries", "fund_metric_alerts", "fund_metric_state"):
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE isin='SHORT'").fetchone()[0] == 0, t
        assert conn.execute(f"SELECT COUNT(*) FROM {t} WHERE isin='LONG'").fetchone()[0] >= 1, t
    # Short is not invalid: no quarantine flag, so discovery/loading treat it as a normal source.
    assert conn.execute("SELECT data_status FROM nav_sources WHERE isin='SHORT'").fetchone()[0] == "OK"

    # Idempotent: nothing left to delete on the next run, and no error.
    assert rp._clear_insufficient_history(conn, "SHORT", dry_run=False) == 0


def test_under_floor_check_runs_on_the_full_series_before_clipping_and_hash():
    """A long fund merely clipped short by --from-date/--to-date must keep its metrics, so the
    clearing check has to sit before the date clipping (and before the cache-hash shortcut)."""
    src = Path(rp.__file__).read_text(encoding="utf-8")
    calls = [i for i in range(len(src)) if src.startswith("_clear_insufficient_history(conn, isin", i)]
    # Two clearing paths share the helper: under MIN_NAV_ROWS (FND-0168) and excluded from the EUR view (FND-0243).
    assert len(calls) == 2
    for call in calls:
        assert call < src.index("compute_input_hash(\n                    nav_df, ipc_df")
        assert call < src.index("if from_date:\n                    nav_df = nav_df[")
