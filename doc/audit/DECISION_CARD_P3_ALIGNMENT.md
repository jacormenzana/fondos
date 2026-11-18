# Decision card: P3 alignment (P3-0 item 9), 2026-11-18

One page, answered once. Every value below is **my proposal**, with its reason. **None is recorded as your decision until you confirm it** (reply with the number and "ok", or the value you mean instead).

| # | What is needed | Proposed value | Reason | Needed by |
|---|---|---|---|---|
| 1 | Package letter and queue position for the roadmap (Packages I, J, K are queued) | Letter: your call (I, J, K are taken; C, E, G are reserved for other clusters). Position: after Package I closes (FND-0427 waits for your diff review) | Only one writing session at a time in the shared tree | Before the canonical plan page is committed |
| 2 | **C1 rotation-drag threshold**: continue to the construction study if the champion's yearly cost drag (gross minus net) exceeds R1's by more than | 0.25 pp a year | It is the move in the champion's mean 12-month return between the 25 bp and 50 bp cost assumptions in the stored sensitivity (4.01 / 3.75 / 3.49 %, `cost_sensitivity.csv`), i.e. a cost difference the project already treats as material. For scale: the champion's drag is 0.96 pp at band 0 and 0.50 pp at band 0.10 (T0 §1) | Before the C1 numbers are read |
| 3 | **C1 equivalence margin δ** (cancel the construction study only if the construction gap lies inside ±δ) | 0.10 in Sharpe | Reuses `MIN_MEANINGFUL_DELTA_SHARPE`, a value you already locked on 2026-11-17. Units note: if you prefer return units, the choice is yours | Before the C1 numbers are read |
| 4 | **`MIN_NAV_ROWS` diff rule** (P3-1.7): recommend ON when exclusions touch only funds with under 12 NAV rows and no `Fund_Nature` loses more than this share of its eligible funds | 5 % | Placeholder with no measurement behind it: I propose to set it after P3-1.6 shows the month-to-month change of the eligible set per nature, so that the threshold sits above normal variation | Before the diff is read |
| 5 | **Reconciliation alert delay N** (P3-4, conditional): days after a promotion before an unconfirmed-fills alert | 10 calendar days | The build runs monthly; fills should be confirmed well before the next build | Only if C2 starts P3-4 |
| 6 | **DEFER review date** for the 12 deferred entries of the finding matrix | 2027-01-15 | Same date already used for FND-0257 | Now |
| 7 | **PIT universe for the construction study**: default (retired funds included) or survivor-only | Default universe (survivor-only inflates absolute levels, see the fidelity register item 1), which needs a new PIT run of about 8 minutes cold | Removes the largest known bias that can be removed | Before the P3-2' prereg is signed |
| 8 | **Common-window result** (FND-0303): which page holds it? | Tell me the page, or I search again in P3-5a | I found the prereg but no result page | Before P3-5a |
| 9 | **Static-mix result** of FND-0193 (A3): where is it? | Same | Decides whether P3-6 is retired | Before any P3-6 work |

| 10 | **How soon must a predictive change (Judge, Navigator) be confirmable?** | Follow the FND-0299 checkpoints, 24 months (2028-10) and 36 months (2029-10), unless the feasibility check of P3-5b shows earlier power | `DESIGN_FND-0158_hysteresis_shadow.md` §5 reads the return stream at those dates; only turnover and cost are informative from the first rows. A shorter wait needs a power argument. If you do not want to wait until 2028-10, the honest alternative is to retire REQ-08..10 and REQ-14..19 at C2 | At C2 |

Not on this card because they are not needed yet: the marginal tax rate (only if the vehicle census shows non-transferable funds), a broker statement sample (only if P3-4 starts), and each switch-ON (decided after its diff).
