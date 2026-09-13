"""Named, audited tolerance constants for the statistical audit catalogs
(catalog_invariants.py, catalog_pairs.py).

Centralizing here closes a live P#11/R-1 gap: catalog_invariants.py already
carried a comment citing "ROUNDING_TOLERANCE_PP=0.06" as if it were a named
constant, but no such constant existed in code — both cost-schedule rules
had it typed as a raw literal. Every value below was chosen from a live-DB
tolerance sweep (2026-09-13, doc/reglas/AUDITORIA_ESTADISTICA.md §2.7), not
from convention, and each docstring records the sweep evidence so a future
change is a re-measurement, not a guess.

Three distinct tolerance *classes* live here — never conflate them by
reusing one constant across classes it wasn't measured for:

  1. Published-precision (KID_ROUNDING_TOLERANCE_PP) — comparing a
     regulator-rounded published value against a computed one.
  2. Float-identity (FLOAT_IDENTITY_TOLERANCE) — comparing two raw computed
     floats that are either the SAME number by construction/defect or
     genuinely different by design; there is no publication rounding step
     to accommodate, so the constant only needs to absorb ordinary
     floating-point noise, not a real-world rounding grid.
  3. Derived-quantity (IPC_ELIGIBILITY_FLOOR) — a tolerance whose correct
     value is tied to another domain constant already in the codebase
     (here, the same IPC floor that gates REAL_EQUALS_NOMINAL eligibility),
     not to an unrelated bare epsilon.

Rules deliberately NOT centralized here (each needs its own dedicated
sweep/decision before touching):
  - FROZEN_NAV_ZERO_VOL (catalog_invariants.py) uses 0.0001 as a periodic
    *return-variance* floor — different physical units (squared returns)
    from every constant above; aliasing it to FLOAT_IDENTITY_TOLERANCE
    would be a coincidence-driven rename, not a justified one.
  - CAPTURE_UP_EQUALS_DOWN (catalog_pairs.py) uses 0.001, already distinct
    from the 0.0001 rules audited here; left untouched pending its own
    measurement.
  - SCALAR_EQUALS_TIMESERIES (catalog_pairs.py) is NOT resolved by any
    single absolute constant — see the module docstring in catalog_pairs.py
    and AUDITORIA_ESTADISTICA.md §2.7 for the sweep showing per-metric
    divergence rates that never converge to a shared floor (sharpe still
    diverges on 82% of rows at tolerance=0.10). Left at its prior value,
    flagged as an open architectural question (relative vs. absolute
    tolerance), not silently "fixed" with an unjustified number.
"""
from __future__ import annotations

# --- Class 1: published-precision (KID/PRIIPs) comparisons ---
# The KID publishes Annual_Impact_Pct (Reduction in Yield) rounded to 1
# decimal place while Total_Costs_Pct carries 2-decimal computed precision.
# Sweep (§2.6, applied 2026-09-13, ANNUAL_LE_ACCUMULATED /
# ANNUAL_EQUALS_TOTAL_AT_1Y): tolerance 0.0001 produced an 87% false-positive
# rate (1,778/2,042 horizon=1y rows) purely from rounding; 0.06 eliminates
# the rounding noise while preserving the genuine 447-fund cost-schedule
# duplication defect (264 residual rows). Grounded in the KID's own
# regulatory publication format, not an arbitrary proximity heuristic.
KID_ROUNDING_TOLERANCE_PP = 0.06

# --- Class 2: float-identity comparisons ---
# OC_NOT_CONTAMINATED, SHARPE_EQUALS_SORTINO, SORTINO_VS_SHARPE_UP/DOWN,
# DEFLATION_ORDER and MONTH_SHARE_OVERFLOW compare raw computed floats with
# no publication-rounding step between them. Sweep (2026-09-13, live DB,
# tolerances 0.0001/0.001/0.01/0.06):
#   OC_NOT_CONTAMINATED:      89 -> 89 -> 93 -> 190 violations (monotonic,
#                              no plateau — widening only adds coincidental
#                              near-matches, not rounding noise).
#   SHARPE_EQUALS_SORTINO:    173 -> 401 -> 2,092 -> 8,575 matches out of
#                              43,766 eligible (same pattern).
# Both confirm these are identity tests, not near-equality tests: the KID
# case's steep drop-then-plateau shape (evidence of a real rounding grid)
# is absent here. 0.0001 is the correct, already-appropriate value — kept,
# not defaulted-to.
FLOAT_IDENTITY_TOLERANCE = 0.0001

# --- Class 3: derived-quantity (deflation) comparisons ---
# REAL_EQUALS_NOMINAL only means anything when IPC over the horizon is
# large enough that deflation should visibly separate return_ann_real from
# return_ann_nominal. catalog_pairs._ipc_eligibility already gates the
# comparison at ipc_yoy > this same floor; tying the match tolerance to it
# too (rather than an unrelated bare 0.0001) is the DRY choice (P#11) — a
# single "deflation is negligible below this IPC print" threshold serves
# both purposes. Sweep (2026-09-13, live DB, ipc_yoy=3.90%): matches grow
# 26 (tol=0.0001) -> 348 (tol=0.001) -> 3,317 (tol=0.01) -> 16,121
# (tol=0.06) out of 22,367 eligible rows — no natural floor above this
# point, so the eligibility floor itself is the only principled anchor
# available without a fresh, dedicated investigation of the wider curve.
IPC_ELIGIBILITY_FLOOR = 0.001
