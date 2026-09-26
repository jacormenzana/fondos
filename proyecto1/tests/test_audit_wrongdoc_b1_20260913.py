# proyecto1/tests/test_audit_wrongdoc_b1_20260913.py
# -*- coding: utf-8 -*-
"""
Regression test for pipelineP1P2Audit finding (2026-09-13): B1 in
audit_benchmark_consistency.py re-flagged already-known-unreliable
classifications (WRONG_DOC / stuck FORCE_REFRESH funds whose cached KIID text
is actually a wrong multi-fund document) as fresh CRITICAL findings.

Confirmed via GB00BMW6N332 (KIID_Status='WRONG_DOC') and LU2257586540
(retired, KIID_Status='FORCE_REFRESH' but cached text is an umbrella SICAV
annual report — never reached by nature-first dispatch to get the status
finalized). Fix: run_audit() now cross-references KIID_Status AND re-runs
detect_wrong_kiid_document() live against the cached text, routing hits to a
separate "B1_known_wrongdoc" bucket instead of B1_critical/B1_warn.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "tools"))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", ".."))
for _p in (_ROOT_DIR, _TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audit_benchmark_consistency import run_audit  # noqa: E402

_GENUINE_KIID = (
    "KEY INVESTOR INFORMATION\nSome Bond Fund\nObjectives and Investment Policy\n"
    "The fund invests in fixed income securities. Risk indicator: 3/7.\n" + "x" * 100
)

_UMBRELLA_ANNUAL_REPORT = (
    "Allianz Global Investors Fund\nSociete d'Investissement a Capital Variable\n"
    "R.C.S. Luxembourg Nr. B71.182\nAudited Annual Report\n30 September 2025\n"
    "- Allianz China A-Shares\n- Allianz Euro Bond\n" + "x" * 100
)


def _build_db(con) -> None:
    """Siembra las tablas REALES (db/pg) con tres fondos; la conexion es la del test (SAVEPOINT)."""
    funds = [
        # (ISIN, Fund_Nature, KIID_Status, Raw_KIID_Text)
        ("XX_LIVE_BUG",   "Renta Fija Flexible", "OK",             _GENUINE_KIID),
        ("XX_WRONGDOC",   "Renta Fija Flexible", "WRONG_DOC",      _GENUINE_KIID),
        ("XX_STUCK_FR",   "Renta Fija Flexible", "FORCE_REFRESH",  _UMBRELLA_ANNUAL_REPORT),
    ]
    for isin, nature, status, text in funds:
        con.execute(
            "INSERT INTO fund_master (ISIN, Fund_Name, Fund_Nature, Heuristic_Block, Heuristic_Core) "
            "VALUES (%s,%s,%s,'RESTANTES',0)",
            (isin, f"{isin} FUND", nature),
        )
        con.execute(
            "INSERT INTO fund_benchmarks (ISIN, source, benchmark_name, provider, "
            "asset_class, confidence, benchmark_role) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (isin, "MORNINGSTAR", "Some Equity Index", "Morningstar", "Equity",
             "HIGH", "asset_proxy"),
        )
        con.execute(
            "INSERT INTO fund_kiid_metadata (ISIN, KIID_Class, KIID_Status, "
            "Raw_KIID_Text) VALUES (%s,1,%s,%s)",
            (isin, status, text),
        )


@pytest.fixture()
def findings(pg_app_conn):
    _build_db(pg_app_conn)
    return run_audit(conn=pg_app_conn)


class TestB1KnownWrongDocBucket:
    def test_genuine_mismatch_still_flagged_critical(self, findings):
        isins = {r["ISIN"] for r in findings["exercise_B"]["B1_critical"]}
        assert "XX_LIVE_BUG" in isins

    def test_persisted_wrong_doc_not_reflagged_critical(self, findings):
        critical_isins = {r["ISIN"] for r in findings["exercise_B"]["B1_critical"]}
        warn_isins = {r["ISIN"] for r in findings["exercise_B"]["B1_warn"]}
        assert "XX_WRONGDOC" not in critical_isins
        assert "XX_WRONGDOC" not in warn_isins
        assert "XX_WRONGDOC" in {r["ISIN"] for r in findings["exercise_B"]["B1_known_wrongdoc"]}

    def test_stuck_force_refresh_detected_live(self, findings):
        """LU2257586540 case: status says FORCE_REFRESH, but the cached text is
        an umbrella annual report — must be caught by live re-detection, not
        just the persisted KIID_Status."""
        critical_isins = {r["ISIN"] for r in findings["exercise_B"]["B1_critical"]}
        assert "XX_STUCK_FR" not in critical_isins
        assert "XX_STUCK_FR" in {r["ISIN"] for r in findings["exercise_B"]["B1_known_wrongdoc"]}
