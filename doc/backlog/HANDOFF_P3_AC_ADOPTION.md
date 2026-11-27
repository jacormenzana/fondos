# Handoff: P3 fund-selection, after Package M and the 2026-11-27 sessions (rewritten 2026-11-27 night)

Next step: **check the first 09:00 build (2026-11-28) and close what waits** (section 2). Plan and rules: `PKG_M_ac_adoption.md`; decision record: `doc/decisiones/DECISION_P3_7_AC_ADOPTION_ROUTE.md`; pre-registration: `doc/decisiones/PREREG_P3_7.md` sections 13-14. This supersedes `HANDOFF_P3_SELECTION_RESUME.md`.

## 0. How to start (standing orders)
- `python scripts/gov/repo/preflight_check.py` first. Interpreter `C:\Users\jacor\.conda\envs\des\python.exe`; backlog scripts need `import shared.config` first. WSL keepalive in the background before database work.
- Untracked `SKILL.md` belongs to another session: never touch it.
- Replies: English, ultra-short, decision first. Stay on selection; side findings get a ticket or a log line.
- No live write, switch flip, study or promotion without the owner's order. Agents cannot start launcher `.bat` cycles and cannot change a ticket's attributes: give the owner the `cmd` line.
- **Do not read** turnover, cost or selection-difference fields of the forward shadows before the first review (about 2027-05). The P3 build logs of 2026-11-22 and 2026-11-27 (before `4b3be73`) contain such values: do not open their shadow lines.
- **Selection is FROZEN until the R6 verdict is recorded on FND-0438** (owner, 2026-11-27): no allowlist on the builder / gate / shadows (FND-0455), no `fund_family_id` repair, no retiring of the builder fallback.
- Standing orders for unattended runs cover only `FondosP1P2MonthlyRefresh` and `FondosP3MonthlyDraft` (DRAFT only, never promotes).

## 1. State
| Item | State |
|---|---|
| PRODUCTION | `cartera_shock_energetico_202611_b20261115113822` (unchanged) |
| Newest DRAFT | `..._b20261127214508` (owner's evening cycle) |
| P2 audit | `post_20261127_220859_p2` = PASS_WITH_WARNINGS (no hard invariant open) |
| AC adoption | review about 2027-05; December 2026 is the first counted month; the R6 reader needs 6 counted months, one gap allowed (`ffda028`) |
| Baseline shadow | switch ON (`72c1fb6`); no row yet; the 2026-11-28 09:00 build rebuilds November and writes the first one |
| Monthly refresh | retries a cycle that did not finish, and a month without its DRAFT also outside the window (`a9a5ec5`) |
| Promotion | the owner's hard gate FND-0443 is met in the data but the ticket is still open (see below) |
| Autopilot | **SHADOW since 2026-11-27 18:56**: it moves nothing |
| Backups | external disk failing; off-box physical backup last verified 2026-11-20 (FND-0463) |

## 2. Open work, in order
1. **First baseline row** (FND-0445): `python scripts/audit/p3/p3_shadow_read.py` (default view) must show `identity MATCH` and 0 ERROR rows. Owner still to confirm the reader's readings (PKG_M section 2).
2. **Closures wait for a person.** Autopilot dry run (2026-11-27 22:30) would move FND-0443, 0445, 0451, 0452, 0453 to READY_FOR_DEPLOY. It demotes itself while 2 of its closures were reopened in 30 days: FND-0375 (2026-11-03) and FND-0446 (2026-11-27 17:15). The first ages out on **2026-12-03**; after that `autopilot_mode.py --set ON` holds. Until then closures need the owner's `close_release.py`.
3. **Backups** (FND-0463): the owner takes the logical backup on 2026-11-28 and checks the disk.
4. **FND-0443 data:** all 446,655 exported rows are deleted (the last 6,820, in 7 frozen funds, by the owner's order). Keep `out\audit\fnd0443_export_20261127\` until the ticket is closed.
5. **FND-0444:** the check `db_check family_class_collisions` is built (`507e39e`): live 417 split pairs, 47 allowlisted until 2027-05-26. The key repair is frozen.
6. **FND-0454:** items 5 (a failing shadow rebuilds daily) and 7 (failed report / audit never retried) are code work, not ordered; item 6 is the owner's decision; item 4 fixed.
7. FND-0449 no longer reproduces (logged). FND-0438 stays open until the R6 verdict; then un-defer FND-0455 (and FND-0456 / 0458 / 0459).

## 3. Pitfalls met on 2026-11-27 (do not repeat)
- Adding a query to a gate or builder: run the matching `*_pg.py` tests before committing.
- Adding an audit rule: run every audit test, **including `proyecto1/tests`**.
- Never put a closed ticket in `Refs:` on unrelated work: it reopens it, and two reopens demote the autopilot.
- A switch change is published within minutes unless the range holds a `Closes:`: take the full receipt first.
- A receipt is per commit: put the `Closes:` of a package on the commit that gets the receipt (the tip).
- "The cycle finished" is not proof: check the cycle lock before editing.

## 4. Starter prompt
```
Read doc/backlog/HANDOFF_P3_AC_ADOPTION.md. Run the preflight first, leave SKILL.md alone, start the WSL keepalive before database work. Check read-only that the 2026-11-28 build wrote the first P3_BASELINE_SHADOW row with identity MATCH and no ERROR row, and whether my backup ran. Then report what still waits for me. No code change, live write, switch flip or promotion without my order; selection is frozen until the R6 verdict; do not read the shadow comparison fields. Replies in English, ultra-short, decision first.
```
