# shared/p1_quality.py
# -*- coding: utf-8 -*-
"""FND-0456: the P1 quality signals of a set of funds, for a reader that reports them (the monthly report's 1_Cartera column, behind
P3_REPORT_P1_QUALITY_ENABLED). REPORT ONLY (owner ruling 2026-11-28): nothing here gates, ranks or excludes a fund.

A fund's P1 quality is doubtful when ANY of:
  - fund_master.Data_Quality_Flag is not OK (WARN / MISSING / INFERRED);
  - fund_data_quality_issues holds a non-INFO row for it (its check codes are listed);
  - its latest P1 classification pass logged NATURE_LOW_CONFIDENCE (the newest NATURE_LOW_CONFIDENCE row is not older than its newest
    OPT_B3_DISPATCH row: both are written by the same pass, the dispatch first, so a later clean pass clears it).
"""
from __future__ import annotations

from typing import Iterable, Optional

_SQL = """
    SELECT m.ISIN,
           m.Data_Quality_Flag,
           (SELECT string_agg(DISTINCT q.check_code, ',' ORDER BY q.check_code)
              FROM fund_data_quality_issues q
             WHERE q.ISIN = m.ISIN AND q.level <> 'INFO')                                              AS issue_codes,
           (SELECT MAX(l.created_at) FROM ingestion_log l WHERE l.isin = m.ISIN AND l.step = 'NATURE_LOW_CONFIDENCE') AS low_conf_ts,
           (SELECT MAX(l.created_at) FROM ingestion_log l WHERE l.isin = m.ISIN AND l.step = 'OPT_B3_DISPATCH')        AS dispatch_ts
      FROM fund_master m
     WHERE m.ISIN = ANY(%s)
"""


def quality_note(flag: Optional[str], issue_codes: Optional[str], low_conf_ts, dispatch_ts) -> Optional[str]:
    """The note for one fund, or None when nothing is doubtful. Pure."""
    parts = []
    if flag not in (None, "OK"):
        parts.append(f"flag {flag}")
    if issue_codes:
        parts.append(f"issues {issue_codes}")
    if low_conf_ts is not None and (dispatch_ts is None or low_conf_ts >= dispatch_ts):
        parts.append("naturaleza de baja confianza")
    return "; ".join(parts) or None


def p1_quality_notes(conn, isins: Iterable[str]) -> dict:
    """{isin: note} for the funds whose P1 quality is doubtful (a fund absent from the dict is clean). Read-only."""
    isins = sorted({i for i in isins if i})
    if not isins:
        return {}
    out = {}
    for isin, flag, codes, low_ts, disp_ts in conn.execute(_SQL, (isins,)).fetchall():
        note = quality_note(flag, codes, low_ts, disp_ts)
        if note:
            out[isin] = note
    return out
