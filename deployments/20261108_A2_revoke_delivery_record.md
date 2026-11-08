# Delivery record: step A-2 (revoke of the direct UPDATE privilege) and the close of the FND-0355 epic

Written 2026-11-08 for the backlog record. Owner decisions of the day: fix the A-2 trigger and let it apply (relying on the restore point, the drift check and the automatic rollback); verify-and-close the pending tickets; close FND-0355. The tickets closed by this commit: FND-0331, FND-0363, FND-0355.

## The defect

`scripts/ops/owner_deploy.py::revoke_due` returned False whenever the remembered deployed commit differed from the head of master. That commit only advances when DDL is applied, while master advances on every commit, so after the first commit without DDL the A-2 trigger could never fire, although `BACKLOG_AUTO_REVOKE_ENABLED` was on and the gate was met (62 of 50 real transitions). Evidence on 2026-11-08: deployed `cb9a634`, head `ee734f6`, no DDL file changed between them, no pending phase. The existing test only used equal refs, which is why it passed.

## The fix and its delivery

`f1b30e6`: `revoke_due(..., other_pending=...)` waits only while another phase of master has DDL that changed since the remembered commit; an unknown pending set, or no remembered commit, judges nothing. `revoke_pass` works the set out with `changed` and `registry`. Tests: `tests/test_owner_deploy.py` 75 passed, among them the defect case (deployed behind master by commits without DDL: due) and the guard (other DDL pending: waits). Receipt of `f1b30e6`: PASS, scope `area`, 299 passed, digest `9f40f0f9c69c`.

## What happened live

- The watcher (`owner_deploy.py watch`, restarted by itself when the file changed on disk) applied `revoke` on its first pass: deploy receipt `20261108T083300Z_29dd270e.json`, `ok: true`, `applied: [revoke]`, four steps (restore point, apply, drift, receipt). It ran on the code of the working tree before the commit; the tests and the receipt came right after and passed.
- `backlog_drift.py`: "no drift".
- `bc.raw_update_privilege_live()`: False (it was True before).
- `backlog_phase_a_acceptance.py --verify a2 --baseline-file out/audit/fnd0331_baseline_20261101T070544Z.json --migration-rc 0`: **ACCEPTED, all criteria met**: the writer cannot update status, release_version or assignee, nor insert tickets, nor write the audit tables; it keeps exactly the exceptions E1 (log insert), E2 (link event insert), E3 (descriptive update) and the routines; the author-check triggers exist; the history up to the baseline ids is identical (1,388 logs and 325 events, same md5).
- The routine door works after the revoke: a note was appended to FND-0363 through `sp_append_log` after 09:33.
- No incident was opened and `revoke_failed` is false in the state file.

## Tickets

- **FND-0331** (Phase A: transition kernel, single door, shadow stage, UPDATE-grant revoke): the last open piece was the revoke; it is live and accepted.
- **FND-0363** (apply A-2 once its gate holds, by the automatic deploy path): done by the path it names.
- **FND-0355** (Backlog v3 epic): the finish line is met (O2, O3, efficiency re-measured; `deployments/20261107_FND-0355_checkpoint_pack.md`), the live dispositions are applied (`deployments/20261108_backlog_dispositions_delivery_record.md`) and the GitHub return is deferred by the owner. Children left open on purpose and listed: FND-0362 (the W6 views do not exist), FND-0372 (no measurement), FND-0384 (owner's close condition: the 8 standing-accepted funds cleared), FND-0370 (hold, owner), FND-0390 (GitHub return, deferred). FND-0334 and FND-0335 wait on FND-0331 as their prerequisite and close after it.

## Stated limits

- The retirement of the shared login `backlog_writer` (`backlog_phase_a_acceptance.py --verify retire`) is a separate step and is not done: it needs three distinct bound human operators; the preflight shows 0 bound logins.
- The revoke was applied before the commit that fixes the trigger was tested, because the watcher reloads its code from disk. The change is small and its tests passed right after; the acceptance above is the check that matters.
