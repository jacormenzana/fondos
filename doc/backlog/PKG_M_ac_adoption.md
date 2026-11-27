# Package M - adoption plan of AC (fee-aware selection live, safely)

**Status: SIGNED OFF by the owner 2026-11-26 (on `9b0adf3`); tickets open (section 4), no work started.** One page, rewritten in place, never amended. Rules: `doc/decisiones/PREREG_P3_7.md` sections 13-14 (Amendment 2, `bc69c7d`; its three additions approved by the owner 2026-11-26). Decision record: `doc/decisiones/DECISION_P3_7_AC_ADOPTION_ROUTE.md`. Study ticket: FND-0438. Letter M chosen by the assistant.

**Exit metric.** At the R6 review, one of:
- the candidate it names (AC, or B under the fallback) runs in PRODUCTION, with the baseline shadow recording;
- the R6 verdict is recorded as "no adoption".

**Time box.** The build work below takes a few sessions. The review is a dated event (earliest about 2027-05/06), not a session.

## Owner rulings (2026-11-26)
1. The plan is signed off as written.
2. Tickets:
   - T1 widens FND-0438;
   - T2, T3 and T4 are new tickets;
   - T3 is kept separate from FND-0444.
3. **Standing order:** scheduled monthly builds are authorised strictly in DRAFT-only mode. They generate the DRAFT and the shadow rows and never promote to PRODUCTION without the owner's explicit order. This replaces "an order per run" for these builds only; P1 / P2 runs are not covered.
4. FND-0443 and FND-0444 are HARD gates before any live promotion (logged on both tickets). **Amended 2026-11-27 (owner):** FND-0444 is REMOVED from the hard gates and stays open as ordinary work, because the harm it guarded against is now prevented twice (the builder skips twins, `cf46f1c`; `GATE:DUPLICATE_FUND` refuses them). **FND-0443 remains a hard gate.**
5. **Standing order extended (2026-11-26, second ruling):** the monthly NAV refresh is automated, and the standing order is extended EXCLUSIVELY to the P1/P2 ingestion and calculation cycle (`P1P2_runCycle.bat`, harvest read-only), scheduled from day 25 and retried until the data will pass the P3 gate on the next build. Promotions, switch flips, KIID sync / retire and every other run still need his order. Ticket FND-0448.

**Open point (for the owner, not worked):** FND-0444 has no closing condition yet, so "CLOSED on its probe" cannot be evaluated by a machine. It gets one when the ticket is started (`NORMAS_BACKLOG.md` section 4).

## 1. The R6 reader (`scripts/audit/p3/p3_shadow_read.py`, built `54fb3d2`)
**State.**
- The first live row (#1569488, 2026-11-22) was verified on integrity fields only (`HANDOFF_P3_AFTER_AC_SHADOW.md`).
- The reader exists, built test-first (22 tests).
- Its default mode prints only the whitelisted integrity fields. `--review` is refused (exit 3) until the rows span 6 calendar months with at most 1 gap.
- It applied Amendment 1 only: it filled an unknown fee with the median of the known fees of the funds held (a reading the owner validated on 11-22, replaced on 11-26) and had no coverage condition.
- **DONE 2026-11-27 (`Refs: FND-0438`): changes (a) to (e) below are built** (31 tests; the two that pinned the old rule, the median fill and the two-column snapshot, were rewritten to the new one; the other 20 are unchanged apart from the snapshot columns). Integrity mode on live data: candidate pool 940 funds, 97.4 % with a fee or a ceiling on both sides. The review itself still waits for its window.

**Change (test-first; the integrity-only output is unchanged, and no comparison, turnover or cost field of any stored row is printed while the change is built or tested):**
- (a) Cost fill per fund and per side: exact fee → declared ceiling (`*_Fee_Pct_Max` / 100) → p90 of the exact fees of the same `Fund_Nature` in the frozen review snapshot (all natures when the nature has none). The same fill for L, B and AC. The frozen snapshot gains the two ceiling columns and `Fund_Nature`, with their hashes.
- (b) An optimistic variant, where a fund with no figure costs 0, reported next to the verdict and never gating.
- (c) The coverage condition: AC's bought notional over the window (positive weight changes between consecutive counted rows) with an exact fee or a ceiling on both sides, against `fee_coverage_min` = 95 %. Below it, R6 = NOT INFORMATIVE and carried to the next review. The same share is reported for L and B, plus the exit coverage of sold notional.
- (d) Integrity mode: `n_fee_unknown` is relabelled for what it counts (the whole scored frame). The candidate pool's own coverage (exact or ceiling, from the candidates, not from AC's selection) is added.
- (e) A failing test first for each of (a)-(d), on synthetic rows only. Gate of the build: the existing 22 tests stay green, and a mutation of the fill or of the threshold must be caught.

