# Handoff: P3 fund-selection and the backlog, after the 2026-11-27 / 28 night sessions (rewritten 2026-11-28)

Next step: **check the 2026-11-28 09:00 build** (section 2, item 1). Plan and rules: `PKG_M_ac_adoption.md`; decision record: `doc/decisiones/DECISION_P3_7_AC_ADOPTION_ROUTE.md`; pre-registration: `doc/decisiones/PREREG_P3_7.md` sections 13-14; backlog ranking: `PKG_S3_backlog_ranking.md` (rewritten 2026-11-28). This supersedes the version of 2026-11-27 night (git history).

## 0. How to start (standing orders)
- `python scripts/gov/repo/preflight_check.py` first. Interpreter `C:\Users\jacor\.conda\envs\des\python.exe`; backlog scripts need `import shared.config` first. WSL keepalive in the background before database work.
- Untracked `SKILL.md` belongs to another session: never touch it.
- Replies: English, ultra-short, decision first. Stay on selection; side findings get a ticket or a log line.
- No code change, live write, switch flip, study or promotion without the owner's order. Agents cannot start launcher `.bat` cycles: give the owner the `cmd` line. Check the cycle lock before any edit or commit (scheduled tasks run the working tree).
- **Do not read** turnover, cost or selection-difference fields of the forward shadows before the first review (about 2027-05), nor the shadow lines of the P3 build logs of 2026-11-22 and of 2026-11-27 before `4b3be73`.
- **Selection is FROZEN until the R6 verdict is recorded on FND-0438** (owner, 2026-11-27): no allowlist on the builder / gate / shadows, no `fund_family_id` repair, no retiring of the builder fallback, no switch flip of the three fixes built behind switches OFF (section 1).
- Standing orders for unattended runs cover only `FondosP1P2MonthlyRefresh` and `FondosP3MonthlyDraft` (DRAFT only, never promotes).

