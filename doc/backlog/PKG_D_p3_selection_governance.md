# Package D - P3 selection quality and promotion governance

Owner order 2026-11-15 ("D first"), after FND-0422; decisions of the same day recorded below. One page, rewritten in place. No P1 / P2 / P3 process is run by this package without the owner's order for that run (the rebuild of section 4 was ordered). Exit metric: every ticket of the cluster is closed on recorded evidence, or has the owner's decision it waits for written down, with nothing left "in progress" without a next event.

## 1. Result (read first)
- **FND-0409 delivered** (`Closes:` of its own commit): `estimate_rotation_cost` mixed two units. `fund_master.Entry_Fee_Pct` / `Exit_Fee_Pct` are RATIOS (live universe: 2,475 stored entry fees, range 0 - 0.0638, none above 1), `rotation_costs.*` and the defaults are PERCENT; the old `COALESCE(...) / 100` divided a ratio by 100 once too often (a 5 % entry fee cost 0.05 %). Now the per-fund ratio is used as it is, the per-nature and default percent are divided by 100, a stored 0.0 stays a real zero (`portfolio_builder.fee_fraction`, pure; 7 unit tests). `ROTATION_COST_GATE_ENABLED` stays OFF: no selection changes today.
- **FND-0221, FND-0198 and the epic FND-0259 close on recorded evidence, by the delivery route** (this commit carries their `Closes:`; the owner chose it over `close_release.py` because FND-0198's probe state is STALE since an impact note of 2026-11-14 and only a delivery fact lets the autopilot re-run it; FND-0236 followed the same sequence). Evidence: FND-0221 (Crisis_Financiera attribution, log of 11-14 15:31: the 1-month weakness is mostly the 25 bp window cost, the 12-month one is the real signal; diagnostic only, no rule changed) and FND-0198 (three-run isolation, log of 11-14 15:29: config effect and universe effect separated) are analyses with their results on file; FND-0259 has every child phase CLOSED as CODE_FIX (GOV-01..06 = FND-0260 .. 0265 on 2026-10-12 / 10-17; also FND-0251, 0234, 0254) and six `ticket_closed` conditions. The autopilot stamps `CODE_FIX`, the only solution type a delivery closure has.
- **FND-0158: the owner's rule is adopted.** The most conservative pre-registered rule: the MINIMAL hysteresis band that captures the core turnover cut, not the in-sample Sharpe peak. On the PIT grid of 2026-11-14 (`out/reports/pit_backtest/20261114_145358`, 25 bp, 262 months) bands 0 / 0.05 / 0.10 / 0.20 / 0.40 give monthly turnover 32.2 / 18.8 / 14.8 / 11.2 / 9.2 %: the minimal band is 0.05 (turnover -41.6 %, cost drag 0.97 -> 0.56 pp/yr; its Sharpe 0.69 is within noise and is NOT the reason). The ticket is unblocked (IN_PROGRESS) for implementation behind out-of-sample / shadow validation; nothing is enabled. The plan is in its log: (1) register 0.05 in `shared/config.py` (today 0.10) with `PORTFOLIO_HYSTERESIS_ENABLED` still False, after the rebuild, with a full receipt; (2) validate on the forward holdout of FND-0299 and in shadow against band 0 on each monthly build; (3) the enabling decision is separate and the owner's.
- **FND-0257: DEFERRED** (review 2027-01-15) with the owner's trigger: any build whose master non-EUR exposure moves off 30.0 %. Evidence: the six latest builds (DRAFT, PRODUCTION, ARCHIVED, 10-26 .. 11-15) are exactly 30.0 % of the master (`shared.fx_exposure`).
- **The stale DRAFT** `cartera_shock_energetico_202611_b20261115002532` was built (11-15 00:25) from the wrong natures of FND-0422. Against PRODUCTION (11-14 03:39, built before the bug) 28 of 30 funds are common; the two swaps are the two held funds whose nature the FND-0422 pass changed. PRODUCTION is untouched. It is not to be promoted.
- **Receipt speed (feeds FND-0385 / 0387):** the full receipt of `c4c94a2`: scope full, 7,265 passed, 545.8 s (9.1 min).

## 2. Cluster state
| Ticket | State after this commit | What it waits for |
|---|---|---|
| FND-0409 | closes by its own commit | its receipt |
| FND-0221, FND-0198, FND-0259 | close by this commit's `Closes:` | the receipt of this commit, then the autopilot |
| FND-0257 | DEFERRED, review 2027-01-15 | the trigger of section 1 |
| FND-0158 | IN_PROGRESS (unblocked) | steps (1) - (3) of section 1 |
| FND-0306 | BLOCKED | a SECOND stamped DRAFT compared in shadow, then the (a)/(b) decision and the review of `PROMOTION_AUDIT_IDENTITY_GATE_ENABLED` |
| FND-0222, 0299, 0206, 0304 | DEFERRED | their own review dates (0299: blind months from 2026-11) |

## 3. Fallback
If, after the watcher's next pass, any of the three is still READY_FOR_DEPLOY, the owner's `close_release.py` (dry run first, in cmd.exe) is the fallback: `C:\Users\jacor\.conda\envs\des\python.exe scripts\gov\backlog\close_release.py --release R2026.11-PKG-D --tickets FND-0221,FND-0198,FND-0259 --log "closing on the recorded evidence of PKG_D_p3_selection_governance.md"`.

## 4. The rebuild (owner's order: right after FND-0409 closes)
One `P3_buildPortfolio.bat` run (a new DRAFT plus the P2 audit it persists afterwards; `ROTATION_COST_GATE_ENABLED` OFF, PRODUCTION untouched, no band change before it so the identity comparison of FND-0306 is not disturbed). Then the new DRAFT is compared with PRODUCTION and the stale DRAFT, and the real promotion attempt that gives FND-0306 its second shadow row is the owner's.

## 5. Decisions pending
(1) The promotion attempt on the new DRAFT (owner, after the rebuild). (2) The enabling of hysteresis, after the validation of section 1, step (2).
