# Plan — Correct the P3 report's return metrics, then decide whether a predictive selection challenger is worth building

## Context

**Trigger.** The P3 report cell "Retorno real %" for IE00008TZ2E8 (8.08%) is `fund_metrics.return_ann`, horizon `since_inception`, `real_flag=1`: an annualized, IPC-deflated, since-inception return. It is not a YTD or 12-month figure. Investigating it exposed three live defects in P2 and the report.

**Objective.** (1) Make every return the report shows correctly scoped, correctly computed and automatically cross-checked against NAV. (2) Decide, on evidence and before spending money or build effort, whether a predictive challenger to the current scoring layer can beat the simplest alternative. The governing rule is the owner's: no new complexity reaches production unless it beats the current model objectively and reproducibly.

**Verified state, 2026-11-15 (read-only):**

| Fact | Source |
|---|---|
| `ytd` horizon has 0 rows in `fund_metrics`. The P2 recompute of 2026-11-15 did not change this | DB |
| `rolling_5y` / `rolling_10y` rows are written when only `MIN_NAV_ROWS = 12` rows exist: 224 and 925 of 3,706 rows cover less than 90% of their window (1,149 rows). `rolling_10y` for a 3-year-old fund equals its since-inception value. No open ticket covers this | DB; `proyecto2/src/pipeline/run_pipeline.py:1463`; `backlog_links.py` |
| "Exceso obj %" subtracts the NOMINAL objective from the REAL return; return columns carry no horizon or basis | `proyecto3/src/monthly_report.py:278, 428-432` |
| The stored `rolling_1y` for IE00008TZ2E8 is 3.68% over 2025-11-28 → 2026-11-05 (about 11.3 months: monthly rows are last-NAV-of-month). NAV-derived: YTD 3.09% (139.99 / 135.80 − 1), 12-month 3.90%. The reference the owner quoted (about 4.02% YTD) matches none. The fund is an EUR-hedged Accumulation class | `fund_nav_daily`, `fund_nav_monthly`, `fund_master` |
| Span rule at 35 days would purge 1,553 rows (all horizons; 1,411 for 5y + 10y); the 90% coverage rule purges 1,269 (1,149 for 5y + 10y). Choosing 21, 35 or 60 days barely matters (1,555 / 1,553 / 1,510 rows) | DB measurement |
| Hysteresis and the rotation cost gate are OFF (`PORTFOLIO_HYSTERESIS_ENABLED`, `ROTATION_COST_GATE_ENABLED`). On 2026-11-15 the owner confirmed the band 0.10 and treats FND-0158 as validation, not a change; the monthly shadow comparison was built that day and writes its first row at the next real build | `shared/config.py`; `doc/backlog/PKG_D_p3_selection_governance.md`; `doc/decisiones/DESIGN_FND-0158_hysteresis_shadow.md` |
| Newest PIT run (20261114_152205): champion Sharpe 0.56, max drawdown −14.6%, turnover 32% a month. Equal-weight pool benchmark (B-POOL): Sharpe 0.75, drawdown −11.9%. With band 0.10: Sharpe 0.655, drawdown −12.5%, turnover 16.6%, cost drag 0.50% a year (0.96% with no band). Hysteresis' robust effect is lower turnover and cost; its Sharpe gain is within noise. **This run (full universe, current universe only, start 2005-01, git 1f936a6) predates the 2026-11-15 repair of 119 fund natures (FND-0422) and the next recompute, so its figures are indicative; WP5 regenerates them. Its manifest does not record the weight method** | `out/reports/pit_backtest/20261114_152205/` |
| The history supports about 7 independent 36-month windows and about 21 twelve-month windows (260 months). Regimes have 21–65 months each. The measured 90% interval of a portfolio Sharpe difference is about ±0.058 | `summary.txt`; `proyecto3/AGENTS.md` |
| Only about 10 of the 822 `In_Current_Universe=0` funds with NAV are true deaths; 812 still publish NAV in 2026. The universe history is therefore nearly survivor-only | DB |
| P2 metrics are EUR views (`EUR_NAV_CONVERSION_ENABLED = True`, ECB rates), deflated by `REGION_IPC = "ES"`; 609 of 2,927 universe classes have a USD NAV | `shared/config.py`; `proyecto2/src/readers/db_readers.py` |
| The scorer reads `rolling_3y` snapshots (`ROLLING_PCTILE_HORIZON = "rolling_3y"`; `sharpe_slope` and `return_ann_slope` at `fund_scorer.py:298-304`, with the slope floor `ROLLING_SLOPE_MIN_OBS = 12`); momentum shares `trailing_window`; the PIT harness reads `TRAILING_KEYS`. Purging windows therefore reaches the scorer, the PIT harness, matviews and alerts | `shared/config.py:243, 1064`; `proyecto3/src/fund_scorer.py` |
| The statistical audit marks a run INCOMPLETE when a detector's canary fixture does not fire, and an INCOMPLETE run is not evidence for the promotion gate: a new detector or metric must be registered with its fixture | `shared/statistical_audit/completeness.py`, `canaries.py` |
| Stored PIT scores (`scores_long.parquet`, 444,113 rows): `as_of, regime, isin, fund_nature, fund_family_id, subportfolio, score_base, multiplier, score_final, eligible, exclusion_reason` | run 20261114_152205 |
| `fund_metrics` has 1.62M rows (about 900 MB with indexes); `fund_metric_timeseries` has 15.9M rows. PITR was drilled on 2026-09-26 (FND-0070 / FND-0071: base backup plus 407 WAL segments restored); its freshness since then is unknown | DB; backlog |
| `CALC_VERSION` is `"20261112"`; it was bumped in 16 commits, 12 of them in the 7 weeks to 2026-10-11 | `AGENTS.md`; git |

