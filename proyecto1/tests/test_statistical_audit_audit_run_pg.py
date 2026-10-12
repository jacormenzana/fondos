# proyecto1/tests/test_statistical_audit_audit_run_pg.py
# -*- coding: utf-8 -*-
"""FND-0261: control.audit_run and the one-transaction persist (persistence.persist_run, runner._persist), and the gate's evidence
selection, on the hermetic Postgres container (python scripts/ops/run_pg_tests.py). persist_run commits, so these use
pg_session_conn + a throwaway schema; audit_run is created from the REAL DDL in db/pg/35_control.sql."""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "ops"))

from migrate_fund_metric_family_state import ddl_sql  # noqa: E402
from shared.statistical_audit import completeness as cpl  # noqa: E402
from shared.statistical_audit.gating import select_evidence_run  # noqa: E402
from shared.statistical_audit.persistence import audit_run_table_exists, persist_run  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit_arpg", os.path.join(_ROOT, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
sys.modules["run_statistical_audit_arpg"] = runner
_spec.loader.exec_module(runner)

DOMAIN = "p2_metrics"
T0 = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def tx_conn(pg_session_conn, pg_conn_module_schema):
    """The module-schema connection with PRODUCTION transaction semantics. pg_conn_module_schema switches the shared connection to
    autocommit=True (every statement commits on its own, so no rollback could ever be observed); shared.db.get_connection() is
    autocommit=False. Restored before the fixture's own teardown (DROP SCHEMA) runs."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.autocommit = True


def _tables(conn, with_header=True):
    conn.execute("""CREATE TABLE audit_statistic (run_id TEXT NOT NULL, computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        domain TEXT NOT NULL, population TEXT NOT NULL, group_key TEXT NOT NULL, stat_name TEXT NOT NULL,
        stat_value DOUBLE PRECISION, stat_text TEXT, n INTEGER, catalog_version TEXT,
        PRIMARY KEY (run_id, domain, population, group_key, stat_name))""")
    conn.execute("""CREATE TABLE audit_finding (id SERIAL PRIMARY KEY, run_id TEXT NOT NULL,
        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, domain TEXT NOT NULL, block TEXT NOT NULL, rule_id TEXT NOT NULL,
        rule_class TEXT NOT NULL, severity TEXT NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ALARM')), group_key TEXT NOT NULL,
        isin TEXT, value DOUBLE PRECISION, reference_value DOUBLE PRECISION, threshold DOUBLE PRECISION, distance DOUBLE PRECISION,
        evidence TEXT, root_cause_candidate TEXT, catalog_version TEXT)""")
    conn.execute("""CREATE TABLE audit_finding_isin (run_id text NOT NULL, domain text NOT NULL, rule_id text NOT NULL,
        group_key text NOT NULL, isin varchar(12) NOT NULL, PRIMARY KEY (run_id, domain, rule_id, group_key, isin))""")
    if with_header:
        conn.execute(ddl_sql(table="control.audit_run").replace("control.audit_run", "audit_run"))
    conn.commit()


def _finding(rule="X", severity="ALARM", isins=("A",)):
    return {"block": "BLOCK5", "rule_id": rule, "rule_class": "HARD_INVARIANT", "severity": severity, "group_key": rule,
            "evidence": "e", "violating_isins": tuple(isins)}


def _header(status="FAIL", scope="FULL"):
    return {"scope": scope, "status": status, "started_at": T0, "universe_size": 2, "skips": [{"check": "OUTLIER"}],
            "catalog_version": "cv", "git_commit": "abc1234", "git_clean": True, "config_hash": "v1:000000000000"}


def _counts(conn, run_id):
    return tuple(conn.execute(f"SELECT count(*) FROM {t} WHERE run_id = %s", (run_id,)).fetchone()[0]
                 for t in ("audit_statistic", "audit_finding", "audit_finding_isin", "audit_run"))


STATS = [("GLOBAL", "g1", {"p50": 1.0, "n_total": 2}, 2)]


def test_persist_run_writes_rows_and_header_together(tx_conn):
    conn = tx_conn
    _tables(conn)
    res = persist_run(conn, "r1", DOMAIN, STATS, [_finding()], catalog_version="cv", header=_header())
    assert (res.n_stats, res.n_findings, res.n_isin_rows, res.header_written) == (2, 1, 1, True)
    assert _counts(conn, "r1") == (2, 1, 1, 1)
    row = conn.execute("SELECT scope, status, skips, git_clean, finished_at IS NOT NULL FROM audit_run WHERE run_id = 'r1'").fetchone()
    assert row == ("FULL", "FAIL", [{"check": "OUTLIER"}], True, True)


def test_finished_at_is_the_clock_time_not_the_start_of_the_long_audit_transaction(tx_conn):
    # the runner reads the whole audit inside one transaction; now() would stamp the header with that transaction's start
    conn = tx_conn
    _tables(conn)
    tx_start = conn.execute("SELECT now()").fetchone()[0]                                 # opens the transaction, like the audit reads
    conn.execute("SELECT pg_sleep(0.3)")
    persist_run(conn, "r1", DOMAIN, STATS, [], header=_header())
    finished_at = conn.execute("SELECT finished_at FROM audit_run WHERE run_id = 'r1'").fetchone()[0]
    assert (finished_at - tx_start).total_seconds() >= 0.25


def test_a_failure_mid_persist_leaves_the_previous_run_intact_and_no_partial_rows(tx_conn):
    conn = tx_conn
    _tables(conn)
    persist_run(conn, "r1", DOMAIN, STATS, [_finding()], header=_header("PASS"))
    with pytest.raises(Exception):
        # the second finding violates the severity CHECK after the statistics and the first finding were inserted
        persist_run(conn, "r1", DOMAIN, STATS + [("GLOBAL", "g2", {"p50": 9.0}, 1)],
                    [_finding("Y", isins=("B",)), _finding("Z", severity="BOGUS")], header=_header("FAIL"))
    assert _counts(conn, "r1") == (2, 1, 1, 1)                                          # the prior run, untouched
    assert conn.execute("SELECT status FROM audit_run WHERE run_id = 'r1'").fetchone()[0] == "PASS"
    assert conn.execute("SELECT rule_id FROM audit_finding WHERE run_id = 'r1'").fetchall() == [("X",)]


def test_a_re_persist_replaces_rows_and_header(tx_conn):
    conn = tx_conn
    _tables(conn)
    persist_run(conn, "r1", DOMAIN, STATS, [_finding(), _finding("Y")], header=_header("FAIL"))
    persist_run(conn, "r1", DOMAIN, STATS, [], header=_header("PASS"))
    assert _counts(conn, "r1") == (2, 0, 0, 1)
    assert conn.execute("SELECT status FROM audit_run WHERE run_id = 'r1'").fetchone()[0] == "PASS"


def test_without_the_header_table_the_rows_are_still_written(tx_conn):
    conn = tx_conn
    _tables(conn, with_header=False)
    assert audit_run_table_exists(conn) is False
    res = persist_run(conn, "r1", DOMAIN, STATS, [_finding()], header=_header())
    assert (res.n_findings, res.header_written) == (1, False)
    conn.rollback()


def test_the_runner_persists_a_classified_header(tx_conn, monkeypatch):
    conn = tx_conn
    _tables(conn)
    monkeypatch.setattr(runner, "git_state", lambda: ("abc1234", False))
    run = runner.AuditRun(DOMAIN)
    run.statistics = STATS
    run.findings = [_finding()]
    run.skip("CPI_COVERAGE", cpl.KIND_MISSING, "no ES CPI coverage")
    runner._persist(conn, run, "audit_full")
    runner._persist(conn, run, "audit_x_sample")
    assert run.persisted_header is True
    rows = dict((r[0], r[1:]) for r in conn.execute("SELECT run_id, scope, status, git_clean FROM audit_run").fetchall())
    assert rows == {"audit_full": ("FULL", "INCOMPLETE", False), "audit_x_sample": ("SAMPLE", "INCOMPLETE", False)}


def test_the_gate_selects_the_latest_full_complete_header(tx_conn):
    conn = tx_conn
    _tables(conn)
    for run_id, status, scope in (("r_fail", "FAIL", "FULL"), ("r_incomplete", "INCOMPLETE", "FULL"), ("r_sample", "PASS", "SAMPLE")):
        persist_run(conn, run_id, DOMAIN, [], [], header=_header(status, scope))
    run, note = select_evidence_run(conn, DOMAIN, contract=True)
    assert run == ("r_fail", T0) and note == ""                                       # run_ts = started_at, when the data was read
    conn.rollback()


def test_real_ddl_audit_run(pg_session_conn):
    """db/pg/35_control.sql as loaded into the container: columns, key, and the CHECKs refuse an unknown state or scope."""
    conn = pg_session_conn
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'control' AND table_name = 'audit_run'").fetchall()}
    assert cols == {"run_id", "domain", "scope", "status", "started_at", "finished_at", "universe_size", "skips", "catalog_version",
                    "git_commit", "git_clean", "config_hash"}
    for scope, status in (("FULL", "DONE"), ("PARTIAL", "PASS")):
        conn.execute("SAVEPOINT s")
        with pytest.raises(Exception):
            conn.execute("INSERT INTO control.audit_run (run_id, domain, scope, status) VALUES ('x', 'p2_metrics', %s, %s)", (scope, status))
        conn.execute("ROLLBACK TO SAVEPOINT s")
    conn.rollback()
