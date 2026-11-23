# PREREG P3-9: layer 3, the turning-point risk levers: nature bonus and the Equilibrada performance-chasing control

**Status: PREREGISTERED 2026-11-23 on the owner's approval of D1-D5 as drafted (section 11), committed BEFORE any code or number of this study.** Drafted 2026-11-22 on the owner's order ("draft the single preregistration for Layer 3") after he restated the mandate. The text of sections 0-10 is the draft he approved, unchanged except this status line and the file name (renamed from `PREREG_P3_9_LAYER3_DRAFT.md`). Ticket: FND-0435 (it closes through the code commit of whatever this builds, with its own receipt; no other commit cites it as a closure). Note of record: `doc/audit/P3_L3_MANDATE_AND_FEASIBILITY.md`. Diagnostic: `doc/audit/DIAGNOSTICO_TURNING_POINT_P3.md`.

## 0. The mandate this study serves (owner, 2026-11-22; his numbers)
Capital preservation: the 15 % maximum drawdown cap is UNCHANGED. The target annual return is restated from IPC+M3 (6.94 %) to an attainable NET band of **3.2 % to 4.0 %** (net of the stated fees, the primary regime of P3-7). Reason on record: no drawdown limit lets a static allocation of the pools reach 6.94 % (best static mix at no limit 6.32 %; 4.14 % at the cap, gross, frictionless).

