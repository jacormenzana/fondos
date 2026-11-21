# Handoff: Package L after the live block and the turning-point diagnostic (written 2026-11-21)

Start with `python scripts/gov/repo/preflight_check.py --mine <your files>`; the untracked `SKILL.md` belongs to another session, never touch it. Plan of record: `doc/backlog/PKG_L_p3_alignment.md`. Standing rules: English, ultra-short, lead with the decision needed; live runs only on the owner's order; never record a derived value as his decision; policy scanners after the LAST edit (`shared.delivery_receipt.POLICY_TESTS`); `Refs:` in the last paragraph with Co-Authored-By; ONE full receipt at package delivery (`python scripts/gov/delivery/delivery_attest.py --scope full`), none per commit; heredocs with quotes break in Bash here: write patch scripts with the Write tool.

## Done (HEAD after 16200ec + this docs commit)
- Live `selection_health` block (98d1f7e): report-only, fail-soft, `P3_SELECTION_HEALTH_ENABLED` ON, limits from the sealed run's calibration. The owner-ordered `--dry-run` build ran: RC 0, nothing persisted, one alert (Dinamica raw-top same-manager count 4 against the limit 3).
- Turning-point diagnostic (9ec7db9, 16200ec): D0-D5, frozen counterfactuals, integrity. Findings: `doc/audit/DIAGNOSTICO_TURNING_POINT_P3.md`. Re-run: `python -X utf8 scripts/audit/p3/p3_crisis_diagnostic.py --run-dir out/reports/pit_backtest/20261119_202509 --d1d2` (about 3 minutes; reads only the sealed run plus the IPC series from the database; `--dates` for a subset). The D0 error of the first slice (eligibility inferred from reason text) is corrected and logged (FND-0435 log 2673).
- Tickets: FND-0435 (diagnostic; can be closed on the owner's word or by a `Closes:` commit with a probe), FND-0436 (NEW: the PIT evidence has no dead funds; investigation, not worked), FND-0430 cluster, FND-0434 unchanged.
- P3-2' amendment: DRAFT `doc/decisiones/PREREG_FND-0193_AMENDMENT5_P3-2prime_DRAFT.md`; it becomes an amendment of `PREREG_FND-0193` section 8 only after the owner confirms its PROPOSED numbers.

## Waiting on the owner
1. Row 12, the adoption objective (gates `PREREG_P3_5A.md`; never commit that file before he answers, the diagnostic's date lock opens when it is in git HEAD).
2. The numbers of the P3-2' draft (30 %, 0.50 pp, 0.10 pp, 1.0 pp, 0.15 pp, 12 builds).
3. Whether FND-0436 (survivorship) changes the priority of P3-5a.

## Next (roadmap v4 order)
P3-2' commit as an amendment (after 2) -> P3-5a prereg (at most 4 levers chosen by mechanism, 2 values each; the diagnostic supports: the nature bonus, a rolling or decayed since-inception drawdown filter, a performance-chasing control in Equilibrada; continuous shock trigger; adoption rule; contamination log with the 2008 design reads) -> its runs -> C2 with the option "retire ranking complexity" -> ONE full receipt, rewrite `PKG_L_p3_alignment.md` in place, update memory `project_p3_alignment_roadmap_20261118.md`.

## Traps
`.gitignore:28` ignores `*_2026[0-9]*` (never date-stamp a tracked file name); the silent-fill scanner flags `fillna`; `tabulate` is not installed; pytest cannot mix `tests/` and `proyecto3/tests/` in one command; a `cd` inside a Bash call can move the session's working directory; the seal covers scores and inputs but NOT the IPC series (read from the database, validated by `score_base` reproduction).
