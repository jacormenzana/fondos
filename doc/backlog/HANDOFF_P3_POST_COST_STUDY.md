# Handoff: Package L after the cost study (P3-7) and the CPI-input study (P3-8), written 2026-11-22

Supersedes the state block of `HANDOFF_P3_STEP_F.md` (that file keeps the history of step F and the plan that was executed). Plan of record: `doc/backlog/PKG_L_p3_alignment.md`. Everything below was verified against the repository and the stored runs on 2026-11-22; nothing in production was changed by any of it.

## 0. How to start (standing orders, unchanged)
- First `python scripts/gov/repo/preflight_check.py --mine <your files>`. The untracked `SKILL.md` belongs to another session: never touch it. Interpreter `C:\Users\jacor\.conda\envs\des\python.exe`; read `.env` with `load_dotenv('.env')` for backlog scripts; `FONDOS_ACTOR=agent:claude-sonnet-5-5`.
- Replies: English, ultra-short, lead with the decision needed, then the evidence. Tests first (R5), AST check after every Python edit, policy scanners after the LAST edit (`shared.delivery_receipt.POLICY_TESTS`, 196 non-PG tests pass), commit with explicit paths (`git commit -F <file> -- <paths>`), `Refs:` / `Closes:` in the LAST paragraph with `Co-Authored-By`. Write multi-line code with the Write tool, never a shell heredoc (quoting breaks).
- ONE full receipt at package delivery: `python scripts/gov/delivery/delivery_attest.py --scope full`, with a WSL keepalive first (`wsl -d Ubuntu -- bash -c "sleep infinity"` as a background task; the one started on 2026-11-22 lives only as long as that session, so restart it). A docs-only commit gets an instant `docs_only` receipt.
- Never change production or start a new study without the owner's order. Never run a live write the owner has not ordered.

## 1. Completed work, with hashes
| Step | What | Commits | Result |
|---|---|---|---|
| P3-5A | 4 ranking levers L1-L4 (all OFF), 11 preregistered trials | prereg `7b41e9d`, levers `01e1e8d`, evaluation code `af63cc4`, results `008f040`, decisions applied `a50908f` | **0 candidates**. L1 and L3 CLOSED (code stays OFF); L2 PARKED in FND-0437 (P3-6); Amendment 2 NOT executed. Baseline nets 0.08 %/yr at the stated fees against 1.54 % for the costed matched pool; the baseline fails the 15 % drawdown cap under the stress (-15.9 %); the ceiling KPI is NOT EVALUABLE at the stated fees. Report `doc/audit/P3_5A_TRIALS_REPORT.md`; artifacts `out/reports/p3_p35a_trials/20261121_163723/` |
| P3-7 | cost study (hysteresis band, fee-aware class choice, fee penalty), 8 trials, FND-0438 | D1 `5eb7cd7`, prereg `1c2c74d`, code `013d725`, fix `2d098a5`, results `be04e80` | **7 of 8 arms are forward-shadow candidates** (all but B). Report `doc/audit/P3_7_TRIALS_REPORT.md`; ticket log 2685; run `out/reports/p3_p37_trials/20261121_p37/` (snapshot id `42d92e50d483`; `superseded_first_build/` holds the first, defective build of arm B, never read for performance) |
| P3-8 | classifier CPI input (average/maximum x lag 2/1), FND-0439 | prereg `ceed294`, tables script `91b4c19`, study code `0a4d7e0`, results `a0876c7` | **Owner decision 2026-11-22: CPI input UNCHANGED, FND-0439 closed** (see section 5). Report `doc/audit/P3_8_REGIME_CPI_REPORT.md`; tables `doc/audit/P3_BASELINE_VS_INFLATION_TABLES.md`; run `out/reports/p3_p38_regime/20261122_p38/` (snapshot `04aa54682f9b`); ticket log 2688 / 2690, condition 138 |
Earlier Package L commits (session 1-2, the plan): `793d256`, `2f9cd37`, `b2c8be7`, `b890572`, `3b7b437`, `b53b1a6`, `06269e7`, `b680d00`. Sealed PIT run `out/reports/pit_backtest/20261119_202509` (9 SHA-256 verified, `inputs/` snapshot, `scores_long.parquet`).

