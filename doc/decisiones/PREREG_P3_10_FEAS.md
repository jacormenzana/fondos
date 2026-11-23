# PREREG P3-10F: feasibility of marginal-CDaR admission (C.2 of the drawdown-management RFC)

**Status: PREREGISTERED 2026-11-23 on the owner's approval of D1-D5 as recommended (section 10), committed BEFORE any outcome stage (F1 census, F1b gate, F2) was built or run.** Drafted the same day; sections 0-9 are the draft he approved, changed only by this status line, the episode definition of section 3 (D1), the primary-cell rule of section 4 and section 10. Plan of record: the approved plan rev. 3 (session 2026-11-23), cluster ticket FND-0430, package `doc/backlog/PKG_L_p3_alignment.md`. Pure code and its tests exist and are mutation-checked (`proyecto3/src/pit_cdar.py`, `proyecto3/tests/test_pit_cdar.py`); the only number read so far is the DESIGN-POWER count of section 6.

## 0. Mandate
15 % maximum drawdown unchanged; target an attainable NET band of 3.2-4.0 % (owner, 2026-11-22); IPC+M3 reported next to it (`d06353f`). AC (band 0.10 + fee penalty) nets 3.12 % at the stated fees in the PIT history, 0.08 pp under the band floor.

## 1. Question
The RFC proposes to replace the rigid individual drawdown exclusion by the marginal contribution to the portfolio's CDaR. Its individual-cap half is lever L1 of P3-5A (CLOSED: deeper 2008 drawdown, +0.20 pp not distinguishable from zero). The untested half is C.2: **can funds that the drawdown filters exclude, but that hold up when the whole pool is under stress, raise the best constant mix of the pools under the 15 % cap above its 4.14 % gross ceiling, by an amount that survives friction, survivorship and chance?** This study answers only whether a preregistered adoption study (P3-10) is worth building. A PASS adopts nothing (R7).

## 2. What is already known
Static ceiling of the three matched pools under -15 %: 4.14 % gross (`doc/audit/P3_L3_MANDATE_AND_FEASIBILITY.md`; survivors only, hindsight, frictionless). The history detects effects of 0.62-1.29 pp at best (MDE, P3-5A), so a gain of 0.5 pp cannot be confirmed on it: the study is a necessary-condition gate, and adoption is decided by the forward shadow. Time to recovery as a multiplier was retired neutral (FND-0193) and the recovery lever L3 was inert, so pillar A is not carried forward.

## 3. Data and conventions (read-only; sealed run `out/reports/pit_backtest/20261119_202509`, `pit_seal.verify_seal`; no database)
- Pool series: equal-weight return over month t of the funds ELIGIBLE at t-1 in the sealed scores (any sub-portfolio). Returns: month-end NAV of `inputs/nav.parquet`. Every statistic at t uses data <= t; a decision at t earns month t+1.
- Stress month: the 12-month pool level more than 3 % below its 12-month high (the arithmetic of `ranking_levers.pool_state`, pinned by a test). Episode: runs of stress months separated by at most 2 months out of stress are ONE episode (D1, approved: 10 episodes in the sealed history); the bootstrap resamples these episodes.
- Candidate pool (structural filters passed): sealed first reason not data / document / hold / suspect; not failing the independent return, SRRI or High Yield filters (`pit_crisis_decompose.independent_filters` on the rescored frames, validated against the sealed `score_base` as in `p3_crisis_diagnostic.py`). Excluded sets: (a) DD only: first reason the since-inception drawdown filter and no other metric filter failing; (b) DD and/or a short gate: (a) plus first reason `short_*`. Limitation stated in advance: the sealed run keeps only the first failing reason, so a short-DD first reason hides a later short-volatility failure.
- **Convex candidate** (trailing 60 months, stress months only, a full window of returns): at least 6 stress months; stress beta <= 0.3 (cov with the pool / var of the pool); mean return in the stress months above the pool's; 60-month total return > 0.
- **Fourth column ("Convex")**: ONE equal-weight basket of the candidates across all sub-portfolios (D2). Cash (`cash_ret_1m` of the sealed table) when the window has fewer than 6 stress months or fewer than 3 candidates (thin-window rule); the % of months it is active is reported.

