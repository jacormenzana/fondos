# proyecto1/tests/test_statistical_audit_finding_isin_pg.py
# -*- coding: utf-8 -*-
"""FND-0234(d): audit_finding_isin writer/loader, clear_run, the runner's _persist, and the real DDL, on the hermetic Postgres
container (python scripts/ops/run_pg_tests.py). The writers commit, so these use pg_session_conn + a throwaway schema."""
from __future__ import annotations

import importlib.util
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.statistical_audit.persistence import (  # noqa: E402
    clear_run, emit_finding_isins, finding_isin_table_exists, load_finding_isins,
)

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit_fipg", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_statistical_audit_fipg"] = runner
_spec.loader.exec_module(runner)

RUN, DOMAIN = "r1", "cost_attributes"


def _audit_tables(conn, with_isin_table=True):
    conn.execute("""CREATE TABLE audit_statistic (run_id TEXT NOT NULL, computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        domain TEXT NOT NULL, population TEXT NOT NULL, group_key TEXT NOT NULL, stat_name TEXT NOT NULL,
        stat_value DOUBLE PRECISION, stat_text TEXT, n INTEGER, catalog_version TEXT,
        PRIMARY KEY (run_id, domain, population, group_key, stat_name))""")
    conn.execute("""CREATE TABLE audit_finding (id SERIAL PRIMARY KEY, run_id TEXT NOT NULL,
        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, domain TEXT NOT NULL, block TEXT NOT NULL, rule_id TEXT NOT NULL,
        rule_class TEXT NOT NULL, severity TEXT NOT NULL, group_key TEXT NOT NULL, isin TEXT, value DOUBLE PRECISION,
        reference_value DOUBLE PRECISION, threshold DOUBLE PRECISION, distance DOUBLE PRECISION, evidence TEXT,
        root_cause_candidate TEXT, catalog_version TEXT)""")
    if with_isin_table:
        conn.execute("""CREATE TABLE audit_finding_isin (run_id text NOT NULL, domain text NOT NULL, rule_id text NOT NULL,
            group_key text NOT NULL, isin varchar(12) NOT NULL, PRIMARY KEY (run_id, domain, rule_id, group_key, isin))""")


def _finding(rule="OC_NOT_CONTAMINATED", isins=("A", "B")):
    return {"block": "BLOCK5", "rule_id": rule, "rule_class": "HARD_INVARIANT", "severity": "ALARM", "group_key": rule,
            "distance": float(len(isins)), "evidence": "x", "violating_isins": tuple(isins)}


def test_emit_then_load_roundtrip_is_idempotent(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _audit_tables(conn)
    findings = [_finding(isins=("B", "A", "A")), _finding(rule="MGMT_LE_TOTAL", isins=("C",)),
                {"block": "BLOCK2", "rule_id": "PAIR", "group_key": "PAIR"}]            # no ISINs: ignored
    assert emit_finding_isins(conn, RUN, DOMAIN, findings) == 3
    assert emit_finding_isins(conn, RUN, DOMAIN, findings) == 3                          # re-run: same rows, no error
    got = load_finding_isins(conn, RUN, DOMAIN)
    assert sorted(got.itertuples(index=False, name=None)) == [
        ("MGMT_LE_TOTAL", "MGMT_LE_TOTAL", "C"), ("OC_NOT_CONTAMINATED", "OC_NOT_CONTAMINATED", "A"),
        ("OC_NOT_CONTAMINATED", "OC_NOT_CONTAMINATED", "B")]
    assert load_finding_isins(conn, "other", DOMAIN).empty and load_finding_isins(conn, RUN, "p2_metrics").empty


def test_a_missing_table_is_not_an_error_anywhere(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _audit_tables(conn, with_isin_table=False)
    assert finding_isin_table_exists(conn) is False
    assert emit_finding_isins(conn, RUN, DOMAIN, [_finding()]) == 0
    assert load_finding_isins(conn, RUN, DOMAIN).empty
    clear_run(conn, RUN, DOMAIN)                                                         # must not touch the missing table
    conn.rollback()


def test_clear_run_removes_the_isin_detail_of_that_run_only(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _audit_tables(conn)
    emit_finding_isins(conn, "r1", DOMAIN, [_finding(isins=("A",))])
    emit_finding_isins(conn, "r2", DOMAIN, [_finding(isins=("B",))])
    clear_run(conn, "r1", DOMAIN)
    conn.commit()
    assert load_finding_isins(conn, "r1", DOMAIN).empty
    assert load_finding_isins(conn, "r2", DOMAIN)["isin"].tolist() == ["B"]


def test_persist_writes_the_detail_and_a_re_persist_replaces_it(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _audit_tables(conn)
    run = runner.AuditRun(DOMAIN)
    run.findings = [_finding(isins=("A", "B"))]
    runner._persist(conn, run, "rp")
    assert run.persisted_isin_rows == 2
    run.findings = [_finding(isins=("B", "C"))]                                          # the same run_id, a changed result
    runner._persist(conn, run, "rp")
    assert sorted(load_finding_isins(conn, "rp", DOMAIN)["isin"]) == ["B", "C"]          # replaced, not accumulated


def test_a_persist_without_the_table_still_works_and_reports_zero(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _audit_tables(conn, with_isin_table=False)
    run = runner.AuditRun(DOMAIN)
    run.findings = [_finding()]
    n_stats, n_findings = runner._persist(conn, run, "rq")
    assert n_findings == 1 and run.persisted_isin_rows == 0


def test_real_ddl_columns_and_the_fund_master_foreign_key(pg_session_conn):
    """db/pg/35_control.sql as loaded into the container."""
    conn = pg_session_conn
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='control' "
        "AND table_name='audit_finding_isin'").fetchall()}
    assert cols == {"run_id", "domain", "rule_id", "group_key", "isin"}
    kinds = {r[0]: r[1] for r in conn.execute(
        "SELECT conname, contype FROM pg_constraint WHERE conrelid = 'control.audit_finding_isin'::regclass").fetchall()}
    assert kinds == {"audit_finding_isin_pkey": "p", "audit_finding_isin_isin_fk": "f"}
