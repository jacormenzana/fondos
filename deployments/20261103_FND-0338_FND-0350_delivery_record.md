# Delivery record: FND-0338 and FND-0350 (backlog identity history and governed gate override)

Written 2026-11-03 for the backlog record. Both pieces were built and applied to the live backlog in October; neither ticket was ever moved to its end state. This record is the delivery evidence that lets the autopilot close them (a docs-only commit with `Closes:`); nothing in the code changes.

## FND-0338: descriptive-change history (exception E3)

- **What is live.** `backlog.field_change_event` (append-only) records, through a trigger, every change of the eight descriptive columns of exception E3 with the previous and new value and the declared actor; `backlog.v_field_change_attribution` measures the share of changes that carry an actor. Applied on the live backlog by the owner on 2026-10-28 (step `history`, exit code 0).
- **Evidence read on 2026-11-02 (read-only).** `field_change_event` holds 1 change, 1 of them attributed: 100 %. The ticket's own criterion (real changes recorded through `update_attributes()` and the share measured and noted) is met and was logged on the ticket on 2026-10-28.
- **Stated limit.** The sample is one edit. It shows the path works, not what the attribution share will be under load; `LIMITACIONES_CONOCIDAS_BACKLOG_V2.md` L5 stays the place to re-measure.

## FND-0350: governed executive override of the delivery gate

- **What is live.** `backlog.sp_set_gate` takes `p_executive_override` (human actor on the owner-only login, ENFORCE only, a reason of 80+ characters), `backlog.gate_switch` has the `override` column, and `backlog.v_gate_override_event` lists the overrides (code `V-GATE-OVERRIDE`, counted, never blocking). Read on 2026-11-02: the argument is in the routine's signature, the view exists, the column exists.
- **Use so far.** None: both gate events (AUDIT on 2026-10-28, ENFORCE on 2026-10-28 by `system:owner-waiver`) carry `override = false`, as documented in limitation L8.
- **Implementation.** Commit 2e8bd4d of 2026-10-29, approved by the owner on 2026-10-28 (specification in the ticket).

## Why a record and not a code change

Both tickets were still open for lack of a delivery event, not for missing work (`out/audit/plan_friccion_20261102/VERIFY_2_0.md`). Closing them removes two false "in progress" entries from the backlog.