## 2. P3-7 numbers (stated KIID fees, fund by fund = the owner's regime; in-sample, today's fees, survivors only)
| Arm | Net | Gross | Turnover/month | Holding (mo) | Max DD (stated fees) | Paired gain vs baseline pp (lower bound) |
|---|---|---|---|---|---|---|
| Baseline | 0.08 % | 4.69 % | 32.2 % | 6.9 | -26.4 % | - |
| A1 band 0.10 | 1.92 % | 4.15 % | 14.8 % | 16.9 | -15.7 % | +1.81 (+1.15) |
| A2 band 0.20 | 2.34 % | 4.04 % | 11.3 % | 23.8 | -15.4 % | +2.22 (+1.38) |
| B class choice | 0.12 % | 4.70 % | 32.3 % | 6.9 | -25.9 % | +0.04 (-0.06) |
| C penalty H 7 mo | 2.53 % | 4.42 % | 28.1 % | 7.6 | -15.1 % | +2.40 (+1.64) |
| AB | 1.92 % | 4.14 % | 14.8 % | 17.2 | -15.7 % | +1.82 (+1.16) |
| **AC** (band 0.10 + penalty) | **3.12 %** | 4.12 % | **13.4 %** | 18.7 | **-13.4 %** | +2.96 (+2.08) |
| ABC | 3.10 % | 4.10 % | 13.3 % | 18.7 | -13.4 % | +2.95 (+2.06) |
| **D** (band 0.20 + penalty) | **3.23 %** | 3.95 % | **10.0 %** | 27.9 | **-12.7 %** | +3.06 (+2.01) |
Matched pools (20 draws, stated fees): persistent at band 0 / 0.10 / 0.20 nets 0.99 % / 1.63 % / 1.64 %; the monthly redraw -20.5 % (turnover 190 %/month). Amendment 5 of PREREG_FND-0193 applied as written: A1 and A2 PASS (rule E2 not run). At the flat 25 bp every arm nets 3.55-3.71 %: the gain is fee arithmetic; the price is a small gross loss (-0.5 to -0.7 pp for bands, -0.3 pp for the penalty), likely understated (no dead funds, FND-0436). Design: the penalty H is 0.58 years; the class-choice tolerance 0.02; Holm family 8 (level 0.00625).

## 3. UNRESOLVED CHECKPOINT: the P3-7 readout still needs the owner's sign-off (the main plan has NOT advanced past it; it was paused at `be04e80` to investigate CPI)
1. **Which candidate goes to the forward shadow.** Recommended **AC** (band 0.10 + fee penalty H = 7 months): 3.12 % net, 13 % turnover, -13.4 % max drawdown, against D 3.23 %, 10 %, -12.7 % and the baseline 0.08 %, 32 %, -26.4 %. Reason for AC over D: D rests on the largest band and 28-month holds, which the survivor-only panel flatters most; the band 0.10 is the configured value.
2. **Retire arm B** (fee-aware class choice): +0.04 pp, lower bound -0.06 (not significant), swaps 1,548 of 660,264 score rows; the code stays OFF.
3. **Build the forward-shadow stream for the fee penalty** (needed if AC is approved; the band already has `P3_HYSTERESIS_SHADOW`, which only appends `ingestion_log` rows). Not started; it needs the owner's order.
4. **Open the structural gross-return question.** Gross 4.0-4.7 % against the IPC+M3 target of 6.94 % (the gap has been known since FND-0303: 3.44 % vs 6.94 %); even the best arm beats the 36-month target in at most 19 % of windows (the baseline in 0 %); 2010-19 gross 4.7 % matched the target 4.4 %, the shortfall is in 2005-09 and 2020-26. No cost lever closes it; candidates for the next question: selection value, P3-6 (geography), the regime layer.

