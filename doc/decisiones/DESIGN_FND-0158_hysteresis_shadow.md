# FND-0158 - monthly shadow comparison of hysteresis (design, 2026-11-15)

Status: DESIGN ONLY. Nothing is built, no P3 process was run for it. Owner rulings of 2026-11-15: the band stays **0.10** (his confirmed value; the model under test of `PREREG_FND-0193`); the shadow compares hysteresis ON at 0.10 with OFF on every monthly build, with `PORTFOLIO_HYSTERESIS_ENABLED` strictly OFF; it is paired with the forward holdout of FND-0299 (blind months from 2026-11); enabling stays a separate owner ruling once both streams hold evidence.

## 1. What is measured, every monthly build
The live build (flag OFF, no incumbents) is the OFF arm. After it is persisted, the shadow runs the SAME selection a second time with incumbents and writes ONE append-only row; it never changes what the DRAFT holds.

| Stream | Arm ON is built from | Answers |
|---|---|---|
| **One-step** | the incumbents are the funds of the PRODUCTION scenario (what the owner actually holds) | What would THIS rotation have been with hysteresis: the exact selection diff and turnover of the next promotion. |
| **Chain** | the incumbents are the funds of the previous month's ON portfolio (its own history; the first month starts from PRODUCTION) | What the policy does over time: the series whose forward returns can be compared with the live series (the FND-0299 pairing). |

Per row and per stream: master weights of the ON portfolio (ISIN -> weight), one-way turnover against the baseline (live: PRODUCTION; ON chain: its previous portfolio), funds in / out, **selection diff** (ISINs only in OFF, only in ON, and for each the score and the sub-portfolio; the funds hysteresis kept that OFF would have rotated out), cost drag in the PIT convention (25 bp per side on the turnover, so it is comparable with the grid of 2026-11-14) and the per-fund estimate of `estimate_rotation_cost` (units fixed by FND-0409), scenario id, `CALC_VERSION` mix, band, code sha and config hash.

## 2. How it is computed (the builder already allows it)
`PortfolioBuilder.build(regime_result, scenario_id, previous_portfolio=<incumbents>, dry_run=True)` applies the incumbent bonus of `HYSTERESIS_BAND` whatever `PORTFOLIO_HYSTERESIS_ENABLED` says (that flag only decides whether `load_previous()` is called for you). The one-step arm passes `load_previous()` (PRODUCTION), the chain arm rebuilds a `Portfolio` from the previous shadow row. `ROTATION_COST_GATE_ENABLED` stays OFF and is not applied in the shadow. Same scores, same regime, same constraints, so the only difference between the arms is the bonus.

## 3. Where it lives
- **Pure module** `proyecto3/src/hysteresis_shadow.py` (turnover, diff, cost, row assembly; no database, testable without `pipeline` imports).
- **Hook** at the end of `scripts/launch/p3_build_portfolio.py`, after `_persist`, in a `try / except`: a failure is logged and NEVER changes the RC or the DRAFT (the pattern of `lineage.safe_stamp`).
- **Storage** one `ingestion_log` row, step `P3_HYSTERESIS_SHADOW`, message = JSON (append-only, dated by `created_at`, so the stream is blind by construction: a row exists before its outcome is known). The chain reads its previous state from the last row.
- **Switch** `P3_HYSTERESIS_SHADOW_ENABLED` in `shared/config.py` (a kill switch of the registry in `NORMAS_IMPLEMENTACION.md` section 8). It only appends a log row and cannot refuse anything, so it ships ON.
- **Reporting** one line in the P3 build output and in the cycle report: months accumulated, mean turnover OFF / ON, mean cost drag, mean selection diff.

## 4. Invariants a test pins (before it ships)
1. With the shadow ON the persisted DRAFT (weights, notes, scenario row) is byte-identical to the one built with it OFF.
2. `PORTFOLIO_HYSTERESIS_ENABLED` is never read as ON by the shadow; the live arm has no incumbents.
3. A failure of the shadow (no PRODUCTION, empty candidates, bad JSON in the previous row) leaves the build RC 0 and writes one `P3_HYSTERESIS_SHADOW_ERROR` row.
4. The turnover and the diff are symmetric where they must be (two equal portfolios: turnover 0, empty diff); a hand-computed case pins the numbers.
5. The new SQL is a literal read by the `EXPLAIN` sweep.

## 5. Pairing with FND-0299 and what the owner decides
- **Turnover and cost stream** (deterministic, from the live universe): informative from the first rows. Proposed reads: an interim look at 6 rows, the ruling not before 12 rows (2027-11), the criteria below to be fixed BEFORE the rows are read.
- **Return stream** (the chain against the live series, net of the same cost convention): needs the forward months; read at the FND-0299 checkpoints (2028-10, 24 months; 2029-10, 36 months) with the paired bootstrap of `pit_oos`.
- **Criteria to pre-register (proposal, the owner sets the numbers):** the minimum turnover / cost-drag reduction that justifies the change, the tolerance on the forward net return and drawdown (mandate: max drawdown <= 15 %), and the minimum number of months. They are written into an amendment of `PREREG_FND-0193` before the first interim read, never after.
- **Not decided here:** the enabling of `PORTFOLIO_HYSTERESIS_ENABLED`, any change of the band, and `ROTATION_COST_GATE_ENABLED`.

## 6. Effort and sequence
One small module plus the hook, a switch, about ten tests, one full receipt (it touches `proyecto3/` and `shared/`); no P2 / P1 change. It ships inert for the live arm. It is NOT built until the owner orders it (his ruling "design now" covers this record). Note for FND-0306: any commit made between a build and its audit, this one included, exercises the identity gate's MISMATCH path in shadow by itself; no dummy commit is ever made for that.