**Decisions already taken by the owner.**
- The challenger ends if the vendor budget is denied or the scorecard fails (terminate, no dormant state).
- The hysteresis band stays 0.10; enabling it is the owner's separate ruling after the shadow and the forward holdout (FND-0299) hold evidence.
- No manual handling in any data pipeline; where no automated route exists, the component is not built.
- Champion-side data captures (WP3) continue under any outcome; the owner can switch any of them off.
- Every live P1 / P2 / P3 run needs the owner's order for that run.

**Constraints (working method, `AGENTS.md`).** Work in packages; tickets only for what outlives the package; one full receipt per package delivery; one-page decision record rewritten in place; `preflight_check.py --mine <files>` before each work session and commit; one writing session at a time; the GitHub-out freeze banner stands until the owner lifts it.

## Out of scope (not built unless a later decision reopens it)

Learned scoring weights, regime-conditioned weights, new factors as score inputs, rotation economics, tax and transferability modelling, an execution and holdings ledger, any target-return or drawdown-budget optimizer.

## Work packages (in this order)

### WP0 — Governance and tickets (before any code)

1. Owner answers: is the freeze lifted for WP1, WP2 and WP3 (decision list below). The work runs as three packages in the owner's order, each with one exit metric and a time box of a few sessions: **Package 1 (defects)** = WP0–WP2, exit metric: the report reconciles to NAV, zero truncated windows, `ytd` present, one full receipt. **Package 2 (captures)** = WP3, exit metric: every capture has written its first snapshot and the ~10 stale funds are classified. **Package 3 (decision)** = WP4's interface, WP5 and WP6, exit metric: a recorded CONTINUE, PIVOT or untestable verdict and, only after CONTINUE, the owner's vendor decision. WP7 becomes a fourth package only after approval.
2. `preflight_check.py --mine <files>`; `backlog_links.py --new` for each ticket; `--for` against FND-0158, FND-0299 and FND-0389.
3. Open, via `backlog_client.open_ticket()` with a close condition each: three defect tickets (missing `ytd`; rolling windows without a coverage check; report real-minus-nominal excess and unscoped labels) and one cluster ticket for the selection decision (WP5 and WP6). Gates and studies are items of the package record, not tickets.
4. On the first authorised commit, place one one-page record per package in `doc/backlog/PKG_<letter>_*.md` (rewritten in place as work advances) and this plan with its parameter register (below) in `doc/especificacion/`.

### WP1 — Correct the three defects (independent of every other work package)

**1.0 Consumer inventory and report provenance (read-only, before any code).**
- (a) List every reader of the affected rows and the behaviour each must have when a value is missing: the scorer (`fund_scorer.py` percentile and slope snapshots at `rolling_3y`; momentum), the PIT harness (`TRAILING_KEYS`), the `gold.mv_*` matviews, the alert engine, `rolling_dashboard.py`, `export_metrics.py` and the report. The existing slope floor (`ROLLING_SLOPE_MIN_OBS = 12`) and the new coverage rule are one definition, not two floors.
- (b) Build the report provenance table: every displayed column of every sheet mapped to (metric, horizon, real_flag, metric_version, unit, window). A column without a scoped definition is fixed or removed. The labels spec of 1.3 is generated from this table.

