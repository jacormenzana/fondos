# Handoff: start of the next session (receipt-speed package first, then cluster A, then B..G)

Refreshed 2026-11-11 at HEAD `36256ee` (origin/master = HEAD; its full receipt PASS: 6,895 passed, 0 failed, 54 skipped, 544.1 s through the new sharded path; the previous one took ~28 min). The text between the rules is the prompt to paste into the new session.

---

You are continuing the Fondos project (C:\desarrollo\fondos). Read `AGENTS.md` first (especially "Working method — outcome packages"), then this file, then the two package records: `doc/backlog/PKG_RECEIPT_SPEED.md` (first) and `doc/backlog/PKG_A_data_integrity.md`. Run the read-only pre-flight BEFORE touching anything:
`C:\Users\jacor\.conda\envs\des\python.exe scripts/audit/preflight_check.py --mine <your files>` (it can take a while: let it finish, do not start a second copy). Other sessions may share the tree; only your own files are yours.

**Method (owner directive 2026-11-10, non-negotiable).** Quality is never traded for efficiency, and quality must not be a source of inefficiency: same coverage at lower cost by FEWER, CONSOLIDATED and FASTER controls, never weaker ones. Work in outcome PACKAGES, one cluster at a time (A, B, C, D, E, F, G), each with one exit metric and a one-page decision record rewritten in place (no amendments);
tickets only for what outlives the package; close on recorded evidence; ONE full receipt per package delivery (background `delivery_attest.py --scope full` on the last commit; per-commit receipts are PROHIBITED unless isolating an error; between commits: AST check, targeted tests, policy scanners); reports lead with the decision needed. Reply in English, ultra-short.

