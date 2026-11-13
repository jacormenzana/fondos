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
| 0308 | KEEP-verify | launcher names match the design (V); audit ownership and RC 107 gate are U |
| 0289 | KEEP-verify | gate `ALARM_WATCH` in `scenario_lifecycle.py` (V), alarm fund not rising in the DRAFT (V); the construction-time cap is U |
| 0256 | KEEP-verify | EU/ES CPI now 2026-09 (V); the `mean(axis=1)` silent-ES-only code path is U |
| 0391, 0393, 0394, 0198, 0370, 0385, 0387, 0281, 0282 | KEEP (U) | looked finished in the logs; none verified here. 0385/0387 close organically on a measured commit; 0281/0282 are the owner's call at 2027-01-15 |
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
- Not touched (no decision yet): FND-0397 stays NO GO, DRAFT promotion, the 0239 / 0385 pair, the WIP limit.

## 5. Next steps after the checkpoint
Add closing conditions (`add_close_condition`); one docs-only commit with `Closes:` and `Solution:` in the same last paragraph as `Co-Authored-By`; autopilot dry run (`backlog_autopilot.py --since-days 1`) must print a decision per ticket; the rest goes to the owner's `close_release.py` list. Then package 1 (integrity of the current selection).
