# tests/test_backlog_gate_rbac.py
# -*- coding: utf-8 -*-
"""Phase C slice 3 (FND-0334): which login each gate tool uses, pinned by reading the code. The hooks use the scoped login backlog_gate EXCLUSIVELY (no other client function, no general
DSN); the adjudication tool writes only through the adjudicator login; the calibrator and the mode tool write only through the routine sp_set_collision_mode (the calibrator as
system:calibrator, a person through the owner-only login) and never through the adjudicator login. No test here needs a database."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "audit"


def used(name: str) -> set:
    """The attributes of the backlog client a script uses (`bc.x` and `self.bc.x`)."""
    return set(re.findall(r"\bbc\.(\w+)", (AUDIT / name).read_text(encoding="utf-8")))


HOOK_CALLS = {"gate_statuses", "gate_forward", "gate_check", "gate_collision", "collision_state", "gate_undecided", "make_session_id", "BacklogUnavailable"}


def test_the_hooks_use_only_the_scoped_gate_functions():
    for script in ("commit_msg_gate.py", "pre_push_gate.py"):
        assert used(script) <= HOOK_CALLS, (script, used(script) - HOOK_CALLS)
    text = (AUDIT / "commit_msg_gate.py").read_text(encoding="utf-8") + (AUDIT / "pre_push_gate.py").read_text(encoding="utf-8")
    for forbidden in ("FONDOS_BACKLOG_PG_DSN\"", "FONDOS_BACKLOG_PG_DSN'", "list_tickets", "transition(", "_dsn(", "psycopg.connect"):
        assert forbidden not in text, forbidden


def test_the_scoped_client_functions_go_through_the_gate_operation_with_no_fallback():
    client = (ROOT / "shared" / "backlog_client.py").read_text(encoding="utf-8")
    for fn in ("gate_check", "gate_statuses", "gate_forward", "gate_collision"):
        body = client.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        assert 'operation=None if conn is not None else "gate"' in body, fn
    for fn in ("collision_state", "gate_undecided"):                    # the hooks ask for them with scoped=True
        body = client.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        assert 'operation="gate" if scoped and conn is None else None' in body, fn
    assert 'if operation == "gate":' in client and "return os.environ.get(_GATE_DSN_ENV_VAR) or None" in client      # no fallback to FONDOS_BACKLOG_PG_DSN
    for call in ("collision_state", "gate_undecided"):                  # the pre-push hook passes scoped=True
        assert re.search(re.escape(call) + r"\([^)]*scoped=True", (AUDIT / "pre_push_gate.py").read_text(encoding="utf-8")), call


def test_the_adjudication_tool_writes_only_through_the_adjudicator_login():
    assert used("gate_adjudicate.py") == {"collision_store_present", "gate_queue", "_ADJUDICATOR_DSN_ENV_VAR", "require_live_write_cli", "adjudicate_overlap"}
    client = (ROOT / "shared" / "backlog_client.py").read_text(encoding="utf-8")
    body = client.split("def adjudicate_overlap(", 1)[1].split("\ndef ", 1)[0]
    assert "adjudicator=conn is None" in body and "operation=" not in body                                   # never an allow-listed (non-interactive) operation
    assert '"adjudicate"' not in client.split("NON_INTERACTIVE_OPERATIONS =", 1)[1].split("\n", 1)[0]


def test_the_calibrator_and_the_mode_tool_write_only_the_mode_event_and_never_with_the_adjudicator_login():
    assert used("gate_calibrator.py") == {"COLLISION_MODES", "collision_store_present", "gate_measurements", "require_live_write_cli", "set_collision_mode"}
    assert used("gate_mode.py") == {"COLLISION_MODES", "collision_store_present", "gate_measurements", "gate_queue", "require_live_write_cli", "dsn_for_actor", "set_collision_mode", "current_actor"}
    for script in ("gate_calibrator.py", "gate_mode.py"):
        assert "ADJUDICATOR" not in (AUDIT / script).read_text(encoding="utf-8"), script
    assert 'os.environ["FONDOS_ACTOR"] = ACTOR' in (AUDIT / "gate_calibrator.py").read_text(encoding="utf-8") and 'ACTOR = "system:calibrator"' in (AUDIT / "gate_calibrator.py").read_text(encoding="utf-8")


def test_the_ddl_grants_match_the_roles():
    ddl = (ROOT / "db" / "pg_backlog" / "90_collision.sql").read_text(encoding="utf-8")
    grants = ddl.split("-- Ownership and privileges, as in the earlier phases.", 1)[1]
    stmts = [x for x in re.split(r";[ \t]*\r?\n", grants) if "GRANT EXECUTE" in x]
    to = lambda role: " ".join(x for x in stmts if x.rstrip().endswith(f"TO {role}"))
    gate = to("backlog_gate")
    assert set(re.findall(r"backlog\.(\w+)\(", gate)) == {"sp_gate_collision", "fn_collision_state", "fn_gate_undecided"}                # the hooks: the rule, the state, the undecided pairs
    adj = to("backlog_adjudicator")
    assert set(re.findall(r"backlog\.(\w+)\(", adj)) == {"sp_adjudicate_overlap", "fn_gate_undecided"}                                      # the adjudicator decides pairs and nothing else
    mode = [role for role in ("backlog_gate", "backlog_adjudicator", "backlog_writer", "backlog_human") if "sp_set_collision_mode" in to(role)]
    assert sorted(mode) == ["backlog_human", "backlog_writer"]                                                                           # not the hook login, not the adjudicator