**1.1 Window coverage rule.** `window_coverage_ok()` beside `trailing_window()` in `proyecto2/src/calculations/returns.py` checks two conditions:
- (a) **Span:** the first NAV of the window is at most `end − horizon + ROLLING_WINDOW_COVERAGE_TOLERANCE_DAYS` (35 days, which absorbs the last monthly close).
- (b) **Continuity:** no internal gap beyond the existing NAV continuity thresholds, by reusing `shared/nav_continuity.gap_reason` with `P3_FUND_NAV_MIN_COVERAGE` (0.80) and `P3_FUND_NAV_MAX_GAP_RUN_MONTHS` (6). No second threshold is invented.
- Applied in the rolling loop of `run_pipeline.py` (replacing the `MIN_NAV_ROWS`-only gate), in `rolling_stats.compute_rolling_rows`, and in `momentum.py`, which already has its own span check without tolerance and now calls the same function. A window that fails is not written and its stale row for that (isin, horizon) is deleted, as `_clear_short_horizon_rows` does for the short horizons. Kill switch `ROLLING_WINDOW_COVERAGE_ENABLED`.
- Before coding, the pre-test report prints the rows purged at 21 / 35 / 60 days and under the 90% rule (table in Context). The owner chooses rule A (span, 1,553 rows, recommended because a "10y" label then means ten years) or rule B (at least 90% coverage, 1,269 rows). Condition (b), continuity, applies under both rules.

**1.2 YTD.** `ytd_return(nav_df, as_of)` in `returns.py`: cumulative, never annualized, nominal and real; base is the last NAV on or before the prior Dec 31 and must lie within 5 business days of it (daily NAV, else the December month-end row), otherwise no row. A fund whose first NAV is in the current year gets the cumulative return since its first NAV, flagged `metric_version='ytd_partial'`. Written as metric `return_cum`, horizon `ytd` (no CHECK on `gold.fund_metrics.horizon`, so no DDL).

