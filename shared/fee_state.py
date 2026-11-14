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

Also here (Slice 1): `fee_state_sql` (the generated-column expression, proven equal to `derive_fee_state` on a real Postgres by
tests/test_fee_state_pg.py) and `resolve_pair` (the per-side pair resolver the pipeline calls after a full cost pass). Everything is pure.

It also holds the accuracy-gate arithmetic the plan fixes (R7.1 #3): a Wilson interval decision instead of a fixed count, because with
n = 30 a fixed 95 % cut fires on 2 errors (28/30 = 93.3 %) even when the true accuracy is above 95 %.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
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


# ── the SQL rendering of derive_fee_state (generated columns, plan R6.3) ───────────────────────────────────────────

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def fee_state_sql(point_col: str, ceiling_col: str, side: str = "entry") -> str:
    """The SQL expression equal to `derive_fee_state` (the generated-column definition). A Postgres test proves the equality on the full
    truth table, negative, NaN and infinite values included (tests/test_fee_state_pg.py): PG orders NaN above every number, so the NaN guard
    is explicit. Identifiers are checked, never quoted from outside."""
    if side not in _SIDES:
        raise ValueError(f"side must be 'entry' or 'exit', got {side!r}")
    for name in (point_col, ceiling_col):
        if not _IDENT_RE.match(name):
            raise ValueError(f"not a plain lower-case column name: {name!r}")
    p, c, cond = point_col, ceiling_col, _SIDES[side]
    ok_p = f"{p} IS NOT NULL AND {p} >= 0 AND {p} <> 'NaN'::float8"
    ok_c = f"{c} IS NOT NULL AND {c} >= 0 AND {c} <> 'NaN'::float8"
    return (f"CASE WHEN {ok_p} THEN (CASE WHEN {p} = 0 THEN 'ZERO_CONFIRMED' ELSE 'EXTRACTED' END) "
            f"WHEN {ok_c} THEN (CASE WHEN {c} = 0 THEN 'ZERO_CONFIRMED' ELSE '{cond}' END) "
            f"ELSE 'NOT_FOUND' END")


# ── the pair resolver (plan R6.4, R7.1 #2) ────────────────────────────────────────────────────────────────────────

WARN_CONFLICT = "FEE_PAIR_CONFLICT"
WARN_STORED_POINT_CLEARED = "FEE_STORED_POINT_CLEARED"
WARN_NEGATIVE = "FEE_NEGATIVE_INVALID"


@dataclass(frozen=True)
class PairEvidence:
    """What THIS run found for one side. point and conditional value are RATIOS as the parser emits them; the ceiling is a PERCENT.
    parser_point: the parser's point (0.0 for an explicit 'we do not charge'); negation: the text negates the fee (the extractor's
    FIX-FEE-NEGATION pattern); parser_conditional: the parser's own value when it read a 'up to / maximum' fee (today thrown away);
    extractor_ceiling_pct: the cost extractor's ceiling (None when it emitted none)."""
    parser_point: Optional[float] = None
    negation: bool = False
    parser_conditional: Optional[float] = None
    extractor_ceiling_pct: Optional[float] = None


@dataclass(frozen=True)
class PairResult:
    point: Optional[float]
    ceiling_pct: Optional[float]
    source: str                     # "this_run" (re-derived from the evidence) or "stored" (no evidence at all: kept whole)
    warnings: tuple = ()


def resolve_pair(evidence: PairEvidence, stored_point: Optional[float], stored_ceiling_pct: Optional[float],
                 same_document: Optional[bool] = None, tol_pp: float = PAIR_TOLERANCE_PP) -> PairResult:
    """One side's (point, ceiling) after a FULL cost pass. Pure. The caller (the pipeline) only calls it when both the parser and the
    extractor ran (`full_cost_pass`), and once per side, so evidence on one side never touches the other.

    No evidence at all (no point, no negation, no ceiling): the stored pair is kept whole (P#1). Otherwise the pair is re-derived from THIS
    run: point = (1) the parser's point, else (2) 0 on an explicit negation (even beside a positive ceiling: 'none now, up to 5 %'),
    else (3) the stored point when the current ceiling corroborates it (point x 100 = ceiling within tol_pp), else (4) NULL, with a
    warning carrying both values when a stored point is cleared while the document is not known to have changed. The ceiling is the
    extractor's, else the parser's conditional value x 100, else NULL (a stale ceiling cannot survive next to current evidence).
    An applied fee above its own maximum is kept as it is and reported (FEE_PAIR_CONFLICT): no number can arbitrate two readers."""
    warnings: list = []

    def clean(value, name):
        if value is None:
            return None
        ok = _valid(value)
        if ok is None:
            warnings.append(f"{WARN_NEGATIVE}: {name}={value!r} is not a fee (negative or not a number); read as NULL")
        return ok

    parser_point = clean(evidence.parser_point, "parser point")
    conditional = clean(evidence.parser_conditional, "parser conditional value")
    extractor = clean(evidence.extractor_ceiling_pct, "extractor ceiling")
    s_point = clean(stored_point, "stored point")
    s_ceiling = clean(stored_ceiling_pct, "stored ceiling")
    ceiling = extractor if extractor is not None else (None if conditional is None else conditional * RATIO_TO_PCT)

    if parser_point is None and not evidence.negation and ceiling is None:
        return PairResult(s_point, s_ceiling, "stored", tuple(warnings))

    if parser_point is not None:
        point = parser_point
    elif evidence.negation:
        point = 0.0
    elif s_point is not None and ceiling is not None and abs(s_point * RATIO_TO_PCT - ceiling) <= tol_pp:
        point = s_point
    else:
        point = None
        if s_point is not None and same_document is not False:
            warnings.append(f"{WARN_STORED_POINT_CLEARED}: stored point {s_point!r} (stored ceiling {s_ceiling!r}) was not found again; "
                            f"this run has ceiling {ceiling!r}")
    if pair_conflict(point, ceiling, tol_pp):
        warnings.append(f"{WARN_CONFLICT}: point {point!r} x 100 is above the ceiling {ceiling!r}; both kept")
    return PairResult(point, ceiling, "this_run", tuple(warnings))


# ── the pipeline's one call: reconcile both sides of a record (plan R6.4) ─────────────────────────────────────────

PAIR_COLUMNS = {"entry": ("Entry_Fee_Pct", "Entry_Fee_Pct_Max"), "exit": ("Exit_Fee_Pct", "Exit_Fee_Pct_Max")}
CONDITIONAL_KEYS = {"entry": "Entry_Fee_Conditional", "exit": "Exit_Fee_Conditional"}
MAX_CEILING_PCT = 25.0                      # the schema CHECK of entry_fee_pct_max / exit_fee_pct_max (db/pg/20_silver.sql): above it the whole UPSERT would abort
RESOLVED_KEY = "_fee_pairs_resolved"        # {"entry": bool, "exit": bool}: which sides the writer must overwrite as a unit
WARN_CEILING_RANGE = "FEE_CEILING_OUT_OF_RANGE"
# DQ level per warning. A cleared stale point is an AUTO-CORRECTION (INFO, as every successful auto-correction in the semantic rules), so a first pass
# over hundreds of funds does not flip their Data_Quality_Flag; a real inconsistency (conflict, invalid or out-of-range number) is a WARN.
DQ_LEVEL = {WARN_STORED_POINT_CLEARED: "INFO", WARN_CONFLICT: "WARN", WARN_NEGATIVE: "WARN", WARN_CEILING_RANGE: "WARN"}


def reconcile_fee_pairs(record: dict, parsed: dict, stored: Optional[dict], *, full_cost_pass: bool, negation: Optional[dict] = None,
                        same_document: Optional[bool] = None) -> list:
    """Resolve the entry and the exit pair of `record` IN PLACE and return the DQ tuples (check_code, dq_level, log_status, message). Pure: no database.

    `parsed`: the parser's output (Entry_Fee_Pct, Exit_Fee_Pct, Entry_Fee_Conditional, Exit_Fee_Conditional). `record`: carries the extractor's ceilings when the
    extractor emitted them. `stored`: the stored (point, ceiling) of the four columns, or None for a fund with no row yet. `negation`: {"entry": bool, "exit": bool}.

    Without a full cost pass, and with a stored row, the pair columns are REMOVED from the record and the stored pair stays whole (the parser runs on every pass,
    the extractor only with a PDF or --recompute-costs: a point without its extractor would erase stored ceilings). A new fund always gets its pair derived. On a
    side with no evidence at all the stored pair also stays whole. Every side that is resolved is marked in record[RESOLVED_KEY] so the writer overwrites it as a unit."""
    negation = negation or {}
    resolved: dict = {}
    issues: list = []
    for side, (point_col, ceiling_col) in PAIR_COLUMNS.items():
        if stored is not None and not full_cost_pass:
            record.pop(point_col, None)
            record.pop(ceiling_col, None)
            continue
        evidence = PairEvidence(parser_point=parsed.get(point_col), negation=bool(negation.get(side)),
                                parser_conditional=parsed.get(CONDITIONAL_KEYS[side]), extractor_ceiling_pct=record.get(ceiling_col))
        result = resolve_pair(evidence, None if stored is None else stored.get(point_col), None if stored is None else stored.get(ceiling_col), same_document)
        if result.source == "stored" and stored is not None:
            record.pop(point_col, None)
            record.pop(ceiling_col, None)
            issues.extend(_tuples(side, result.warnings))
            continue
        ceiling = result.ceiling_pct
        warnings = list(result.warnings)
        if ceiling is not None and ceiling > MAX_CEILING_PCT:
            warnings.append(f"{WARN_CEILING_RANGE}: ceiling {ceiling!r} is above the {MAX_CEILING_PCT:g} % schema bound; read as NULL")
            ceiling = None
        record[point_col], record[ceiling_col] = result.point, ceiling
        resolved[side] = True
        issues.extend(_tuples(side, warnings))
    if resolved:
        record[RESOLVED_KEY] = resolved
    return issues


def _tuples(side: str, warnings) -> list:
    out = []
    for warning in warnings:
        code = warning.split(":", 1)[0]
        level = DQ_LEVEL.get(code, "WARN")
        # fund_data_quality_issues is unique on (isin, check_code): both sides of one fund can emit the same warning, so the code carries the side
        out.append((f"{code}_{side.upper()}", level, level, f"{side} fee: {warning}"))
    return out


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
