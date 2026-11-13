# Package S0: impact triage of the 70 open tickets (record, rewritten in place)

Written 2026-11-13 at HEAD `f307764`. Read-only census; nothing was changed in the backlog or the repository. Source scripts: scratchpad (`bl_*.py`, `census_*.py`).

## 1. Exit metric (impact-based; ticket count is a by-product)
1. No open risk to the PRODUCTION or DRAFT selection without an owner and a closing condition.
2. Every active ticket has a closing condition or a dated deferral.
3. At most 5 tickets IN_PROGRESS outside the package being worked.

## 2. Measured state
- 70 open: 53 active (24 OPEN, 22 IN_PROGRESS, 5 TODO, 2 BLOCKED) + 17 DEFERRED. 16 HIGH, of which 13 active.
- 38 of 53 active have no closing condition; 10 have STALE probes. Flow since 09-21: 307 created, 250 closed; last 2 weeks 32 in, 42 out.
- Portfolio: PRODUCTION `…202610_b20261025150959` (30 holdings, 19 days old); latest DRAFT `…202611_b20261112220456` differs by 6 funds, one-way turnover 18 %; 11 DRAFTs exist, none promoted since 10-25.
- Only 5 open tickets cite a held ISIN: 0289 (LU0690374029, DE000A0X7541; DE000A0X7541 has an ALARM, weight 1 % -> 1 %, not rising), and 0391/0393/0397/0402 (IE00008TZ2E8, 5.5 % in PRODUCTION and in the DRAFT; Renta Fija Corto Plazo, SRRI 3, duration and credit derived, not discovered).
- Alerts: 557 ALARM rows across the universe (RET_CAT_P05 264, DD_CAT_P03 180, VOL_CAT_P97 113); 1 ALARM holding in PRODUCTION.

## 3. Verdicts (V = verified read-only today; U = not verified, stays KEEP until it is)
| Ticket | Verdict | Evidence |
|---|---|---|
| 0041 | CLOSE (V) | 7 funds flagged STALE_FROZEN (ticket: 0 ever flagged), all `In_Current_Universe=0`; NAV quarantine ON |
| 0233 | CLOSE (V) | its 3 funds left the universe through the quarantine; 0 active funds with NAV > 60 d not flagged |
| 0230 | CLOSE (V) | `beta_ipc_jp` 2,045 funds, `beta_ipc_cn` 3,083; no dominant value (top value 1.9-4.3 %) |
| 0293 | CLOSE after unit check (V partial) | all cited series fresh (CPI JP/CN/EU 2026-09, M2 JP/CN 2026-09, CN policy rate 43 d); the "CN stored as a YoY rate" item is U |
| 0296 | CLOSE (recorded) | log 2026-11-04: NAV discover 48 s vs ~95 min |
| 0308 | CLOSE (V, wave 2) | renames done; `lib/cyclePhases.bat` exists; `--audit-compare-to` in `P3_buildPortfolio.bat` and both orchestrators; RC 107 baseline gate in `lib/common.bat` |
| 0289 | CLOSE (V, wave 2) | its own condition ("until a promotion passes under the gate") is met: `ALARM_WATCH_GATE_ENABLED` True; PRODUCTION promoted 2026-10-25 15:48 after the gate went live (10-17), record "defects on scenario 0 ... AUTHORISED", no override; watched fund not rising; part 3C deferred by the owner on 2026-10-17 |
| 0394 | CLOSE (V, wave 2) | predicate, exemptions, scorer exclusion, gate, PIT and selection-impact KPIs delivered; switched on by `96cd9bb` (2026-11-12); the plausibility tier is conditional on Leverage_Used (FND-0398, deferred) |
| 0256 | KEEP | monitoring delivered (`shared/macro_freshness.py`: TTL per series, composite legs judged one by one); it is "monitoring only"; `regime_classifier.py:247` still averages with `mean(axis=1)` and nothing refuses a stale leg. Missing: the owner's decision to enforce (today both legs, ES and EU 2026-09, are inside their 100-day TTL) |
| 0393 | KEEP | detector canaries exist (`run_statistical_audit.py`); the applicability-drop rule (> 50 % below the median of the last 5 FULL runs) is not in `shared/statistical_audit` |
| 0391 | KEEP | epic: closes when 0393 closes; WS3 / WS4 are deferred in 0395 / 0396 (review 2027-02-10) |
| 0281, 0282 | KEEP by design | 25 and 40 ISINs accepted until 2027-01-15; a ticket stays open while its acceptance is active (0282 log, 2026-11-04) |
| 0370 | KEEP | the writer-proof norm is live; a test pinning "config default = documented default" (item d) is not confirmed: `test_backlog_writers.py` has none and a wider search was not completed |
| 0198, 0385, 0387 | KEEP (U) | 0198: the owner-run A/B (`--current-universe-only`) is not recorded; 0385 / 0387 close organically on a measured commit |
| 0290 | KEEP, ACTIVE INCIDENT (V) | `Fondos PG offbox copy` exited 3 at 03:30 today; no logical-dump task registered |
| 0021 | owner: reject as superseded? | the NAV quarantine (universe=0) does what the ticket's FORCE_REFRESH aimed at, without the cross-layer write |
| LOW, no recorded impact: 0090, 0180, 0182, 0267, 0324, 0325, 0326, 0327, 0328, 0329 | propose DEFER to 2027-02-10 | same review date as the package A housekeeping |

