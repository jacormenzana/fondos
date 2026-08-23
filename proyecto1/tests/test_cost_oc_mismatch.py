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


# ─── FIX-OC-WRITE-ORDER regression (2026-08-23) ──────────────────────────────

def _coalesce_upsert(conn, isin, new_oc):
    """Simulates publish_fund's COALESCE(excluded.col, col) UPSERT for OC."""
    conn.execute("""
        INSERT INTO fund_master (ISIN, Ongoing_Charge_Recurrent, Updated_At)
        VALUES (?, ?, '2026-08-23')
        ON CONFLICT(ISIN) DO UPDATE
        SET Ongoing_Charge_Recurrent = COALESCE(excluded.Ongoing_Charge_Recurrent,
                                                 Ongoing_Charge_Recurrent),
            Updated_At = excluded.Updated_At
    """, (isin, new_oc))


def test_write_order_correction_survives_coalesce_upsert():
    """
    FIX-OC-WRITE-ORDER: correct_oc_aci_mismatch must run AFTER publish_fund.

    Scenario: fund has ACI value (2.4) in DB as OC (the contaminated state).
    Parser emits a management-component value (0.63) via the COALESCE UPSERT.
    The extractor then repairs OC to the reconstructed TER (0.70).
    Correct order: UPSERT → repair. Final DB value must be 0.70.
    """
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()  # fund starts with OC=2.4 (the ACI, contaminated)

    # Step 1 — COALESCE UPSERT (publish_fund equivalent)
    # Parser found management component = 0.63; non-NULL incoming overwrites via COALESCE.
    _coalesce_upsert(conn, 'TEST0001', new_oc=0.63)

    # Step 2 — no-COALESCE repair (correct_oc_aci_mismatch, must be AFTER UPSERT)
    correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)

    row = conn.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0001'"
    ).fetchone()
    assert abs(row[0] - 0.70) < 0.001, (
        f"Repair must win; got {row[0]} (expected 0.70 from correct_oc_aci_mismatch)"
    )


def test_wrong_write_order_demonstrates_bug():
    """
    Documents the pre-fix bug: correction BEFORE UPSERT gets overwritten.

    This test intentionally shows that running the repair before publish_fund
    causes the COALESCE UPSERT to clobber the correction.
    Kept as documentation; NOT the production path (see FIX-OC-WRITE-ORDER).
    """
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()  # OC=2.4 (contaminated)

    # Step 1 — repair fires first (OLD, wrong order)
    correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)

    # Step 2 — COALESCE UPSERT fires after; non-NULL incoming overwrites the repair
    _coalesce_upsert(conn, 'TEST0001', new_oc=0.63)

    row = conn.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0001'"
    ).fetchone()
    # The repair (0.70) was lost; COALESCE took the parser's 0.63 instead.
    assert abs(row[0] - 0.63) < 0.001, (
        "This test documents the pre-fix bug: correction before UPSERT is clobbered"
    )
