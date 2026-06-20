# proyecto1/tests/test_cost_oc_mismatch.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de sqlite_writer.correct_oc_aci_mismatch.

BL-COST-4d (Sprint 2 S2-C). Verifica la ruta de escritura no-COALESCE
que BL-COST-5 usará para corregir fondos con OC=ACI@RHP en BD.
"""

import sqlite3
import pytest


def _make_conn():
    """Crea una BD en memoria con la columna necesaria."""
    conn = sqlite3.connect(":memory:")
    conn.isolation_level = None
    conn.execute("""
        CREATE TABLE fund_master (
            ISIN TEXT PRIMARY KEY,
            Ongoing_Charge_Recurrent REAL,
            Updated_At TEXT
        )
    """)
    conn.execute("INSERT INTO fund_master VALUES ('TEST0001', 2.4, '2026-01-01')")
    return conn


def test_correct_oc_updates_value():
    """El valor se sobrescribe directamente (sin COALESCE)."""
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    result = correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)
    assert result is True
    row = conn.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0001'"
    ).fetchone()
    assert abs(row[0] - 0.70) < 0.001


def test_correct_oc_returns_false_for_missing_isin():
    """ISIN inexistente → retorna False, no lanza excepción."""
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    result = correct_oc_aci_mismatch(conn, 'NONEXIST', ter_pct=0.50)
    assert result is False


def test_correct_oc_does_not_touch_other_isins():
    """Solo modifica el ISIN solicitado; otros registros no se alteran."""
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    conn.execute("INSERT INTO fund_master VALUES ('TEST0002', 1.5, '2026-01-01')")
    correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)
    row2 = conn.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0002'"
    ).fetchone()
    assert abs(row2[0] - 1.5) < 0.001   # TEST0002 no debe haber cambiado


def test_correct_oc_updated_at_is_populated():
    """Updated_At se rellena como ISO string tras la corrección."""
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)
    row = conn.execute(
        "SELECT Updated_At FROM fund_master WHERE ISIN='TEST0001'"
    ).fetchone()
    assert row[0] != '2026-01-01'   # debe haber cambiado respecto al valor original
    assert len(row[0]) >= 10        # al menos "YYYY-MM-DD"
