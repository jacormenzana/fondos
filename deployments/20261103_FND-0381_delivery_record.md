# Delivery record: FND-0381 (O3, module groups that work and are managed by the system)

Written 2026-11-03 for the backlog record. The code was delivered in `a268cb3`, `8ea013d`, `8b6f2b0`, `9bd7669` and `fb41668`; this record is the delivery evidence that closes the ticket.

## What is live

- **Unit of a group.** One group per module (`BACKLOG_CLUSTER_UNIT = "MODULE"`, rollback `"COMPONENT"`), named after the module. The old unit, the connected component of the shared-module graph, chained unrelated work (the largest group held 70 tickets).
- **Proof.** Measured on the decisions on record: components 64 % precision on 22 decided pairs; one group per module **78 % on 37 pairs** (recall 39 % against 19 %), above the standing bar (70 % on at least 20). The proof is cached per day **and per unit**.
- **The system keeps them.** The autopilot, in `AUTO` mode, wrote the groups on its own after the proof held: hook log line of 2026-11-03 22:58, `module clusters 74 event(s)`: **19 MODULE groups** (`backlog.group_tag`, actor `system:autopilot`) and **55 memberships** (`ADD`), none refused. The plan measured before writing was 74 events, busiest ticket 4, no repeated write.
- **Caps on the writer (owner measures).** Before any write the plan is measured (volume, writes per ticket, busiest-ticket share, repeats) against `GROUP_CAPS` (150 / 6 / 25 % / 0); a plan beyond them is refused whole.
- **No silent failures.** The autopilot summary line now names refused or failed group writes and the first error. That is how the two faults found on the way were seen and fixed: the proof cache keyed by day only (`8ea013d`), and the group helpers taking the console path instead of the autopilot's scoped login (`LiveWriteForbidden`, 19 groups refused on the first live run; `fb41668`).

## Stated limits

- Recall is 39 %: a group is a precise signal of "same module", not a complete one. Recall is reported, never a bar (owner decision of 2026-11-01).
- **Relations (links).** An automatic writer for links was not built: on the 132 decided OVERLAPS pairs, "the text cites the other ticket and they share a module" is right 88 % of the time (n = 33), but only 4 of the 68 pending RELATED proposals have a shared-module edge and RELATED has no gate, note or review queue behind it. Creation stays automatic and capped; decisions stay with agents on written evidence. Reopen if a consumer of RELATED status appears.
- The tests of the full suite run through the local receipt; the receipts of `be49e1b` and `a268cb3` failed two sweep tests of the proof harness (fixed in `7c0d4fa`, receipt PASS).

## Why a record and not a code change

The delivering commits cite the ticket with `Refs:` so that the ticket would not close before the groups were seen working. They are seen working; this record carries the closing trailer.
