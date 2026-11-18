# Handoff - Package L (P3 alignment), after P3-1 slices 1.1-1.4 and 1.7 (2026-11-18)

Start from verified facts: run `python scripts/gov/repo/preflight_check.py --mine <your files>` first (another session's `SKILL.md` is untracked in the tree: not yours). Plan page: `doc/backlog/PKG_L_p3_alignment.md`; matrix `doc/audit/MATRIZ_HALLAZGOS_P3.md`; decision card `doc/audit/DECISION_CARD_P3_ALIGNMENT.md` (the owner confirmed its values on 2026-11-18; the `MIN_NAV_ROWS` share is moot). Tickets: FND-0430 (cluster), FND-0431 (rotation gate; delivered, closing condition not defined), FND-0432 (finding ticket the assistant proposes to reject, see its log).

## Delivered (commits `f5afafb`, `7c279a2`, and the one after it)
- **1.1 / 1.2** `portfolio_builder.rotation_plan`, `_apply_rotation_gate`, `score_candidates.load_run_scores`: the gate could never revert (the revert branch was unreachable), the incumbent score was 0, pairing was first-match over a set. Now score from the latest scoring run, one-to-one pairing, ROT-0, reinstatement subject to the construction rules. Gate OFF: no live effect. 15 tests.
- **1.3** `pit_selection_test.py`: E-14, H-11, H-12, E-15, E-16. 8 tests. The FND-0429 verdict (`83b296a`) is unchanged.
- **1.4** `pit_benchmarks.py` + `scripts/audit/p3/p3_pit_benchmarks.py`: matched pool, R1 persistent random, R2 monthly redraw, turnover split, paired bootstrap, mechanical C1 reading. 18 tests.
- **1.7** `scripts/audit/p3/nav_rows_eligibility_diff.py`: 0 of 963 live eligible funds have fewer than 12 monthly NAV rows. No switch needed.
- **1.5 reclassified:** E-13 is a documented policy (owner question); E-7 has no production path (monitor only).

## What the dry run says (survivor-only PIT run 20261117_201724, NOT C1 evidence, 10-50 draws)
The champion's gross return is at or above the frictionless pool's (+0.28 pp, interval about -0.4..+0.9) and its cost drag is about 0.47 pp a year higher, so most of FND-0429's net "construction loss" is cost charged to one side. R1 mixes the caps with a persistence effect: gross caps effect (R2 - matched pool) about -0.4 pp, persistence (R1 - R2) about -1.0 pp. The mechanical C1 reading is PROCEED on both branches, but the run is survivor-only, so it decides nothing.

## Next, in this order
1. **Owner job (needs his order):** a default-universe PIT run (no `--current-universe-only`; about 8 minutes cold): `python scripts/launch/p3_pit_backtest.py` as in `proyecto3/AGENTS.md`, then `python scripts/audit/p3/p3_pit_benchmarks.py --run-dir out/reports/pit_backtest/<STAMP>` (50 R1 draws take about 6 minutes). Read the report with the survivor-only banner absent. That is the C1 evidence; the owner decides C1.
2. **1.6 `selection_health`: built in calibration mode** (`proyecto3/src/selection_health.py`, `scripts/audit/p3/p3_selection_health.py --run-dir <PIT run>`, 8 tests). Calibrate it on the default-universe run of step 1 and read the thresholds; the survivor-only calibration found `top_nature_max` of the RAW top-10 degenerate (it cannot fire). Then wire the LIVE block (report-only): eligible-set change (E-7), top turnover, concentration on the CONSTRUCTED portfolio (nature and manager), `nan_weight_share` (E-5, in the module), counts of funds under a data hold or suspicion flag. Alerts never block.
3. **E-5 measured (done):** the missing weight is almost all `alpha_persistence` (about 19 % of eligible funds); median imputation would change 6 of the Defensiva top 10, 2 of Equilibrada, 0 of Dinamica. The decision is an out-of-sample test in P3-5a, not a code change.
4. **H-2, H-8 docstrings: done.**
5. **One full receipt for the P3-1 delivery** (`python scripts/gov/delivery/delivery_attest.py --scope full`, started in the background on the last commit of the slice), then define FND-0431's closing condition (`backlog_client.add_close_condition`) and close on recorded evidence.

## Standing orders that bit this session
- A scanner failing is information: `tests/test_silent_fill_scan.py` caught three `fillna` calls; remove them, do not exempt.
- A test that passes on the first run proves nothing until you break the property it guards (the persistence test missed a missing incumbent bonus).
- `.gitignore:28` ignores names matching `*_2026[0-9]*`: never date-stamp a tracked file name.
- Postgres aggregate over `fund_scores`: join a `runs` CTE (`MAX(as_of_date)` per block), never the correlated subquery (over 40 s here).
- No sleep loops: use `run_in_background` or Monitor.
