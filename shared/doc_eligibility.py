# shared/doc_eligibility.py
# -*- coding: utf-8 -*-
"""
FND-0323 D1 -- ONE definition of "this ISIN has no valid KIID document" for P3 eligibility.

An ISIN whose `fund_kiid_metadata.kiid_status` is WRONG_DOC has no usable document, so the attributes that only the KIID can give cannot be
trusted for it. P1 already drops those ISINs from every block (proyecto1/core/pipeline.py); with `P3_WRONG_DOC_EXCLUSION_ENABLED` the live scorer
(`proyecto3/src/fund_scorer`) and the PIT backtest (`proyecto3/src/pit_inputs.load_attributes`) both read the SAME reason from here and let the
scorer's own `check_hard_filters` apply it (P#11: no second implementation of the rule).

Read-only, no DDL. Pure apart from the one query.
"""
from __future__ import annotations

from typing import Dict

WRONG_DOC_STATUS = "WRONG_DOC"
REASON_CODE = "wrong_doc"                                   # prefix of fund_scores.exclusion_reason, like nav_stale / nav_gap / accepted_residual
REASON_TEXT = f"{REASON_CODE}: KIID_Status={WRONG_DOC_STATUS} (no valid KIID document)"


def wrong_doc_reasons(conn) -> Dict[str, str]:
    """{isin: exclusion reason} for every ISIN with a WRONG_DOC row in fund_kiid_metadata (one row per ISIN today; EXISTS-style DISTINCT keeps the
    answer right if a second row ever appears). One static statement."""
    rows = conn.execute("SELECT DISTINCT isin FROM fund_kiid_metadata WHERE kiid_status = 'WRONG_DOC'").fetchall()
    return {r[0]: REASON_TEXT for r in rows if r[0]}
