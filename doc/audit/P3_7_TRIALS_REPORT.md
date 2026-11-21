# P3-7: results of the eight preregistered cost trials (read 2026-11-22)

Preregistration: `doc/decisiones/PREREG_P3_7.md` (committed before any code or number). Code: `scripts/audit/p3/p3_p37_trials.py`, `proyecto3/src/pit_p37_eval.py`, `proyecto3/src/pit_p37_snapshot.py`. Run: `out/reports/p3_p37_trials/20261121_p37/` (git-ignored; `REPORT.md`, `adoption_table.csv`, `amendment5_side_verdict.csv`). Everything is IN-SAMPLE history (today's fees, survivors only, values chosen after the cost check); history yields CANDIDATES for the forward shadow, never an adoption (R7).

## What the owner must decide
1. **Which candidate goes to the forward shadow.** Seven of the eight arms pass all four conditions. My recommendation: **AC** (hysteresis band 0.10 + the fee penalty, H = 7 months): 3.12 % net a year at the stated fees against 0.08 % for the baseline, turnover 13 % a month against 32 %, holding 19 months against 7, drawdown at the stated fees -13.4 % against -26.4 %. D (band 0.20 + penalty) is slightly better on paper (3.23 %) but it rests on the largest band and the longest holds (28 months), which the survivor-only panel flatters most. The band alone already has its shadow stream (Amendment 5); the fee penalty has none yet (needs its own, on your order).
2. **Retire arm B** (the fee-aware class choice): +0.04 pp a year (lower bound -0.06 pp), 1,548 class swaps out of 660,264 score rows. Nothing to shadow; the code stays OFF.
3. **The larger finding, which no cost lever fixes: the model's GROSS return is below the mandate target.** IPC+M3 over the same dates is 6.94 % a year; the baseline's gross (before any fee) is 4.69 %, the candidates' 4.0-4.4 %. At the stated fees the best arm nets 3.2 %, and it beats the 36-month IPC+M3 target in 19 % of windows at best (the baseline 0 %). Cost explains why the baseline nets 0.08 %, not why it misses the target. Whether to open the gross-return question (selection value, P3-6, the regime layer) is your call.

## Reading (stated fees fund by fund = the primary regime; annualised)
| Arm | Net | Gross | Turnover / month | Holding (months) | Max DD at stated fees | Paired gain vs baseline (pp, lower bound) |
|---|---|---|---|---|---|---|
| Baseline | 0.08 % | 4.69 % | 32.2 % | 6.9 | -26.4 % | - |
| A1 band 0.10 | 1.92 % | 4.15 % | 14.8 % | 16.9 | -15.7 % | +1.81 (+1.15) |
| A2 band 0.20 | 2.34 % | 4.04 % | 11.3 % | 23.8 | -15.4 % | +2.22 (+1.38) |
| B class choice | 0.12 % | 4.70 % | 32.3 % | 6.9 | -25.9 % | +0.04 (-0.06) |
| C penalty | 2.53 % | 4.42 % | 28.1 % | 7.6 | -15.1 % | +2.40 (+1.64) |
| AB | 1.92 % | 4.14 % | 14.8 % | 17.2 | -15.7 % | +1.82 (+1.16) |
| AC | 3.12 % | 4.12 % | 13.4 % | 18.7 | -13.4 % | +2.96 (+2.08) |
| ABC | 3.10 % | 4.10 % | 13.3 % | 18.7 | -13.4 % | +2.95 (+2.06) |
| D band 0.20 + penalty | 3.23 % | 3.95 % | 10.0 % | 27.9 | -12.7 % | +3.06 (+2.01) |

- **Condition 1 (drawdown, relative under the stress):** all arms pass (unstressed within 15 % at the flat 25 bp; the stressed worst window no deeper than the baseline's -15.9 % by more than 0.5 pp: the candidates are 0.2 to 2.1 pp shallower).
- **Condition 2:** every arm except B has a Holm-adjusted lower bound well above 0 and a point estimate above 0.5 pp. **The gain is fee arithmetic, not return:** at the flat 25 bp every arm nets the same (3.55-3.71 %), the gain at the stated fees comes from paying 0.7-2.2 pp of cost a year instead of 4.5 pp. The price is a small loss of gross return (-0.5 to -0.7 pp for the bands, -0.3 pp for the penalty alone), which the survivor-only panel probably understates for the long-holding arms.
- **Condition 3:** passes everywhere (the non-crisis return is higher, not lower; the share of 36-month windows above IPC+M3 is 0.0 for the baseline and 0.06-0.19 for the others).
- **Condition 4 (both pools):** the persistent pool at each arm's band (20 draws) nets 0.99 % (band 0), 1.63 % (0.10), 1.64 % (0.20) at the stated fees; the monthly redraw nets -20.5 % (turnover 190 % a month: it is beaten by anything and tells little). The ranking beats the persistent pool in every candidate, by +0.3 pp (A1) to +1.5 / +1.6 pp (AC, D; C +1.5) net; gross it is ahead by +1.1 pp (A1 against its band-0.10 pool, gross 3.07 %) and +0.4 pp (C against the band-0 pool, gross 3.99 %). **The ranking earns its place against a random pool, modestly once the turnover is the same, clearly once the fee penalty cuts it.** The pools do not carry the penalty or the class choice (the prereg's wording), so the penalty arms are compared with a pool that lacks them.
- **Amendment 5 of PREREG_FND-0193, applied as written to A1 and A2 (a separate verdict): both PASS all three rules** (turnover cut 54 % and 65 %, cost drag cut 2.3 and 2.9 pp, net return higher, drawdown shallower, the 25 bp check within -0.01 pp and the 100 bp check +1.6 / +1.9 pp). Its rule E2 (the best design Sharpe among the qualifying bands, 0.05 included) was not run; the band decision stays with the shadow review of Amendment 5.

## Limits that bound every number above
- No independent history is left (the locked episodes were opened by P3-5A): the confirmation is the forward shadow (about one independent month per row; the 6-row review is not before about 2027-05).
- Today's fees (a look-ahead) and a panel with no dead funds (FND-0436): the long-holding arms are flattered. The gross loss shown above is therefore a floor.
- The values (bands 0.10 / 0.20, H = 7 months) were chosen after seeing the cost check and the P3-5A informative runs.
- The class choice does not know what the portfolio holds; with a band it could swap a held class (AB, ABC): the effect is nil here (AB equals A1 to 0.1 bp).

## Integrity and what went wrong (full log: PREREG_P3_7.md section 12)
Identity gate PASSED on the final build (scores, eligible set, monthly gross, turnover, every shipped switch OFF). One check of the gate itself was wrong (a forward return required on the last date) and was corrected test-first. The first build of arm B was a no-op because the PIT scorer already keeps one class per family; found by an integrity check before any performance number was read, the class choice moved into the scorer's own de-duplication, and every arm and the baseline were rebuilt on the final code. All arms read the same frozen snapshot (`42d92e50d483`); A1 and A2 reproduce the sealed score rows exactly; the penalty and the class choice only swap classes inside families (rows per family equal, matched rows keep their eligibility).
