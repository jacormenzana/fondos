# proyecto1/tests/test_statistical_audit_persistence_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration — "shared P1/P2/P3 functionality" phase (2026-09-20) — dedicated regression
tests for shared/statistical_audit/persistence.py's three DB-writing functions (emit_statistics,
emit_findings, preserve_and_write). This module is used by scripts/audit/run_statistical_audit.py
(and, until its 2026-09-26 retirement, scripts/diag/diag_cost_duplication_reconcile.py), backing the
auditStatisticalDataDistributionCostAttributes/P2Metrics skills — genuinely shared across the P1
cost-attribute audit and the P2 metrics audit (audit_statistic/audit_finding are tagged "P1/P2" in
AGENTS.md's table roster), so it is the first module ported in this phase rather than under either
project's own P1/P2/P3 write-path tranche.

All three functions commit (or rollback) internally, so every test here uses
pg_conn_module_schema/pg_session_conn — never bare pg_conn/SAVEPOINT — same discipline as every
other commit-owning function in this migration.
"""
from __future__ import annotations

import math

from shared.statistical_audit.persistence import (
    CorrectionRecord,
    emit_findings,
    emit_statistics,
    preserve_and_write,
)


def _make_audit_statistic(conn):
    conn.execute("""
        CREATE TABLE audit_statistic (
            run_id      TEXT NOT NULL,
            computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            domain      TEXT NOT NULL,
            population  TEXT NOT NULL,
            group_key   TEXT NOT NULL,
            stat_name   TEXT NOT NULL,
            stat_value  DOUBLE PRECISION,
            stat_text   TEXT,
            n           INTEGER,
            catalog_version TEXT,
            PRIMARY KEY (run_id, domain, population, group_key, stat_name)
        )
    """)


def _make_audit_finding(conn):
    conn.execute("""
        CREATE TABLE audit_finding (
            id                    SERIAL PRIMARY KEY,
            run_id                TEXT NOT NULL,
            detected_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            domain                TEXT NOT NULL,
            block                 TEXT NOT NULL,
            rule_id               TEXT NOT NULL,
            rule_class            TEXT NOT NULL,
            severity              TEXT NOT NULL,
            group_key             TEXT NOT NULL,
            isin                  TEXT,
            value                 DOUBLE PRECISION,
            reference_value       DOUBLE PRECISION,
            threshold             DOUBLE PRECISION,
            distance              DOUBLE PRECISION,
            evidence              TEXT,
            root_cause_candidate  TEXT,
            catalog_version       TEXT
        )
    """)


def _make_fund_cost_corrections(conn):
    conn.execute("""
        CREATE TABLE fund_cost_corrections (
            isin         TEXT NOT NULL,
            column_name  TEXT NOT NULL,
            old_value    DOUBLE PRECISION,
            new_value    DOUBLE PRECISION,
            reason       TEXT NOT NULL,
            evidence     TEXT,
            corrected_at TIMESTAMP NOT NULL DEFAULT now(),
            PRIMARY KEY (isin, column_name, corrected_at)
        )
    """)


def _make_fund_master_minimal(conn):
    conn.execute("""
        CREATE TABLE fund_master (
            isin TEXT PRIMARY KEY,
            fund_name TEXT,
            ongoing_charge_recurrent DOUBLE PRECISION
        )
    """)


def test_emit_statistics_splits_numeric_and_text_and_replace_semantics(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_audit_statistic(conn)

    stats = {"n_total": 100, "cv": 0.42, "cv_status": "OK", "dominant_value": "MCY"}
    n_rows = emit_statistics(conn, "run1", "cost_attributes", "GLOBAL", "vol_ann", stats, n=100)
    assert n_rows == 4

    rows = conn.execute(
        "SELECT stat_name, stat_value, stat_text FROM audit_statistic "
        "WHERE run_id='run1' ORDER BY stat_name"
    ).fetchall()
    by_name = {r[0]: (r[1], r[2]) for r in rows}
    assert by_name["n_total"] == (100.0, None)
    assert by_name["cv"] == (0.42, None)
    assert by_name["cv_status"] == (None, "OK")
    assert by_name["dominant_value"] == (None, "MCY")

    # NaN -> NULL, not a Postgres NaN literal
    emit_statistics(conn, "run1", "p2_metrics", "GLOBAL", "return_ann", {"cv": math.nan})
    value = conn.execute(
        "SELECT stat_value FROM audit_statistic WHERE domain='p2_metrics' AND stat_name='cv'"
    ).fetchone()[0]
    assert value is None

    # ON CONFLICT DO UPDATE (SQLite's INSERT OR REPLACE) on a rerun with the same PK
    emit_statistics(conn, "run1", "cost_attributes", "GLOBAL", "ACI_RHP", {"p50": 1.0})
    emit_statistics(conn, "run1", "cost_attributes", "GLOBAL", "ACI_RHP", {"p50": 2.0})
    rows = conn.execute(
        "SELECT stat_value FROM audit_statistic WHERE group_key='ACI_RHP' AND stat_name='p50'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 2.0


def test_emit_findings_writes_rows_with_missing_keys_as_null(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_audit_finding(conn)

    findings = [{
        "block": "BLOCK2", "rule_id": "R1", "rule_class": "STATISTICAL_ANOMALY",
        "severity": "WARN", "group_key": "Exit_Fee_Pct__EQUALS__Management_Fee_Pct",
    }]
    n = emit_findings(conn, "run1", "cost_attributes", findings, catalog_version="abc123def456")
    assert n == 1
    row = conn.execute(
        "SELECT rule_id, severity, isin, catalog_version FROM audit_finding WHERE run_id='run1'"
    ).fetchone()
    assert row == ("R1", "WARN", None, "abc123def456")

    assert emit_findings(conn, "run1", "cost_attributes", []) == 0


def test_preserve_and_write_preserves_before_writing_and_rolls_back_on_failure(
    pg_session_conn, pg_conn_module_schema,
):
    """preserve_and_write()'s atomicity guarantee (preservation row never exists without its
    write, or vice versa) is provided by conn.commit()/conn.rollback() spanning BOTH statements —
    that requires autocommit=False. pg_conn_module_schema runs with autocommit=True (needed for
    CREATE SCHEMA), so this test flips autocommit off for its own body and restores it to True
    before returning, matching the fixture's own documented precondition for its DROP SCHEMA
    teardown (see pg_fixtures.py's pg_conn_module_schema docstring)."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_cost_corrections(conn)
    _make_fund_master_minimal(conn)

    conn.execute(
        "INSERT INTO fund_master (isin, fund_name, ongoing_charge_recurrent) "
        "VALUES ('LU1', 'Test Fund', 0.05)"
    )
    conn.commit()
    conn.autocommit = False
    try:
        record = CorrectionRecord(
            isin="LU1", column="ongoing_charge_recurrent",
            old_value=0.05, new_value=0.015, reason="TEST-FIX",
        )

        def _apply(c):
            c.execute(
                "UPDATE fund_master SET ongoing_charge_recurrent=%s WHERE isin=%s",
                (0.015, "LU1"),
            )

        preserve_and_write(conn, record, _apply)

        preserved = conn.execute(
            "SELECT old_value, new_value, reason FROM fund_cost_corrections WHERE isin='LU1'"
        ).fetchone()
        assert preserved == (0.05, 0.015, "TEST-FIX")

        written = conn.execute(
            "SELECT ongoing_charge_recurrent FROM fund_master WHERE isin='LU1'"
        ).fetchone()[0]
        assert written == 0.015

        # A failed apply_write must roll back the preservation row too (whole-transaction
        # rollback, not a SAVEPOINT-scoped one -- preserve_and_write owns its own transaction
        # boundary).
        record2 = CorrectionRecord(
            isin="LU1", column="ongoing_charge_recurrent",
            old_value=0.015, new_value=0.02, reason="TEST-FIX-2",
        )

        def _apply_fails(c):
            raise RuntimeError("simulated write failure")

        try:
            preserve_and_write(conn, record2, _apply_fails)
            assert False, "expected RuntimeError to propagate"
        except RuntimeError:
            pass

        count = conn.execute(
            "SELECT COUNT(*) FROM fund_cost_corrections WHERE reason='TEST-FIX-2'"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.rollback()
        conn.autocommit = True


def test_clear_run_makes_repersist_idempotent(pg_session_conn, pg_conn_module_schema):
    from shared.statistical_audit.persistence import clear_run

    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_audit_statistic(conn)
    _make_audit_finding(conn)
    finding = {"block": "BLOCK2", "rule_id": "R1", "rule_class": "STATISTICAL_ANOMALY",
               "severity": "WARN", "group_key": "g"}

    for _ in range(2):
        clear_run(conn, "run1", "cost_attributes")
        emit_statistics(conn, "run1", "cost_attributes", "GLOBAL", "g", {"p50": 1.0})
        emit_findings(conn, "run1", "cost_attributes", [finding])
    emit_findings(conn, "run2", "cost_attributes", [finding])

    assert conn.execute("SELECT COUNT(*) FROM audit_finding WHERE run_id='run1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM audit_statistic WHERE run_id='run1'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM audit_finding WHERE run_id='run2'").fetchone()[0] == 1
