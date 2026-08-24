# proyecto1/tests/test_cost_oc_mismatch.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de sqlite_writer.correct_oc_aci_mismatch.

BL-COST-4d (Sprint 2 S2-C). Verifica la ruta de escritura no-COALESCE
que BL-COST-5 usará para corregir fondos con OC=ACI@RHP en BD.
"""

import os
import sys
import sqlite3
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)


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
    """El valor se sobrescribe directamente (sin COALESCE), en escala RATIO.

    FIX-OC-SCALE (2026-08-24): esta prueba afirmaba `ter_pct=0.70 -> 0.70`, es
    decir un gasto corriente del 70 %. `ter_pct` es porcentaje entero, pero la
    columna `Ongoing_Charge_Recurrent` está en ratio decimal, así que el valor
    correcto es 0,007. Evidencia de la escala: 2.233 fondos activos cumplen
    `Ongoing_Charge_Recurrent*100 == Management_Fee_Pct`, relación que solo se
    sostiene si OC es ratio y la comisión de gestión porcentaje entero.
    """
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()
    result = correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)
    assert result is True
    row = conn.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0001'"
    ).fetchone()
    assert row[0] == pytest.approx(0.0070)


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

    Scenario: fund has the ACI in DB as OC (the contaminated state).
    Parser emits a management-component value (0,0063 ratio) via the COALESCE UPSERT.
    The extractor then repairs OC to the reconstructed TER (0,70 % -> 0,007 ratio).
    Correct order: UPSERT → repair. Final DB value must be the repaired one.

    FIX-OC-SCALE (2026-08-24): el escenario se expresa ahora en las escalas
    reales de cada frontera — el parser emite RATIO, `ter_pct` entra en
    PORCENTAJE y se almacena como RATIO. La intención de la prueba (el orden de
    escritura: la reparación debe ganar al UPSERT) no cambia.
    """
    from sqlite_writer import correct_oc_aci_mismatch
    conn = _make_conn()  # fund starts with OC=2.4 (the ACI, contaminated)

    # Step 1 — COALESCE UPSERT (publish_fund equivalent)
    # Parser found management component = 0.0063 (ratio); non-NULL incoming
    # overwrites via COALESCE.
    _coalesce_upsert(conn, 'TEST0001', new_oc=0.0063)

    # Step 2 — no-COALESCE repair (correct_oc_aci_mismatch, must be AFTER UPSERT)
    correct_oc_aci_mismatch(conn, 'TEST0001', ter_pct=0.70)

    row = conn.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN='TEST0001'"
    ).fetchone()
    assert row[0] == pytest.approx(0.0070), (
        f"Repair must win; got {row[0]} (expected 0.0070 ratio from correct_oc_aci_mismatch)"
    )
    assert row[0] != pytest.approx(0.0063), "El UPSERT no debe sobrevivir a la reparación"


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
