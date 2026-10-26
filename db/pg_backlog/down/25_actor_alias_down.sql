-- ROLLBACK of db/pg_backlog/25_actor_alias.sql (FND-0330). Run by scripts/ops/migrate_backlog_v2.py --phase alias --down --apply
-- (dry run without --apply). Nothing else references these objects: logs and link events keep their raw `username` untouched, so
-- removing the mapping loses no history. Exactly the block between the markers is executed.

-- BEGIN backlog_actor_alias_down
DROP VIEW IF EXISTS backlog.v_actor_unmapped;
DROP VIEW IF EXISTS backlog.v_backlog_log_actor;
DROP TABLE IF EXISTS backlog.actor_alias;
DROP FUNCTION IF EXISTS backlog.trg_actor_alias_immutable();
-- END backlog_actor_alias_down
