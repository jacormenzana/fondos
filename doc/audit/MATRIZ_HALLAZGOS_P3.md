# P3 finding → package matrix (P3-0, 2026-11-18)

Every finding of `INVENTARIO_FUNCIONAL_proyecto3.md` (H-, E-), `GAP_ANALYSIS_proyecto3.md` (GAP-, IMP-) goes to a package of the approved roadmap or to a dated DEFER. Package names follow the plan: P3-0 governance, P3-1 integrity fixes and benchmarks, P3-2' construction study (after checkpoint C1), P3-5a score-layer tests, and the conditional packages P3-3, P3-4, P3-5b, P3-6 (each needs a go-ahead at checkpoint C2).

**DEFER review dates are proposals** (2027-01-15 is the date already used by FND-0257); nothing here is a recorded decision of the owner until confirmed on the decision card. Severity is the report's own where it has one, otherwise my judgement.

## Inventory findings

| ID | Finding (short) | Severity | Destination |
|---|---|---|---|
| H-1 | Regime tree: `AGENTS.md` says Estanflacion IPC > 4 %, code uses 3 % | Low | **Done 2026-11-18** in `proyecto3/AGENTS.md` (P3-0 item 5) |
| H-2 | `fund_scorer.py` header still cites FX penalty and alpha bonus (both 1.0) | Low | **Done 2026-11-18** (`fund_scorer.py` header: both multipliers are neutral since FND-0193) |
| H-3 | Rolling-percentile multipliers 0.90 / 1.10 hardcoded outside the YAML, OFF | Low | DEFER 2027-01-15 (dead-code removal "after two stable cycles", `proyecto3/AGENTS.md`) |
| H-4 | `DATA_SUSPECT_ENABLED` documented as OFF, value is True | Medium (misleads agents) | **Done 2026-11-18** (also the gate status, now ENFORCED in `controls.py:176`) |
| H-5 | "Score ranks and selects, hysteresis included" while hysteresis is OFF; three band values | Medium | **Done 2026-11-18**; the rule itself stays with Package D (FND-0158) |
| H-6 | Report colour thresholds hardcoded and out of line with the scorer | Low | DEFER 2027-01-15 (report package) |
| H-7 | Legacy benchmark takes the first 100 valid funds by ISIN order | Low-Medium | DEFER 2027-01-15; sheet `3_Backtesting` only |
| H-8 | PIT docstrings obsolete (costs, hysteresis, weights) | Low | **Done 2026-11-18** (`pit_backtest.py` header and `build_weights` docstring) |
| H-9 | `Dinámica` in docs vs `Dinamica` in code and YAML | Low | **Done 2026-11-18** (note added under the regime table) |
| H-10 | Rotation gate reads `fund_scores` without regime or date | High (latent) | P3-1.2 |
| H-11 | `VOL_FLOOR` duplicated as a literal | Low | P3-1.3 |
| H-12 | `n_dates` counts dates without outcome | Low | P3-1.3 |
| E-1 | Zero shown as "N/D" in report labels | Low | DEFER 2027-01-15 |
| E-2 | Cover sheet shows today's regime, not the scenario's | Medium (transparency) | DEFER 2027-01-15; revisit at C2 if the report is touched |
| E-3 | Backtest sheet compares all months with today's target | Low | DEFER 2027-01-15 |
| E-4 | Short-horizon weights can push the score out of [0, 1] (switch OFF) | Low | DEFER 2027-01-15 (reopen only if the switch is considered) |
| E-5 | Missing metric is penalised, not neutral | Medium (revised 2026-11-18: measured; material only in Defensiva) | **Measured 2026-11-18 (P3-1.5)**: the missing weight is almost all `alpha_persistence` (about 19 % of eligible funds), an implicit short-history discount; median imputation would change 6 of the Defensiva top 10, 2 of Equilibrada, 0 of Dinamica. Decision moved to **P3-5a** (arm: missing metric = median percentile, tested out of sample); no code change by reading |
| E-6 | Fund with no monthly NAV row passes the age rule | Medium (revised 2026-11-18: measured, no live effect) | **Done 2026-11-18** (P3-1.7, `scripts/audit/p3/nav_rows_eligibility_diff.py`): of 963 eligible funds of the live scoring run (regime Shock_Energetico), 0 have no monthly NAV and 0 have fewer than 12 rows. No switch needed; reopen only if the count becomes non-zero |
| E-7 | Latest run assumed to be the full universe | Low-Medium (revised 2026-11-18: no production path writes a partial run) | P3-1.6 `selection_health` (eligible-set change, calibrated alert); no refusal, no set comparison (deduplication removes rows, so no expected set exists) |
| E-8 | Legacy backtest has no history for some regimes | Low | DEFER 2027-01-15 |
| E-9 | No `assert_no_overlay` inside `proyecto3/src` | Medium | DEFER 2027-01-15; verify in the launcher audit when P3-1 touches the launchers |
| E-10 | Cross-exclusion favours the sub-portfolio built first | Medium | P3-2' (correctness fix with a non-inferiority test) |
| E-11 | Reverted rotation skips family, manager, nature, FX checks | High (latent) | P3-1.2 |
| E-12 | `generate_report(score_version)` ignored | Low | DEFER 2027-01-15 |
| E-13 | Promotion override passes when the backlog is unreachable | Policy question (revised 2026-11-18: documented, tested behaviour, "a warning, never a blocker") | Owner decision: keep, or refuse when the owning ticket cannot be checked. Not changed |
| E-14 | Drawdown condition and survivorship gap default to 0.0 when missing | Medium | P3-1.3 |
| E-15 | PIVOT by the sign of the point estimate | Low (it is the signed rule) | P3-1.3: `verdict` also returns lower bound and standard error |
| E-16 | `pick_units` does not validate a null `fund_family_id` | Low (static reading) | P3-1.3 diagnostic |

