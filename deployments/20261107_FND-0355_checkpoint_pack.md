# Checkpoint pack: FND-0355 (Backlog v3, automation by default, human by exception)

Measured 2026-11-07, read-only, from the live backlog, the git history and the receipts. The plan is `out/audit/plan_friccion_20261102/ACTION_PLAN_BACKLOG_REFACTORING.md` (Revision 2); the checkpoint is event-based and the 2026-11-09 date is a reference, not a deadline. Definitions are the plan's where it gives them; where it does not, mine are stated and the figure is approximate.

## 1. The finish line (Phase 2 exit criterion)

| Criterion | Result | Evidence |
|---|---|---|
| **O2** a ticket is moved AND closed in one autopilot run from a plain commit, GitHub off | **MET** | FND-0380: `IN_PROGRESS -> READY_FOR_DEPLOY` 09:12:58 and `-> CLOSED` 09:13:05 by `system:autopilot` on 2026-11-07; FND-0278 the same on 2026-11-06 (19:30:40 / 19:30:50). Seven cost incidents closed that way on 11-06 |
| **O3** precision >= 70 % on >= 20 decided pairs | **MET** | `backlog_modules.py --measure`: precision **72 % on 40 decided pairs**, recall 39 % (reported, never a bar); mode AUTO would be chosen |
| **Efficiency** baseline re-measured | **DONE** (section 2) | below |
| **Dispositions** of every B/C item applied | **NOT MET** | section 4: seven tickets still TODO where the plan says defer or verify-and-close (FND-0366 and FND-0373 were already CLOSED) |
| FND-0355 closed with its deferred children listed (B6, "close at 2.9") | **NOT YET** | waits on the dispositions |

## 2. Efficiency re-measure

Step 2.7 numbers (`vw_backlog_kpis` is not readable with the agent login; computed from `backlog.backlog` and `backlog.status_event` with the same definitions):

| Measure | 2026-11-02 | 2026-11-07 |
|---|---|---|
| Tickets closed in the last 30 days | 112 | **108** |
| Median days creation -> closure of those | 1.14 | **2.06** (see note) |
| Status changes not made by a person (last 30 d) | 89 % | **96 %** (system 67 %, agent 30 %, human 4 %; 296 changes) |
| IN_PROGRESS idle >= 14 days | not recorded | 11 of 19 |
| Open tickets (not CLOSED, not DEFERRED) | 80 | **58** (68 counting the 10 DEFERRED) |
| Undecided collision pairs | 3 (20 on 11-07 before today's decisions) | **0** |

Note on the median: the window now holds the seven cost incidents opened 2026-10-12 and closed 2026-11-06 (25 days each) and similar long-lived tickets; the closing speed of new work did not worsen, the sample changed. Not re-computed on tickets opened inside the window.

Section 7 of the base document (definitions of the original not recorded for rows 1 and 5, so mine are stated):

| Measure | 2026-11-02 | 2026-11-07 |
|---|---|---|
| Share of commits on backlog / governance / ops tooling | ~80 % (74 of 92) | **36 %** (34 of 92: commits whose files are ALL backlog, governance, ops, docs, tests or deployment records) |
| Commits that change product code | 0 of 92 | **31 of 92** (cost extraction, universe, M2, audit) |
| Days since the last P2 run / age of the audit | 7 / 7.4 | **0.4 / 0.4** (P2 run 2026-11-06 22:48, full audit `post_20261106_225457_p2`) |
| CI minutes per change / full local suite | 11 / 22 | **no CI** (GitHub off) / full local suite **27 min** (19 receipts, 25.7-29.9); an area receipt is about 1 min since FND-0380 |
| Standing rules | 36 | not re-measured: the counting rule was not recorded |
| Open tickets / undecided collision pairs | 80 / 3 | **58 / 0** |
| Cross-session branch collisions per week | at least 1 | none observed; 6 unpublished local branches remain (the owner's call) |

## 3. State of the switches (read from `shared/config.py` and the preflight)

`BACKLOG_AUTO_DEPLOY_MODE = AUTO` (already back, behind the local DDL gate); `BACKLOG_AUTO_REVOKE_ENABLED = True`; `BACKLOG_GITHUB_EVIDENCE_ENABLED = False` (receipts are local); `BACKLOG_MODULE_CLUSTERS_MODE = AUTO`; `BACKLOG_IMPACT_NOTES_ENABLED = True`. Delivery gate ENFORCE, 0 violations in the last 48 h. A-2 gate counter 61 of 50 real transitions: its threshold is passed, so the revoke may apply by itself behind the local DDL gate; a direct-write tool of the owner that stops working is the sign.

## 4. Dispositions still to apply (the plan's own dispositions, not mine)

| Ticket | Plan | State now |
|---|---|---|
| FND-0331 A-2 revoke | B1 defer, revisit 2026-11-09 | TODO |
| FND-0334 Phase C | B2 verify and close | TODO |
| FND-0335 Phase D | B3 verify, close the live part, move `v_backlog_lean_metrics` to FND-0362 | TODO |
| FND-0351 staging QA | B5 verify, then close or defer | TODO |
| FND-0362 W6 metrics | C2 defer | TODO |
| FND-0363 A-2 revoke | C3 defer until ASK ends; ASK has ended (AUTO) | TODO |
| FND-0366, FND-0373 CI | start of the "GitHub returns" package | already CLOSED when read on 2026-11-08 (this pack listed them as TODO by mistake) |
| FND-0370 writers ship OFF | C7 defer; the owner decides | IN_PROGRESS |
| FND-0372 token saving | measurement | TODO |

## 5. Decisions that are the owner's

1. **Does GitHub return, and in which form?** Evidence for the decision: local receipts work (full 27 min, area about 1 min), the autopilot closes from plain commits with GitHub off, publication is automatic, and there have been no branch collisions since 2026-11-02. Against: the off-machine CI signal and branch protection are gone until a layer comes back (the plan's order: PR flow with a measured CI target, then protection; FND-0366 and FND-0373 are the starting package).
2. **Apply the dispositions of section 4**, or amend them.
3. **Review the 10 DEFERRED tickets** (FND-0033, 0035, 0078, 0167, 0185, 0206, 0209, 0222, 0299, 0304); they return to TODO by themselves on their revisit date.
4. **FND-0370** (writers ship OFF): keep the standing rule or defer it, in tension with D-0 per the plan.

## 6. Verified and not verified

Verified here: the figures above (queries and commands in this session), the autopilot events of FND-0380 and FND-0278, the cluster measure. Not verified: the original definitions of "share of commits on tooling" and "standing rules" (not recorded), any figure of CI minutes (CI is off), and whether the 11 idle IN_PROGRESS tickets are really abandoned (listed in the log of FND-0355, not judged).
