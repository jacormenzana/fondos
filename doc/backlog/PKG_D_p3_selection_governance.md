# Package D - P3 selection quality and promotion governance

Owner order 2026-11-15 ("D first"), after FND-0422. One page, rewritten in place. No P1 / P2 / P3 process is run by this package without the owner's order for that run. Exit metric: every ticket of the cluster is closed on recorded evidence, or has the owner's decision it waits for written down, with nothing left "in progress" without a next event.

## 1. Result (read first)
- **FND-0409 delivered in code** (the only defect of the cluster): `estimate_rotation_cost` mixed two units. `fund_master.Entry_Fee_Pct` / `Exit_Fee_Pct` are RATIOS (live universe: 2,475 stored entry fees, range 0 - 0.0638, none above 1), `rotation_costs.*` and the defaults are PERCENT; the old `COALESCE(...) / 100` divided a ratio by 100 once too often (a 5 % entry fee cost 0.05 %). Now the per-fund ratio is used as it is, the per-nature and default percent are divided by 100, and a stored 0.0 stays a real zero (`portfolio_builder.fee_fraction`, pure; 7 unit tests, `proyecto3/tests/test_rotation_cost_units_fnd0409.py`). `ROTATION_COST_GATE_ENABLED` stays OFF, so no selection changes today; the gate can now be evaluated on correct costs.
- **FND-0257 (LOW): the re-open trigger has not fired.** The ticket's own trigger is "any build whose master non-EUR exposure is below the budget". Read-only on the six latest scenarios (`shared.fx_exposure`): all six are exactly 30.0 % of the master (budget 30 %), including the 11-15 DRAFT and the PRODUCTION of 11-14. Proposal: DEFER with the same trigger and a review date.
- **FND-0259 (epic, HIGH): every child is CLOSED.** GOV-01..06 (FND-0260 .. 0265) closed 2026-10-12 / 10-17 as CODE_FIX, and so did FND-0251, 0234 and 0254 (FND-0173 REJECTED). Nothing of the epic is open. Proposal: close it on that evidence (owner's `close_release.py`, see section 3).
- **FND-0221 and FND-0198: the analysis is done, closing is the owner's step.** The attribution (log of 11-14 15:31) and the three-run isolation (11-14 15:29) are recorded; both carry only the default `tests_pass` and no delivering commit, so the autopilot cannot close them. An agent cannot even dry-run `close_release.py` on the live backlog (refused: only a person at a terminal).
- **FND-0306 and FND-0158 are waiting on events, not on work:** 0306 needs a SECOND stamped DRAFT compared in shadow (the (a)/(b) decision and the switch review follow; condition `manual`); 0158 needs your decision on the hysteresis band (evidence on file: the 11-14 PIT grid, any band cuts turnover 42 - 71 % and cost drag 0.4 - 0.7 pp/yr, Sharpe flat 0.62 - 0.69; the in-sample caveat stands).
- **The stale DRAFT.** `cartera_shock_energetico_202611_b20261115002532` was built (11-15 00:25) from the wrong natures of FND-0422. Against PRODUCTION (11-14 03:39, built before the bug) 28 of 30 funds are common; the two swaps are the two held funds whose nature the FND-0422 pass changed (LU1006075656 held in PRODUCTION, nature Mixtos again; LU1799936510 held in the DRAFT, was labelled Monetario, is Renta Fija Flexible). PRODUCTION is untouched. The freshness gate would accept a build now (all inputs fresh, `family_refresh_pending` 0, 100 % of funds on one `CALC_VERSION`).
- **Receipt speed (feeds FND-0385 / 0387):** the full receipt of `c4c94a2` (a product commit changing `proyecto1/`, `scripts/` and `tests/`): scope full, 7,265 passed, 545.8 s (9.1 min).

## 2. Cluster state (live backlog, 2026-11-15)
| Ticket | Status | What it waits for |
|---|---|---|
| FND-0409 | IN_PROGRESS -> closes by `Closes:` | this package's receipt |
| FND-0221, FND-0198 | IN_PROGRESS | owner's `close_release.py` |
| FND-0259 | OPEN | owner's `close_release.py` (epic; children all closed) |
| FND-0257 | OPEN, LOW | owner: DEFER with the trigger |
| FND-0306 | BLOCKED | second stamped DRAFT in shadow: P3 build + a real promotion attempt (owner) |
| FND-0158 | BLOCKED | owner: hysteresis band decision |
| FND-0222, 0299, 0206, 0304 | DEFERRED | their own review dates (0299: blind months from 2026-11) |

## 3. Owner closure commands (cmd.exe; dry run first, then add `--apply`)
`C:\Users\jacor\.conda\envs\des\python.exe scripts\gov\backlog\close_release.py --release R2026.11-PKG-D --tickets FND-0221,FND-0198 --log "PIT attribution and universe isolation delivered 2026-11-14 (logs 2357-2358); closing on the recorded analysis"` and a second call for FND-0259 (`--tickets FND-0259 --log "all child phases GOV-01..06 closed 2026-10-12 / 10-17"`; the ticket must be IN_PROGRESS first: the agent moves it when you agree).

## 4. Decisions pending
(1) P3 rebuild now (clean natures; it is also the second stamped DRAFT FND-0306 needs, followed by your real promotion attempt). (2) FND-0158: hysteresis band, or a pre-registered rule. (3) The two closures of section 3. (4) FND-0257: DEFER.