## 4. Pending operational closures (none done; the owner ordered them deferred to the package closure)
- **The one full receipt:** `python scripts/gov/delivery/delivery_attest.py --scope full` (about 9-10 min; hermetic PG tests). It must cover the Postgres tests of the changed scorer loader: `proyecto3/tests/test_p3_reads_pg.py`, `test_wrong_doc_eligibility_pg.py`, `test_fund_data_eligibility_pg.py`, plus the new optional parameters `fee_aware` / `fee_tolerance` on `score_funds_from_df`, `pit_candidates.pit_scores` and `pit_run.compute_pit_scores` (default None = the global switch, OFF).
- **Close FND-0435** (turning-point diagnostic): a `Closes: FND-0435` commit in the last paragraph with `Co-Authored-By`, the ticket IN_PROGRESS with a machine closing condition (probe), after that receipt passes.
- **FND-0437 (P3-6, geographic caps, construction layer): open, not worked** (proposed review 2027-01-15). **FND-0438 (P3-7): open**, waiting on section 3. FND-0436 (no dead funds in the PIT panel): open. FND-0439: closed by the owner's decision through the `Closes:` delivery of the commit that added this file (the autopilot closes it once the docs-only receipt passes; check `backlog` status first).
- **WSL keepalive:** the background session started on 2026-11-22 stops with that Claude session; restart it before any DB work and before the receipt.
- The impact of `shared/config.py` / new modules: run `python scripts/gov/repo/sync_agents_md.py --check` (and `--write`) or the pre-commit hook refuses.

## 5. P3-8 verified facts (owner decision: keep the classifier's CPI input unchanged)
- The regime classifier AND the IPC+M3 target use the instantaneous plain average of Spain and euro-area y/y CPI, each lagged 2 months (not a 12-month moving average). "M3" is euro-area M3 growth y/y.
- Gap in Sep 2026: Spanish y/y 4.96 % vs the model input 3.43 % = 1.53 pp, ~0.9 pp from the lag and ~0.6 pp from averaging with the euro area.
- INE final CPI ~12th-15th of month t+1; Eurostat final HICP 15-18 days after month-end; the euro-area flash comes after month-end. A 1-month lag has no look-ahead; lag 0 does.
- Study: R1 (max, lag 2) and R3 (max, lag 1) HARMFUL by the rule (non-inferiority not provable: MDE 0.25 / 0.19 pp vs a 0.10 pp margin; point estimates -0.03 / +0.03 pp); R2 (average, lag 1) NEUTRAL (+0.04 pp, lower bound -0.04); max drawdown moves <= 0.25 pp; mean weights <= 0.2 pp; Aug-Nov 2026 is Shock_Energetico under every arm (oil outranks inflation). The unlagged baseline net (0.08 %) beats CPI+M3 in only 3 of 22 years (2009, 2019, 2024).

## 6. Traps found (all fixed or documented)
- The PIT scorer de-duplicates families itself (`score_funds_from_df` -> `deduplicate_by_family`): any family/class lever belongs there, never in the engine (the first build of arm B was a no-op for that reason; `fee_aware` is now a per-call parameter and part of the score cache key only when ON).
- The last evaluation date has NaN returns: drop it before annualising (pool statistics and the Amendment 5 verdict had this bug); the sealed table prints turnover with two decimals.
- `RegimeClassifier.classify_historical()` caches its first result: clear `_historical_cache` after overriding `_macro` and check at least one label changed.
- Identity gates must compare the same months in both series (NaN in both is a match); an identity check that demands no NaN is wrong.
- Synthetic test series with constant growth hide the lag and the aggregator: use varying growth and run a mutation check.
- Freeze every DB-derived input of a chain in a hash-verified snapshot before the first run (`pit_p37_snapshot.freeze_frames` / `load_frames`).

## 7. The starter prompt for a fresh session (copy exactly)
```
Read doc/backlog/HANDOFF_P3_POST_COST_STUDY.md and continue Package L (P3 alignment) from section 3, "UNRESOLVED CHECKPOINT". First run python scripts/gov/repo/preflight_check.py --mine on any file you will touch; leave the untracked SKILL.md alone. Then present the four open P3-7 decisions (candidate for the forward shadow, retire arm B, build the forward-shadow stream for the fee penalty, open the gross-return question) with your recommendation, and wait for my answers before building, running or closing anything. Do not change production. Do not run the full receipt or close FND-0435 until I order the package closure; when I do, start the WSL keepalive first. Replies in English, ultra-short, decision first. Tests first, policy scanners after the last edit, commits with explicit paths and the Refs/Closes + Co-Authored-By trailer in the last paragraph.
```
