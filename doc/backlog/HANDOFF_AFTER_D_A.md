# Handoff after Packages D, A and the receipt tail (2026-11-15)

Read `AGENTS.md` ("Working method - outcome packages"), then this file, then `HANDOFF_AFTER_PKG_H.md` section 2 (standing orders: all still in force), `PKG_D_p3_selection_governance.md`, `PKG_A_data_integrity.md`, `PKG_F0422_nature_evidence_load.md` and `doc/decisiones/DESIGN_FND-0158_hysteresis_shadow.md`. Run the read-only pre-flight first. Reply in English, decision first, ultra-short.

## 1. State in six lines
1. HEAD `faca31f`, master equals origin, nothing running, cycle lock free. 53 tickets open: 38 DEFERRED and 15 active (IN_PROGRESS 0158, 0290, 0384, 0385, 0387, 0400; OPEN 0100, 0388, 0389, 0404; TODO 0239, 0248, 0364; BLOCKED 0029, 0306).
2. Closed today: FND-0422 (a local import broke the nature evidence loads for a day), 0409 (rotation cost units), 0221, 0198, 0259 (by `Closes:` delivery), 0393 + epic 0391 (unshared-jump rule ON with a 12-month window), 0386 (sharded receipts). Deferred: 0257 (2027-01-15), new child 0424 (2026-12-15).
3. Live changes made with the owner's orders: a P1 pass restored 119 natures (backup `out/f0422/`); P3 PRODUCTION is `cartera_shock_energetico_202611_b20261115113822` (the owner promoted it; the previous one is ARCHIVED); DE000DWS3LN1 is quarantined (INVALID_NAV, 344 metric rows deleted).
4. The cycle report has a new line (`nature_evidence`: the share of dispatches without a volatility band or Morningstar class against the share of the universe that lacks them).
5. Receipts of the day: 545.8, 582.7, 535.8 (failed: Postgres deadlock between shards), 506.2, 570.1 s. `pg_conn` now gets a private schema per test (`shared/testing/pg_fixtures.py`).
6. `PORTFOLIO_HYSTERESIS_BAND` stays 0.10, `PORTFOLIO_HYSTERESIS_ENABLED` and `ROTATION_COST_GATE_ENABLED` OFF, `PROMOTION_AUDIT_IDENTITY_GATE_ENABLED` OFF.

## 2. Owner decisions pending
1. **FND-0158:** the order to BUILD the monthly hysteresis shadow (design recorded; criteria are his to pre-register before the first read). Enabling is a later, separate ruling.
2. **FND-0290 (backups):** the dump runs by itself without D: (proven: `fondos_20261115_0400.dump`, taken right after the cycle). He decides to close it on that, retiring the three `task_ok` conditions with his words; the copy to D: and the PITR mirror need the disk plugged in (`[CARGA MANUAL]`; the automated alternative is an off-box target, the scope of FND-0071).
3. **FND-0306:** nothing until a real promotion attempt has a commit or a P2 recompute between build and audit. **FND-0404 (later the same day): RELEASED by the owner ("release")**: the manager's own page (BlackRock: a Distributing class at a constant EUR 1 under the Reverse Distribution Mechanism, rebased x100 on 2019-03-18) and the Morningstar chart match our series; the five spikes of the chart are five isolated prints of 1.0 that the FND-0177 gate removed (not nulls). The `P3_DATA_HOLDS` entry is deleted (registry empty again) and the ticket closes by its delivery; IE00B3L10570 is eligible at the next P3 build (its selection is not measured). Caveat kept on the ticket: before 2019-03-18 its history is a price series (income distributed), so any metric over those years understates it.
4. **FND-0388** (a control for the receipt scope: an owner decision), **FND-0239** (collision pair with FND-0385, only he decides) and **FND-0248** (repoint DBeaver / Metabase): his. **FND-0100:** he said "not now" (2026-09-27).
5. Dates: rulings on FND-0411 and 0415 due 2026-11-29; Package H residual review and FND-0424 on 2026-12-15; FND-0281 / 0282 expire and FND-0257 is reviewed on 2027-01-15.

## 3. What an agent can do without him (small)
- At the next P3 build confirm DE000DWS3LN1 is absent from the candidates, and read the cycle report's evidence line on the next P1 pass.
- FND-0385 / 0387: close organically when a BOUNDED change (under 3 minutes) is receipted; add its scope and seconds to `PKG_RECEIPT_SPEED.md`.
- FND-0400: the "a failing PG test cascades" subclass is not addressed (the deadlock one is fixed). FND-0389 (Bund / ES-DE yield factors, LOW) only if he wants it.
- Package order if he wants more: the F tails, then E (FND-0389), B (FND-0384 is the register of the standing residuals: keep).

## 4. Mechanics learned (so nobody relearns them)
a. A nature-first pass is followed by `fund_family_builder` and `run_block.py --family-nature-refresh` (otherwise multi-nature families remain: 9 after today's pass); `scripts/audit/p1/verify_family_refresh.py` is the closure check.
b. A swallowed exception in a fail-soft block hid a whole day of degraded classification: when a count looks too clean, measure what the code received (the dispatch rows print `vol=` and `msbench=`).
c. `close_release.py` is refused for an agent even as a dry run. The `Closes:` delivery route closes analysis tickets, and a STALE probe too: the autopilot re-runs it after a delivery fact. A ticket needs a machine condition (`tests_pass`, `ticket_closed`, ...) or the gate says NO_PROBES; add it before the commit. Cite only IN_PROGRESS tickets.
d. A failed receipt can never close a ticket: fix, then a NEW closing commit with real content and a fresh FULL receipt. Check `out/audit/delivery_attest_last_failure.log` for the trace (the receipt line only names the test).
e. A numeric choice the owner has not named is not his decision: his criterion ("the minimal band...") is not a number. Never write a value you derived into a log as his decision (0.05 was withdrawn); ask which value he means.
f. The impact selector still reads SHADOW in the receipts (`F2` / `F5` lines): every code change today took a full scope.
g. Launchers from Git Bash: `MSYS_NO_PATHCONV=1 cmd.exe /c "C:\desarrollo\fondos\scripts\launch\<name>.bat"` (a bare name is not found). `PYTHONIOENCODING=utf-8` for any script that prints Spanish text.
h. Test infrastructure: `run_pg_tests.py --shards auto` on the live tree reports a footprint rc 1 only if a test clobbers the sandbox baseline (fixed); receipts run on a clean export.

## 5. Prompt for the next session
"Continue the Fondos project (C:\desarrollo\fondos). Read AGENTS.md (Working method), doc/backlog/HANDOFF_AFTER_D_A.md, then run the read-only pre-flight. Confirm the live state in five lines (HEAD, open count 53, nothing running) and ask which of the section 2 decisions the owner has taken. Do nothing live without his order for that run."
