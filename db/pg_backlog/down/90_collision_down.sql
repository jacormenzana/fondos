-- Rollback of Phase C slice 3 (FND-0334): scripts/ops/migrate_backlog_v2.py --phase collision --down (dry run by default; ONE transaction).
-- Drops the views and the routines. KEEPS backlog.gate_state, backlog.gate_collision and backlog.gate_adjudication (append-only history), every link event the gate recorded and every hold
-- it raised (a hold is released by a person): applying the phase again resumes with every recorded fact. No ticket, log or link is touched. The role backlog_adjudicator is the owner's.

-- BEGIN backlog_collision_down
DROP VIEW IF EXISTS backlog.v_gate_queue;
DROP VIEW IF EXISTS backlog.v_gate_pair;
DROP FUNCTION IF EXISTS backlog.sp_adjudicate_overlap(text, text, text, text, text, text);
DROP FUNCTION IF EXISTS backlog.fn_gate_undecided(text[]);
DROP FUNCTION IF EXISTS backlog.sp_gate_collision(text[], text[], text, text, text);
DROP FUNCTION IF EXISTS backlog.fn_collision_pairs(text[], text[], text);
DROP FUNCTION IF EXISTS backlog.sp_set_collision_mode(text, text, text, text, text, jsonb);
DROP FUNCTION IF EXISTS backlog.fn_audit_start();
DROP FUNCTION IF EXISTS backlog.fn_gate_share(integer);
DROP FUNCTION IF EXISTS backlog.fn_collision_state(text);
DROP FUNCTION IF EXISTS backlog.fn_mode_rank(text);
-- END backlog_collision_down
