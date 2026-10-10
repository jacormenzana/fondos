#!/usr/bin/env python
"""scripts/audit/verify_fund_currency.py — FND-0243 release gate (read-only).

Exit 0 when every active fund's stored Fund_Currency equals what a P1 pass would persist now (shared/fund_currency_gate.py);
exit 1 with the full list when some still differ -- run the P1 pass (P1_discoverAllFunds.bat, owner) first. The EUR view
(EUR_NAV_CONVERSION_ENABLED + FX_CONTRIBUTION_EUR_VIEW_ENABLED) may be switched on only after this exits 0. Exit 4 = DB unreadable.

    C:\\data\\envs\\des\\python.exe -X utf8 scripts\\audit\\verify_fund_currency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

RC_OK, RC_PENDING, RC_DB = 0, 1, 4


def main(argv=None) -> int:
    try:
        from shared.db import get_connection
        from shared.fund_currency_gate import pending_fund_currency, summarize
        conn = get_connection()
        try:
            pending, unknown = pending_fund_currency(conn)
        finally:
            conn.rollback()
            conn.close()
    except Exception as exc:                                     # a gate that cannot read is not a pass
        print(f"[ERROR] fund-currency gate unreadable: {type(exc).__name__}: {exc}")
        return RC_DB
    print(summarize(pending, unknown))
    for isin, name, stored, resolved in pending:
        print(f"    {isin}  {stored or 'NULL':>4} -> {resolved:<4}  {name}")
    if unknown:
        print("unknown class currency (excluded from EUR scoring): " + ", ".join(unknown))
    if pending:
        print("\nGATE: FAIL -- run the P1 pass before enabling EUR_NAV_CONVERSION_ENABLED / FX_CONTRIBUTION_EUR_VIEW_ENABLED.")
        return RC_PENDING
    print("\nGATE: PASS -- stored Fund_Currency is current; the EUR view may be enabled.")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
