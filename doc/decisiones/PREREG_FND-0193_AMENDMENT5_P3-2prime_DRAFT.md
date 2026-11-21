# PREREG FND-0193, Amendment 5 (DRAFT): P3-2' hysteresis band and rebalance cadence, scores frozen

Status: DRAFT written 2026-11-21 by the assistant for the owner (Package L, `doc/backlog/PKG_L_p3_alignment.md` step 4). It becomes an amendment of `PREREG_FND-0193` §8 only when the owner confirms the numbers below; every number marked PROPOSED is the assistant's proposal, not a decision. No code. It is written BEFORE any new number of this question is read.

## 1. Question and scope
Does slowing the turnover of the selection (hysteresis band, rebalance cadence) pay once trading costs are the ones the owner actually faces? ONLY these two levers; the scores, the filters, the caps and the weights are frozen. Out of scope: transition cap (REQ-24, untestable on history), caps, nature cap, family-class arm; E-10 stays DEFER (review 2027-01-15).
Grid (frozen): band {0, 0.05, 0.10, 0.20} x cadence {1, 3, 6, 12 months, phase-averaged}; baseline = band 0, monthly. 16 configurations are added to the trial count N of the ledger by a dated amendment (the ledger file is never edited).

## 2. What has already been read (disclosure, R9)
The cost check on the sealed run `20261119_202509` (in-sample): net annual return of every band and cadence at 25 / 50 / 100 / 200 bp, and at the stated fees fund by fund. Its break-evens (9-33 bp against the baseline) are the reason this amendment exists, so the criteria below are NOT independent of them; the live shadow stream (`P3_HYSTERESIS_SHADOW`, one row per monthly build) is the only information not yet seen.

## 3. Cost regimes (R10)
Primary: the stated entry and exit fees of each fund, fund by fund (owner answer 2026-11-19: the analysed funds are the platform's offer; unverified assumption that the stated fee is what he pays). Cross-check: flat 25 / 50 / 100 / 200 bp per side. A variant is judged on both; the primary decides, the grid may only veto.

## 4. Decision rule (deterministic, PROPOSED values)
A variant V replaces the baseline only if ALL hold on the PIT history (sealed scores) and, for items 1-2, on the shadow stream when it has the rows:
1. Turnover reduction of at least 30 % against the baseline (PROPOSED), and a cost-drag reduction at the primary regime of at least 0.50 pp a year (PROPOSED).
2. Net annual return at the primary regime no lower than the baseline's minus 0.10 pp (tolerance, PROPOSED); maximum drawdown not worse than the baseline's by more than 1.0 pp and, in absolute terms, no deeper than 15 % (mandate; if the baseline itself is deeper, the criterion is "not worse").
3. Cross-check: at 25 bp per side the net return is no lower than the baseline's minus 0.15 pp (PROPOSED), and at 100 bp it is higher.
Among the qualifying variants, rule E2: the highest design Sharpe, then the lowest turnover within a Sharpe tolerance of 0.02. If none qualifies, the baseline stays and the shadow keeps measuring.

## 5. Revert trigger (once adopted)
Revert to the baseline if, over at least 12 consecutive monthly builds, the adopted arm's forward net return is below the baseline arm's by more than 0.50 pp annualised, or its maximum drawdown is deeper by more than 2.0 pp (PROPOSED), or the realised turnover reduction is below half of the PIT one.

## 6. Timing and authority
The owner's ruling may be taken at the 6-row review of the shadow stream (one build per calendar month; about 2027-05 only if builds run monthly) on the deterministic items 1 and 3; items 2 and the revert trigger need 12 rows. History yields a CANDIDATE, not an adoption (R7): adoption switches `PORTFOLIO_HYSTERESIS_ENABLED` / the cadence by the owner's order, shipping OFF until then (R2). After any adopted P3-5a lever this study is re-run (R8).

## 7. Feasibility and informativeness (R1)
The PIT history has 262 monthly dates but about 1 independent month per live shadow row, so the live part is informative only on turnover and cost (deterministic), not on return. The return and drawdown tolerances are therefore judged on the sealed history, whose cost check already showed the net effect at 25 bp to be about zero by structure; a pass at the primary regime and a neutral result at 25 bp is the expected outcome, a fail the baseline-keeps outcome.

## 8. Questions for the owner (each blocks the commit of this draft as an amendment)
- Are the PROPOSED numbers (30 %, 0.50 pp, 0.10 pp, 1.0 pp, 0.15 pp, 12 builds) acceptable, or which does he set?
- Row 12 (the adoption objective) is independent of this amendment but fixes the mandate wording of item 2.