## 4. The tests (all numbers below are proposals until section 9 is answered)
**F1 census.** Counts (a) and (b) per month x sub-portfolio x nature. Stop: active share of the Convex column below 25 % of the months, or median candidate count below 3 in every sub-portfolio on count (b).

**F1b predictive gate (before F2).** At each t, candidates versus controls from the same excluded set; outcome: the mean of (r_i - r_pool) over the stress months of (t, t+12]. Pairing: cell (sub-portfolio x nature), else the whole sub-portfolio, else the observation is dropped; coverage reported by level; **more than 30 % dropped = not evaluable = STOP.** Statistic: nominal one-sided 95 % lower bound of the mean, by a bootstrap that resamples whole EPISODES (2,000 draws, fixed seed); with fewer than 8 episodes also the pooled mean > 0 and at least 75 % of the episodes positive. PASS needs the bound above 0.

**F2 hindsight upper bound** (only after F1 and F1b pass). `optimise_mix` on [Defensiva, Equilibrada, Dinamica, Convex] at a -15 % cap, step 0.1 (it first reproduces 4.14 % with the three pools, a test pins equality with `pit_crisis_diagnostic.best_static_mix`).
- Friction: the basket's turnover x the stated entry / exit fees of the P3-7 frozen snapshot (unknown fee = median of the known, traded share reported), flat 25 bp per side as a cross-check; monthly and quarterly membership.
- Survivorship haircut: `r~ = (1 - a k) r_basket + a k r_P10` with a = 10 %, k in {2, 3}; drawdown under `tail_stress_max_dd`; breakdown share reported.
- CDaR90 on the full path (CDaR95 reported).
- Null: 500 random baskets from the SAME excluded set (same size each month, cash in the observed cash months), each re-optimised, scored on `Gain_ACTIVE` with the observed state. The observed `Gain_ACTIVE` must exceed the 95th percentile of the null.
- Attribution: `dR_t = R_new,t - R_front,t`; `Gain_s = 12 * sum over t in s of dR_t / N` for s in {ACTIVE, CASH}; ACTIVE + CASH = total exactly. Brinson split (Convex sleeve effect + base reallocation) per state.
- Construction reach: share of months with at least 3 basket funds left after at most 2 per management company.
- **PASS (all):** `Gain_ACTIVE` >= +0.5 pp over 4.14 % gross; >= +0.35 pp after friction and haircut (monthly or quarterly variant); above the null's 95th percentile; stressed drawdown <= 15 %. Gain reported in band terms.

**Inference level.** F is a go/no-go gate, not an adoption test: F1b and F2 use NOMINAL 95 % levels. The Holm family (676 + 2 = 678 trials, level 0.05 / 678 = 7.4e-5) is printed as a reference and reserved for the adoption contrast of P3-10; neither a 2,000-draw episode bootstrap nor a 500-draw null can resolve it. F1b and F2 are logged as 2 trials of the family. **Sensitivity grid** declared now: stress beta cap {0.3, 0.5} x stress threshold {3 %, 5 %}; all four reported, none picked as the best (R8). **The verdict is the PRIMARY cell (beta cap 0.3, stress threshold 3 %)**; the other three cells are reported as a fragility check and cannot turn a STOP into a PASS: a PASS in the primary cell that fails in two or more of the other three is reported as FRAGILE next to the verdict.

