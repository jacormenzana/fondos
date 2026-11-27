# Backlog ranking for FND-0425 (rewritten 2026-11-28) - ONE page, rewritten in place

Read-only analysis (skill `backlog-priorizer`) of 2026-11-27 night, followed by the owner's rulings and the work of the same session. Facts are marked **[F]** (read from the backlog, the code or the database), inferences **[I]**. The previous ranking (2026-11-17) is in this file's git history.

## 1. Executive verdict
- **Risk: MEDIUM, driven by recovery, not by selection [I].** The off-box backup has not been verified since 2026-11-20 and the external disk fails (FND-0463, owner action) **[F]**. Everything that changes what P3 reads is frozen until the R6 verdict and now waits on it automatically.
- **70 open items [F]** (2026-11-28 00:40): DEFERRED 28, BLOCKED 18, IN_PROGRESS 16, READY_FOR_DEPLOY 5, OPEN 2, TODO 1; HIGH 15, MEDIUM 27, LOW 28.
- **No ticket waits for a manual un-deferral any more [F].** The 15 items gated on the R6 verdict are BLOCKED on FND-0438 with a CONFIRMED DEPENDS_ON link; the autopilot's UNBLOCK rule releases them when FND-0438 closes (it needs the autopilot ON). FND-0078 waits on FND-0100 the same way.
- **Decision required:** the autopilot ON on 2026-12-03 after 20:37 (owner); the disk and the logical backup (FND-0463); the sign-off of FND-0457, 0461 and 0462 after the next P1 / P2 runs; this ticket (FND-0425) is ready to close on this page.

## 2. Evidence coverage
| Source | Scope | Retrieved | Limitations | Completeness |
|---|---|---|---|---|
| `gestion.backlog` | all non-CLOSED, 2026-11-27 23:15 and 2026-11-28 00:40 | 72 then 70 of 70 | none | complete |
| links | CONFIRMED, between open items | 310 events read; 19 open-to-open at the start, 18 DEPENDS_ON on FND-0438 / FND-0100 at the end | PROPOSED links not read | complete for CONFIRMED |
| ticket logs | last non-system log of each item | all open items | older history read only where needed | partial by design |
| `fund_metric_alerts` | all rows, aggregates | 18,382 rows, 3,708 funds | no ticket column: no alert-to-ticket correlation | complete (aggregates) |

Alerts **[F]**: the 2026-11-27 batch holds 405 ALARM rows over 302 funds, all from the three tail-percentile rules (`RET_CAT_P05`, `DD_CAT_P03`, `VOL_CAT_P97`): threshold crossings by design, not defects. The 2026-08-14 batch still holds 7,246 rows without a value (FND-0415, now BLOCKED on FND-0438 because the promotion gate reads the table).

## 3. Prioritised clusters (state at the end of the session)
| Rank | Cluster / cause | Priority | Items | State |
|---|---|---|---|---|
| 1 | **Recovery**: one failing backup disk, a deferred backup not retried the same day | High | 0463 | OPEN, owner action (disk, logical backup); excluded from this session by the owner |
| 2 | **Closure queue**: delivered, not closed | High (leverage) | 0427, 0443, 0445, 0451, 0452, 0453, 0400 (IN_PROGRESS); 0431, 0433, 0446, 0447, 0448 (READY_FOR_DEPLOY) | waits for the autopilot ON (2026-12-03); 0449 and 0450 CLOSED 2026-11-28 by the owner (`R2026.11-FND-0449-0450`) |
| 3 | **P1 attribute truth**: values imputed from Fund_Nature or name tokens; quality flags P3 never reads | High (silent) | 0457 (delivered 77a735d); 0456, 0458, 0459 (built behind switches OFF: 075775c, 76a6f7d); 0460, 0402, 0398, 0397, 0442 | the switches and the rest are BLOCKED on FND-0438 |
| 4 | **Selection value and the R6 verdict** | High value, frozen | 0438 (the gate), 0429, 0430, 0158, 0444, 0454, 0455, 0389, 0434, 0306, 0222, 0257, 0304, 0299, 0436, 0437 | frozen until R6; reader readings of PKG_M section 2 confirmed (ba83977) and reading 4's per-month line built (d867ff7) |
| 5 | **Ops automation and data provenance** | Medium | 0100, 0078, 0239, 0090, 0326, 0248, 0167, 0182, 0209, 0324, 0325, 0461 | 0461 delivered (1027ca7); 0100 deferred to 2027-01-15 by the owner (no new daily task before the 12-01 build) |
| 6 | **Delivery and test speed** | Medium | 0385, 0387, 0400 | 0400 delivered (48c2e6a, Closes); 0387 deferred to 2027-01-15 |
| 7 | **Cost and policy attribute residuals** | Low | 0281, 0282, 0384, 0410-0414, 0416-0418, 0421, 0424, 0462, 0185, 0029 | dated deferrals; 0462 items 2-3 delivered (1027ca7); 0411 returns to TODO on 2026-11-29 |

