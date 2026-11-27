# proyecto1/tests/test_dq_signals_fnd0456.py
# -*- coding: utf-8 -*-
"""FND-0456 (P1 side): a low-confidence nature and the SRRI LOW_CONFLICT base level become fund_data_quality_issues rows while
P1_DQ_SIGNAL_DETAIL_ROWS_ENABLED is ON (OFF until the R6 verdict, owner order 2026-11-28).

R-7: no imports of pipeline.py or core.io (pipeline.py's wiring is checked on its source text).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import dq_signals as ds  # noqa: E402


def test_switch_ships_off_and_is_read_at_call_time(monkeypatch):
    import shared.config as cfg
    assert cfg.P1_DQ_SIGNAL_DETAIL_ROWS_ENABLED is False, "ships OFF (selection freeze until the R6 verdict)"
    assert ds.detail_rows_on() is False
    monkeypatch.setattr(cfg, "P1_DQ_SIGNAL_DETAIL_ROWS_ENABLED", True, raising=False)
    assert ds.detail_rows_on() is True
    assert ds.detail_rows_on(False) is False and ds.detail_rows_on(True) is True


def test_low_confidence_nature_is_a_warn_issue_with_its_own_message():
    code, level, log_status, msg = ds.nature_low_confidence_issue("[OPT-B3] Fund_Nature=Mixtos con confianza baja (0.4 < 0.6): x")
    assert (code, level, log_status) == ("NATURE_LOW_CONFIDENCE", "WARN", "WARN") and "confianza baja" in msg


def test_srri_low_conflict_gets_one_detail_row_and_nothing_else_does():
    rows = ds.base_level_issues({"SRRI_Quality_Flag": "LOW_CONFLICT", "SRRI_Visual": 4, "SRRI_Textual": 3})
    assert len(rows) == 1 and rows[0][:3] == ("SRRI_LOW_CONFLICT", "WARN", "WARN") and "visual=4" in rows[0][3] and "textual=3" in rows[0][3]
    for flag in (None, "NONE", "HIGH", "OK"):
        assert ds.base_level_issues({"SRRI_Quality_Flag": flag}) == []
    assert ds.base_level_issues(None) == []


def test_pipeline_keeps_the_old_log_row_when_the_switch_is_off():
    src = (Path(_CORE_DIR) / "pipeline.py").read_text(encoding="utf-8")
    assert "_dq_signals.detail_rows_on()" in src
    assert 'log_ingestion(conn, isin, "NATURE_LOW_CONFIDENCE", "WARN", _nlc_msg)' in src     # the OFF branch
    assert "_dq_issues.extend(_dq_signals.base_level_issues(parsed))" in src
    assert "_dq_issues.append(_dq_signals.nature_low_confidence_issue(_nature_low_conf_msg))" in src
