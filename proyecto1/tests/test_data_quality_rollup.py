# proyecto1/tests/test_data_quality_rollup.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de core.pipeline._finalize_data_quality_issues() y
_derive_data_quality_flag() (FIX-DQ-1, 2026-07-05).

Antes de este fix, Data_Quality_Flag se mutaba secuencialmente desde
multiples puntos de pipeline.py, cada uno con su propio guard
('== OK', '!= WARN', o sin guard alguno). Al menos un caso
(INTER_NTC_CONTRADICTION) condicionaba el propio log_ingestion al guard,
por lo que el evento se perdia por completo cuando otro chequeo anterior
ya habia tocado el flag. Estos tests verifican el reemplazo: un
acumulador de issues (check_code, dq_level, log_status, message) volcado
de forma incondicional a ingestion_log + fund_data_quality_issues, con el
rollup final calculado como el maximo de severidad (shared.config.
DATA_QUALITY_SEVERITY), no una mutacion secuencial.
"""

import os
import sys
import sqlite3

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..'))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline import _derive_data_quality_flag, _finalize_data_quality_issues
from sqlite_writer import create_schema


def _memory_conn():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# _derive_data_quality_flag (baseline sin cambios de comportamiento)
# ---------------------------------------------------------------------------

def test_derive_missing_when_srri_quality_absent():
    assert _derive_data_quality_flag({}) == "MISSING"
    assert _derive_data_quality_flag({"SRRI_Quality_Flag": "NONE"}) == "MISSING"


def test_derive_warn_on_low_conflict():
    assert _derive_data_quality_flag({"SRRI_Quality_Flag": "LOW_CONFLICT"}) == "WARN"


def test_derive_ok_otherwise():
    assert _derive_data_quality_flag({"SRRI_Quality_Flag": "HIGH"}) == "OK"


# ---------------------------------------------------------------------------
# _finalize_data_quality_issues — rollup por severidad
# ---------------------------------------------------------------------------

def test_no_issues_returns_base_level():
    conn = _memory_conn()
    result = _finalize_data_quality_issues(conn, "LU_TEST_1", "OK", [])
    assert result == "OK"


def test_single_warn_issue_upgrades_ok_base():
    conn = _memory_conn()
    issues = [("FUNDCCY_NAME_KIID_MISMATCH", "WARN", "WARN", "mismatch detectado")]
    result = _finalize_data_quality_issues(conn, "LU_TEST_2", "OK", issues)
    assert result == "WARN"


def test_inferred_does_not_downgrade_or_upgrade_past_warn():
    """WARN debe ganar sobre INFERRED, independientemente del orden de
    aparicion en la lista de issues (el rollup es un maximo, no secuencial)."""
    conn = _memory_conn()
    issues = [
        ("RC08_STRATEGY_INFERRED", "INFERRED", "INFO", "strategy inferida"),
        ("HEDGCCY_NO_MISMATCH_INCONSISTENCY", "WARN", "WARN", "hedge inconsistente"),
    ]
    result = _finalize_data_quality_issues(conn, "LU_TEST_3", "OK", issues)
    assert result == "WARN"


def test_inferred_alone_upgrades_ok_base_but_stays_below_warn():
    conn = _memory_conn()
    issues = [("RC08_STRATEGY_INFERRED", "INFERRED", "INFO", "strategy inferida")]
    result = _finalize_data_quality_issues(conn, "LU_TEST_4", "OK", issues)
    assert result == "INFERRED"


def test_missing_base_is_not_downgraded_by_a_later_inferred_issue():
    """Reproduce el caso real: SRRI_Quality_Flag ausente (MISSING) Y Strategy
    inferido desde nombre (INFERRED) en el mismo fondo -- MISSING debe
    prevalecer (misma severidad que WARN), no ser silenciosamente sustituido
    por el ultimo chequeo en ejecutarse (el bug que motivo este fix)."""
    conn = _memory_conn()
    issues = [("RC08_STRATEGY_INFERRED", "INFERRED", "INFO", "strategy inferida")]
    result = _finalize_data_quality_issues(conn, "LU_TEST_5", "MISSING", issues)
    assert result == "MISSING"


def test_multiple_concurrent_issues_all_reach_ingestion_log():
    """El bug original (INTER_NTC_CONTRADICTION) perdia el log_ingestion
    cuando otro chequeo anterior ya habia tocado el flag. Ahora TODOS los
    issues deben quedar registrados en ingestion_log, sin importar cuantos
    haya ni su orden."""
    conn = _memory_conn()
    isin = "LU_TEST_6"
    issues = [
        ("FUNDCCY_NAME_KIID_MISMATCH", "WARN", "WARN", "fundccy mismatch"),
        ("ASSETCCY_NAME_KIID_MISMATCH", "WARN", "WARN", "assetccy mismatch"),
        ("INTER_NTC_CONTRADICTION", "WARN", "WARNING", "ntc contradiction"),
    ]
    _finalize_data_quality_issues(conn, isin, "OK", issues)

    rows = conn.execute(
        "SELECT step, status, message FROM ingestion_log WHERE ISIN = ? ORDER BY id",
        (isin,),
    ).fetchall()
    assert len(rows) == 3
    steps = {r[0] for r in rows}
    assert steps == {
        "FUNDCCY_NAME_KIID_MISMATCH",
        "ASSETCCY_NAME_KIID_MISMATCH",
        "INTER_NTC_CONTRADICTION",
    }
    # log_status se preserva tal cual (vocabulario de ingestion_log, distinto
    # del dq_level) -- INTER_NTC_CONTRADICTION usaba historicamente "WARNING",
    # no "WARN".
    ntc_row = [r for r in rows if r[0] == "INTER_NTC_CONTRADICTION"][0]
    assert ntc_row[1] == "WARNING"


def test_issues_persisted_to_fund_data_quality_issues_table():
    conn = _memory_conn()
    isin = "LU_TEST_7"
    issues = [
        ("HEDGCCY_NO_MISMATCH_INCONSISTENCY", "WARN", "WARN", "hedge inconsistente"),
        ("RC08_STRATEGY_INFERRED", "INFERRED", "INFO", "strategy inferida"),
    ]
    _finalize_data_quality_issues(conn, isin, "OK", issues)

    rows = conn.execute(
        "SELECT check_code, level, message FROM fund_data_quality_issues "
        "WHERE ISIN = ? ORDER BY check_code",
        (isin,),
    ).fetchall()
    assert rows == [
        ("HEDGCCY_NO_MISMATCH_INCONSISTENCY", "WARN", "hedge inconsistente"),
        ("RC08_STRATEGY_INFERRED", "INFERRED", "strategy inferida"),
    ]


def test_fund_data_quality_issues_rebuilt_each_cycle_not_accumulated():
    """Cada ciclo debe reemplazar por completo las filas de un ISIN, no
    acumularlas -- un fondo que ya no tiene un problema resuelto en un
    ciclo posterior no debe seguir apareciendo como 'activo'."""
    conn = _memory_conn()
    isin = "LU_TEST_8"

    _finalize_data_quality_issues(
        conn, isin, "OK",
        [("FUNDCCY_NAME_KIID_MISMATCH", "WARN", "WARN", "mismatch ciclo 1")],
    )
    rows = conn.execute(
        "SELECT check_code FROM fund_data_quality_issues WHERE ISIN = ?", (isin,)
    ).fetchall()
    assert len(rows) == 1

    # Ciclo siguiente: el mismatch ya no se detecta (se corrigio el dato).
    _finalize_data_quality_issues(conn, isin, "OK", [])
    rows = conn.execute(
        "SELECT check_code FROM fund_data_quality_issues WHERE ISIN = ?", (isin,)
    ).fetchall()
    assert rows == []


def test_finalize_does_not_affect_other_isins():
    conn = _memory_conn()
    _finalize_data_quality_issues(
        conn, "LU_TEST_9A", "OK",
        [("FUNDCCY_NAME_KIID_MISMATCH", "WARN", "WARN", "mismatch A")],
    )
    _finalize_data_quality_issues(conn, "LU_TEST_9B", "OK", [])

    rows_a = conn.execute(
        "SELECT check_code FROM fund_data_quality_issues WHERE ISIN = ?", ("LU_TEST_9A",)
    ).fetchall()
    rows_b = conn.execute(
        "SELECT check_code FROM fund_data_quality_issues WHERE ISIN = ?", ("LU_TEST_9B",)
    ).fetchall()
    assert len(rows_a) == 1
    assert len(rows_b) == 0
