# Handoff: after FND-0440 / FND-0441 (Geography), written 2026-11-26

Supersedes `HANDOFF_P3_AFTER_LAYER3.md` for the next steps (that file keeps the Package L history up to layer 3). Facts below were verified on 2026-11-26 against git, the live database and the backlog.

## 0. How to start (standing orders)
- First `python scripts/gov/repo/preflight_check.py --mine <your files>`. The untracked `SKILL.md` belongs to another session: never touch it. Interpreter `C:\Users\jacor\.conda\envs\des\python.exe`; backlog scripts need `import shared.config` first. WSL keepalive in the background before database work.
- Replies: English, ultra-short, the decision needed first. Tests first, mutation check from a verified green baseline, AST check after every Python edit, policy scanners after the last edit and before each commit.
- Commits with explicit paths; `Refs:` / `Closes:` and `Co-Authored-By` together in the last paragraph. One full receipt per package delivery. The owner's watcher publishes a commit without `Closes:` within minutes.
- Never change production, start a study or run a live write without the owner's order. Never read the owner vault or apply DDL: give the owner the command.
- Do not read the turnover, cost or selection fields of the AC shadow rows before the 6-month review.

## 1. What was done
| Item | Result | Commit |
|---|---|---|
| FND-0440 | A single-region equity index is evidence of `Geography`; 26 funds corrected. CLOSED. | `f45ab05` |
| FND-0441 Steps 1-2 | 59 % of default-`Global` funds state a mandate; mandate-sentence fallback, 56 of 56 right on the sealed holdout (owner ruling on one label). | `0a16ab5`, `b788bc0` |
| FND-0441 delivery | Switch `GEO_MANDATE_FALLBACK_ENABLED` ON (29 funds `Global` -> region, 23 marked `Emerging`); column `fund_master.Geography_Source` added and filled; report prints "not determined" for `default` / `wrong_doc`; two `db_check` probes. Full receipt PASS (8,059). | `6c67384` |

Record: `doc/audit/GEO_GROUND_TRUTH_FND-0441.md`.

## 2. State
- **FND-0441 CLOSED** by the owner (release `R2026.11-FND-0441`). A merge that touches `db/**` of the `fondos` database does not close by itself (a DEPLOY fact is only written for `db/pg_backlog`): the owner runs, in cmd, `set FONDOS_ACTOR=human:<login>`, then `close_release.py --release ... --tickets ... --log "..."` (dry run, then `--apply`), then `set FONDOS_ACTOR=`. `--actor` alone is refused.
- `Geography_Source`, all funds: kiid 2,122, name 758, mandate 339, index 17, default 467, unreproduced 59, wrong_doc 5. In the PRODUCTION portfolio of 2026-11-26, 9 of 30 funds (38.5 % of weight) are `default`.
- **FND-0442** (new, DEFERRED, review 2027-01-15): the Geography leftovers (blanking defaults, unreproduced values, false `Global` of the KIID extractor).
- **FND-0029** (BLOCKED) holds the five wrong-document funds; LU2363199204 stores a wrong `Asia-Pacific` (noted there).
- Collision pairs FND-0384 / FND-0440 and FND-0384 / FND-0441: decided `NONE` (adjudications 48, 49). Preflight still alerts on other undecided pairs (7 older than 96 h).
- FND-0431 and FND-0433 are READY_FOR_DEPLOY and wait on receipt scope (their delivery receipts are docs-only).
- The 29 re-labelled funds get new betas at the next quarterly P2 recompute (`_ols_is_fresh` ignores `Geography`).
- Not ticketed: the benchmark extraction captures a licensing disclaimer as the benchmark (seen on a Fidelity S&P 500 fund).

## 3. Package L: nothing actionable without data or time
- **FND-0436** (dead funds in the PIT evidence): the code part is built; the real attrition needs data on deregistered funds, a task for the owner to schedule (proposed review 2027-01-15).
- **FND-0437** (geographic caps): parked; no holdings data. `Geography_Source` now separates evidenced values from defaults, which it did not before.
- **FND-0438** (cost study, AC forward shadow): waits for the 6-month review window, about 2027-05.

## 4. Recommended next step (needs the owner's order)
**FND-0427** (HIGH, IN_PROGRESS; the only open HIGH ticket): rolling windows not covered by NAV history. Verified on 2026-11-17: no stored `rolling_1y`..`10y` row violates the coverage rule, POST audit PASS_WITH_WARNINGS. What is left is the manual closing condition: **the owner reviews the selection diff against PRODUCTION**. The work is to rebuild that diff read-only against today's PRODUCTION scenario and DRAFT and present it for the decision (re-verify the counts first: they are from 2026-11-17).
Start with `backlog_links.py --for FND-0427` and `doc/backlog/PKG_I_report_defects.md`.

## 5. Traps found
- A mutation run against a red baseline reports every mutant killed: assert a green baseline first.
- Extractor silence is not absence of evidence: read the documents before changing extractor-produced values.
- A new migration tool must be listed in `shared/owner_vault.py::OWNER_TOOLS`, or the owner's command does not receive the owner string.
- A change to the monthly report SQL moves its entry in `_UNVERIFIED_BASELINE` (`tests/test_sql_explain_sweep_pg.py`).
- Running `proyecto1/tests` and `proyecto3/tests` in one pytest call gave 14 cost-test failures that do not appear per file or in the receipt.

## 6. Starter prompt for a fresh session
```
Read doc/backlog/HANDOFF_AFTER_FND0441.md and work section 4 (FND-0427): rebuild the selection diff read-only and give me what I need to decide. Run the preflight first, leave the untracked SKILL.md alone, start the WSL keepalive before any database work. Do not change production, start a study or run a live write without my order. Replies in English, ultra-short, decision first.
```
