# Package K - Selection quality decision (selection quality, package 3)

Owner order and decisions 2026-11-15: letters I / J / K. One page, rewritten in place. Plan: `doc/especificacion/PLAN_SELECCION_CALIDAD.md` (WP4 interface, WP5, WP6). Cluster ticket: FND-0429 (parked). Exit metric: a recorded CONTINUE, PIVOT or untestable verdict and, only after CONTINUE, the owner's vendor decision. Starts only after package I has recomputed P2 and the owner has re-run the full-universe PIT.

## 1. Decision needed
None now.

## 2. State
Not started. Interfaces: FND-0158 (hysteresis shadow, the owner decides enabling), FND-0299 (forward holdout from 2026-11), FND-0389 (sovereign yield factors). Rules fixed in the plan: mini-prereg first; null and planted-signal controls first; feasibility against `MIN_MEANINGFUL_DELTA_SHARPE`; selection metric M2 and portfolio metric M1; outcomes CONTINUE (with CONSTRUCTION-LOSS handled first), PIVOT, INCONCLUSIVE; no vendor contact before CONTINUE. Denial, a failed scorecard or a PIVOT ends the challenger.

## 3. Decisions pending
Batch 2 constants (plan register rows 7 to 16) before any vendor contact.