## 4. Dependency map (CONFIRMED links)
- **FND-0438 (R6 verdict) -> 15 items**: 0158, 0389, 0397, 0398, 0402, 0415, 0434, 0442, 0444, 0454, 0455, 0456, 0458, 0459, 0460 (BLOCKED, released by UNBLOCK).
- **FND-0100 -> FND-0078** (BLOCKED, released by UNBLOCK).
- **FND-0427 -> FND-0443** (leftover), **FND-0445 <-> FND-0453** (RELATED).
- Inferred **[I]**: FND-0463 open = no recent off-box restore point under any live write.

## 5. Owner rulings of this session (2026-11-27 / 28)
1. A ticket that waits on another goes to **BLOCKED on it, never DEFERRED with a manual un-deferral** (D-0): applied to the 15 + 1 above.
2. Cluster 3 fixes that change P3 inputs are **built now behind switches OFF** and flipped after R6 (0456, 0458, 0459).
3. **0456, P3 side: report only** (the monthly report's last column `Calidad P1`); never a gate, a ranking or an exclusion.
4. **0462 stays unblocked**: its log-noise and unit items are worked; only the old-metric-row cleanup waits for R6.
5. **0437** deferred to 2027-06-01 (after R6); **0100** to 2027-01-15; **0429**'s refactor scope held until after R6; **0248** OPEN for the owner's manual step by 2026-11-29.

## 6. Roadmap
| Sequence | Priority | Action | Items | Exit criteria |
|---|---|---|---|---|
| 1 | High | Disk and logical backup (owner) | 0463 | one verified off-box copy newer than 2026-11-20 |
| 2 | High | Check the November rebuild (2026-11-28 09:00) | 0445 | first baseline row, identity MATCH, 0 ERROR |
| 3 | High | Autopilot ON (2026-12-03 after 20:37), then verify the closures | cluster 2 | 0443, 0445, 0451, 0452, 0453, 0400 CLOSED with no reopening |
| 4 | Medium | Sign off after the next runs | 0457, 0461, 0462 | the stored share of dated KIIDs re-measured; the P2-01 OK line; no false frequency rows |
| 5 | Medium | Cycle telemetry | 0239 | its own plan, several sessions |
| 6 | Low | Dated reviews | cluster 7 | each decided on its date |
| 7 | - | After the R6 verdict (about 2027-05) | clusters 3 and 4 | FND-0438 closed releases the 15 by itself; flip the switches of 0456, 0458, 0459 (one config commit with a full receipt; 0458 also needs the cost recompute) |

## 7. Open questions
- **0458** narrows: the UCITS extractor finds no management fee in the 70 newly recognised documents (a classic KIID publishes ongoing charges), so the switch fixes `KID_Format` and the routing, not those empty fee columns **[F]**.
- **0462 item 1** (201 funds with the legacy `Liquidity_Profile` `T5`): the root fix reads the dealing frequency from the KIID; P3 does not read the column, so it is outside the freeze. Not started.
