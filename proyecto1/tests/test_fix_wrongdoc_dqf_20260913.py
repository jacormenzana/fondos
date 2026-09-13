# proyecto1/tests/test_fix_wrongdoc_dqf_20260913.py
# -*- coding: utf-8 -*-
"""
Regression test for FIX-WRONGDOC-DQF (pipeline.py, 2026-09-13), found during
pipelineP1P2Audit's Reliability Control 1 check.

Root cause: both WRONG_DOC-setting code paths in pipeline.py (the REMOTE/LOCAL
re-download confirmation branch, and the FIX-WRONGDOC-STUCK-FR escalation for
FORCE_REFRESH funds with no download links) updated only
fund_kiid_metadata.KIID_Status='WRONG_DOC' — publish_fund is then skipped
(`continue`), so fund_master.Data_Quality_Flag never got touched and kept
whatever value it had before (found in production: 4 funds stuck at
'MISSING', never 'WARN' — LU0193173076/159/233, LU0399027613). Per the skill's
Reliability Control 1, any WRONG_DOC fund with Data_Quality_Flag != 'WARN' is
silently stale to P2/P3 (looks like a normal, DQ-clean fund).

Fix: both branches now also run
`UPDATE fund_master SET Data_Quality_Flag='WARN' WHERE ISIN=?` in the same
`with conn:` transaction as the KIID_Status update.

This test mirrors the exact two UPDATE statements (not the surrounding
control flow) against a throwaway on-disk sqlite DB, and asserts the RC1
invariant afterward. R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import sqlite3


def _apply_wrong_doc_fix(conn: sqlite3.Connection, isin: str) -> None:
    """Mirrors the exact statements pipeline.py now runs in its WRONG_DOC
    branches (both the REMOTE/LOCAL confirmation and the stuck-FR escalation)."""
    with conn:
        conn.execute(
            "UPDATE fund_kiid_metadata SET KIID_Status='WRONG_DOC' "
            "WHERE ISIN=? AND KIID_Class=1", (isin,)
        )
        conn.execute(
            "UPDATE fund_master SET Data_Quality_Flag='WARN' WHERE ISIN=?",
            (isin,)
        )


def _build_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE fund_master (ISIN TEXT PRIMARY KEY, Data_Quality_Flag TEXT);"
        "CREATE TABLE fund_kiid_metadata (ISIN TEXT, KIID_Class INTEGER, KIID_Status TEXT);"
    )
    return con


class TestWrongDocSetsDataQualityFlag:
    def test_wrong_doc_escalation_sets_warn(self, tmp_path):
        db_path = str(tmp_path / "test.sqlite")
        con = _build_db(db_path)
        con.execute(
            "INSERT INTO fund_master (ISIN, Data_Quality_Flag) VALUES ('XX1', 'MISSING')"
        )
        con.execute(
            "INSERT INTO fund_kiid_metadata (ISIN, KIID_Class, KIID_Status) "
            "VALUES ('XX1', 1, 'FORCE_REFRESH')"
        )
        con.commit()

        _apply_wrong_doc_fix(con, "XX1")

        row = con.execute(
            "SELECT Data_Quality_Flag FROM fund_master WHERE ISIN='XX1'"
        ).fetchone()
        assert row[0] == "WARN", (
            "Data_Quality_Flag must become WARN the moment KIID_Status "
            "becomes WRONG_DOC — RC1: anything else is silently stale to P2/P3"
        )
        status = con.execute(
            "SELECT KIID_Status FROM fund_kiid_metadata WHERE ISIN='XX1'"
        ).fetchone()
        assert status[0] == "WRONG_DOC"

    def test_rc1_invariant_no_wrong_doc_without_warn(self, tmp_path):
        """The exact RC1 query from the skill must return 0 after the fix."""
        db_path = str(tmp_path / "test2.sqlite")
        con = _build_db(db_path)
        for isin, dqf in (("A", "MISSING"), ("B", "WARN"), ("C", None)):
            con.execute(
                "INSERT INTO fund_master (ISIN, Data_Quality_Flag) VALUES (?, ?)",
                (isin, dqf),
            )
            con.execute(
                "INSERT INTO fund_kiid_metadata (ISIN, KIID_Class, KIID_Status) "
                "VALUES (?, 1, 'OK')",
                (isin,),
            )
        con.commit()

        for isin in ("A", "B", "C"):
            _apply_wrong_doc_fix(con, isin)

        violations = con.execute(
            "SELECT COUNT(*) FROM fund_master fm "
            "JOIN fund_kiid_metadata km ON fm.ISIN=km.ISIN AND km.KIID_Class=1 "
            "WHERE km.KIID_Status='WRONG_DOC' "
            "AND (fm.Data_Quality_Flag IS NULL OR fm.Data_Quality_Flag != 'WARN')"
        ).fetchone()[0]
        assert violations == 0