Corrections to Revision 1: the flat `ipc_yoy_avg` for Sep-Nov is expected (September is the latest published CPI), not a defect; IE00008TZ2E8's 5.5 % holding was already known to the owner (FND-0397 text), so the question is only whether the NO GO order should stand.

## 4. Decisions for the owner (ranked, with a default)
1. **Backup, today:** attach D: and re-run the off-box copy, or approve registering a 03:30 logical-dump task independent of the D: mount (default: register it). Verify on two consecutive days.
2. **FND-0397 / 0402 (IE00008TZ2E8, 5.5 %):** keep NO GO (current order) or start a read-only review now (default: keep NO GO; the review changes no portfolio).
3. **DRAFT promotion:** the owner decides; evidence still to be produced: the promotion dry run and the P2 audit link for the 11-12 DRAFT.
4. **Closure list:** approve closing 0041, 0233, 0230, 0296 (and 0293 after the unit check), and the DEFER list of section 3.
5. **WIP limit and the 0239 / 0385 collision pair** (undecided for 153 h).

## 5a. Applied 2026-11-13 (owner: "start without the backup")
- FND-0290: incident note logged (log 2270); the backup fix stays the first item of the next owner window.
- DEFERRED to 2027-02-10 (events 658-667): 0090, 0180, 0182, 0267, 0324, 0325, 0326, 0327, 0328, 0329.
- 0293 unit check done: China CPI is stored as an index (69-117), so 0293 joins the closure list.
- This commit closes 0041, 0233, 0230, 0296, 0293 (CODE_FIX) through the autopilot; closing conditions `tests_pass` are bound to this commit.
- Wave 1 closed by the autopilot (all five CLOSED). Wave 2 (second closing commit): 0289, 0308, 0394.
- Later the same day (owner delegated judgement; backup deferred): FND-0256 closed after the P3 leg-level freshness check shipped SHADOW (`cfb35b1`) and ON (`4c7a56b`); pair 0239 / 0385 decided NONE (adjudication 41); 0221, 0198, 0239 back to TODO; `redireccion` removed (`f6c7799`, owner's written OK); the 11-12 DRAFT was promoted by the owner. Next: `HANDOFF_S0_PACKAGE_1.md`.
- Not touched at that point (no decision yet): FND-0397 stays NO GO, DRAFT promotion, the 0239 / 0385 pair, the WIP limit.

## 5b. Promotion readiness of DRAFT `cartera_shock_energetico_202611_b20261112220456` (dry run, read-only, 2026-11-13)
- Passes: weights sum 1.0000, 30 funds; non-EUR exposure 30.0 % (budget 30 %); structural liquidity 0 % (cap 10 %); P3 freshness clean; P2 audit `post_20261112_221002_p2` (age 0 d, built after the DRAFT); 0 hard defects in or out of the portfolio; audit coverage OK; alarm watch 0 funds; data suspicion 0 funds; DATA pillar PASS, ORCHESTRATION PASS.
- **Only blocker: `[GATE:CODE]` "1 modified tracked file" = the foreign deletion `redireccion`** (TECHNICAL = FAIL). Whoever owns that deletion must commit or restore it; then the same gate should pass. Promotion stays the owner's command.
- Informational (does not block): 175 statistical ALARM flags in 19 held funds; the largest are LU0474963815 (26), FR0000989626 (23), LU2095450123 (22), FR001400I0X7 (16), IE00B3Q8M574 (14) and **IE00008TZ2E8 (46, the fund of FND-0397 / 0402, held at 5.5 %)**. Types: pct_negative_months, pct_positive_months, ret_vol_simple, return_ann_slope. This is new evidence for the FND-0397 decision, not proof of a defect.
- Turnover against PRODUCTION: 6 funds out (15.5 %), 6 in (18.0 %), one-way 18 %.

## 5. Next steps after the checkpoint
Add closing conditions (`add_close_condition`); one docs-only commit with `Closes:` and `Solution:` in the same last paragraph as `Co-Authored-By`; autopilot dry run (`backlog_autopilot.py --since-days 1`) must print a decision per ticket; the rest goes to the owner's `close_release.py` list. Then package 1 (integrity of the current selection).
