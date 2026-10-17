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
  - (CAPTURE_UP_EQUALS_DOWN was here until 2026-10-07: measured, it is a float-identity
    comparison like the others and now uses FLOAT_IDENTITY_TOLERANCE -- see catalog_pairs.py.)
  - (SCALAR_EQUALS_TIMESERIES was here until 2026-10-16: it could not be resolved by any single absolute constant while the two tables
    disagreed because of NAV staleness and a formula asymmetry (AUDITORIA_ESTADISTICA.md §2.7/§2.9). Both were fixed at the cause; the
    measurement after the full recompute is under SCALAR_TIMESERIES_TOLERANCE below, decision D5: absolute, no relative tolerance.)
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

# One-year relation band (FND-0278, owner decision 2026-10-17). At horizon 1 the KID's Annual_Impact_Pct and Total_Costs_Pct are the same cost, but the KID itself
# publishes them on slightly different bases (the 1-decimal ACI, and the older template that assumes the moderate first-year return instead of 0 %), so the pair can differ
# by more than the 0.06 pp rounding noise without any extraction defect: LU1683489089 6.7 % vs 6.37 % (ratio 1.05), IE0031575503 10.6 % vs 9.14 % (ratio 1.16). Measured on
# the live schedule (2,076 one-year rows, 198 outside the 0.06 pp tolerance): 164 have annual / total in [0.956, 1.160]; NONE lie in [0.77, 0.95) or in [1.20, 2.50); the
# other 34 (down to 0.03, up to 118) are extraction errors (a euro total read as 4 EUR for a 4.6 % impact). A ratio inside the band is a KID convention, not a defect. The
# owner chose the NARROW band (0.9 to 1.25): it classifies today's data exactly like the wide one (0.8 to 2.0) and keeps the room (0.06 below, 0.09 above the accepted
# values) without tolerating a doubling in future data. Applies ONLY at horizon 1; beyond one year the strict relation stays.
KID_ONE_YEAR_RATIO_MIN = 0.9
# Upper edge of the same band: the largest accepted ratio observed is 1.160 (IE0031575503), 0.09 below the cut; the next flagged value is 2.5.
KID_ONE_YEAR_RATIO_MAX = 1.25

# One published unit of the KID's Annual Cost Impact (ACI_1Y, ACI_RHP): both are published with ONE decimal, so each carries up to +-0.05 pp of
# rounding and a difference of two of them is exact only to 0.10 pp (FND-0276/0277, measured 2026-10-17 over fund_master, 2,852 funds with both
# ACI: 59 of the 135 ACI_AMORTISATION_ORDER violators differ by exactly 0.1; every one of the 97 ACI_MUST_AMORTISE violators has an entry fee of
# 0.0525 % or less, whose amortisation moves the ACI by < 0.05 pp, below the grid). The two bounds below add / subtract
# FLOAT_IDENTITY_TOLERANCE so that a difference of exactly one unit survives floating point (6.5 + 0.1 != 6.6 bit for bit).
ACI_PUBLISHED_UNIT_PP = 0.10
# ACI_RHP may exceed ACI_1Y by one published unit (ACI_AMORTISATION_ORDER): ACI_PUBLISHED_UNIT_PP + FLOAT_IDENTITY_TOLERANCE.
ACI_ORDER_TOLERANCE_PP = 0.1001
# An entry fee amortises visibly from one published unit (ACI_MUST_AMORTISE): ACI_PUBLISHED_UNIT_PP - FLOAT_IDENTITY_TOLERANCE.
ACI_AMORTISATION_VISIBLE_PP = 0.0999

# --- Class 2: float-identity comparisons ---
# OC_NOT_CONTAMINATED, SHARPE_EQUALS_SORTINO, SORTINO_VS_SHARPE_UP, CAPTURE_UP_EQUALS_DOWN (2026-10-07: at the former
# 0.001 the expected CHANCE matches, ~10 of 2,810 rows, exceeded min_matches),
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

# --- Class 3b: magnitude floor for a ratio compared with another ratio (FND-0234, 2026-10-06) ---
# Sharpe and Sortino share their numerator (return_ann - rf), so when it is ~0 both ratios are ~0 and "equal within 0.0001" is
# arithmetic, not evidence. 17 of the 25 live SHARPE_EQUALS_SORTINO matches had |ratio| < 3e-4 (return_ann within 0.1 pp of the
# 2.5% risk-free rate). 0.01 = a numerator of about 0.1 pp of annual return at 10% volatility: below it the ratio is
# numerically indistinguishable from zero.
RATIO_ELIGIBILITY_FLOOR = 0.01

# --- Class 1b: published precision of a cost percentage compared with another one (FND-0262) ---
# The cost columns of the X__EQUALS__Y pairs (fees, ACI) are percentages the KID publishes with 2 decimals, so two components "hold the
# same value" when they agree within HALF a unit of that last published decimal: 0.005 percentage points. Same class as
# KID_ROUNDING_TOLERANCE_PP (regulator-rounded values), a different grid (2 decimals, not the 1-decimal Reduction in Yield). The pairs are
# population-level coincidence detectors (MINOR in the criticality model), so a tolerance one decimal wider or narrower moves a count,
# never a gate; it was not re-swept in FND-0262.
COST_PAIR_TOLERANCE_PP = 0.005

# --- Class 2b: scalar vs latest timeseries snapshot (SCALAR_EQUALS_TIMESERIES, FND-0262, decision D5) ---
# The scalar in fund_metrics and the latest fund_metric_timeseries row come from two write paths over the SAME series, so after a
# consistent recompute they are the same float. Measured 2026-10-16 on the persisted universe after the FND-0253 full recompute
# (audit_20261012T201920Z data; 25 combinations of vol_ann / max_dd / return_ann / sharpe / sortino x rolling_1y..10y, 5,612-5,738 funds
# each): the largest absolute divergence is 1.4e-12 (sharpe rolling_1y), and ZERO funds diverge beyond 1e-6, in every family, at any
# relative tolerance from 0.1% to 5%. The 2026-09-13 divergences (50-97% of rows, never converging under any rtol up to 0.30) were NAV
# staleness between the two tables plus the flat-vs-date-aligned risk-free asymmetry, both fixed at the cause (AUDITORIA_ESTADISTICA 2.9).
# So no metric family needs a relative tolerance (D5): the class-2 float-identity constant keeps eight orders of magnitude of margin, and
# a looser tolerance would only hide a stale snapshot, which is exactly what the rule exists to detect.
SCALAR_TIMESERIES_TOLERANCE = FLOAT_IDENTITY_TOLERANCE
