# Delivery record: FND-0380 (the area receipt runs the whole-tree policy tests)

Written 2026-11-07 for the backlog record. The code was delivered in `e5bed07` and `a12d781`; this record is the delivery evidence that closes the ticket.

## The defect

The receipt scope `area` ran only the tests named after each changed module (`select_tests`). Tests that scan the WHOLE tree for a convention have no module of their own, so they never ran in it: `scripts/audit/auto_publish.py` (it reports incidents) passed its area run with its own 29 tests and failed the full suite on `test_every_incident_reporter_has_a_fixture_case`; a change confined to `scripts/` or `tests/` would have been published and its tickets closed with that defect. The same gap cost a failed full receipt on 2026-11-06 (`test_log_paths` against a new script).

## What is live

- **A fixed policy set in every area receipt.** `shared/delivery_receipt.py::POLICY_TESTS` (18 entries): the incident-reporter fixture rule, the single door of the backlog, the live-write gate, the registry of automatic writers, the staging guard, the operation allow-list, the gate-switch door, dialect coverage, log paths, maintenance log routing, report dirs, the silent-fill and period-key scans, the owner vault, the launcher standards, the entry-point smoke test and the SQL EXPLAIN sweep. `delivery_attest.py` adds it to every `area` run (plain pytest for the plain entries, the Postgres container for the two Postgres ones) and the receipt records the files in `files`. A `file::test` entry runs one test of a file that also holds module tests.
- **Nobody has to remember to extend it.** `tests/test_delivery_receipt.py` scans `tests/` statically and fails when a test file that walks a repository root is neither in `POLICY_TESTS` nor in `NOT_POLICY` with a reason (the allowlist protocol of `tests/_allowlist.py`); a stale entry in either list fails too. The scan found two scanners the first draft had missed.
- **Documented** in `doc/reglas/NORMAS_BACKLOG.md` next to the rules of the local receipt.

## Acceptance, measured on a throwaway clone (scratchpad `accept380`, `e5bed07`)

| Change | Area receipt |
|---|---|
| A new script that calls `report_incident` with no calibration case, plus its own passing test | **FAIL**: 207 passed, 1 failed, 57.3 s; names `tests/test_backlog_overlap.py::test_every_incident_reporter_has_a_fixture_case` |
| A harmless comment in a test | **PASS**: 207 passed, 53.5 s; the receipt lists the 16 policy entries it ran (18 since `a12d781`) |

Before this change the first row would have passed.

## Stated limits

- The set costs about a minute per area receipt (against about 26 minutes for a full one). A change under `shared/` or `proyecto*/` still needs the full suite, which already included everything.
- Policy tests that live under `proyecto*/tests/` are not scanned by the detector (it reads `tests/` only). None is known today.
- The four scanners classified as not policy (two launcher end-to-end tests, the launcher state guard, the GitHub workflow test) read produced logs or workflow files, not the sources; if GitHub comes back the workflow test must be re-classified.