## Gap analysis findings

| ID | Finding (short) | Severity | Destination |
|---|---|---|---|
| GAP-01 | "Judge" absent (net appraisal ratio, Newey-West, FDR, shrinkage) | High | P3-5b, only if P3-5a leaves headroom (C2) |
| GAP-02 | "Navigator" absent (HMM, probabilities, EMAs) | Medium | P3-6 (C2). The FND-0193 static-mix result shows the regime layer earns its place on drawdown, so a Navigator is judged against the current tree, not retired by default |
| GAP-03 | No `TRANSITION_MAX_DEVIATION` | High | P3-2' arm 1 (behind C1) |
| GAP-04 | Economic layer inoperative: gate OFF, incumbent score defect, no tax, no pessimistic cost | High | **P3-1.1 done 2026-11-18 (FND-0431)**; the rest in P3-4 (conditional) |
| GAP-05 | ROT-0 not explicit, can be reverted | High (revised: the revert branch was unreachable, so it could not reinstate anyone; the real defect was that the gate never reverted) | **P3-1.2 done 2026-11-18 (FND-0431)** |
| GAP-06 | Vehicle eligibility (UCITS / ETF, allowed types) absent | Medium | P3-3 (census first) |
| GAP-07 | Family class not chosen by cost | Medium | P3-1.3 diagnostic, then P3-2' arm 2 |
| GAP-08 | L3 and L4 coupled | Medium | P3-4 (conditional) |
| GAP-09 | No execution ledger or orders | Medium | P3-4 (conditional) |
| GAP-10 | L4 friction parameters absent | Medium | P3-4 (conditional) |
| GAP-11 | `MIN_NAV_ROWS` and survival only indirect in the live scorer | Low | **Measured 2026-11-18 (P3-1.7): the live eligible set has no fund under 12 rows**, so the indirect guarantee (P2 does not produce metrics for short histories) holds today. Documented as a precondition in `ESPECIFICACION_P3.md` REQ-02; an explicit check in `check_hard_filters` stays optional |

## Unspecified behaviours found in code (IMP)

| ID | Behaviour | Destination |
|---|---|---|
| IMP-01 | Risk filters by sub-portfolio (drawdown, real return, SRRI, High Yield) | Absorbed into `ESPECIFICACION_P3.md` as L1 mandate requirements |
| IMP-02 | Short-horizon daily gates | Absorbed into the spec as L1 |
| IMP-03 | Six-metric composite, weights by sub-portfolio, nature bonus | Absorbed as "scoring in force"; the nature bonus is tested in P3-5a |
| IMP-04 | Multiplier layer and hardcoded rolling-percentile values (neutral since FND-0193) | Absorbed as "retire pending"; code removal DEFER with H-3 |
| IMP-05 | Seven-regime tree and traffic light | Absorbed as the regime implementation in force |
| IMP-06 | Data-governance and promotion gates | Absorbed as governance requirements |
| IMP-07 | DRAFT / PRODUCTION / ARCHIVED lifecycle | Absorbed as the real execution step today |
| IMP-08 | Structural cash and 10 % cap | Absorbed |
| IMP-09 | Data hold, data suspicion, exemptions | Absorbed (merged with REQ-01 quarantine) |
| IMP-10 | Hysteresis shadow measurement | Absorbed as an L4 control; generalised in P3-2' |
| IMP-11 | PIT backtest and model-value study | Absorbed as the validation layer |
| IMP-12 | Report reconciliation gates and colour thresholds | Gates absorbed; colours DEFER with H-6 |
| IMP-13 | FND-0429 selection test | Absorbed as the validation criterion for the Judge and for L3 |
| IMP-14 | Blindness of the point estimate; 2 pp drawdown margin | Absorbed as pre-registration policy |

## Coverage check
Inventory H-1..H-12 (12), E-1..E-16 (16), GAP-01..11 (11), IMP-01..14 (14): 53 IDs, all assigned. DEFER entries: 12 (the ten findings H-3, H-6, H-7, E-1, E-2, E-3, E-4, E-8, E-9, E-12, plus the code-removal part of IMP-04 and the colour part of IMP-12), none without a review date.