**Verdict table of R6 (deterministic, as section 13 / section 14):**

| R6 result | Consequence |
|---|---|
| Rules 1-3 PASS, coverage met, condition (c) met (already PASS on the PIT: 25 bp 3.71 against 3.69 %, 100 bp 2.47 against 0.74 %) | AC is the candidate; the owner's order adopts |
| Rule 3 FAILS, rules 1 and 2 PASS | B (band alone) is the candidate; the penalty is dropped |
| Coverage below 95 %, or more than 1 gap | NOT INFORMATIVE; the review moves one month |
| Rule 1 or 2 FAILS | No adoption; the shadows keep measuring |

## 2. L kept as a shadow arm after adoption (needed by R24, R36 and the revert trigger)
**Problem.** Today L is the live arm: the `off` of each AC row equals the persisted DRAFT. Once AC is live, the DRAFT is AC, so neither stream records L any more. B (`P3_HYSTERESIS_SHADOW`) and the AC stream would then compare the live arm with itself.

**Design (proposal):**
- A third stream `P3_BASELINE_SHADOW` with its own kill switch `P3_BASELINE_SHADOW_ENABLED`, shipping OFF.
- At each real build it records arm L: the live scorer with the levers of L (L4 off) and the builder at band 0.
- It is built with the same functions as the live build (R3), as `fee_penalty_shadow.run_shadow` does for AC.
- It keeps two streams: `chain` from its own previous row, and `one_step` from PRODUCTION.
- Its first `chain` row starts from the last DRAFT before adoption, which IS L, so the L series is continuous across the switch.
- Every row records the live flags of its build (`PORTFOLIO_HYSTERESIS_ENABLED`, `P3_L4_FEE_PENALTY_ENABLED`, `P3_L4_H_YEARS`), so the reader can find the month of adoption from the rows alone.
- The design is generic: if B is adopted instead, the same stream is its baseline.

**Identity check before adoption (free validation).** Switched ON in SHADOW mode before the adoption, its `one_step` must equal the live DRAFT exactly, because live is L. This compares L with L, never AC with L, so it is outside the reading ban of section 13. Every mismatch is a defect found before it matters. It needs the owner's order to switch it ON.

**Reader after adoption.**
- Series: AC = consecutive DRAFTs; L = `chain` of the baseline stream. Before adoption the sources stay as they are today.
- R12 (alarm), R24 and R36 (AC against L, paired bootstrap, the FND-0299 checkpoints 2028-10 / 2029-10): a FAIL = REVERT.
- The Amendment 5 revert trigger over at least 12 consecutive builds after adoption: net return more than 0.50 pp a year below L, or drawdown more than 2.0 pp deeper, or a realised turnover reduction below 0.5 of the PIT one. The PIT reduction is (32.2 - 13.4) / 32.2 = 58 %, so the trigger fires below 29 %.
- The fill of section 1 applies to every cost.
- A REVERT verdict is printed. The owner's order switches the flags OFF; the baseline shadow keeps running.

