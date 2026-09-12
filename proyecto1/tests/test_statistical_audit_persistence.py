# proyecto1/tests/test_statistical_audit_persistence.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/persistence.py (emit_statistics,
emit_findings, preserve_and_write — funciones #15-#16,
doc/reglas/AUDITORIA_ESTADISTICA.md §4/§6).

Usa create_schema() de sqlite_writer.py (via sys.path, no import de paquete
proyecto1.*) para construir una BD en memoria con audit_statistic,
audit_finding y fund_cost_corrections reales — mismo patron que
test_data_quality_rollup.py. Cumple R-7.
"""

import math
import os
import sqlite3
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
for _p in (_CORE_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlite_writer import create_schema

from shared.statistical_audit.persistence import (
    CorrectionRecord,
    emit_findings,
    emit_statistics,
    preserve_and_write,
)


def _memory_conn():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


class TestEmitStatistics:
    def test_numeric_and_text_stats_split_correctly(self):
        conn = _memory_conn()
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

    def test_nan_stored_as_null_not_as_nan(self):
        conn = _memory_conn()
        emit_statistics(conn, "run1", "p2_metrics", "GLOBAL", "return_ann", {"cv": math.nan})
        value = conn.execute(
            "SELECT stat_value FROM audit_statistic WHERE stat_name='cv'"
        ).fetchone()[0]
        assert value is None

    def test_replace_semantics_on_rerun_same_run_id(self):
        conn = _memory_conn()
        emit_statistics(conn, "run1", "cost_attributes", "GLOBAL", "ACI_RHP", {"p50": 1.0})
        emit_statistics(conn, "run1", "cost_attributes", "GLOBAL", "ACI_RHP", {"p50": 2.0})
        rows = conn.execute(
            "SELECT stat_value FROM audit_statistic WHERE group_key='ACI_RHP' AND stat_name='p50'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 2.0


class TestEmitFindings:
    def test_writes_rows_with_missing_keys_as_null(self):
        conn = _memory_conn()
        findings = [{
            "block": "BLOCK2", "rule_id": "R1", "rule_class": "STATISTICAL_ANOMALY",
            "severity": "WARN", "group_key": "Exit_Fee_Pct__EQUALS__Management_Fee_Pct",
        }]
        n = emit_findings(conn, "run1", "cost_attributes", findings)
        assert n == 1
        row = conn.execute(
            "SELECT rule_id, severity, isin FROM audit_finding WHERE run_id='run1'"
        ).fetchone()
        assert row == ("R1", "WARN", None)

    def test_empty_findings_writes_nothing(self):
        conn = _memory_conn()
        assert emit_findings(conn, "run1", "cost_attributes", []) == 0


class TestPreserveAndWrite:
    def test_preserves_before_writing(self):
        conn = _memory_conn()
        conn.execute(
            "INSERT INTO fund_master "
            "(ISIN, Fund_Name, Fund_Nature, Heuristic_Block, Heuristic_Core, Ongoing_Charge_Recurrent) "
            "VALUES ('LU1', 'Test Fund', 'Mixtos', 'MIXTOS', 0, 0.05)"
        )
        conn.commit()

        record = CorrectionRecord(
            isin="LU1", column="Ongoing_Charge_Recurrent",
            old_value=0.05, new_value=0.015, reason="TEST-FIX",
        )

        def _apply(c):
            c.execute("UPDATE fund_master SET Ongoing_Charge_Recurrent=? WHERE ISIN=?", (0.015, "LU1"))

        preserve_and_write(conn, record, _apply)

        preserved = conn.execute(
            "SELECT Old_Value, New_Value, Reason FROM fund_cost_corrections WHERE ISIN='LU1'"
        ).fetchone()
        assert preserved == (0.05, 0.015, "TEST-FIX")

        written = conn.execute(
            "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='LU1'"
        ).fetchone()[0]
        assert written == 0.015

    def test_rolls_back_preservation_if_write_fails(self):
        conn = _memory_conn()
        record = CorrectionRecord(
            isin="LU1", column="Ongoing_Charge_Recurrent",
            old_value=0.05, new_value=0.015, reason="TEST-FIX",
        )

        def _apply(c):
            raise RuntimeError("simulated write failure")

        with pytest.raises(RuntimeError):
            preserve_and_write(conn, record, _apply)

        count = conn.execute("SELECT COUNT(*) FROM fund_cost_corrections").fetchone()[0]
        assert count == 0  # the preservation row must not survive a failed write
