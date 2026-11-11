# Package: receipt speed (FND-0386, FND-0385, FND-0387)

One page, rewritten in place. Owner order 2026-11-10: reducing the ~29-minute receipt is a structural objective; ONE full receipt per package; quality is never traded for speed.

**Exit metric.** A full receipt of a typical change runs in under 10 minutes (from ~28), a change the selector can bound runs in under 3, with the same coverage: no defect the old receipt caught is missed (replay of the 6 failing receipts of 2026-11-02..10).

**Measured (8 days of receipts, 2026-11-02..10).** 37 full runs = 17.3 h wall (median 28 min); 26 of 31 passing runs covered ONE commit. The 6 failing runs were all real defects found by tests that take under 6 s together. Of 1,584 s of test time, 5 of 415 files take 50% and 3 launcher
end-to-end files take 43% (356 + 189 + 136 s); 327 files take 39 s in total. Receipts always fell back to `full` because nearly every change touches `shared/config.py`, a hub read by 386 of 439 test files; all 14 shadow receipts so far decided `full`, so the selector has no `impact` streak yet.

**Delivered, FND-0386.** `run_pg_tests.py --shards auto`: the full suite as concurrent pytest shards (one process per suite per shard), balanced by recorded per-file seconds, a failing file re-run alone to classify it (real or parallel-only; BOTH fail the run), behind `DELIVERY_SHARDED_RUN_ENABLED`.
Measured: shards in 483 s and 509 s, 6,895 passed / 0 failed, against ~28 min before.

**Built, FND-0387 (2026-11-11, uncommitted until its receipt).** `shared/test_impact.py::symbol_impact`, ALGO_VERSION 3: a changed file in `SYMBOL_HUBS` (`shared/config.py`) is bounded by the top-level symbols the change touches instead of F3. Changed names (added, edited, removed, plus
what uses them inside the hub) are followed through the repository BY NAME, qualified by the defining file and its importers, to a fixed point. Affected for any change: a binding that reads the hub without naming a symbol (the file as text, `dir`/`vars`/`__dict__`, a computed `getattr`, the module passed on,
a star import), module-level code that uses a changed name (the whole file), and a changed or unreadable anonymous statement (the hub stays F3). A `__main__` block is reached only by a run of the script. A conftest or test infrastructure that is affected makes the hub F3 again. `delivery_attest` hands it the base text.
Result on the real tree: a new constant selects ~90-105 of 440 files (~10 min of serial test time, mostly the three launcher e2e files that run the scripts reading config by enumeration); five switches + one number + `SCHEMA_VERSION` select 172; a 26-switch edit stays `full`.
**Evidence (mutation runs, full suite on a scratch git clone):** 5 switches + 1 number + `SCHEMA_VERSION` changed: 14 failing test files, all inside the 172 selected (0 escapes); 3 constants added: 0 failures. 50 selector tests green; the 18 policy scanners and the sentinel check green.

**Left, in order.** (1) Commit, then ONE full receipt (background). (2) FND-0385 ENFORCE: owner present. The mode is derived from receipts (`effective_mode`) and needs, for the new algo/selector digest, an evidence line (re-run the harness: replay, trace, mutate, finalize) AND a streak of 10 `impact` shadow receipts over 7 days;
the owner waived the DATE (2026-11-14) but the streak is zero and one receipt per package cannot reach 10: the owner decides whether to lower `min_receipts` / `min_days` in `current_impact_mode`, or to enforce on the evidence alone. (3) Optional: run the three launcher e2e files only when a launcher or its library changes (would cut ~680 s from every config selection).
(4) Re-measure on real receipts; close FND-0386 / 0385 / 0387 when the exit metric holds.
