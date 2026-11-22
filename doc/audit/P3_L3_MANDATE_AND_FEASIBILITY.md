# P3 layer 3 (regime / allocation, FND-0435): the mandate frontier and what a study could decide, 2026-11-22

Read-only, on the sealed run `20261119_202509` (9 artifacts hash-verified). The frontier below is DESCRIPTIVE (hindsight, frictionless, survivors only); no model variant was run and nothing was tested. It answers the item agreed before layer 3 (handoff section 4, point 4): the static ceiling under the drawdown cap against the target.

## Decision needed (the mandate, yours; I record it, never derive it)
The target (IPC+M3, 6.94 % a year) cannot be reached by ANY drawdown limit with a static allocation of these pools: the best static mix at no limit at all is 6.32 % (all in Dinamica, -39.9 % drawdown). The 15 % cap is not what blocks the target; the target is out of reach of the allocation itself, before fees. Choose:
1. **Keep target and cap, and say so in every report** ("the model cannot meet the target; its attainable range is X"). No layer-3 study can close the gap except by near-perfect avoidance of one episode (section 3).
2. **Restate the target** to the attainable range at the 15 % cap (gross 4.1 %, net of stated fees about 3.1-3.2 % for the P3-7 arms) or to a net-of-fee objective. A number is yours to set.
3. **Relax the cap** (20 % gives 4.9 %, 25 % gives 5.2 %): still below the target, at a loss the mandate (capital preservation) does not accept.
My recommendation is 2, stated as an attainable band with the cap kept, because options 1 and 3 leave a promise the data cannot keep. Whatever you choose I will write as your decision, not as a derived value.

## 1. The static frontier (best constant mix of the three matched pools, 262 months)
| Drawdown limit | Static ceiling (annual, gross) | Mix Defensiva / Equilibrada / Dinamica | Realised max drawdown |
|---|---|---|---|
| -10 % | 3.36 % | 0.6 / 0.4 / 0.0 | -9.7 % |
| **-15 %** | **4.14 %** (reproduces the D0 figure) | 0.3 / 0.7 / 0.0 | -14.8 % |
| -20 % | 4.90 % | 0.0 / 1.0 / 0.0 | -19.6 % |
| -25 % | 5.20 % | 0.0 / 0.8 / 0.2 | -23.9 % |
| -30 % | 5.64 % | 0.0 / 0.5 / 0.5 | -30.0 % |
| -35 % | 5.92 % | 0.0 / 0.3 / 0.7 | -33.9 % |
| -40 % and beyond | 6.32 % | 0.0 / 0.0 / 1.0 | -39.9 % |
Caveats: the pools are today's survivors (FND-0436), so every row is flattered; the ceiling is gross and frictionless (the P3-7 arms net 3.1-3.2 % at the stated fees); it is a hindsight optimum over 262 months, not something a model could have chosen in advance.

## 2. What the existing evidence says about layer 3
- **The regime mix earns its place on drawdown, not on Sharpe** (FND-0193, run 20261019_080808, in-sample, predates the nature repair): the static mix instead of the seven regime rows reaches -20.5 % against about -14 % for the model; Sharpe difference not significant.
- **Turning-point diagnostic (FND-0435, `doc/audit/DIAGNOSTICO_TURNING_POINT_P3.md`):** one episode, 2008. The Monetario weight at the top is almost all the nature bonus (x1.25), not the filters; the max-drawdown weight protects at the top and costs at the bottom (a symmetric trade-off); the since-inception drawdown filter blocks the 2009 recomposition. The ranking-side levers that act on these (L1 filters, L3 recovery) were tested in P3-5A: L1 worsened the 2008 drawdown, L3 was inert, nothing was adopted.
- **Not built:** the fast turning-point layer (the "Navigator": HMM, short EMAs). Today nothing rescues funds at a turning point except the hard short gates.

## 3. What a layer-3 study could decide
- **Bound:** only perfect avoidance of 2007-11..2009-03 reaches 7.75 % (clears 6.94 %); halving that episode's losses gives 6.34 % (does not). A target-closing effect therefore needs near-perfect, ex-ante timing of one episode: not a credible design goal.
- **Test power:** the KPI is a drawdown under the 15 % cap, a maximum with no closed-form standard error, from few episodes; the locked episodes were spent by P3-5A and the confirmation rests on the forward shadow (FND-0299 checkpoints 2028-10, 2029-10), where a 15 % pool drawdown may simply not occur. By the rule already signed (P3-5A and P3-7), any layer-3 candidate can be tested for NON-INFERIORITY on the 15 % cap and the return, with adoption left to the forward shadow, never shown to improve on the history.
- **Candidates with a mechanism and no prior trial:** the nature bonus (x1.25; sets the Monetario weight at both turning points) and a performance-chasing control in Equilibrada (the top-gap carrier: alpha_persistence, since-inception return). A recovery-aware allocation (the Navigator) is a new build, not a lever.

## 4. Proposal after your mandate answer
Open ONE preregistration for the risk question that has a mechanism: the nature bonus and the chasing control as at most two levers, two values each, judged on non-inferiority and the 15 % cap, confirmation by the forward shadow, a trial ledger and Holm over its family. If you choose option 2 the attainable band replaces the 6.94 % in the reports and in the adoption rules that cite it. No code and no production change before your answer. FND-0435 closes through the code commit of whatever is built, with its own receipt.

## 5. Disclosure (R9)
Read: the pool monthly returns of the sealed run, summarised only by the best static mix at eight drawdown limits (one of which, -15 %, was already read in D0). No model series, no variant, no point estimate of any effect. Script: `frontier.py` in the session scratchpad (calls `pit_crisis_diagnostic.best_static_mix` on `pool_returns`, nothing else); it is the D0 call with other limits.
