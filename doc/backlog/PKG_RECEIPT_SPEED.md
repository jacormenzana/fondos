# Package: receipt speed (FND-0386, FND-0385, FND-0387)

One page, rewritten in place. Owner order 2026-11-10: reducing the ~29-minute receipt is a structural objective; ONE full receipt per package; quality is never traded for speed.

**Exit metric.** A full receipt of a typical change runs in under 10 minutes (from ~28), a change the selector can bound runs in under 3, with the same coverage: no defect the old receipt caught is missed (replay of the 6 failing receipts of 2026-11-02..10).

**Measured (8 days of receipts, 2026-11-02..10).** 37 full runs = 17.3 h wall (median 28 min); 26 of 31 passing runs covered ONE commit. The 6 failing runs were all real defects found by tests that take under 6 s together. Of 1,584 s of test time, 5 of 415 files take 50% and 3 launcher
end-to-end files take 43% (356 + 189 + 136 s); 327 files take 39 s in total. My receipts always fell back to `full` because nearly every change touches `shared/config.py`, a hub read by 386 of 439 test files (FND-0387).

**Delivered here (FND-0386, step 1).** `run_pg_tests.py --shards auto`: the full suite as concurrent pytest shards (one process per suite per shard: proyecto1 / proyecto3 / tests / proyecto2 cannot share a process), balanced by the recorded per-file seconds, a failing file re-run alone to classify it
(real or parallel-only; BOTH fail the run). `delivery_attest.py` uses it for the `full` scope behind `DELIVERY_SHARDED_RUN_ENABLED` (rollback = the sequential command). Measured on this tree: shards in 483 s and 509 s, 6,895 passed / 0 failed / 54 skipped = the 6,885 of the last receipt + 10 new tests.
Side finding fixed: `test_delivery_attest_impact.py` passed in every receipt and failed in a working tree holding `out/audit/test_durations.json` (hermetic now).

**Left, in order.** (2) Measure the first receipt through the sharded path; a node-level split of the 356 s launcher file would cut the floor further (not worth it unless that receipt is over 8 minutes). (3) FND-0387: symbol-level impact for `shared/config.py` so a new constant stops forcing `full`.
(4) FND-0385: promote the selector from SHADOW to ENFORCE on its own recorded evidence (earliest 2026-11-14 under its rule; the owner may waive the date). (5) Run the three launcher end-to-end files only when a launcher or its library changes. (6) Re-measure: this package is closed when the exit metric is measured on real receipts.
