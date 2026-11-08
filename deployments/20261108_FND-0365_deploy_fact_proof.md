# Delivery record: the DEPLOY-fact path is verified live (FND-0365)

Written 2026-11-08 for the backlog record. FND-0365 asked to verify, on a real DDL deployment, that the watcher records the DEPLOY fact of the tickets whose DDL it applied, because the first automatic
deployment could not (it had no previously deployed commit to list tickets from). That verification was done on 2026-11-01 and is recorded here as its delivery evidence.

## What was verified, with the live evidence

| Step | Evidence |
|---|---|
| The login and the routine work against the live database | `python scripts/ops/owner_deploy.py facts --from a667b5d` (owner, 2026-11-01 15:55) recorded `ops_event` 377: kind DEPLOY, ticket FND-0355, ref `deploy-83bff0274537`, actor `system:deploy`, through the scoped login `backlog_facts` from the owner vault. |
| The call from inside an automatic deployment | The restarted watcher applied phase `modules` on its first pass (receipt `20261101T185254Z_df6df4a7`: restore point rc 0, apply rc 0, drift check `no drift`) and recorded `ops_event` 384: kind DEPLOY, ticket FND-0360, ref `deploy-df6df4a783ac`, actor `system:deploy`, at 19:52:54. Nobody ran the migration by hand. |
| The autopilot reads it | `bc.ops_event_keys("DEPLOY")` returns both facts, which is what lets it close a ticket whose merge changed `db/**`. |
| Nobody else can forge it | A DEPLOY row from any login other than `backlog_facts` is refused by the trigger `trg_deploy_fact_guard` (hermetic test `test_no_other_login_can_forge_a_deploy_not_even_through_the_event_routine`). |

## Code behind it

`b7da2ae` (the automatic deploy mode, the facts recorder and `record_deploy_fact`), `ae00be9` (the `facts` command), `a565c3a` (the watcher reads the phase list from master's own sources and starts itself again
when its code changes: the first `modules` deployment was invisible to a watcher started before it). Hermetic coverage: `tests/test_backlog_v31_scenarios_pg.py`
(`test_the_watchers_facts_recorder_writes_the_deploy_fact_of_every_ticket_the_ddl_commits_cite`, `test_a_db_merge_waits_for_the_deploy_fact_which_only_the_facts_login_can_record`) and `tests/test_owner_deploy.py`.

## Limits stated

The two facts above cover two deployments. Later deployments depend on the watcher finding at least one ticket cited by the DDL commits; with none cited it records nothing and says so in its receipt line
(`DEPLOY facts: no ticket cites the DDL commits`), which is visible, not silent. The unrelated finding of the same day about the A-2 `revoke` trigger is tracked in FND-0363, not here.
