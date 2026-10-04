# P2 recompute bundle — runbook (FND-0196, FND-0226, FND-0200, FND-0202)

Four corrections that change stored P2 metric values. The code is merged **dormant**: every flag in `shared/config.py`
defaults to `False` and nothing changes until the owner flips them together and runs ONE full P2 recompute.

| Flag (`shared/config.py`) | Ticket | What changes | Measured / expected effect |
|---|---|---|---|
| `MACRO_VIF_ITERATIVE_ENABLED` | FND-0196 | Macro OLS drops ONE factor at a time (iterative VIF) with `MACRO_ITERATIVE_VIF` (protect `spread_hy`, `vix_yoy`; drop `spread_ig`; at most n_obs/10 factors), instead of dropping every factor over the threshold in one pass | `beta_spread_hy` coverage about 10% of funds today (306 of 3,100) -> about 54% of fund-dates in the sample; 17 vs 12 factors kept on the 2010+ window |
| `MACRO_FACTOR_CLEAN_ENABLED` | FND-0226 | A non-positive CPI index level is missing; +/-inf macro cells become NaN | `ipc_yoy_cn` is +/-inf in 13 months today (14 zero values in `series_macro` CN, last observation 2024-03) |
| `PERSISTENCE_FIRST_LAST_NAV_ENABLED` | FND-0200 | Peer return in a window from the FIRST and LAST NAV (was MAX/MIN) | Peer returns overstated by 5.2 pp a year on average (0 to 24 pp; equities 2008 window +24 pp), so `alpha_persistence` rises for most funds |
| `CAPTURE_MONTH_END_ENABLED` | FND-0202 | Capture ratios on a month-end grid, 1-month returns only | Fund observations dated mid-month stop dropping out of the join; coverage and values change |

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
5. `CAPTURE_MONTH_END_ENABLED` makes the PIT capture ratios refuse to run (`NotImplementedError`) until the PIT replica of the
   fix exists. Decide whether PIT backtests are needed in the next days.

## Flip (one commit)
1. `shared/config.py`: set the four flags to `True`.
2. `proyecto2/src/pipeline/run_pipeline.py`: bump `CALC_VERSION` (new date string) and add a one-line comment naming the four tickets.
3. `python scripts/audit/sync_agents_md.py --write` (kill-switch line), commit.

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