**1.3 Labels, units, window helper.**
- `shared/metric_labels.py` maps (metric, horizon, real_flag) to a label and definition; the report and `export_metrics.py` use it.
- `shared/metric_labels.py` also exposes `metric_window(isin, horizon)` (start, end, span, computed from NAV with `trailing_window`; no DDL), so a "1Y" label can show its real span.
- Report columns: YTD (nominal), 1Y (nominal, with its span), since inception (annualized, real), history in years. "Exceso obj %" = nominal 1Y return minus the nominal objective. A legend footer defines each return column. Each return shows its as-of date (the fund's last NAV), and a fund whose last NAV is older than `P3_FUND_NAV_MAX_AGE_DAYS` is flagged.

**1.4 Report reconciliation gate.** An independent minimal function recomputes the headline return metrics from the same source series (monthly rows for monthly metrics, daily rows for short-horizon metrics) over the metric's declared window. The report build fails if any identical-definition mismatch exceeds 5 bp. Each label shows its actual span. A non-blocking warning fires only when a retained window's span falls below the horizon minus the tolerance (a regression guard that cannot fire once 1.1 is in place) and for daily-versus-monthly differences above the tolerance. Monthly rows are last-NAV-of-month, so 1,251 of the 2,927 universe funds have a `rolling_1y` span under 350 days; the span is therefore shown on the label, not warned about. This is a deliberate second implementation, limited to five headline metrics and documented as an exception to the one-implementation rule (P#11).

**1.5 Tests (hermetic, R-7).** Coverage cases (span edge, tolerance, internal gap); YTD cases (stale base, launched this year, normal fund); report contract (labels come from the spec; no real-minus-nominal subtraction); reconciliation gate (a planted mismatch fails, a label-span gap warns); statistical-audit rule `ROLLING_COVERAGE` = 0; `SCALAR_EQUALS_TIMESERIES` still holds. AST check after every Python edit. The `ROLLING_COVERAGE` rule and a generic invariant (no `rolling_Ny` value equals its `since_inception` value unless the history is about N years) are registered in the audit with a canary fixture (`shared/statistical_audit/canaries.py`), and `return_cum` / `ytd` are registered in the catalogs. A FULL audit on the 40-ISIN sample must not be INCOMPLETE before the recompute.

**1.6 Sample run, rollback and recompute (on the owner's order).** First a P2 run on the 40-ISIN sample, with a diff of every changed metric against the stored values (tests run on the sample only). Then the full recompute:
1. Check `scripts/mnt/backup/setup_wal_archive.sh status` (PITR is the last resort).
2. Export to parquet by direct query, with no dependency on any other tool. `fund_metrics` is exported in full (1.62M rows). `fund_metric_timeseries` (15.9M rows; five windows of about 3.17M rows each) is exported as the subset the rule can touch: for each fund and window, the points dated within the first `horizon + tolerance` of its history (about a million rows, an estimate to be measured in the sample run). A subset by window label would still cover 80% of the table and is not used. A checksum (row count and sum of value) of the remainder is verified after the run; a mismatch outside the subset lists the changed (isin, metric) rows for the owner to explain, because other commits since the last calculation can change them.
3. Rehearse the restore once on a throwaway database pinned to `postgres:17`: DELETE and COPY of the exported rows with the application role, in batches (a commit every N funds, N from the rehearsal), never one transaction over a whole table. Watch the WAL archive and table bloat during the rehearsal (the archive has been active since FND-0070 / FND-0071).
4. Bump `CALC_VERSION` and run the standard cycle, which owns the frozen PRE audit and the POST audit with `--compare-to` (`scripts/launch/AGENTS.md`). A failed POST audit means restore from the parquet export.

**1.7 After the recompute.** Rebuild the P3 DRAFT and list every selection change against the current PRODUCTION scenario (expected to be near zero: the score uses the since-inception `momentum_rank`, the percentile signals are off and the slope multipliers are neutral; other commits since the last calculation can still change it) before any promotion. Promotion stays the owner's. The owner then re-runs the full-universe PIT on the recomputed data (the 2026-11-14 run took about 7 minutes); WP5 needs it.

**Acceptance.** IE00008TZ2E8 has no `rolling_5y` / `rolling_10y` rows; its YTD equals the NAV-derived value; the report shows scoped labels and a nominal-versus-nominal excess; the reconciliation gate passes on the 40-ISIN sample; both audits pass; one full receipt for the delivery.

### WP2 — Reconcile the incident figure (parallel to WP1, timebox 3 working days)

Record three checks for IE00008TZ2E8 against the reference that gave about 4.02%:
- (a) its share class and distribution policy against ours (an EUR-hedged Accumulation class);
- (b) the exact cut-off date and time convention (our last NAV is 2026-11-05; T versus T−1);
- (c) the manager's or Morningstar's official factsheet for the ISIN.

Outcomes: explained; a NAV-source defect (a ticket); or still unexplained after the three checks, which opens a NAV-intake incident. It is never closed as a note. WP1 proceeds in every case, because its fixes do not depend on the answer.

### WP3 — Champion-side data captures (start now; writes only inside existing harvest and refresh paths)

1. **Attribute history.** An append-only history of `fund_master` attributes (costs, `Fund_Nature`, `KIID_Status`, `vehicle_structure`, and the fields of item 5). DDL through `scripts/gov/backlog/owner_deploy.py`.
2. **KID version archive.** `fund_kiid_metadata` keeps only the latest KID per fund. The harvest also writes an append-only version row (ISIN, `kiid_pdf_hash`, published date, extracted costs, SRRI, declared benchmark, manager name when the document has it). A probe counts older PDFs still on disk and re-extracts their costs with the existing extractor.
3. **Survivorship measurement (read-only).** Classify the ~10 funds with stale NAV as liquidation, merger or data loss. Record each fund's first and last appearance in `db_document_catalogue` (9 harvests since 2026-07-17) as a dated presence series, so exits are counted by date and peer group.
4. **Macro vintages.** Classify the stored indicators as revised or unrevised (market prices, yields, spreads, FX, oil, gold and VIX are not; CLI, M2/M3, unemployment, GDP, debt and deficit are). Capture the revised ones from now into an append-only vintage store; probe ALFRED for FRED-hosted revised series.
5. **Free source survey (zero cost).** Probe the integrated Morningstar endpoints (`_MS_SCREENER_URL`, `_MS_PERF_URL` in `nav_discovery.py`), the CNMV registry and the PDFs already harvested for true inception date, size and manager; store results in item 1. **Scope:** a read probe of feasibility and coverage on the existing Morningstar endpoints and on a limited sample of CNMV records and PDFs already on disk. No web scraper or new extraction framework is built in this package; a source that would need one is recorded with its estimated effort for the owner to decide. **Rate limits:** run outside the daily cycle window, under the cycle's single-instance lock, with a hard requests-per-second cap and a daily request quota, so production NAV discovery cannot be blocked. `nav_sources.first_nav_date` is renamed `nav_history_start` in specs and code and is never used as an inception date; age-based features need the true inception date, and a fund without one is excluded, not approximated.

**Acceptance.** Each capture writes its first snapshot; the ~10 stale funds are classified; the probes record pass or fail per field.

### WP4 — Hysteresis (owned by Package D / FND-0158; no parallel rule)

- After the WP1 recompute the shadow keeps accumulating rows; the owner pre-registers the criteria in an amendment of `PREREG_FND-0193` before the first interim read (ruling not before 12 rows).
- Two pin tests are proposed to Package D, on the owner's order: an incumbent absent from the eligible candidate frame is never retained (`score_candidates.py:98` loads only `fs.eligible = 1` funds); with no PRODUCTION scenario `load_previous()` yields no bonus.

### WP5 — Ranking-layer test (decides whether a challenger continues)

**5.0 Mini-prereg first.** One page in the format of `PREREG_FND-0193.md`, committed before any run: the exact comparison, data cutoff, units, metrics, outcome rules, the reference attrition rate, the power statement, and the entry in the trial counter (a lower bound: the 22 PIT run folders plus FND-0193 / 0303 / 0315 / 0318). It also records the PIT run identity (git commit, configuration and weight method) and uses the production weight method (equal) for both arms.

**5.1 Controls first.** Permuted-label runs (the PASS rate must be at most α) and planted α in a random 10% of funds (the detection rate must meet its target) on the real data layout. A failure blocks the test.

**5.2 Feasibility.** Report the independent periods per horizon, the minimum detectable ΔSharpe and the minimum detectable selection spread. `MIN_MEANINGFUL_DELTA_SHARPE` (policy, proposed 0.10, about the gap between B-POOL and the champion with hysteresis) defines the plausible gain. If the minimum detectable ΔSharpe exceeds it, stop: the question is untestable.

**5.3 Units and peers.** One class per `fund_family_id` (the cheapest accessible). Peers are `Fund_Nature` × ex-ante volatility tercile (from NAV as of the formation date) × FX-exposure class (`shared/fx_exposure.py`). A cell needs at least 15 families, otherwise it collapses to `Fund_Nature` × FX, then to `Fund_Nature`. Estimates are pooled across cells and months. Scores are the PIT `score_final` of `eligible` funds per (`as_of`, `subportfolio`).

**5.4 Metrics, on the PIT scores regenerated after WP1.7 (`scores_long.parquet`), hysteresis ON for both arms.**
- **M2, selection:** per (`as_of`, `subportfolio`), the champion's top quintile by `score_final` against the peer median on the outcome *forward return divided by the fund's trailing 36-month volatility at formation* (the risk is known at formation, so it cannot be gamed after the fact). Forward Sortino, with the existing downside-deviation floor, is a secondary outcome. 12 months is primary; 36 months is reported and must be sign-consistent. Standard errors by block bootstrap clustered by date and family. The top quintile's max drawdown must be no worse than the peer median's by more than 2 pp. **Sensitivity:** repeat with peers that avoid `Fund_Nature` (ex-ante volatility and FX classes only), because the nature classifier uses realized volatility over the full history; if the verdict changes, the result is INCONCLUSIVE.
- **M1, portfolio:** net Sharpe of the champion minus B-POOL with its paired 90% interval.

**5.5 Outcomes.**
- **CONTINUE:** the one-sided 95% lower bound of the 12-month M2 is above zero with the drawdown condition met. If M1 is at most zero, the verdict adds CONSTRUCTION-LOSS: scoring has value and portfolio construction loses it. The champion-side construction study runs first and WP6 does not start until its result is recorded. It needs no vendor: on the existing PIT harness it removes or relaxes one construction element at a time (fund and manager caps, nature caps, regime weights, FX budget, weight method) and reports which one closes the gap to B-POOL.
- **PIVOT:** the M2 point estimate is at most zero and the survivorship bias bound (the pre-registered reference attrition rate applied with worst-decile paths) cannot flip its sign, or the champion's max drawdown is worse than B-POOL's by more than 2 pp.
- **INCONCLUSIVE:** anything else. No pivot and no vendor contact; the case returns to 5.2.

**5.6 If PIVOT.** The challenger ends. The production approach becomes the simplest configuration not worse than the best (the owner decides). Effort moves to cost-aware and drawdown-aware eligibility, tested against the B-CHEAP baseline. WP3 continues.

### WP6 — Value case and vendor decision (only after CONTINUE, a recorded construction study if CONSTRUCTION-LOSS was flagged, and sufficient power)

1. **Value case (one page, three scenarios).** Low, base and high estimates of the extra net Sharpe that better selection could add, built from the measured M2 effect, the number of independent selection decisions a year and the correlation between selections, expressed as basis points of return a year. They are set against `MIN_MEANINGFUL_DELTA_SHARPE` and against the total cost: the data price a year divided by the portfolio size the owner supplies, plus engineering sessions and owner time. Proceed only if the base-case benefit is at least `VALUE_COST_RATIO_MIN` times the cost and the low case does not lose money. The page also states the earliest date a promotion decision could be taken; if it is more than `EARLIEST_EVIDENCE_MONTHS` away, starting WP7 needs an explicit owner decision.
2. **Vendor evaluation** (the only point where a vendor is contacted). Scorecard criteria are frozen before the trial extract (about 200 ISINs including the ~10 stale funds) is read.
   - **Gate-critical:** dead-fund coverage (history to the last NAV plus an event class and, for mergers, the successor ISIN; 8 of 10 covered, with the 95% interval 44%–97% printed beside it); vendor-universe completeness by domicile and year against regulator totals (never against our single catalogue); a licence for storage and derived use; ISIN mapping above 98%.
   - **Optional bonus:** point-in-time AUM (5 years for 90% of the trial ISINs), flows, manager tenure.
   - Candidates to evaluate, not recommended: Morningstar Direct, LSEG Lipper, Bloomberg, FactSet. Credentials stay in the owner vault.
3. **Decision:** completing the package opens a decision ticket and notifies the owner, with a re-alert every 14 days; there is no default decision. Approval starts WP7 with a stop-loss of 30 agent sessions or 5 calendar months. Denial or a failed scorecard ends the challenger.

### WP7 — If approved: post-approval stages (each opens only on the recorded result of the previous one)

1. **Specification and prereg (documents).** Layer contracts (eligibility → prediction → construction → rotation); all metrics in EUR on the existing EUR view, hedged and unhedged classes compared on realized EUR returns; the survivorship-free versus survivor-only evidence rule; the trial registry and alpha spending; every number frozen before any run.
2. **Factor library** into the empty `series_benchmark`: duration returns ≈ y·Δt − D·Δy from `yield_10y_gov` (consuming FND-0389's output if delivered: FND-0389 writes P2 betas to `fund_metrics`, this writes factor return series to `series_benchmark`, so no shared table and no second lag logic; publication lags as in `pit_macro.visible_macro`), credit IG/HY (ICE BofA via FRED), regional equity indices; commodities and FX already stored. Unrevised market series only, enforced by an audit rule. Validation: coverage, plausibility and a cross-check against the 20 live ETFs.
3. **Forward ledger** extending the FND-0158 shadow pattern (append-only `ingestion_log` row per build, build identity, kill switch) and the FND-0299 pairing; candidates frozen in the prereg; 12- and 36-month labels joined at maturity.
4. **Study S0 + S1a:** power on survivorship-free data, then characteristics against forward peer-relative risk-adjusted performance (point-in-time-safe features only; cost as a prior; B-CHEAP baseline). A cell whose adverse-direction survivorship bound exceeds `SURVIVORSHIP_BIAS_MAX_FRACTION_OF_MDE` (0.5) × its minimum detectable effect is INCONCLUSIVE, never PASS.
5. **Study S1b:** rolling constrained style analysis (β, α, tracking error, drift) in a read-only research sandbox `proyecto2/research/` (an AST isolation test in the delivery receipt, and the `fondos_ro` role); empirical-Bayes α with FDR; exposure-based peers; a B-PASSIVE baseline.
6. **Provisional tier (only if S0 shows 12-month power).** One primary candidate; a tilt of at most 10 pp per sub-portfolio and `Fund_Nature` per cycle through the existing DRAFT → PRODUCTION gates; reversion to the champion in the same cycle on a drawdown worse than the champion's by more than 2 pp or a tilt breach; an evidence-based reversion on an exponentially weighted average of the 6-month IC, with a threshold derived empirically in S0 and no detection-speed promise. Full promotion only at the 24- and 36-month forward checkpoints after the ledger starts.
7. **Monitoring and transparency.** The report cover shows `CALC_VERSION`, data cutoffs, switch states (hysteresis, cost gate, weight method), a definitions legend and the model's evidence status. A post-promotion monitoring spec (forward shadow versus live, drift in IC and turnover, automatic alert) exists before any promotion. An assumptions register (Spanish tax resident, retail investor, platform, EUR, 25 bp per side) is reviewed by the owner.

## Decisions needed from the owner

1. **Freeze and packages (before any code):** is the freeze lifted for WP1, WP2 and WP3? Which letters for the three packages?
2. **Coverage rule (WP1.1):** rule A (span, 35 days, 1,553 rows purged, recommended) or rule B (at least 90% coverage, 1,269 rows)?
3. **Batch 1 constants:** rows 1–6 of the register.
4. **Hysteresis:** confirm WP4 is fully absorbed by Package D (FND-0158), with no parallel activation rule.
5. **Recompute order:** issued after the WP1 tests pass, with the WP1.6 backup.
6. **Batch 2 constants (rows 7–16), before any vendor contact.**

## Parameter register (class: S structural, P policy, C operational)

| # | Constant | Proposed | Class | Evidence | Batch |
|---|---|---|---|---|---|
| 1 | `ROLLING_WINDOW_COVERAGE_TOLERANCE_DAYS` | 35 | S | One monthly close; 21 / 35 / 60 days purge 1,555 / 1,553 / 1,510 rows | 1 |
| 2 | Coverage rule | A (span) or B (90%) | P | 1,553 vs 1,269 rows | 1 |
| 3 | `YTD_BASE_MAX_STALENESS_BDAYS` | 5 | P | Launched-this-year funds get `ytd_partial` | 1 |
| 4 | Reconciliation tolerance, identical definitions | 5 bp | P | Warnings cover label-span and daily-monthly gaps | 1 |
| 5 | Incident reconciliation timebox | 3 working days | P | Escape only after the three checks | 1 |
| 6 | Restore batch size (funds per commit) | 50 | C | Measured 2026-11-15 on postgres:17 (600 funds, 2.7M rows per trial): the longest transaction took 10.5 s at 50 funds, 15.7 s at 150, 26.4 s at 300 and 69.7 s at 600; every trial reproduced the export exactly; about 2.6 MB of WAL per fund, so a whole-universe restore of 3,706 funds is about 10 GB of WAL and about 10 minutes | 1 |
| 7 | Minimum families per peer cell | 15 | P | Smallest categories have 18–20 funds | 2 |
| 8 | CONTINUE confidence level | one-sided 95% | P | The history is already mined | 2 |
| 9 | Drawdown margin | 2 pp | P | Precedent in the FND-0193 layer verdicts | 2 |
| 10 | Reference attrition rate for the PIVOT bound | 5% a year | P | **Assumption, not measured**; replaced by the WP3.3 measurement when available | 2 |
| 11 | `EARLIEST_EVIDENCE_MONTHS` | 18 | P | Forward labels are 12 and 36 months | 2 |
| 12 | Challenger stop-loss | 30 sessions or 5 months from approval | P | Estimate of the pre-decision work is 14–18 sessions (below), unmeasured | 2 |
| 13 | Scorecard thresholds | 8 of 10 dead funds; ISIN mapping above 98% | P | 8 of 10 has a 95% interval of 44%–97%; a screen, not an estimate | 2 |
| 14 | Decision-ticket re-alert interval | 14 days | C | Operational | 2 |
| 15 | `MIN_MEANINGFUL_DELTA_SHARPE` | 0.10 | P | About the 0.095 gap between B-POOL and the champion with hysteresis (2026-11-14 run, to be re-measured) | 2 |
| 16 | `VALUE_COST_RATIO_MIN`; portfolio size | 3; supplied by the owner | P | Needed before any vendor contact | 2 |
| 17 | `SURVIVORSHIP_BIAS_MAX_FRACTION_OF_MDE`; tilt cap 10 pp; EWMA threshold; alpha spending | 0.5; 10 pp; from S0; frozen in the prereg | P | Needed only in WP7 | later |

## Outcome measures (tracked from the first delivery)

- Share of displayed report columns with a scoped definition in the provenance table: 100%.
- Reconciliation-gate failures per report build: 0; warnings listed on the report cover.
- Truncated rolling windows in `fund_metrics`: 0 (today 1,149 for 5y and 10y).
- Champion versus B-POOL net Sharpe, drawdown and monthly turnover: from the PIT run after each recompute and from the FND-0158 shadow rows at each build.

## Risks and stop rules

| Risk | Mitigation |
|---|---|
| The recompute fails or corrupts metrics | Parquet export of the write-set, restore rehearsed, PITR as last resort, POST audit as the gate |
| The new coverage rule changes live selections (momentum is in the score) | Full-universe selection diff before any promotion (WP1.7) |
| The reconciliation gate blocks a legitimate report | Compares identical definitions only; warnings for definitional gaps |
| Morningstar blocks production NAV discovery | Rate limits, quota, off-cycle window, single-instance lock (WP3.5) |
| Low power makes the ranking test inconclusive | Feasibility check first; an untestable question stops the challenger before any spend |
| Vendor data is unavailable or too costly | Terminate the challenger; the champion and WP3 continue |
| The earliest evidence is more than 18 months away | Explicit owner decision before WP7 |
| Another session changes the shared tree | One writing session; `preflight_check.py`; the owner's order per live run |
| Purging windows breaks a consumer, or a new detector makes the audit INCOMPLETE and blocks promotion | Consumer inventory (1.0); canary fixtures and catalog registration (1.5); a sample run and a FULL audit on the sample before the full recompute |
| The evidence behind the challenger decision is stale (the PIT run predates the nature repair and the recompute) | WP5 runs only on a PIT regenerated after WP1.7 |
| Owner time is the real bottleneck (six decisions and three orders) | Three packages with one exit metric each; decisions batched; each package record leads with the decision needed |

**Stop rules.** PIVOT ends the challenger. Denial, a failed scorecard, or an untestable question ends it. The stop-loss applies from the approval of WP7. Nothing in WP5–WP7 runs before WP1 is delivered or the owner explicitly says to run it earlier.

## Effort (my estimate, unmeasured)

WP0 about 1 agent session; WP1 4–6, plus an owner-run recompute whose duration is to be read from the last P2 log; WP2 about 1; WP3 3–4; WP5 3–4; WP6 1–2. Pre-decision total about 14–18 sessions. WP7 is not estimated until the value case exists. **Owner time:** six decisions (listed above), three orders (the recompute, the full-universe PIT, any later live run) and, only after a CONTINUE, one vendor decision. No calendar date is committed because every step waits on an owner answer; each package carries its own time box.

## Verification, end to end

- **WP1:** hermetic tests and AST checks pass; on the 40-ISIN sample IE00008TZ2E8 loses its `rolling_5y` / `rolling_10y` rows and shows the NAV-derived YTD; the regenerated DRAFT report shows scoped labels and nominal excess; the reconciliation gate fails on a planted mismatch; the PRE and POST audits pass; a restore from the parquet export reproduces the pre-run tables on the throwaway database; one full receipt.
- **Consumers and audit:** the consumer inventory (1.0) is complete with a defined behaviour for each missing value; the report provenance table covers every displayed column; the FULL audit on the sample is not INCOMPLETE, and the new detector's canary fires.
- **WP2:** the three checks are recorded and the outcome is a ticket or an explanation.
- **WP3:** each capture's first snapshot exists; two harvests of a fund with a changed KID give two version rows; a planted revision of a macro indicator gives a second vintage row; no request rate exceeds the cap during a cycle.
- **WP5:** the mini-prereg is committed before the run; the controls pass (null PASS rate at most α, planted detection at target); the verdict names its power.
- **WP6:** the scorecard criteria are committed before the extract is read; the decision ticket opens and notifies automatically.
- **Always:** policy scanners after the last edit and before each commit; `delivery_attest.py` once per package delivery.

## Critical files

- **WP1:** `proyecto2/src/calculations/returns.py`, `rolling_stats.py`; `proyecto2/src/pipeline/run_pipeline.py`; `shared/config.py`; `shared/metric_labels.py` (new); `shared/nav_continuity.py` (reused); `shared/statistical_audit/` (with `canaries.py`); `proyecto3/src/fund_scorer.py` (consumer); `proyecto3/src/monthly_report.py`; `proyecto2/src/analysis/export_metrics.py`; `scripts/mnt/backup/setup_wal_archive.sh`.
- **WP3:** `db/pg/` DDL through `scripts/gov/backlog/owner_deploy.py`; the P1 harvest; `proyecto2/src/discovery/nav_discovery.py`.
- **WP4:** `proyecto3/src/score_candidates.py`, `portfolio_builder.py`, `hysteresis_shadow.py`.
- **WP5:** `proyecto3/src/pit_baselines.py`, `pit_oos.py`, `pit_run.py`; the stored `scores_long.parquet`; `shared/fx_exposure.py`.
- **Documents:** `doc/backlog/PKG_<letter>_selection_quality.md`; this plan and its parameter register in `doc/especificacion/`; the mini-prereg in `doc/decisiones/`.
