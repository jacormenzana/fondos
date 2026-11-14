# -*- coding: utf-8 -*-
"""fee_state.py -- the entry / exit fee STATE, defined once (Package H, plan sections R6.3 and R7).

A fee state is a FUNCTION of its two numbers, never an opinion stored next to them:

    point   = Entry_Fee_Pct / Exit_Fee_Pct          a RATIO   (0.05 == 5 %)  the fee the fund applies
    ceiling = Entry_Fee_Pct_Max / Exit_Fee_Pct_Max  a PERCENT (5.0  == 5 %)  the maximum the document declares
              (units: shared/statistical_audit/catalog_cost_columns.py; the ratio-to-percent factor lives here, once)

    point set                  -> ZERO_CONFIRMED when it is 0, else EXTRACTED
    point NULL, ceiling set    -> ZERO_CONFIRMED when it is 0, else ENTRY_CONDITIONAL / EXIT_CONDITIONAL
    both NULL                  -> NOT_FOUND
    a negative number is invalid and is read as NULL (the pair writer guards it again; plan R7.1 #2)

What is NOT here yet (Slice 1 of the plan): the pair resolver (`resolve_pair`), the SQL expression of the generated columns and the
Postgres test that proves it equals `derive_fee_state` on the full truth table. This Slice 0 module is pure and nothing imports it
in production, so it changes no behaviour; the read-only baseline script and the tests use it.

It also holds the accuracy-gate arithmetic the plan fixes (R7.1 #3): a Wilson interval decision instead of a fixed count, because with
n = 30 a fixed 95 % cut fires on 2 errors (28/30 = 93.3 %) even when the true accuracy is above 95 %.
"""
from __future__ import annotations

import math
from typing import Optional

from shared.statistical_audit.tolerances import COST_PAIR_TOLERANCE_PP

RATIO_TO_PCT = 100.0                       # the point is a ratio, the ceiling an integer percent
PAIR_TOLERANCE_PP = COST_PAIR_TOLERANCE_PP  # half a unit of the 2 decimals a KID publishes (tolerances.py, class 1b)

ENTRY_STATES = ("EXTRACTED", "ZERO_CONFIRMED", "ENTRY_CONDITIONAL", "NOT_FOUND")
EXIT_STATES = ("EXTRACTED", "ZERO_CONFIRMED", "EXIT_CONDITIONAL", "NOT_FOUND")
_SIDES = {"entry": "ENTRY_CONDITIONAL", "exit": "EXIT_CONDITIONAL"}


def _valid(value: Optional[float]) -> Optional[float]:
    """The number itself when it can be a fee (not NULL, not NaN, not negative), else None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or number < 0:
        return None
    return number


def derive_fee_state(point: Optional[float], ceiling_pct: Optional[float], side: str = "entry") -> str:
    """The fee state of one side from its point (ratio) and its ceiling (percent). Pure; see the module docstring."""
    if side not in _SIDES:
        raise ValueError(f"side must be 'entry' or 'exit', got {side!r}")
    p, c = _valid(point), _valid(ceiling_pct)
    if p is not None:
        return "ZERO_CONFIRMED" if p == 0 else "EXTRACTED"
    if c is not None:
        return "ZERO_CONFIRMED" if c == 0 else _SIDES[side]
    return "NOT_FOUND"


def pair_conflict(point: Optional[float], ceiling_pct: Optional[float], tol_pp: float = PAIR_TOLERANCE_PP) -> bool:
    """True when the applied fee is above its own declared maximum (point x 100 > ceiling + tol): the pair is impossible as stored.
    Both numbers must exist (a missing one cannot conflict); a negative number is invalid and never conflicts."""
    p, c = _valid(point), _valid(ceiling_pct)
    if p is None or c is None:
        return False
    return p * RATIO_TO_PCT > c + tol_pp


# ── accuracy gate (plan R7.1 #3) ──────────────────────────────────────────────────────────────────────────────────

def wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple:
    """Wilson score interval (lower, upper) of a proportion; z = 1.959964 is the two-sided 95 %."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be within 0..n")
    p = successes / n
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / den
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / den
    lower = 0.0 if successes == 0 else max(0.0, center - half)     # exact at the edges (float error would leave 0.9999999999999999)
    upper = 1.0 if successes == n else min(1.0, center + half)
    return lower, upper


def accuracy_decision(errors: int, n: int, target: float = 0.95) -> str:
    """STOP when the 95 % interval's UPPER bound is below the target (the sample says, with confidence, that the accuracy is worse);
    REVIEW when only the point estimate is below it (every error is listed and root-caused before any write); else PASS.
    n = 30: 0-1 errors PASS, 2-3 REVIEW, 4 or more STOP. The lower bound is never the gate (n = 30 and 0 errors still give 0.887)."""
    if not 0 <= errors <= n or n <= 0:
        raise ValueError("errors must be within 0..n and n positive")
    correct = n - errors
    _lo, hi = wilson_interval(correct, n)
    if hi < target:
        return "STOP"
    if correct / n < target:
        return "REVIEW"
    return "PASS"
