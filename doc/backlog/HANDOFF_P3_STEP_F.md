# Handoff: Package L after session 2, waiting for the owner's review of the P3-5a draft (written 2026-11-21)

Start with `python scripts/gov/repo/preflight_check.py --mine <your files>`; the untracked `SKILL.md` belongs to another session, never touch it. Plan of record: `doc/backlog/PKG_L_p3_alignment.md`; the draft: `doc/decisiones/PREREG_P3_5A_DRAFT.md`. Standing rules: English, ultra-short, lead with the decision needed; live runs only on the owner's order; policy scanners after the LAST edit (`shared.delivery_receipt.POLICY_TESTS`); `Refs:` in the last paragraph with Co-Authored-By; ONE full receipt at package delivery (`python scripts/gov/delivery/delivery_attest.py --scope full`); commit with explicit paths (`git commit -F <file> -- <paths>`); write patch scripts with the Write tool; FND-0435 stays OPEN until that receipt.

## Done
- Session 1 (2f9cd37, b2c8be7, b890572): survivorship tools, lever lock, fee-aware class choice (OFF), P3-2' as Amendment 5.
- Session 2 (this commit and the one before): the draft with its ex-ante rules and the L3 freeze committed BEFORE the numbers (3b7b437); stages C1a, C1b, C2, C3, C4 run on the sealed run, results in the draft sections 6-8 and the log 9.2; the pool-drawdown count refused to run without the freeze (a test and a git HEAD check). Findings: the sealed history cannot detect 0.5 pp (minimum detectable effect 0.62 pp unadjusted), so condition 2 is non-inferiority and the forward shadow decides; 3 informative confirmation episodes (the threshold); the since-inception drawdown filter locks Equilibrada's Renta Variable out for a decade (7-34 % pass against 64-91 % on a rolling 60 months); Geography explains 52.8 % of the Equilibrada top gap; the baseline holds a fund 6.9 months, so L4's H values are 36 and 24 months.
- The R9 lock is CLOSED: `PREREG_P3_5A.md` does not exist; the draft's name does not open it.

## Waiting on the owner (draft section 11)
The three that matter: (1) accept that the historical test is non-inferiority (recommended); (2) the Holm family for the forward test: the 11 trials, with the full N of 676 in the deflated Sharpe (recommended); (3) L2 and geography: keep L2 and open P3-6 (recommended). Then four smaller points (L1 scope and backstop, L4 values, the 3-episode threshold, the trial arithmetic) and one fact to verify (the rolling 60-month `max_dd` in P2).

## Next, only on his order (step F, then C2-decision)
1. He edits or accepts the draft; commit `doc/decisiones/PREREG_P3_5A.md` (a copy of the draft with its status line replaced and the owner's answers written in; this opens the lock).
2. Build the four levers OFF behind switches (R2), each with a failing test first (R5), one function for live and PIT (R3); `check_hard_filters` keeps reading the RAW `return_ann_real` (L1 and L4 orthogonal). Check first whether P2 stores `max_dd` for the `rolling_5y` horizon (`shared/config.py` `ROLLING_WINDOWS`); if not, that is an owner decision (a P2 change and a `CALC_VERSION` bump). The PIT frames carry no fee columns: load them (a diagnostic loader, never `pit_inputs.load_attributes`, whose frame feeds the cache key).
3. Run the 11 trials from scratch, the baseline in the same build, design dates first; use `assert_lever_dates_allowed` for every lever output; the tail stress (`pit_selection_test.tail_stress_max_dd`) on the baseline and every variant alike; the ceiling protocol of the draft section 3.
4. Then the option "retire ranking complexity", ONE re-run of P3-2' (Amendment 5 re-sync clause), the ONE full receipt, rewrite the PKG_L page, update memory.

## Traps
- The NAV panel holds stray mid-month rows (2020-05-13, 2025-12 onward): never count rows as months; `pit_p35a_prep.rolling_max_dd` and `trough_age_months` use month-end rows only. `X.forward_path` and `_ret12` use positions and are safe only on dates (2007-2009) where no stray row exists in the window.
- `tail_stress_max_dd` only deepens the drawdown as the share grows where the stress path is worse month by month.
- Not run yet: the hermetic PG tests that touch the changed scorer loader (`proyecto3/tests/test_p3_reads_pg.py`, `test_wrong_doc_eligibility_pg.py`, `test_fund_data_eligibility_pg.py`); they belong to the one full receipt (hold a WSL session open first).
- After a `shared/config.py` switch change or a new module in `proyecto3/src`, run `python scripts/gov/repo/sync_agents_md.py --check` and `--write` (the pre-commit hook refuses otherwise); `.gitignore:28` ignores `*_2026[0-9]*`; pytest cannot mix `tests/` and `proyecto3/tests/` in one command.
