# Handoff: P3 fund-selection, after Package M and the 2026-11-27 session (written 2026-11-27)

Next step: **FND-0445** (owner: in a fresh session). Plan and rules: `PKG_M_ac_adoption.md`; decision record: `doc/decisiones/DECISION_P3_7_AC_ADOPTION_ROUTE.md`; pre-registration: `doc/decisiones/PREREG_P3_7.md` sections 13-14. This supersedes `HANDOFF_P3_SELECTION_RESUME.md`.

## 0. How to start (standing orders)
- `python scripts/gov/repo/preflight_check.py` first. Interpreter `C:\Users\jacor\.conda\envs\des\python.exe`; backlog scripts need `import shared.config` first. WSL keepalive in the background before database work.
- Untracked `SKILL.md` belongs to another session: never touch it.
- Replies: English, ultra-short, decision first. Stay on selection; side findings get a ticket or a log line.
- No live write, switch flip, study or promotion without the owner's order. Agents cannot start launcher `.bat` cycles (the permission classifier denies it) and cannot change a ticket's attributes: give the owner the `cmd` line.
- **Do not read** turnover, cost or selection-difference fields of the two forward shadows before the first review (about 2027-05). The P3 build logs of 2026-11-22 and 2026-11-27 (before `4b3be73`) contain such values: do not open their shadow lines.
- Standing orders for unattended runs cover only `FondosP1P2MonthlyRefresh` (from day 25) and `FondosP3MonthlyDraft` (DRAFT only, never promotes).

## 1. State
| Item | State |
|---|---|
| PRODUCTION | `cartera_shock_energetico_202611_b20261115113822` (unchanged) |
| Newest DRAFT | `..._b20261127180027`: 30 funds, no fund twice; November's shadow rows are twin-free |
| AC adoption | Amendment 2 committed; R6 reader updated (`0ac659b`); review about 2027-05; December 2026 is the first counted month |
| Monthly tasks | both registered; first real P3 build 2026-12-01 09:00 |
| Promotion | blocked by **FND-0443** only (hard gate). FND-0444 was removed from the hard gates on 2026-11-27 |
| Builder | skips a second share class of a chosen fund (`P3_BUILDER_DUPLICATE_FUND_ENABLED`, `cf46f1c`); the promotion gate `GATE:DUPLICATE_FUND` refuses it |
| Cycle launchers | no longer ask to push |
| Autopilot | ON since 2026-11-27 18:41 (it had demoted itself after two reopens) |

## 2. Open work, in order
1. **FND-0443** (HIGH). Code `34c0e37`, audit rule `adcfe4d`, its test fix `a4ec4b7`. Census: 446,655 rows in 1,148 funds; export `out\audit\fnd0443_export_20261127\rows_to_delete.parquet` (all columns; keep it until the ticket closes). Switch `ROLLING_UNCOVERED_PRUNE_ENABLED` ON by the owner's order (`3188c8f`); the real pipeline's dry run matched the census. **PENDING:** the owner's P2 run (`P2P3_runCycle.bat --no-discover --no-prompts`), which deletes the rows; then verify read-only: the newest p2 audit holds no `WINDOW_FISHER_IDENTITY` and no `TIMESERIES_WINDOW_COVERAGE` finding, and the ticket closes by itself.
2. **FND-0445**: **built 2026-11-27, switch `P3_BASELINE_SHADOW_ENABLED` OFF** (PKG_M section 2, "State"). Pending, owner: (a) order the switch ON for the identity check (the next real build then writes the first row); (b) confirm the reader's readings listed there. The ticket closes by itself after a `SHADOW` row and no `ERROR` row in 40 days: with the switch still OFF it stays open.
3. **FND-0450** (delivered `2497c71`, closes by itself): the candidate query takes under 0.1 s instead of 50-400 s. Same rows; same order per sub-portfolio; the all-blocks form swaps ties (backtester selection identical). `load_run_scores` keeps the old shape (small, used by the switched-off rotation gate).
4. FND-0438 stays open until the R6 verdict. FND-0444 (family key, root cause: five causes, 478 split pairs) is ordinary work; its closing check `family_class_collisions` is not built.
5. FND-0447 / FND-0448 close by themselves after a real scheduled build / refresh logs OK.

**The P2 run may still be pending (owner: later).** FND-0445 does not depend on it. Until it runs, the audit keeps failing and promotion stays blocked; nothing else changes. The cycle executes the shared working tree, one step after another: it must not be launched while a session holds uncommitted code, and a session must not edit code while it runs (check `preflight_check.py` for the cycle lock first). A run on 2026-12-01 or later builds December's counted DRAFT.

## 3. Pitfalls met on 2026-11-27 (do not repeat)
- Adding a query to a gate or builder: run the matching `*_pg.py` tests before committing (36 failures once).
- Adding an audit rule: run every audit test, **including `proyecto1/tests`** (canary list and critical-rule registry are pinned there).
- Never put a closed ticket in `Refs:` on unrelated work: it reopens it, and two reopens demote the autopilot.
- A switch change is published within minutes unless the range holds a `Closes:`: take the full receipt first.
- `pg_stat_user_tables` "never analysed" can be a counter reset, not missing planner statistics.

## 4. Starter prompt
```
Read doc/backlog/HANDOFF_P3_AC_ADOPTION.md. Run the preflight first, leave SKILL.md alone, start the WSL keepalive before database work. Check read-only whether my P2 run for FND-0443 has happened: if it has, verify the audit (section 2 item 1); if not, leave it pending. Then start FND-0445 as PKG_M section 2 defines it, tests first, switch OFF. No live write, switch flip or promotion without my order; do not read the shadow comparison fields. Replies in English, ultra-short, decision first.
```
