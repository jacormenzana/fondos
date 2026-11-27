# proyecto1/core/dq_signals.py
# -*- coding: utf-8 -*-
"""FND-0456 (P1 side): the two P1 quality signals that reached no fund_data_quality_issues row.

  - a low-confidence Fund_Nature (resolve_nature_evidence below _NATURE_CONF_THRESHOLD) was written only to ingestion_log;
  - the SRRI LOW_CONFLICT base level turned Data_Quality_Flag to WARN with no detail row, against the SCHEMA_REFERENCE contract that the
    detail of a WARN lives in fund_data_quality_issues (69 of 78 active WARN funds on 2026-11-27).

With P1_DQ_SIGNAL_DETAIL_ROWS_ENABLED ON (shared/config.py; OFF until the R6 verdict, owner order 2026-11-28) pipeline.py appends these
issues to the fund's _dq_issues, so _finalize_data_quality_issues writes the rows and folds them into Data_Quality_Flag. Pure helpers.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

from typing import Optional

NATURE_LOW_CONFIDENCE_CODE = "NATURE_LOW_CONFIDENCE"
SRRI_LOW_CONFLICT_CODE = "SRRI_LOW_CONFLICT"


def detail_rows_on(enabled: Optional[bool] = None) -> bool:
    """The FND-0456 switch, read at call time (a config that cannot be imported = OFF). Pure when `enabled` is given."""
    if enabled is not None:
        return bool(enabled)
    try:
        from shared import config as _cfg
    except ImportError:
        return False
    return bool(getattr(_cfg, "P1_DQ_SIGNAL_DETAIL_ROWS_ENABLED", False))


def nature_low_confidence_issue(message: str) -> tuple:
    """The issue tuple (check_code, dq_level, log_status, message) of a low-confidence nature: WARN, as its ingestion_log row already was."""
    return (NATURE_LOW_CONFIDENCE_CODE, "WARN", "WARN", message)


def base_level_issues(parsed: Optional[dict]) -> list:
    """The detail rows of the SRRI base level of Data_Quality_Flag (pipeline._derive_data_quality_flag): one WARN row for LOW_CONFLICT (the
    visual and textual SRRI disagree). MISSING (no SRRI) already has its own trail and is not duplicated here."""
    parsed = parsed or {}
    if parsed.get("SRRI_Quality_Flag") != "LOW_CONFLICT":
        return []
    vis, txt = parsed.get("SRRI_Visual"), parsed.get("SRRI_Textual")
    return [(SRRI_LOW_CONFLICT_CODE, "WARN", "WARN",
             f"SRRI visual={vis} y textual={txt} en conflicto (SRRI_Quality_Flag=LOW_CONFLICT) -> Data_Quality_Flag=WARN")]
