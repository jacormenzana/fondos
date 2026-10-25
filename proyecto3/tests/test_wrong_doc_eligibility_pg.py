# proyecto3/tests/test_wrong_doc_eligibility_pg.py
# -*- coding: utf-8 -*-
"""FND-0323 D1 on Postgres: the real WRONG_DOC statement, the live loader's switch and the scorer's outcome.
Throwaway per-test schema (same discipline as test_fund_data_eligibility_pg.py); everything is read-only.

    python scripts/ops/run_pg_tests.py -- proyecto3/tests/test_wrong_doc_eligibility_pg.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from shared import doc_eligibility as de  # noqa: E402

ISINS = ("GOOD1", "GOOD2", "BADDOC", "NODOC")


def _world(conn, schema):
    conn.execute(f"SET search_path = {schema}")
    conn.execute("DROP TABLE IF EXISTS fund_master, fund_metrics, fund_kiid_metadata")
    conn.execute("""CREATE TABLE fund_master (isin text PRIMARY KEY, fund_name text, fund_nature text, srri smallint, investment_focus text,
                    credit_quality text, ongoing_charge_recurrent double precision, srri_quality_flag text, fund_family_id text,
                    in_current_universe smallint)""")
    conn.execute("""CREATE TABLE fund_metrics (isin text NOT NULL, metric text NOT NULL, horizon text NOT NULL, value double precision,
                    real_flag smallint NOT NULL DEFAULT 0, metric_version text NOT NULL DEFAULT 'v1',
                    PRIMARY KEY (isin, metric, horizon, real_flag, metric_version))""")
    conn.execute("CREATE TABLE fund_kiid_metadata (isin text PRIMARY KEY, kiid_status text)")
    for k, isin in enumerate(ISINS):
        conn.execute("INSERT INTO fund_master VALUES (%s,%s,'Mixtos',4,'Broad','Investment Grade',0.01,'OK',%s,1)", (isin, isin, f"f{k}"))
        conn.execute("INSERT INTO fund_metrics (isin, metric, horizon, value, real_flag) VALUES "
                     "(%s, 'return_ann', 'since_inception', %s, 1), (%s, 'sharpe', 'since_inception', %s, 0)",
                     (isin, 0.04 + 0.01 * k, isin, 1.0 + 0.1 * k))
    conn.execute("INSERT INTO fund_kiid_metadata VALUES ('GOOD1','OK'), ('GOOD2','CACHED'), ('BADDOC','WRONG_DOC'), ('NODOC','NOT_FOUND')")
    conn.commit()


def test_the_statement_returns_exactly_the_wrong_doc_isins(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    assert de.wrong_doc_reasons(conn) == {"BADDOC": de.REASON_TEXT}        # NOT_FOUND is a different state: not covered by D1


def test_the_loader_attaches_the_reason_only_when_the_switch_is_on(pg_session_conn, pg_conn_module_schema, monkeypatch):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", False)
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    off = fs.load_fund_metrics_for_scoring(conn)
    assert "doc_status_reason" not in off.columns                           # OFF: the frame is exactly what it was before
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)
    on = fs.load_fund_metrics_for_scoring(conn)
    assert set(on.index) == set(off.index) == set(ISINS)
    assert on.loc["BADDOC", "doc_status_reason"] == de.REASON_TEXT
    assert [on.loc[i, "doc_status_reason"] for i in ("GOOD1", "GOOD2", "NODOC")] == [None, None, None]


def test_with_the_switch_on_the_scorer_excludes_exactly_the_wrong_doc_fund(pg_session_conn, pg_conn_module_schema, monkeypatch):
    conn = pg_session_conn
    _world(conn, pg_conn_module_schema)
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", False)
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", False)
    off = fs.score_funds_from_df(fs.load_fund_metrics_for_scoring(conn), "Expansion", verbose=False)
    monkeypatch.setattr(cfg, "P3_WRONG_DOC_EXCLUSION_ENABLED", True)
    on = fs.score_funds_from_df(fs.load_fund_metrics_for_scoring(conn), "Expansion", verbose=False)
    assert bool(off["eligible"].astype(bool).all())
    bad = on[on["isin"] == "BADDOC"]
    assert len(bad) and not bad["eligible"].astype(bool).any() and (bad["exclusion_reason"] == de.REASON_TEXT).all()
    others = on[on["isin"] != "BADDOC"]
    assert len(others) and others["eligible"].astype(bool).all()
