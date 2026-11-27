# tests/test_p1_quality_report_fnd0456.py
# -*- coding: utf-8 -*-
"""FND-0456 (P3 side, REPORT ONLY, owner ruling 2026-11-28): with P3_REPORT_P1_QUALITY_ENABLED ON the monthly report's 1_Cartera gains a last
column saying why a held fund's P1 quality is doubtful. Hermetic (R-7): a fake connection, the sheet's wiring checked on its source."""
import ast
from datetime import datetime, timedelta
from pathlib import Path

from shared import p1_quality as pq
import shared.config as cfg

T0 = datetime(2026, 11, 27, 20, 0, 0)


def test_note_lists_each_doubtful_signal_and_none_when_clean():
    assert pq.quality_note("OK", None, None, T0) is None
    assert pq.quality_note(None, None, None, None) is None
    assert pq.quality_note("WARN", None, None, T0) == "flag WARN"
    assert pq.quality_note("OK", "FEE_PAIR_GUARD,SRRI_LOW_CONFLICT", None, T0) == "issues FEE_PAIR_GUARD,SRRI_LOW_CONFLICT"
    assert pq.quality_note("WARN", None, T0 + timedelta(seconds=1), T0) == "flag WARN; naturaleza de baja confianza"


def test_low_confidence_of_an_older_pass_is_cleared_by_a_later_clean_pass():
    assert pq.quality_note("OK", None, T0, T0 + timedelta(days=30)) is None          # the latest pass dispatched without the warning
    assert pq.quality_note("OK", None, T0, None) == "naturaleza de baja confianza"


class _Conn:
    def __init__(self, rows):
        self.rows, self.args = rows, None

    def execute(self, sql, args):
        self.args = args
        return self

    def fetchall(self):
        return self.rows


def test_loader_returns_only_the_doubtful_funds():
    conn = _Conn([("A", "OK", None, None, T0), ("B", "WARN", None, None, T0), ("C", "OK", None, T0, T0)])
    assert pq.p1_quality_notes(conn, ["C", "A", "B", "A", None]) == {"B": "flag WARN", "C": "naturaleza de baja confianza"}
    assert conn.args == (["A", "B", "C"],)
    assert pq.p1_quality_notes(_Conn([]), []) == {}


def test_report_column_ships_off_and_is_wired_only_behind_the_switch():
    assert cfg.P3_REPORT_P1_QUALITY_ENABLED is False, "ships OFF (owner order 2026-11-28)"
    from proyecto3.src import monthly_report as mr
    src = Path(mr.__file__).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_build_cartera")
    body = ast.get_source_segment(src, fn)
    assert 'headers.append("Calidad P1")' in body and "if _p1q_on:" in body
    assert "p1_quality_notes(conn, [r[1] for r in rows]) if _p1q_on else {}" in body
    assert mr._PCT_COLS == {8, 9, 10, 11, 16, 17, 18}                                   # the new column is text, not a percentage