## 1. Question
Can two risk-side changes to the ranking reduce the losses at turning points (2008 top and bottom, hindsight; 2022 same sign) without costing return, at the 15 % cap? One question, judged on risk first (the owner's measure, 2026-11-22: drawdown and tail loss anchored to the 15 % cap), with the return held to non-inferiority. A study that improves the return is NOT the aim and could not be shown (section 5).

## 2. What is already known, and how much it can be trusted
- The diagnostic (one episode, in-sample, hindsight forward returns, survivors only): the Monetario weight at both turning points is almost all the nature bonus (x1.25), not the filters; with the bonus at 1.0 the master's next-12-month return moves -0.8 pp at the top and +1.7 pp at the bottom; the max-drawdown weight is a symmetric trade-off; at the top Equilibrada bought the funds with the highest trailing performance (alpha_persistence 33 % and since-inception real return 23 % of the score gap) and fell 18 pp more than its pool, while with the alpha_persistence weight at 0 the master moved -0.2 pp at the top and -1.5 pp at the bottom (so the weight alone is not a free control: this is why the lever below acts on both trailing-performance metrics together).
- P3-5A tested the ranking levers L1-L3 and found no candidate; L3 (recovery modulation of the bonus and the max-drawdown weight) was inert (recovery state on 1.1 % of the dates). The levers here act on the bonus and the weights ALWAYS, not only in a recovery state.
- Minimum detectable effect of the history on the return: 0.62-1.15 pp a year (P3-5A, P3-7), above the 0.5 pp floor. The history cannot show an improvement; it can test non-inferiority and the drawdown cap.

## 3. Design
**Base arm (D1).** All arms are built ON TOP OF the base arm and judged against it, in the same build (the base is re-run, never read from a previous run). Recommended base: **AC** (band 0.10 + fee penalty H 0.58 y), the forward-shadow candidate of P3-7: at the live arm's 0.08 % net the cost noise swamps any layer-3 effect, and the band 3.2-4.0 % is reachable only with the cost levers. Alternative: the live arm (band 0, no penalty).
**Levers (both OFF in the base; each acts through `ranking_levers.LeverConfig`, new fields `l5_nature_k`, `l6_chase_f`).**
- **N, nature-bonus scale `k`:** every `NATURE_PROFILE_BONUS` entry b becomes `1 + (b - 1) k`, in all three sub-portfolios (the existing `modulate_bonus`). Values: **k = 0.5** (Monetario x1.125) and **k = 0** (no bonus).
- **E, Equilibrada chasing control `f`:** in Equilibrada ONLY, the weights of `return_ann_real` and `alpha_persistence` are multiplied by `f` and the other weights rescaled proportionally so that the sum stays 1 (the rule of `modulate_weights`, applied to a set; unit-tested). Values: **f = 0.5** and **f = 0**. Defensiva and Dinamica are untouched.
**Arms (6; the base is re-run in the same build).** N5 (k 0.5), N0 (k 0), E5 (f 0.5), E0 (f 0), NE5 (k 0.5 and f 0.5), NE0 (k 0 and f 0). Less intrusive first. Every run is built from scratch under the identity gate (section 8) with the inputs frozen in the P3-7 snapshot (IPC, fees, geography; hash-verified), so P3-7 and P3-9 read the same data.

## 4. Metrics (primary regime: the stated fees fund by fund; cross-check: the flat 25 bp)
Net annual return of the master portfolio; maximum drawdown of the master (unstressed, and under the primary survivorship tail stress of P3-5A, unchanged); the drawdown in each pool-defined stress window; the share of 36-month windows whose net return is at least the lower edge of the band (3.2 %); the Monetario share of the master and the share of Equilibrada weight in funds in the top decile of trailing 36-month return, at the design dates (mechanism checks, reported).

## 5. The adoption rule (a variant becomes a forward-shadow CANDIDATE only if all hold; history never adopts, R7)
1. **Drawdown cap.** The unstressed full-sample maximum drawdown of the master at the flat 25 bp is at most 15.0 %, AND the stressed worst window is no deeper than the base's by more than 0.5 pp; the same in each informative stress window (reported: the other windows were read by P3-5A and are no longer independent).
2. **Return non-inferiority.** At the stated fees the lower bound of the paired net-return difference against the base (stationary bootstrap of P3-5A, Holm over the 6 arms, one-sided level 0.05 / 6) is above **-0.10 pp a year**. An improvement is not tested (the minimum detectable effect exceeds the floor, section 2).
3. **A risk benefit (so that a lever that does nothing is not a candidate).** The stressed worst window OR the unstressed full-sample maximum drawdown is shallower than the base's by at least **1.0 pp** (value proposed by the assistant, D3).
4. **No collateral damage.** Non-crisis degradation at most 0.35 pp a year and the share of 36-month windows at or above 3.2 % net not lower than the base's.
The condition "the matched pool at the same turnover is beaten" of P3-7 is dropped: these levers change which funds rank first only within the rules of the ranking, and the pool question belongs to layer 1 (parked). The trials add 6 to the trial ledger (`N_full` of the deflated Sharpe; the Holm family of rule 2 is the 6 arms).

## 6. Contamination and bias (R9), stated before any number
- Read already: the diagnostic and its frozen counterfactuals on the 2008 dates (the design episode; the values of N and E were chosen after seeing that bonus 1.0 and alpha_persistence 0 were counterfactuals there, which is a disclosed post-hoc degree of freedom); the P3-5A and P3-7 results; the stress windows 2015-2016, 2018, 2022, 2025, 2026 (read by P3-5A: SPENT as confirmation).
- The history can therefore only SCREEN. **Confirmation is the forward shadow** (section 9). A drawdown benefit on one design episode and spent windows is a hypothesis, not a finding.
- Survivors only (FND-0436): the bias UNDER-states the risk the levers are meant to cut (the missing 2008 casualties are the funds a chasing control would have avoided) and OVER-states every return; the tail stress and the breakdown point are reported as in P3-5A.
- The stated fees are today's attributes (a documented look-ahead).

## 7. Parameters and provenance
| Name | Value | Source | Chosen |
|---|---|---|---|
| Drawdown cap | 15.0 % | mandate, unchanged | before |
| Net band (reporting, rule 4) | 3.2 % - 4.0 % | owner, 2026-11-22 | before this draft |
| N values k | 0.5, 0 | assistant: the half and the removal of the bonus | after seeing the diagnostic |
| E values f | 0.5, 0 | assistant: half and none of the two trailing-performance weights | after seeing the diagnostic |
| E metric set | return_ann_real, alpha_persistence | the diagnostic's carriers in Equilibrada | after |
| Non-inferiority margin | -0.10 pp a year | A5 of PREREG_FND-0193 (reused) | before |
| Stress margin | 0.5 pp | P3-7 section 5 (reused) | before |
| Risk benefit | 1.0 pp | assistant (D3) | before any number of this study |
| Collateral | 0.35 pp | P3-5A (reused) | before |
| Holm family / level | 6 arms / 0.05 / 6 | assistant | before |

## 8. Engineering prerequisites (only after approval; tests first; nothing is built now)
(1) `ranking_levers`: `l5_nature_k` and `l6_chase_f` (default None = nothing happens), the live scorer and the PIT scorer through the ONE function (the parity discipline of P3-5A); `fee_penalty_shadow.other_levers_on` must count them (the AC stream must never mix arms: a test). (2) The identity gate: the base re-run in the same build reproduces the sealed run, scores and turnover, with every switch OFF. (3) The runner (`p3_p37_trials.py` pattern) with the arms of section 3, the P3-7 snapshot verified by hash, the Holm family and the ledger rows. (4) Mutation checks on the new lever functions and the weight-rescale rule (the weights sum to 1; f = 1 and k = 1 are the identity). (5) Policy scanners after the last edit; ONE full receipt at delivery.

## 9. Confirmation, review and what "adopt" means
Forward shadow of any candidate (the pattern of `P3_FEE_PENALTY_SHADOW`: in-memory rescoring on the live build, one append-only row per real build), with its own amendment committed BEFORE any row exists. Reviews: 6 months (deterministic: the mechanism fields and the turnover), then the FND-0299 checkpoints (2028-10, 2029-10) for the drawdown guard, informative only if the live arm's own forward drawdown is at least 3 % (otherwise carried forward, never a pass). Nothing adopts itself (R7): adoption is the owner's order switching the lever. A forward drawdown benefit may never be observed (a 15 % pool drawdown may not occur in the horizon); then the candidate stays a candidate and the deterministic stream is the only evidence.

## 10. Decisions for the owner (each needs an answer before anything is built)
- **D1 Base arm.** AC (recommended) or the live arm.
- **D2 Lever values.** N: k 0.5 and 0; E: f 0.5 and 0 (E acts on `return_ann_real` and `alpha_persistence` together). Approve or give your own values.
- **D3 Risk benefit and margins.** A candidate must cut the drawdown by at least 1.0 pp (rule 3) with the return held to -0.10 pp (rule 2). Alternative: allow a stated trade (for example up to -0.50 pp of return for at least 2.0 pp of drawdown) as P3-5A's L2 would have been; it is a mandate choice, yours.
- **D4 Confirmation.** Forward shadow only (the history is spent); the amendment before any row. Approve.
- **D5 The band.** 3.2 % - 4.0 % used only as the reporting reference of rule 4 (the share of 36-month windows at or above 3.2 %). Approve that use.

## 11. The owner's answers (2026-11-23, in the session, to the question "which of D1-D5 do you approve?")
Answer: **"Approve all as drafted"**: D1 base arm AC; D2 N k 0.5 / 0 and E f 0.5 / 0 (E on `return_ann_real` and `alpha_persistence` together); D3 risk benefit of at least 1.0 pp with the return held to -0.10 pp (no stated trade); D4 confirmation by forward shadow only, with its own amendment before any row exists; D5 the band 3.2 % - 4.0 % only as the reporting reference of rule 4. No value of sections 0-10 changes. Next: the engineering prerequisites of section 8, tests first, every new switch OFF; no trial runs before the identity gate of section 8 (2) passes.
