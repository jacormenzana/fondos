# Package FND-0422 - Fund_Nature classified without its volatility and Morningstar evidence

Owner order 2026-11-15 ("go on", then "fix approved"). One page, rewritten in place. No P1 / P2 / P3 process is run by this package without the owner's order for that run.

## 1. Result (read first)
- **Root cause (measured, not one of the three candidates):** commit `6dbe08a` (Package H slice 1) added a local `from shared.db import fail_soft_block` inside `run_block` (`proyecto1/core/pipeline.py`). A local import makes the name local to the WHOLE function, so the two batch loads at the top of `run_block` (`fund_benchmarks` for the Morningstar class, `fund_metrics.srri_nav` for the realized-volatility band) raised `UnboundLocalError`; their `except` swallowed it as a WARN. From the first universe pass after the commit (2026-11-14 20:42) every pass classified with no Morningstar class and no volatility band.
- **Evidence:** `OPT_B3_DISPATCH` rows of the four universe passes of 11-14 .. 11-15 (2,844 - 2,880 each) carry `vol=None` and `msbench=None` in 100% of the rows; the 11-13 passes: 1.3% and 12%. The pass log line is `[WARN] SC-H: no se pudo cargar fund_benchmarks: cannot access local variable 'fail_soft_block'...` (`log_pipeline_20261115_000258.log:17`). ARB-1 did not act (`arb1_from` None) and the family builder is not involved (FAM_001170 has two members, both Renta Fija Flexible).
- **The four funds** flipped between the 11-13 and the 11-14 passes: LU2441282899 / LU2441283277 (Janus biotech) Renta Variable -> Renta Fija Flexible; LU0279509144 (Ethna) and LU0376989207 (abrdn) Renta Fija Flexible -> Renta Variable. The resolver on today's full evidence gives Renta Variable, Renta Variable, Renta Fija Flexible, Renta Fija Flexible.
- **Blast radius (read-only census, 2,943 in-universe funds, stored against the resolver with full evidence):** 123 differ; 103 are reproduced by the no-evidence replay (the bug); 20 have another cause. Largest moves: Mixtos -> Renta Variable 60, Renta Variable -> Mixtos 29, Monetario -> Renta Fija Flexible 13. Attributes derived from the nature were not measured.
- **Fix delivered in code:** the redundant local import is removed (the module-level import at the top of the file is the one used). Regression test `proyecto1/tests/test_pipeline_no_shadowing_local_import.py`: a static AST scan that no function of `pipeline.py` imports locally a name it already uses earlier (it reports the 11-14 pattern on the previous `HEAD`).
- **Not done (owner's order needed):** one live P1 pass to restore the stored natures, by the Package H procedure (section 3).

## 2. Why the controls did not see it
The loads fail soft by design (a failed SELECT must not poison the transaction), the WARN goes to the pass log only, and the dispatch rows show `vol=None` as a legitimate value (a fund without NAV history). The acceptance of the Package H live pass compared the FEE state to its replay, not the nature. A cheap standing guard would be a cycle-report line: the share of dispatch rows of the last pass with `vol=None` / `msbench=None` against the share of funds that lack the evidence. Not built (ask first).

## 3. Live pass procedure (when ordered)
Backup dump to `out/`; 40-ISIN sample (`out/pkg_h/sample.txt`, plus the four funds); compare with the read-only replay (`census_changed.tsv` in the session scratchpad lists the 123); universe in the background (about 13 min); accuracy reader. Expected: the 103 moves reverse to the full-evidence answer. Hard stops: a sample error, a hard failure. The 20 funds with another cause are reported, not changed.

## 4. Decisions pending
(1) The order for the live P1 pass. (2) Whether to build the evidence-presence guard of section 2. (3) The 20 funds whose difference is not the bug: classify them in a follow-up read, not in this package.
