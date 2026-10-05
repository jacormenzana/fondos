#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p3_freshness_check.py -- would P3 accept the data this cycle left? (read-only)

p3_build_portfolio.py refuses to score or persist (exit P3_EXIT_STALE_INPUTS) when the inputs are
outdated: the NAV of the active universe, the regime inputs, the newest harvest, or fewer than
P3_MIN_UNIFORM_METRICS_SHARE of the funds on ONE CALC_VERSION. Until now that verdict only appeared when
someone launched P3, after the cycle that caused it. This runs the very same gate
(proyecto3/src/data_freshness.py) and prints the same report, writing nothing, so
P1_P2_Complete.bat can say at the end of the cycle whether it left the data P3-ready.

Exit codes: 0 fresh | P3_EXIT_STALE_INPUTS (2) at least one input is stale | 1 the check itself failed.

Usage:
    python scripts/launch/p3_freshness_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_FAILED = 1


def verdict_rc(stale: list, stale_exit_code: int) -> int:
    """0 when nothing is stale, else the exit code P3 itself would use."""
    return stale_exit_code if stale else 0


def main() -> int:
    sys.path.insert(0, str(ROOT))
    try:
        from shared.config import P3_EXIT_STALE_INPUTS
        from shared.db import get_connection
        from proyecto3.src.data_freshness import check_universe_freshness, format_report, stale_checks
        from proyecto3.src.regime_classifier import RegimeClassifier

        conn = get_connection()
        try:
            checks = check_universe_freshness(conn, RegimeClassifier(conn))
        finally:
            conn.close()
    except Exception as exc:
        print(f"[ERROR] freshness check skipped: {type(exc).__name__}: {exc}")
        return RC_FAILED

    print(format_report(checks))
    stale = stale_checks(checks)
    if stale:
        print(f"\nP3 NO aceptaria estos datos: obsoletos = {', '.join(c.name for c in stale)}")
    else:
        print("\nP3 aceptaria estos datos (todas las entradas frescas).")
    return verdict_rc(stale, P3_EXIT_STALE_INPUTS)


if __name__ == "__main__":
    sys.exit(main())
