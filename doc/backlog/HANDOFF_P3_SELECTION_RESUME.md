# Handoff: resume P3 fund-selection optimisation (written 2026-11-26)

Next-step handoff for the selection work. It supersedes the "recommended next step" of `HANDOFF_AFTER_FND0441.md` (FND-0427 was a tail item, not selection work). History stays in `PKG_L_p3_alignment.md`.

## 0. How to start (standing orders)
- `python scripts/gov/repo/preflight_check.py` first. Interpreter `C:\Users\jacor\.conda\envs\des\python.exe`; backlog scripts need `import shared.config` first. WSL keepalive in the background before database work.
- Untracked `SKILL.md` belongs to another session: never touch it.
- Replies: English, ultra-short, decision first. Stay on selection: side findings get a ticket or a log line and are not worked.
- No production change, study, live write, switch flip or promotion without the owner's order.
- Do not read turnover, cost or selection-difference fields of the AC shadow rows before the first review (`PREREG_P3_7.md` section 13).

## 1. The path we were on
| Stage | Subject | State on 2026-11-26 |
|---|---|---|
| `PLAN_SELECCION_CALIDAD` WP0-WP2 | Report and metric defects | FND-0426 and FND-0428 closed. FND-0427 open (section 5). WP2 waits for the owner's reference figure |
| WP3 | Champion-side data captures | Not re-checked today |
| WP4 | Hysteresis | Shadow ON, switch OFF (FND-0158) |
| WP5 (FND-0429) | Does the ranking add value? | Thin: not distinguishable from the costed eligible pool, net |
| WP6-WP7 | Value case, vendor, challenger | Not re-checked today; no result of mine opens them |
| Package L | P3 alignment, then cost and layer studies | P3-5A, P3-9, P3-10F: nothing adopted. Layer 1 untestable. Regional caps wait for holdings data. P3-7 found the cost layer |

## 2. Where we stand (study reports; survivor-only history, no dead funds; not re-run today)
- Live arm at the stated KIID fees: net about 0.08 % a year. AC (band 0.10 + fee penalty H = 0.58 years): net about 3.12 %, 25 bp-cost drawdown -12.5 %, turnover about 13 % a month against 32 %.
- Owner's mandate: max drawdown 15 %, target net band 3.2-4.0 %. AC's 3.12 % is just below the band.
- The gain is fee arithmetic: at a flat 25 bp all arms net about the same.
- AC is not live. Forward shadow: 1 row (2026-11), 0 errors. In that row 570 of 1,432 candidates have an unknown fee.

## 3. The point to progress: the adoption route for AC
Two committed rules differ:
- `PREREG_P3_7.md` section 13: AC is proposed for adoption only if R6 (6 counted months, about 2027-05) **and** R36 (about 2029-10) pass.
- `PKG_L` rule R7: a **structural** rule activates on PIT evidence across the cost grid, plus the deterministic shadow stream, plus the owner's order; only predictive changes also need the FND-0299 checkpoints.

So "wait until May 2027" is only the first review: under section 13 adoption is about three years away.

**Decision for the owner:** keep section 13, or amend it so that the cost rules (band, fee penalty) follow the structural route, with the Amendment 5 revert trigger.

**Assistant's recommendation:** prepare a one-page decision note before choosing. Lean: examine the structural route, because the gain is deterministic cost. Counter-argument to state in it: the fee penalty changes which funds are bought, so the return effect is not arithmetic, and section 13 tests exactly that. If the rule is amended, do it before the first review, while no comparison data has been seen.

## 4. Proposed refocus and work order
Stop searching for a better ranking on this history. One outcome: **a fee-aware selection live, safely**.
1. The decision note of section 3 (documents only; reads no shadow comparison fields).
2. Fee data coverage: how the penalty treats an unknown fee, why 570 candidates lack one, and what it does to the choice. Read-only.
3. Only after the owner's ruling: the adoption or waiting plan, including what must be true before a promotion (section 5).

## 5. Parked (do not work without an order)
- FND-0443 (HIGH): leftover uncovered rolling windows in `fund_metric_timeseries`; POST audit FAIL; by the documented data gate a DRAFT holding the affected funds would be refused (gate not run). Needed before the next promotion.
- FND-0444 (MEDIUM): ABN US ESG held twice (EUR and USD classes) in the DRAFTs of 11-21 and 11-22. Needed before the next promotion.
- FND-0427: stays IN_PROGRESS, not signed off (log 2749).

## 6. Starter prompt
```
Read doc/backlog/HANDOFF_P3_SELECTION_RESUME.md and continue the P3 fund-selection optimisation at section 3. Run the preflight first, leave SKILL.md alone, start the WSL keepalive before any database work. First recover the plan (PLAN_SELECCION_CALIDAD.md, PKG_L_p3_alignment.md, PREREG_P3_7.md section 13), tell me if you would refocus it differently from section 4, and then give me the one-page decision note on the AC adoption route. Documents and read-only work only: no code change, study, switch, live write or promotion without my order; do not read the AC shadow comparison fields; do not work FND-0443, FND-0444 or FND-0427. Replies in English, ultra-short, decision first.
```
