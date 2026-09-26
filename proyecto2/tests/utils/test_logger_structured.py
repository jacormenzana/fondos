# proyecto2/tests/utils/test_logger_structured.py
# -*- coding: utf-8 -*-
"""
FND-0067: _StructuredFmt must not drop the traceback of a per-fund failure.

R-7: imports ONLY utils.logger — no pipeline.py, no core.io, no DB.
"""

import logging
import sys
import traceback
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.logger import _StructuredFmt  # noqa: E402


def _record(msg, exc_info=None, **extra):
    rec = logging.LogRecord("t", logging.ERROR, __file__, 1, msg, None, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def _boom():
    raise ValueError("the query has 0 placeholders but 2 parameters were passed")


def test_fund_err_traceback_message_is_appended_under_the_line():
    # run_pipeline.py logs: logger.error(traceback.format_exc(), extra=dict(p2_evt="FUND-ERR", ...))
    try:
        _boom()
    except ValueError as exc:
        tb = traceback.format_exc()
        rec = _record(tb, p2_evt="FUND-ERR", p2_detail=str(exc), p2_isin="LU0000000001")
    out = _StructuredFmt("run1").format(rec)
    first, *rest = out.split("\n")
    assert "| FUND-ERR |" in first and "LU0000000001" in first
    body = "\n".join(rest)
    assert "Traceback (most recent call last)" in body
    assert "_boom" in body                      # file/line/function survives
    assert all(ln.startswith("    ") for ln in rest)


def test_exc_info_traceback_is_appended():
    try:
        _boom()
    except ValueError:
        rec = _record("failed", exc_info=sys.exc_info())
    out = _StructuredFmt("run1").format(rec)
    assert "\n    Traceback (most recent call last)" in out
    assert "ValueError" in out


def test_plain_record_stays_a_single_line():
    rec = _record("run started", p2_evt="RUN-START", p2_detail="x")
    out = _StructuredFmt("run1").format(rec)
    assert "\n" not in out
    assert "| RUN-START | x |" in out


def test_non_traceback_message_with_p2_evt_is_not_duplicated():
    # a p2_evt record whose message is ordinary text must keep the fixed one-line layout
    rec = _record("ordinary text", p2_evt="FUND-OK", p2_detail="d")
    assert "\n" not in _StructuredFmt("run1").format(rec)
