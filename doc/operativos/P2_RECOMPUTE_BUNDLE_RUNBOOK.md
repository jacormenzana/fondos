# P2 recompute bundle — runbook (FND-0196, FND-0226, FND-0200, FND-0202)

Four corrections that change stored P2 metric values. **Status 2026-10-04: the flags are ON in `shared/config.py` and
`CALC_VERSION` is `20261004`, but the stored metrics are still the old ones until the owner runs the ONE full P2
recompute below.** (Before the flip they were merged dormant, all `False`.)

| Flag (`shared/config.py`) | Ticket | What changes | Measured / expected effect |
|---|---|---|---|
| `MACRO_VIF_ITERATIVE_ENABLED` | FND-0196 | Macro OLS drops ONE factor at a time (iterative VIF) with `MACRO_ITERATIVE_VIF` (protect `spread_hy`, `vix_yoy`; drop `spread_ig`; at most n_obs/10 factors), instead of dropping every factor over the threshold in one pass | `beta_spread_hy` coverage about 10% of funds today (306 of 3,100) -> about 54% of fund-dates in the sample; 17 vs 12 factors kept on the 2010+ window |
| `MACRO_FACTOR_CLEAN_ENABLED` | FND-0226 | A non-positive CPI index level is missing; +/-inf macro cells become NaN | `ipc_yoy_cn` is +/-inf in 13 months today (14 zero values in `series_macro` CN, last observation 2024-03) |
| `PERSISTENCE_FIRST_LAST_NAV_ENABLED` | FND-0200 | Peer return in a window from the FIRST and LAST NAV (was MAX/MIN) | Peer returns overstated by 5.2 pp a year on average (0 to 24 pp; equities 2008 window +24 pp), so `alpha_persistence` rises for most funds |
| `CAPTURE_MONTH_END_ENABLED` | FND-0202 | Capture ratios on a month-end grid, 1-month returns only | Fund observations dated mid-month stop dropping out of the join; coverage and values change (sample: PIT capture coverage 76.7% -> 82.0%) |

The input fingerprint includes whichever flags are on (`utils/fingerprint.effective_calc_version`), so the first run after a
flip recomputes every fund without a separate step. Still bump `CALC_VERSION` in the same change so the stored
`algorithm_version` records the new logic.

## Before
1. FND-0179 healthy: `bash scripts/ops/setup_wal_archive.sh status` shows a small WAL archive and free space; the 3-day base
   backup and daily off-box tasks have run once with result 0.
2. Take a fresh base backup right before: `bash scripts/ops/setup_wal_archive.sh basebackup`.
3. Nothing else writing: no P1, P3 or PIT backtest running. Keep the WSL keepalive session open.
4. Optional but recommended: the two full PIT backtests (control / iterative_hy, see FND-0224) and a look at
   `crisis_variants.csv`. They do not gate this bundle, but they tell whether `MACRO_ITERATIVE_VIF` should keep protecting
   `vix_yoy` / `spread_hy` (the crisis multiplier may be retired, FND-0225).
5. PIT backtests keep working with all four flags on: the PIT persistence follows `PERSISTENCE_FIRST_LAST_NAV_ENABLED` and the
   PIT capture ratios have a month-end replica (`_capture_ratios_month_end`, FND-0227), both pinned to P2's own functions by
   the oracle tests; the PIT cache keys include the flags. On the 32-fund sample the flip moves PIT scores modestly
   (correlation 0.987, capture coverage 76.7% -> 82.0%, identical top-10 in 99.7% of sub-portfolio pools). The PIT guard
   `assert_series_cover` (FND-0228) refuses evaluation dates before 2000-03 (first month with rate and lagged IPC).

## Flip (DONE 2026-10-04, one commit)
1. `shared/config.py`: the four flags are `True`.
2. `proyecto2/src/pipeline/run_pipeline.py`: `CALC_VERSION = "20261004"` (v40) with a comment naming the four tickets.
3. AGENTS.md synced. Tests: P2 334, P3 289, hermetic Postgres suites all green with the flags on; legacy-path tests state their flag values
   explicitly. P2 dry-run on the 32-fund sample with the flags on: 32 processed, 0 errors, 0 warnings, nothing written.

Expected change of the VALUES on that sample (flags off vs on, same data): `alpha_persistence` changes for 27 of 30 funds, mean +0.34
(max +0.84): the alpha bonus (x1.15 above 0.6) fires for about 1.6% of funds today and will fire for many more, so **P3 scores and the
portfolio will shift** after the recompute. `capture_ratio` changes for all funds (mean +0.04), coverage 30 -> 31; `beta_spread_hy`
3 -> 27 funds, `beta_vix` 26 -> 27; `macro_r2` +0.03 on average. The CN CPI factor is unusable either way (series ends 2024-03).
**Recommended before the 4-hour run** (about 15 min, owner-run, P2 paused, containers up):
`C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_backtest.py --hysteresis-grid ""` and compare with the baseline run
20261003_234917 (same code path with the flags off): it shows what the corrected persistence and capture do to the portfolio before
production metrics change. The same recompute also carries the FND-0208 macro OLS guards, whose counters (`cond_guard_funds`,
`ols_funds`) settle the condition-index limit decision.

## Run
Canonical full P2 (about 4 h): `scripts\launch\P2_calculateIndicators.bat`. P3 needs `--allow-stale` until the metrics are
on one `CALC_VERSION` again (its freshness gate).

## Verify (read-only SQL, live store)
- Version uniform: `SELECT algorithm_version, count(*) FROM gold.fund_metrics GROUP BY 1`.
- `beta_spread_hy` coverage: `SELECT count(*) FROM gold.fund_metrics WHERE metric='beta_spread_hy' AND horizon='since_inception'`
  (about 306 before; expect several times more).
- No infinite or implausible betas: none above `MACRO_BETA_PLAUSIBLE_MAX` (the FND-0208 guard logs them).
- `alpha_persistence`: distribution moved up; coverage unchanged or higher.
- Statistical audit drift report (`AUDIT_P2.bat`) and the FND-0208 counters in `control.p2_pipeline_log`.

## Roll back
Set the flags back to `False` and bump `CALC_VERSION` again (another full run), or restore the pre-run base backup with the
PITR runbook if the data is damaged.
