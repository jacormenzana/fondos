# Handoff: start of the next session (Package A, then B..G)

Written 2026-11-10 at HEAD `40040d9` (origin/master = HEAD; full receipt PASS 6,885 passed, 1,717.8 s). The text between the rules is the prompt to paste into the new session.

---

You are continuing the Fondos project (C:\desarrollo\fondos). Read `AGENTS.md` first, in particular the new section "Working method — outcome packages", then `doc/backlog/PKG_A_data_integrity.md` (the decision record of the package in progress). Run the read-only pre-flight BEFORE touching anything:
`C:\Users\jacor\.conda\envs\des\python.exe scripts/audit/preflight_check.py --mine <your files>`. Other sessions may share the tree; only your own files are yours.

**Method (owner directive 2026-11-10, non-negotiable):** executive and agile, never at the cost of rigor. Work in outcome PACKAGES, one cluster at a time in the order A, B, C, D, E, F, G, each with one exit metric, a one-page decision record rewritten in place (no amendments), tickets only for what
outlives the package, close on recorded evidence, ONE full receipt per package delivery (background `delivery_attest.py --scope full` on the last commit; targeted tests + policy scanners between commits), reports that lead with the decision needed. Reply in English, ultra-short.

**State.** Cluster A (epic FND-0391) is in progress: the floors, the suspicion predicate, the exemptions, the scorer / gate / PIT barriers and the P2 volatility floor 0.001 (FND-0405) are committed and green; the floor is NOT yet in the stored rows. Steps left are in `PKG_A_data_integrity.md` (restore point by the owner,
the `CALC_VERSION` recompute, the liveness canaries, the switch-on, the FND-0404 human audit, the housekeeping list). The housekeeping list is a PROPOSAL: show it to the owner before closing or deferring anything.

**Standing orders.** Strict standby: start NO P1, P2 or P3 process (including the P2 recompute) until the owner orders it. `DATA_SUSPECT_ENABLED` stays OFF until the owner authorises it. IE00B3L10570 stays held (`P3_DATA_HOLDS`, FND-0404) until the operations team's verdict. `redireccion` is a foreign deletion in the tree: never stage it.
FND-0397 stays NO GO (its classifier fix is isolated behind `RF_OPEN_ENDED_ARTICLE_GUARD_ENABLED` = False). No live writes, no push by hand (the owner's watcher publishes), never print DSNs. Agent identity: `FONDOS_ACTOR=agent:claude-sonnet-5-5`; session ids `FND_YYYYMMDD_DEV_<TOPIC>`.

**Facts that will save you time.**
- Recompute: the floor reaches stored rows only through a P2 recompute (bump `CALC_VERSION` in `proyecto2/src/pipeline/run_pipeline.py`, or a family override: FND-0236). A NaN ratio is written as a NULL row (no stale values). Simulation (`scripts/audit/p2_vol_floor_impact.py`, report `out/audit/P2/p2_vol_floor_impact.md`): 129 of 155,377 ratio rows, 26 funds, selection unchanged.
- The restore point is owner-only (`FONDOS_PG_DSN_OWNER` is not in the agent environment) and the logical dumps are unscheduled (FND-0290): ask the owner for it before any bulk recompute.
- P3 reads a missing metric as 0 of its weight (a withdrawn Sharpe = the worst Sharpe of its nature); regime Sharpe/Sortino multipliers are 1.0 and the rolling P3 signals are OFF.
- Tests: use `C:\Users\jacor\.conda\envs\des\python.exe`. proyecto2 tests: `cd proyecto2; pytest --ignore=tests/discovery tests` (tests/discovery calls Morningstar at collection). proyecto2 and proyecto3 both have a `src` package: run them separately. Hermetic Postgres: `python scripts/ops/run_pg_tests.py <paths>` (no pytest flags without `--`).
  Policy scanners to run after the LAST edit before each commit: `shared.delivery_receipt.POLICY_TESTS`. AST-check every edited Python file.
- Traps: the Bash heredoc halves backslashes (write files with the Write/Edit tools); psycopg `Row` repr drops duplicate column names (alias your counts); a commit citing a CLOSED ticket reopens it (cite only open tickets in `Refs:`); backlog scripts need `import shared.config` first (it autoloads `.env`); an agent cannot assign a team (assignee must be `agent:`/`human:`).
- Tools already built: `data_suspect_report.py [--diff]`, `p2_vol_floor_impact.py`, `nature_move_impact.py`, `fund_dossier.py`, `nav_forensics.py`, `fault_injection_run.py`, `clean_stale_metric_rows.py` (applied once, dry run by default).

**Cluster map (75 active items on 2026-11-10; order of work).**
A data integrity / selection protection: 0041 0233 0250 0329 0391 0393 0394 0395 0396 0397 0398 0401 0402 0404 0405 · B P1 cost discovery / KIID quality: 0281 0282 0034 0384 0291 0029 0185 · C P2 recompute, recovery, ops efficiency: 0236 0290 0295 0309 0100 0248 0255 0296 0326 0327 0021 0239 ·
D P3 selection quality / promotion governance: 0221 0222 0259 0289 0306 0158 0198 0299 0206 0257 0304 · E data sources (macro, NAV): 0293 0256 0230 0209 0232 0389 0167 · F delivery and backlog tooling (downgrade; only 0386 receipt speed and 0400 test isolation matter): 0354 0364 0370 0385 0386 0387 0388 0390 0400 0399 0308 ·
G P1 classification / housekeeping tail: 0033 0035 0089 0090 0180 0182 0245 0267 0324 0325 0328 0078. Closure-ready on recorded evidence (propose to the owner): FND-0295, FND-0309 ("closing criterion met"), FND-0399 (auto-incident, resolved). Proposed overrides: FND-0354 defer (multi-actor governance), FND-0395 / FND-0396 defer.

**First actions.** (1) Pre-flight and read `PKG_A_data_integrity.md`. (2) Report the state in five lines and ask the owner for the restore point and the order to break standby, plus approval of the housekeeping list (list every ticket). (3) Continue package A step by step; when A's exit metric is met, write `doc/backlog/PKG_B_*.md`
(one page) and start cluster B (P1 cost discovery: FND-0281, 0282, 0034, 0384, 0291, 0029, 0185; root cause to confirm: `Ongoing_Charge_Recurrent` has three independent writers, FND-0034).

---