**Standing orders.** Strict standby: start NO P1, P2 or P3 process (including the P2 recompute) until the speed controls are 100% operational AND the owner orders it. `DATA_SUSPECT_ENABLED` stays OFF until the owner authorises it. IE00B3L10570 stays held (`P3_DATA_HOLDS`, FND-0404) until the operations team's verdict. `redireccion` is a foreign deletion in the tree: never stage it.
FND-0397 stays NO GO (its classifier fix is isolated behind `RF_OPEN_ENDED_ARTICLE_GUARD_ENABLED` = False). No live writes, no push by hand (the owner's watcher publishes), never print DSNs. Agent identity: `FONDOS_ACTOR=agent:claude-sonnet-5-5`; session ids `FND_YYYYMMDD_DEV_<TOPIC>`. Never kill processes by name or pattern (the permission layer blocks it): let runs end.

**FIRST: the receipt-speed package (owner priority, structural objective: 1-3 minute validations).** Done and green: the sharded full receipt (`run_pg_tests.py --shards auto [--durations FILE]`, `DELIVERY_SHARDED_RUN_ENABLED`; shards 483-509 s; a failing file is re-run alone, real AND parallel-only failures both fail). Left, in order:
(1) FND-0387: symbol-level impact for `shared/config.py` (a hub read by 386 of 439 test files): a new constant must stop forcing `full`. (2) FND-0385: promote the impact selector SHADOW -> ENFORCE. The owner WAIVED the 2026-11-14 date (2026-11-10) and ordered "implement and enforce as soon as the code is ready": look at `shared/test_impact.py::effective_mode`,
`DELIVERY_IMPACT_SELECTION_MODE` and what besides the date gates promotion (recorded evidence in `out/audit/delivery_receipts.jsonl`, kind `impact_evidence`); activate WITH THE OWNER PRESENT (it changes a control). (3) Optional: split the 356 s launcher e2e file by test node, or run the 3 launcher e2e files only when a launcher changes.
(4) Re-measure on real receipts and close FND-0386 / 0385 / 0387 when the exit metric holds: typical full receipt < 10 min, a change the selector can bound < 3 min, same coverage (replay of the 6 failing receipts of 2026-11-02..10: none may be missed).

**THEN cluster A (epic FND-0391; `PKG_A_data_integrity.md`).** The floors, the suspicion predicate, the exemptions, the scorer / gate / PIT barriers and the P2 volatility floor 0.001 (FND-0405) are committed and green; the floor is NOT yet in the stored rows. Steps left: restore point by the owner (owner-only DSN; logical dumps unscheduled, FND-0290), the order to break standby,
the `CALC_VERSION` recompute (or a family override, FND-0236) + audit + P3 DRAFT + selection diff, the detector-liveness canaries (FND-0393), the switch-on of `DATA_SUSPECT_ENABLED` (owner), the FND-0404 human audit, and the housekeeping list. The housekeeping list is a PROPOSAL: show it to the owner before closing or deferring anything.

**Facts that will save you time.**
- P3 reads a missing metric as 0 of its weight (a withdrawn Sharpe = the worst Sharpe of its nature); regime Sharpe/Sortino multipliers are 1.0 and the rolling P3 signals are OFF. A NaN ratio is written as a NULL row (no stale values).
- Tests: `C:\Users\jacor\.conda\envs\des\python.exe`. proyecto2: `cd proyecto2; pytest --ignore=tests/discovery tests` (tests/discovery calls Morningstar at collection). proyecto2 and proyecto3 both have a `src` package: separate processes. Hermetic Postgres: `python scripts/ops/run_pg_tests.py <paths>` (no pytest flags without `--`). pytest-xdist is NOT installed; the machine has 6 CPUs.
  Policy scanners after the LAST edit before each commit: `shared.delivery_receipt.POLICY_TESTS`. AST-check every edited Python file.
- Traps: the Bash heredoc halves backslashes (write files with the Write/Edit tools); psycopg `Row` repr drops duplicate column names (alias your counts); a commit citing a CLOSED ticket reopens it; backlog scripts need `import shared.config` first (it autoloads `.env`); an agent cannot assign a team (assignee must be `agent:`/`human:`);
  a test must not read files that exist only in the working tree (`out/`): receipts run on a clean clone.
- Efficiency audit numbers (2026-11-02..10): 37 full runs = 17.3 h, median 28 min, 26 of 31 passes covered ONE commit; the 6 failures were all real and found by tests under 6 s in total; 5 of 415 test files = 50% of test time (3 launcher e2e files 43%).
- Read-only tools already built: `data_suspect_report.py [--diff]`, `p2_vol_floor_impact.py`, `nature_move_impact.py`, `fund_dossier.py`, `nav_forensics.py`, `fault_injection_run.py`, `clean_stale_metric_rows.py` (applied once).

**Cluster map (75 active items on 2026-11-10; order of work after the receipt-speed package).**
A data integrity / selection protection: 0041 0233 0250 0329 0391 0393 0394 0395 0396 0397 0398 0401 0402 0404 0405 · B P1 cost discovery / KIID quality: 0281 0282 0034 0384 0291 0029 0185 · C P2 recompute, recovery, ops efficiency: 0236 0290 0295 0309 0100 0248 0255 0296 0326 0327 0021 0239 ·
D P3 selection quality / promotion governance: 0221 0222 0259 0289 0306 0158 0198 0299 0206 0257 0304 · E data sources (macro, NAV): 0293 0256 0230 0209 0232 0389 0167 · F delivery and backlog tooling (downgrade; the receipt-speed package covers 0386 / 0385 / 0387): 0354 0364 0370 0385 0386 0387 0388 0390 0400 0399 0308 ·
G P1 classification / housekeeping tail: 0033 0035 0089 0090 0180 0182 0245 0267 0324 0325 0328 0078. Closure-ready on recorded evidence (propose to the owner): FND-0295, FND-0309 ("closing criterion met"), FND-0399 (auto-incident, resolved). Proposed overrides: FND-0354 defer, FND-0395 / FND-0396 defer.

**First actions.** (1) Pre-flight; read `AGENTS.md`, this file and the two package records. (2) Report the state in five lines (HEAD, receipt, tree, what runs, standing orders). (3) Start FND-0387, then FND-0385 as above, with the owner present for the enforcement. (4) When the receipt-speed package's exit metric is measured, write the next one-page record and continue with cluster A, then B..G.

---