## 1. State
| Item | State |
|---|---|
| PRODUCTION | `cartera_shock_energetico_202611_b20261115113822` (unchanged) |
| Newest DRAFT | `..._b20261127214508` (owner's evening cycle of 2026-11-27) |
| Baseline shadow | switch ON (`72c1fb6`, after the 21:46 build of 2026-11-27): no row yet; `p3_monthly_draft.py status` on 2026-11-27 23:00 said "build needed, missing P3_BASELINE_SHADOW", so the 2026-11-28 09:00 task rebuilds November (the 11-27 09:00 run ended RC 105, another instance held the lock) |
| Reader readings | all 8 of PKG_M section 2 **confirmed** by the owner 2026-11-27 (`ba83977`); reading 4 gained a per-month line of the renormalised weight, built in the `--after-adoption` review (`d867ff7`) |
| Autopilot | **SHADOW**: moves nothing; the owner sets it ON on **2026-12-03 after 20:37** (or 12-04) |
| Backups | external disk failing; off-box copy last OK 2026-11-20; on 2026-11-27 the rotating backup ended RC 75 (deferred: the evening cycle held the lock) and the off-box copy RC 3 (`/mnt/d/desarrollo` missing on the mounted D:) (FND-0463, owner) |
| Backlog | 70 open; **15 tickets BLOCKED on FND-0438** and FND-0078 BLOCKED on FND-0100, each with a CONFIRMED DEPENDS_ON link: the autopilot's UNBLOCK rule releases them when the blocker CLOSES (no manual un-deferral any more) |
| Switches built OFF | `CREDIT_QUALITY_HIGHYIELD_TOKEN_ENABLED` (FND-0459), `KID_FORMAT_EN_UCITS_COST_SIGNALS_ENABLED` (FND-0458) in `075775c`; `P1_DQ_SIGNAL_DETAIL_ROWS_ENABLED`, `P3_REPORT_P1_QUALITY_ENABLED` (FND-0456, P3 side report only) in `76a6f7d`: flip only after the R6 verdict |

Commits of the session (all with a PASS receipt unless said): `77a735d` FND-0457 KIID published date (parser + COALESCE); `1027ca7` FND-0461 P2-01 check, FND-0462 items 2-3; `ba83977` readings confirmed (docs); `075775c` FND-0458 / 0459 behind switches OFF; `76a6f7d` FND-0456 behind switches OFF; `d867ff7` reader reading 4 line; `48c2e6a` FND-0400 test cascade (**Closes**, receipt running at the time of writing); `d97258c` ranking page (docs).

## 1b. Morning of 2026-11-28 (checked; supersedes items 1, 2 and the first part of 5 below)
- **November build done:** the owner's cycle of 08:09 built DRAFT `..._b20261128082745`; first `P3_BASELINE_SHADOW` row, `identity MATCH`, 0 ERROR rows. The 09:00 task ended RC 105 (the cycle lock was held), so there is no `P3_MONTHLY_DRAFT` record; `p3_monthly_draft.py status` says November is captured. Production unchanged.
- **Published:** `48c2e6a`, `d97258c` and `26b3d63` are on `origin/master` (full receipt of `26b3d63`: 8,420 passed).
- **FND-0457 (live):** `KIID_Published_Date` stored on 2,438 of 2,943 active funds (548 before); log 3007.
- **FND-0461 (live):** the P2 run logged `[P2-01] ISIN match OK: 3746/3753` (7 `STALE_FROZEN` excluded on purpose, 0 without `fund_master`).
- **Closures ordered by the owner for today:** FND-0443, 0445, 0451, 0452, 0453, 0457, 0461, by `close_release.py` from the owner's console. The autopilot stays in SHADOW until 2026-12-03 after 20:37. This commit is the delivery record of FND-0443 (code up to `3188c8f`) and FND-0461 (`1027ca7`), whose earlier commits carry only `Refs:`.
- **FND-0425:** owner sign-off recorded; stays IN_PROGRESS, because no closure route fits a documents-only delivery (log on the ticket).

## 2. Open work, in order
1. **2026-11-28 after 09:00, the November rebuild** (FND-0445): `python scripts/audit/p3/p3_shadow_read.py` (default view) must show a first `P3_BASELINE_SHADOW` row, `identity MATCH`, 0 ERROR rows; the `P3_MONTHLY_DRAFT` record in `ingestion_log` must be OK, with the post-draft audit and the draft report present. If the task did not run (PC off or not logged in), say so and stop.
2. **Check that `48c2e6a` and `d97258c` were published** (`git fetch` then `origin/master`): the range waits for the full receipt of the `Closes:` commit; the receipt tool queued `d97258c` behind it.
3. **Backups (FND-0463, owner):** the logical backup of 2026-11-28 and the disk.
4. **2026-11-29:** FND-0411 returns to TODO by itself (Package H review); FND-0248 is the owner's manual step (DBeaver / Metabase).
5. **Sign-offs after the next runs:** FND-0457 (re-measure the stored share of dated KIIDs after the next P1 pass: replay said 548 -> 2,438 of 2,943 active), FND-0461 (the next P2 run logs `[P2-01] ISIN match OK`), FND-0462 (no false Distribution_Frequency rows). FND-0425 closes on the owner's sign-off of `PKG_S3_backlog_ranking.md` (the agent then retires its manual condition with his words, names `d97258c` in a `tests_pass`, and gives him the `close_release` lines, as for FND-0449 / 0450).
6. **2026-12-01:** the first counted monthly build (DRAFT only). FND-0443 is still open, so that DRAFT is not promotable before the autopilot closes it.
7. **2026-12-03 after 20:37:** remind the owner and give the exact cmd.exe line (`autopilot_mode.py --set ON --reason ...`, with `set FONDOS_ACTOR=human:jacor` before and `set FONDOS_ACTOR=` after). Then verify the closures of FND-0443, 0445, 0451, 0452, 0453 and 0400 at the next published commit. One more reopened closure puts it back into SHADOW.
8. **2026-12-25:** the monthly NAV refresh window opens for the January build.
9. **After the R6 verdict (about 2027-05):** FND-0438 closed releases the 15 by itself; flip the four switches above in one config commit with a full receipt; FND-0458 then also needs the cost recompute (`scripts/launch/p1_cost_recompute.py`), a plain P1 pass does not re-extract costs.

Not started, outside the freeze: FND-0462 item 1 (201 funds with the legacy `Liquidity_Profile` `T5`: read the dealing frequency from the KIID; P3 does not read the column); FND-0239 (cycle telemetry, several sessions).

## 3. Pitfalls met on 2026-11-27 / 28 (do not repeat)
- **Start full receipts and the policy scanners from Git Bash**, never from PowerShell: there `bash` is WSL and the hook / log-routing tests fail falsely (a receipt of `77a735d` recorded a false FAIL, 6 failed and 21 errors, all in those files).
- A ticket that waits on another goes to **BLOCKED on it** (DEFERRED -> IN_PROGRESS -> BLOCKED: DEFERRED cannot go to BLOCKED directly). A dated DEFERRED returns to TODO by itself; an event-only DEFERRED never moves.
- `close_release.py` closes only IN_PROGRESS / READY_FOR_DEPLOY tickets, and its `tests_pass` must NAME the commit while the autopilot is in SHADOW (a condition without a sha reads the autopilot's evidence). The owner's vault opens only for the listed owner tools, by script name: never hand him a renamed script.
- Session ids must look like `FND_20261128_DEV_TOPIC` (the routine refuses `FND_20261128_CLOSE_...`).
- A combined backlog write (transition plus conditions) was refused by the permission classifier until the owner's explicit "go prep"; write the script file in a separate step from running it.
- Adding a query to a gate or builder: run the matching `*_pg.py` tests and the SQL sweep before committing; adding a module or a test file: refresh the sentinels (`sync_agents_md.py --write`).
- Never put a closed ticket in `Refs:` on unrelated work: it reopens it, and two reopens demote the autopilot.
- "The cycle finished" is not proof: check the cycle lock before editing.

## 4. Starter prompt
```
Read doc/backlog/HANDOFF_P3_AC_ADOPTION.md. Run the preflight first, leave SKILL.md alone, start the WSL keepalive before database work, check the cycle lock before any edit. Check read-only that the 2026-11-28 build wrote the first P3_BASELINE_SHADOW row with identity MATCH and no ERROR row and that its P3_MONTHLY_DRAFT record is OK, that 48c2e6a and d97258c are on origin/master, and whether my backup ran. Then report what still waits for me. No code change, live write, switch flip or promotion without my order; selection is frozen until the R6 verdict; do not read the shadow comparison fields. Replies in English, ultra-short, decision first.
```
