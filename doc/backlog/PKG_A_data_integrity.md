# Package A: data integrity and selection protection (cluster A, epic FND-0391)

One page, rewritten in place (never amended). Last rewrite: 2026-11-10, HEAD `40040d9` (full receipt PASS: 6,885 passed).

**Exit metric.** (1) The 0.001 volatility floor is live in the STORED data: P2 recomputed, audit run, P3 DRAFT built, and the selection is unchanged against PRODUCTION (diff reported). (2) `DATA_SUSPECT_ENABLED` is ON with the detector-liveness canaries green and the eligible-set diff reviewed.
(3) Every ticket of the package is CLOSED or DEFERRED with a review date.

**Built and delivered** (all behind switches, tests + receipts green): minimum-sample floors and stale-row cleanup applied on the live store (FND-0401); shared suspicion predicate with tiers NAV-jump / deviation / integrity counted per quantity; verified-genuine exemptions (ticket + expiry, one grace build; first case ES0116848013, verdict GENUINE, FND-0403 closed);
barriers in the scorer, the promotion gate `D-DATA-SUSPECT` (dormant), the PIT backtest and the `exemption_grace` indicator; structural rule for near-zero-volatility ratios (`DATA_SUSPECT_LOW_VOL_FLOOR` 0.005); P3 data hold mechanism (IE00B3L10570, FND-0404, until 2027-02-07);
volatility floor 0.001 in P2 and the PIT (`RATIO_VOL_FLOOR_ENABLED`, FND-0405): committed, NOT yet in the stored rows (no `CALC_VERSION` bump, no recompute). Read-only tools: `p2_vol_floor_impact.py`, `data_suspect_report.py [--diff]`, `nature_move_impact.py`, `fund_dossier.py`, `nav_forensics.py`.

**Steps left, in order**
1. Owner: take a restore point (owner-only DSN; logical dumps are unscheduled, FND-0290), then order "break standby".
2. Recompute: bump `CALC_VERSION` in `proyecto2/src/pipeline/run_pipeline.py` (or a family override, see FND-0236 / `utils/family_versions.py`: risk, regime and rolling families carry the ratios) as ONE commit + receipt; then P2 full pass, POST audit, P3 DRAFT build; report the selection diff. Expected: 129 of 155,377 ratio rows withdrawn on 26 funds, selection unchanged.
3. Canaries (FND-0393): each hard detector run on its known-bad fixture inside the audit process; a failed canary marks the run INCOMPLETE. Hard gate before the selection effect.
4. Switch-on: `python scripts/audit/data_suspect_report.py --diff`, owner authorises `DATA_SUSPECT_ENABLED` (a reviewed commit + receipt).
5. FND-0404: the operations team audits the T0 share class against the manager's documents; on a verdict delete the `P3_DATA_HOLDS` entry (reviewed commit).
6. Housekeeping (PROPOSED, not yet approved: list it to the owner first): close FND-0401 (delivered), FND-0405 (after step 2), FND-0393 / FND-0394 (after step 4), the epic FND-0391; DEFER FND-0395 and FND-0396 (WS3/WS4 engine: no evidence of need), FND-0397 (owner NO GO; fix isolated behind
   `RF_OPEN_ENDED_ARTICLE_GUARD_ENABLED` = False), FND-0398, FND-0402; triage the older data tickets FND-0041, FND-0233, FND-0250, FND-0329 (close or defer on their recorded evidence).

**Standing orders.** No P1/P2/P3 process without the owner's order. `DATA_SUSPECT_ENABLED` OFF. IE00B3L10570 stays held. `redireccion` (a foreign deletion in the tree) is never staged. FND-0397 stays NO GO.