**State (built 2026-11-27, FND-0445; switch ON since the owner's order of 2026-11-27, the next real build writes the first row).**
- Recorder `proyecto3/src/baseline_shadow.py`, hooked last in `p3_build_portfolio.py`. Reader: `p3_shadow_read.py --after-adoption`; the default view adds an integrity block once baseline rows exist.
- Identity check: while the live arm is L, funds, weights and scores must equal the DRAFT. A mismatch keeps the `SHADOW` row (so the monthly schedule does not rebuild every day) and adds one `P3_BASELINE_SHADOW_ERROR` row.
- Funds with equal scores are ordered as the live query ordered them (that order is arbitrary; 1 such tie touched a held fund on 2026-11-27).
- Readings where the preregistration is silent (assistant's; **owner to confirm before the first counted use**):
  - the R24 / R36 lower bound is one-sided at 5 %;
  - R12 / R24 / R36 use the first 12 / 24 / 36 months of the spliced series (shadow months before adoption included);
  - in the adoption month the adopted arm trades from L's DRAFT; that trade is in the returns and out of the turnover ratio;
  - a fund without a month's return is renormalised away;
  - months after a revert are not read;
  - the adopted arm is AC only at H = 0.58 (both flags with another H is not a candidate);
  - a fund's latest observed month is provisional and not read; a month in which nothing held has a return is no observation;
  - rows of one month from different builds are reported, never gating.

**Order of adoption (one config commit, a full receipt BEFORE it is published, because it is a switch change):**
- `PORTFOLIO_HYSTERESIS_ENABLED` = True;
- `P3_L4_FEE_PENALTY_ENABLED` = True and `P3_L4_H_YEARS` = 0.58 (today 3.0);
- `P3_BASELINE_SHADOW_ENABLED` = True (already ON since the identity check);
- `P3_FEE_PENALTY_SHADOW_ENABLED` and `P3_HYSTERESIS_SHADOW_ENABLED` = False, because they would compare the live arm with itself. Their rows stay stored.

## 3. Live promotion gated on FND-0443 (FND-0444 removed 2026-11-27)
Adoption is not promotion. The flags change the NEXT DRAFT. PRODUCTION changes only through `scripts/launch/p3_promote_scenario.py` and the verdict of `shared/governance/orchestrator.py::authorise_promotion` (`GOBIERNO_CONTROLES.md` section 8). No DRAFT is promoted, AC or not, before all of the following hold:
- **FND-0443 (HIGH) CLOSED** on its probe: no `fund_metric_timeseries` point of a window the NAV does not cover, and a POST audit without that FAIL. Today the promotion catches it only indirectly: the data gate (`data_suspects`) refuses a DRAFT that holds an affected fund, and only while the audit is fresh (3 days).
- ~~FND-0444 CLOSED~~ **No longer required (owner 2026-11-27).** A portfolio holding one fund twice is prevented by the builder (`P3_BUILDER_DUPLICATE_FUND_ENABLED`) and refused by the HARD gate `GATE:DUPLICATE_FUND` (`1c757a9`, FND-0446), which stays after FND-0444 closes.
- A fresh POST audit (at most 3 days) and the usual gates (D1 code, manifest, cost).
- The first AC DRAFT is promoted by the owner's order, like every promotion.

## 4. Tickets (opened / widened 2026-11-26, session `FND_20261126_DEV_PKGM_AC_ADOPTION`)
| # | Work | Ticket | Priority | Closing conditions |
|---|---|---|---|---|
| T1 | Section 1: the reader to Amendment 2 | FND-0438, widened (absorbed scope copied in its log) | MEDIUM | `tests_pass`, plus `manual`: the R6 verdict is recorded. The study ends at R6; the reader commit cites `Refs:`, not `Closes:` |
| T2 | Section 2: `P3_BASELINE_SHADOW`, its reader branch, the identity check | FND-0445 | MEDIUM | `tests_pass`; at least 1 `P3_BASELINE_SHADOW` / `SHADOW` row in 40 days; 0 `P3_BASELINE_SHADOW_ERROR` rows in 40 days |
| T3 | Section 3: `GATE:DUPLICATE_FUND` (separate from FND-0444) | FND-0446 | HIGH | `tests_pass` (refusal also when the `fund_family_id` differ; replay refuses the DRAFTs of 11-21 and 11-22) |
| T4 | Section 5: scheduled monthly DRAFT-only build | FND-0447 | HIGH (the 2026-12 row is the first counted month) | `tests_pass`; `task_ok` `FondosP3MonthlyDraft`; at least 1 `P3_FEE_PENALTY_SHADOW` row in 40 days; at least 1 `P3_MONTHLY_DRAFT` OK row in 40 days |
| T5 | Section 5: scheduled monthly P1+P2 cycle (NAV refresh) | FND-0448 | HIGH | `tests_pass`; `task_ok` `FondosP1P2MonthlyRefresh`; at least 1 `P1P2_MONTHLY_REFRESH` OK row in 40 days |

FND-0443 and FND-0444 are worked on their own tickets, in the order the owner sets.

## 5. Risk to the date: the build cadence `[CARGA MANUAL]`
- **What and who.** Rows exist only when a real build runs, and every live build needs the owner's order (owner rule, `PLAN_SELECCION_CALIDAD.md`). So it is the owner, once a month, for at least 7 months (2026-11 is uncounted; 2026-12 to 2027-05 are the 6 counted months).
- **Why it matters.** More than 1 missed month in the 6-month window postpones R6 by a month each time.
- **Automated alternative (proposed).** A scheduled monthly P3 build that writes a DRAFT and the shadow rows and NEVER promotes. Promotion stays the owner's event. It runs behind the same cycle lock, single instance, with its own exit codes (`NORMAS_BATCH.md` for its launcher).
- **Prerequisite: GRANTED 2026-11-26** (standing order, section "Owner rulings"). Built under FND-0447 (`d8403e6`): `scripts/launch/P3_monthlyDraft.bat` wraps `P3_runCycle.bat`, builds only while the current UTC month lacks its DRAFT or a row of an enabled shadow stream, and writes one `P3_MONTHLY_DRAFT` row per attempt. The daily task `FondosP3MonthlyDraft` (09:00) is registered by `scripts/mnt/sched/register_monthly_cycle_tasks.ps1`.
- **NAV freshness: AUTOMATED (FND-0448).** The P3 freshness gate needs `nav_p10` to be at most 14 days old and the harvest at most 45 days.
  - `scripts/launch/P1P2_monthlyRefresh.bat` (daily task `FondosP1P2MonthlyRefresh`, 20:00, same registration script) acts from day 25 to the end of the month. It runs `P1P2_runCycle.bat` (defaults: harvest read-only) while the NAV or the harvest would fail the gate on the next build's last retry day (day 4 of the next month), computed with the gate's own functions.
  - It retries daily until the data is fresh enough, writes one `P1P2_MONTHLY_REFRESH` row per attempt (OK / FAILED / STALE), and shares the single-instance lock.
  - Window: the owner's "25th to 28th", extended to the end of the month so a failed day still has a retry.
  - No recurring manual step is left in the monthly capture.

## 6. Timeline (if builds run monthly)
| When | Event |
|---|---|
| After sign-off | T1, T2, T3 built test-first. T2 switched ON for the identity check (owner's order) |
| 2026-12 to 2027-05 | 6 counted rows; integrity view only |
| about 2027-05/06 | R6 review (`--review`); verdict per the table of section 1 |
| after R6 PASS | owner's order: the adoption commit (section 2), receipt first |
| first build after adoption | AC DRAFT; promotion only per section 3 |
| about 2027-12 / 12 builds after adoption | R12 alarm; the revert trigger becomes active |
| 2028-10, 2029-10 | R24, R36: FAIL = revert |