## 5. Decision table
| Outcome | Action |
|---|---|
| fewer than 4 episodes, or an F1 stop | record "not testable"; close C.2; no study |
| F1b fails or is not evaluable | record "no ex-ante signal"; close C.2; no study |
| F2 fails (null, friction, haircut or drawdown) | record the ceiling and the reason; close C.2 |
| F2 passes | draft PREREG P3-10 (C.2 + B' on the short 3-month volatility gate only; adopts only if it REPLACES the individual drawdown filters and the short DD gate); nothing is built before the owner's order |

## 6. The design-power count (read 2026-11-23, before this draft; a property of the design, not an outcome)
Sealed pool series 2005-02..2026-10 (261 months); stress months 69 (26 %); a stress beta can exist (at least 6 stress months in the trailing 60) at 92.1 % of the dates.
- **Consecutive runs (gap 0): 13 episodes**, lengths 3, 22, 2, 1, 3, 6, 1, 1, 9, 16, 1, 3, 1.
- **Runs separated by at most 2 months joined (gap 2): 10 episodes**, stress months 3, 22, 3, 9, 2, 9, 16, 1, 3, 1: 2006, 2007-11..2009-08, 2011-08..11, 2015-08..2016-05, 2018-10..12, 2020-02..10, 2022-01..2023-04, 2023-10, 2025-03..05, 2026-03.
- Reading: the stop rule "at least 4 episodes" passes either way, but 55 % of the stress months (38 of 69) sit in two episodes (2007-2009, 2022-2023), four episodes hold 9 or more stress months, and six hold 3 or fewer. The effective number of independent stress events is closer to 4 than to 10; the F1b bootstrap and its sign rule are written for that.

## 7. Contamination and bias, stated before any outcome
The sealed run is survivor-only (FND-0436): funds excluded for deep drawdowns are the likeliest to have died, so every result is flattered (the same direction as L1, which is why k = 3 is reported). The stress episodes 2008, 2020 and 2022 were spent by P3-5A: they are descriptive here. The census and the gate read the same history that produced the stress months, so F is necessary, never sufficient. The 2008 and 2022 episodes dominate the stress sample; a result carried by one of them is reported as such.

## 8. Provenance
Code: `proyecto3/src/pit_cdar.py` (32 tests, 33 mutations killed in consecutive runs), driver `scripts/audit/p3/p3_cdar_feasibility.py` (stage `design` only until this is committed). Inputs: the sealed run (nine SHA-256 verified artifacts), the P3-7 frozen fee snapshot for the friction stage. No database read, no production change, no P2 recompute.

## 9. Decisions for the owner (each needs an answer before the preregistration is committed)
- **D1. Episode definition.** (a) consecutive stress months only (13 episodes), as in the approved plan; (b) join runs separated by at most 2 months (10 episodes). Recommended (b): a one-month rebound inside a drawdown is not an independent event, and F1b resamples episodes.
- **D2. One basket or one per sub-portfolio.** The draft uses ONE global basket (a fourth column), with the census still per sub-portfolio. Recommended: keep (a basket per sub-portfolio would add three columns and three degrees of freedom).
- **D3. Gain scale.** The thresholds apply to the ARITHMETIC annual contribution `Gain_s`; the geometric difference of the two optimal mixes is reported next to it. Recommended: keep (the exact ACTIVE + CASH identity needs the arithmetic form).
- **D4. The values of section 4:** beta cap 0.3, 6 stress months, 60-month window, 3 candidates, 25 % active share, 30 % dropped, 8 episodes for the sign rule, 75 %, +0.5 / +0.35 pp, 95 % level, a = 10 %, k in {2, 3}, 500 draws, the sensitivity grid. They are my working values, not calibrated.
- **D5. Go.** Approve the preregistration as drafted (with the answers above) so it is committed and the outcome stages can be built and run.

## 10. The owner's answers (2026-11-23, in the session)
- **D1 approved (b):** runs separated by at most 2 months are one episode (10 episodes); brief rebounds inside one crisis are not separate events in the bootstrap.
- **D2 approved:** one global basket as the fourth column (no three extra degrees of freedom in the static optimiser).
- **D3 approved:** thresholds on the arithmetic annual contribution `Gain_s` (exact ACTIVE + CASH = total); the geometric differential (CAGR) is reported in parallel.
- **D4 approved:** every operating threshold of section 4 is an uncalibrated ex-ante parameter, fixed now, supported by the fixed sensitivity grid {0.3, 0.5} x {3 %, 5 %}.
- **D5 approved (go):** commit this preregistration at once and run F1, then F1b, then F2 in sequence until the PASS / STOP verdict.
