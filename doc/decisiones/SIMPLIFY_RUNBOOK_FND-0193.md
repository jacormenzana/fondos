# SIMPLIFY runbook — FND-0193 (drafted BEFORE any full-universe result exists)

Written 2026-10-18, before the PIT runs R1-R4 and before any baseline or ablation number was computed on full data, so the results cannot shape it.
It is the provisional execution template for the pre-registered **SIMPLIFY** outcome (`PREREG_FND-0193.md` §5): the model does not robustly beat every
primary baseline, no primary baseline beats it with a CI excluding 0, and the simplest statistically indistinguishable configuration is kept.
It is a template: which rows apply is decided mechanically by `layer_verdicts()` (a layer is kept only if removing it lowers Sharpe with the 90 % CI
excluding 0, or deepens the maximum drawdown by more than 2 pp). Nothing here is executed by the verdict itself; see "Who does what".

## 1. What can be retired, where it lives, how to retire it, how to check, how to undo

| Layer (ablation id) | Artefact | Retire action | Check before the commit | Rollback |
|---|---|---|---|---|
| Layer-3 multipliers (A1; per-family A5): alpha persistence ×1.15, FX ×0.80, empirical regime-return / Sharpe / Sortino / max-DD, crisis stress, slopes | `MULT_ALPHA_BONUS`, `MULT_FX_MALUS`, `MULT_REGIME_*`, `MULT_CRISIS_*`, `MULT_SLOPE_*` in `proyecto3/config/scorer_params.yaml`; `fund_scorer.compute_regime_multiplier` | Set the retired values to `1.0` (the multiplier becomes neutral; the config hash changes on purpose). Delete the code path only after one stable cycle (assessment rule: retire what has been stable, do not leave dead branches) | `tests/golden_scorer_params.json` updated deliberately; `proyecto3/tests` suite; PIT dry run on the 32-fund sample; `scorer_config.SCORER_CONFIG_HASH` changes the PIT cache key (expected) | revert the YAML commit |
| Regime → sub-portfolio mix (A3) | `shared/regime_taxonomy.REGIME_WEIGHTS`, consumed by `regime_classifier` (lines 596, 662) | Replace the seven rows by the constant mix used by B-STATIC (Defensiva 0.464286 / Equilibrada 0.364286 / Dinámica 0.171429). The classifier stays: it still labels regimes for reporting and for Safe Mode (FND-0222) | classifier tests; builder tests; report cover sheet shows the regime | revert the file |
| Score-proportional weights (A2) | `PortfolioConstraints.weight_method` (`portfolio_engine`), set where the builder creates its constraints (`portfolio_builder`) | `"equal"` | cap 20 % / floor 3 % tests; FX-exposure constraint tests (selection depends on the weights through `select_within_exposure`) | revert |
| Hysteresis (kept in every outcome unless E2 says otherwise) | `PORTFOLIO_HYSTERESIS_ENABLED` (False today) and `PORTFOLIO_HYSTERESIS_BAND = 0.10` in `shared/config.py`; previous-portfolio load in `portfolio_builder.load_previous` | Flip with the FND-0158 procedure after one clean build | previous portfolio loaded on a live DRAFT; turnover measured on two consecutive uniform-version portfolios | flag off |

Not retired by this runbook: hard filters and construction constraints (20 % cap, manager count, FX budget, eligibility gates). They encode the mandate.

## 2. Day-to-day transition (nothing abrupt)

1. **PRODUCTION does not change** until the owner promotes a new DRAFT. The existing PRODUCTION scenario keeps being reported by `P3_generateReport.bat`.
2. Commit the configuration change in the worktree (rows above), full hermetic gate green.
3. After the owner publishes it, run the normal cycle or `P1P2P3_runCycle.bat --only-p3`: it builds a **DRAFT** under the simplified configuration.
4. Compare the DRAFT with PRODUCTION: turnover (rotation cost), funds kept/dropped, currency exposure (limit 30 %), alarm watch, data-coverage gate — all gates unchanged. The rotation plan in the report (`4_Rotacion`) lists the trades.
5. The owner promotes with `p3_promote_scenario.py <id>`. The previous PRODUCTION becomes ARCHIVED and stays restorable (a DRAFT can be rebuilt from the previous commit).
6. Monitor two consecutive cycles (uniform `CALC_VERSION`) before deleting dead code.

## 3. Tickets, documents, memory

- Close **REJECTED** with the evidence log: FND-0160, FND-0161, FND-0162, FND-0163 (regime axes, fuzzy weights, soft filters, semáforo band). FND-0258 is decided after the per-multiplier ablation (A5): close MOOT if the empirical regime multiplier is retired. FND-0222 is reduced to its data-freshness triggers. FND-0223 (waiver) only if the surviving configuration's maximum drawdown exceeds 15 %.
- Update `AGENTS.md` (scoring and regime sections), the P3 module docs and the golden-params note; add a memory note with the verdict and the evidence folder.
- FND-0193 stays IN_PROGRESS until the forward-holdout checkpoints (2028-10 and 2029-10) are recorded.

## 4. Who does what

| Step | Who |
|---|---|
| Apply the verdict rules, close FND-0160..0163 with logs | automatic, pre-authorised by the owner directive of 2026-10-18 |
| Prepare the configuration commit and build a DRAFT in the worktree, tests green | the assistant |
| Publish the commit (`publish_master.py --apply`) | the owner (explicit OK) |
| Promote the DRAFT | the owner (`p3_promote_scenario.py`) |
