# proyecto1/tests/test_statistical_audit_finding_lineage_pg.py
# -*- coding: utf-8 -*-
"""FND-0275 on the hermetic Postgres container (python scripts/ops/run_pg_tests.py): the data lineage of each violating ISIN
(fund_metrics.algorithm_version / batch_id) stored with the finding's ISIN detail. The lineage columns come from the REAL DDL
(db/pg/35_control.sql, block audit_finding_lineage); persist_run commits, so the tests run in a throwaway schema with PRODUCTION
transaction semantics (autocommit off)."""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))

import migrate_audit_finding_lineage as mig  # noqa: E402
from shared.statistical_audit import persistence as ps  # noqa: E402

P2, COST = "p2_metrics", "cost_attributes"


@pytest.fixture()
def tx_conn(pg_session_conn, pg_conn_module_schema):
    """Throwaway schema: the REAL control.audit_finding_isin (columns incl. the lineage ones, PK) minus its FK, the two tables persist_run
    also writes, and a minimal fund_metrics; autocommit OFF like shared.db.get_connection()."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("CREATE TABLE audit_finding_isin (LIKE control.audit_finding_isin INCLUDING ALL)")
    conn.execute("""CREATE TABLE audit_statistic (run_id TEXT NOT NULL, computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        domain TEXT NOT NULL, population TEXT NOT NULL, group_key TEXT NOT NULL, stat_name TEXT NOT NULL,
        stat_value DOUBLE PRECISION, stat_text TEXT, n INTEGER, catalog_version TEXT,
        PRIMARY KEY (run_id, domain, population, group_key, stat_name))""")
    conn.execute("""CREATE TABLE audit_finding (id SERIAL PRIMARY KEY, run_id TEXT NOT NULL,
        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, domain TEXT NOT NULL, block TEXT NOT NULL, rule_id TEXT NOT NULL,
        rule_class TEXT NOT NULL, severity TEXT NOT NULL, group_key TEXT NOT NULL, isin TEXT, value DOUBLE PRECISION,
        reference_value DOUBLE PRECISION, threshold DOUBLE PRECISION, distance DOUBLE PRECISION, evidence TEXT,
        root_cause_candidate TEXT, catalog_version TEXT)""")
    conn.execute("CREATE TABLE fund_metrics (isin text, metric text, algorithm_version text, batch_id text)")
    conn.execute("""INSERT INTO fund_metrics VALUES
        ('A', 'm1', '20261011', 'b1'), ('A', 'm2', '20261011', 'b2'), ('A', 'm3', '20261004', NULL),
        ('B', 'm1', '20261011', 'b1'), ('C', 'm1', NULL, NULL)""")
    conn.commit()
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.autocommit = True


def _finding(isins, rule="X"):
    return {"block": "BLOCK5", "rule_id": rule, "rule_class": "HARD_INVARIANT", "severity": "ALARM", "group_key": rule,
            "evidence": "e", "violating_isins": tuple(isins)}


def test_the_lineage_of_each_isin_is_its_distinct_sorted_versions_and_batches(tx_conn):
    got = ps.load_metric_lineage(tx_conn, ["A", "B", "C", "Z"])
    assert got["A"] == (["20261004", "20261011"], ["b1", "b2"])          # distinct, sorted, NULLs dropped
    assert got["B"] == (["20261011"], ["b1"])
    assert got["C"] == ([], [])                                          # a row with no stamp
    assert got["Z"] == ([], [])                                          # an ISIN with no metric row at all
    assert ps.load_metric_lineage(tx_conn, []) == {}


def test_persist_run_stores_the_lineage_of_every_violator_for_p2(tx_conn):
    ps.persist_run(tx_conn, "run1", P2, [], [_finding(["A", "B", "C"])])
    assert ps.load_finding_lineage(tx_conn, "run1", P2) == {
        ("X", "X", "A"): (["20261004", "20261011"], ["b1", "b2"]),
        ("X", "X", "B"): (["20261011"], ["b1"]),
        ("X", "X", "C"): ([], []),
    }


def test_the_cost_domain_stores_no_lineage_and_still_persists_its_violators(tx_conn):
    ps.persist_run(tx_conn, "run2", COST, [], [_finding(["A"])])
    assert tx_conn.execute("SELECT count(*) FROM audit_finding_isin WHERE run_id = 'run2'").fetchone()[0] == 1
    assert tx_conn.execute("SELECT algorithm_versions, batch_ids FROM audit_finding_isin WHERE run_id = 'run2'").fetchone() == (None, None)
    assert ps.load_finding_lineage(tx_conn, "run2", COST) == {}


def test_without_the_columns_the_writer_falls_back_to_the_old_row(tx_conn):
    tx_conn.execute("ALTER TABLE audit_finding_isin DROP COLUMN algorithm_versions, DROP COLUMN batch_ids")
    assert not ps.finding_isin_has_lineage(tx_conn)
    result = ps.persist_run(tx_conn, "run3", P2, [], [_finding(["A", "B"])])
    assert result.n_isin_rows == 2
    assert tx_conn.execute("SELECT count(*) FROM audit_finding_isin WHERE run_id = 'run3'").fetchone()[0] == 2
    assert ps.load_finding_lineage(tx_conn, "run3", P2) == {}


def test_a_re_persisted_run_replaces_its_lineage(tx_conn):
    ps.persist_run(tx_conn, "run4", P2, [], [_finding(["A"])])
    tx_conn.execute("UPDATE fund_metrics SET algorithm_version = '20261017', batch_id = 'b9' WHERE isin = 'A'")
    ps.persist_run(tx_conn, "run4", P2, [], [_finding(["A"])])
    assert ps.load_finding_lineage(tx_conn, "run4", P2) == {("X", "X", "A"): (["20261017"], ["b9"])}


def test_the_real_ddl_block_is_idempotent_and_adds_both_columns(pg_app_conn):
    conn = pg_app_conn
    conn.execute("ALTER TABLE control.audit_finding_isin DROP COLUMN IF EXISTS algorithm_versions, DROP COLUMN IF EXISTS batch_ids")
    assert mig.existing(conn) == {"table": True, "lineage_columns": False}
    mig.migrate(conn)
    mig.migrate(conn)
    assert mig.existing(conn) == {"table": True, "lineage_columns": True}
