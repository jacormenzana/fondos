# Package A: data integrity and selection protection (cluster A, epic FND-0391)

One page, rewritten in place (never amended). Last rewrite: 2026-11-15, HEAD `d77355d`. Earlier versions are in git (`80dd015`, 2026-11-12).

**Exit metric.** (1) The 0.001 volatility floor is live in the stored data. (2) `DATA_SUSPECT_ENABLED` is ON with the detector canaries green. (3) Every ticket of the package is CLOSED or DEFERRED with a review date, and no fund with a verified NAV defect is eligible for selection.

## 1. Result (read first)
- **Exit metric 1 and 2 are met.** The floor and the `CALC_VERSION` 20261112 recompute are in the stored rows; `DATA_SUSPECT_ENABLED` is ON (eligible set of the PRODUCTION regime unchanged, 1,472 to 1,472); canaries 20 of 20 fired on the latest FULL audit (`post_20261115_114500_p2`, code `6ab05dc`, PASS_WITH_WARNINGS); `applicability_drops` empty. The suspicion report lists no suspect without an exemption.
- **The extreme-deviation set fell from 22 funds (11-08) to 6** (`|OUTLIER_MAD_Z dev| >= 50`, forensic report): 4 SMALL_SAMPLE_METRIC (ES0125756025, LU2330520862, LU2337294180, LU2334589889), ES0116848013 (the verified-genuine case of FND-0403, exemption to 2027-05-07) and **DE000DWS3LN1**. The three review funds of the 11-08 dossier are ordinary now (LU0232465467 dev 11.5 small sample, LU1663960000 dev 6.1, LU0922333322 dev 4.2): the artefacts left with FND-0401.
- **Exit metric 3 is NOT met: DE000DWS3LN1 is the open defect.** Monthly NAV 565.41 (2026-07) to 1,090.70 (2026-08), x1.93, while peers moved +1.8 % (0 of 1,586 the same way); it stays at the new level afterwards (1,040 / 1,043 / 1,065), so it is a series level shift, not a price. It is in the universe with `data_status` OK, eligible in P3 (Dinamica, score 0.7426, rank 226 of 563 eligible, 0 weight in the last scenarios) and no barrier stops it: `NAV_UNSHARED_JUMP_MODE` is SHADOW, and `shared.data_suspect` does not list it (a 32-month series counts as small sample).
- **The SHADOW rule is stable.** `NAV_JUMP_SHADOW` named the same 11 funds in three full P2 passes (11-12, 11-14, 11-15), no new one; 10 are jumps of 2000 - 2022, only DE000DWS3LN1 is recent. `ON` quarantines (metrics cleared, `nav_sources` INVALID_NAV, re-opens by itself when repaired). The config note lists what `ON` needs: a recency window, the verified-genuine exemption (a NAV-level flag is never exempted today) and a labelled precision (FND-0395, deferred).

## 2. Ticket state
| Ticket | State | What it waits for |
|---|---|---|
| FND-0393 (HIGH) | IN_PROGRESS | owner ruling on the guard (section 3); then closes by a `Closes:` delivery |
| FND-0391 (epic, HIGH) | IN_PROGRESS, condition `ticket_closed(FND-0393)` | FND-0393 |
| FND-0404 (HIGH) | OPEN, parked | the operations team's written answer; `P3_DATA_HOLDS` entry for IE00B3L10570 runs to 2027-02-07 |
| FND-0395, 0396, 0397, 0398, 0402 | DEFERRED | their review dates |
| FND-0392, 0394, 0401, 0403, 0405, 0406 | CLOSED | - |

## 3. Proposal (owner decides)
1. **DE000DWS3LN1 and the guard.** Recommended: add a generic RECENCY WINDOW to the unshared-jump rule (a jump counts for quarantine only when its month is within the last N months; N is the owner's number, 12 is the natural start since the ratios read 1-year windows), then `NAV_UNSHARED_JUMP_MODE` ON. On today's census it would quarantine exactly this one fund (n = 1: the precision is not labelled, say so). It is a code + config change: tests, ONE full receipt BEFORE publishing the switch, then a targeted P2 pass for the fund (owner's order) to clear its metrics. Alternatives: (b) a time-limited P3 data hold for the fund (shared.config `P3_DATA_HOLDS`, ticket + reason, at most 90 days): protects selection at once, repairs nothing; (c) leave SHADOW (it is rank 226 of 563 today).
2. **FND-0393 remainder.** Split into ONE deferred child (review date to set): the jump-cluster-by-source signal (no code exists) and the pair-identity canaries (`P2_PAIRS`, `COST_PAIRS`); the per-fund closing condition G-06 is then met by DE000DWS3LN1 only (`audit_isins_clear` and a valid `data_status`). With that, FND-0393 and the epic FND-0391 close by one `Closes:` delivery.
3. **FND-0404 stays parked** (owner's order).
