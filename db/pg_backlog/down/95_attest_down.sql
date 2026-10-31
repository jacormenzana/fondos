-- Rollback of Phase C slice 4 (FND-0334): scripts/ops/migrate_backlog_v2.py --phase attest --down (dry run by default; ONE transaction).
-- Drops the view and the routine and rebuilds v_backlog_violations without the three V-GATE-* codes. KEEPS backlog.gate_protection_event (append-only history: the readings and the owner's
-- verification and emergency events): applying the phase again resumes with every recorded fact. No ticket, log, link or hold is touched.

-- BEGIN backlog_attest_down
SELECT backlog.fn_rebuild_violations('v_violations_c3');     -- the union without this phase's view first: it depends on it
DROP VIEW IF EXISTS backlog.v_violations_c3;
DROP FUNCTION IF EXISTS backlog.sp_record_gate_protection(text, text, text);
-- END backlog_attest_down
