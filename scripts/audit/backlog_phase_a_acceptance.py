#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_phase_a_acceptance.py -- acceptance checks of Backlog v2 Phase A (FND-0331), read-only, evidence persisted.

    python scripts/audit/backlog_phase_a_acceptance.py --baseline                                     # BEFORE the owner applies a step
    python scripts/audit/backlog_phase_a_acceptance.py --verify a1 --baseline-file F --migration-rc 0   # AFTER step A-1 (30_transitions.sql)
    python scripts/audit/backlog_phase_a_acceptance.py --verify a2 --baseline-file F --migration-rc 0   # AFTER step A-2 (31_revoke_raw_update.sql)
    python scripts/audit/backlog_phase_a_acceptance.py --verify human|history --baseline-file F --migration-rc 0   # AFTER step 32 (FND-0337) / step 33 (FND-0338)
    python scripts/audit/backlog_phase_a_acceptance.py --verify relations --baseline-file F --migration-rc 0         # AFTER Phase B (40_relations_v2.sql, FND-0333)

--baseline fingerprints the append-only history (count + md5 of every log and link event up to the current maximum ids) and the number of tickets, and
writes out/audit/fnd0331_baseline_<UTC>.json. --verify a1 requires ALL of: the migration exited 0; backlog_owner exists and is NOT a superuser and cannot log in;
the routines are SECURITY DEFINER, owned by backlog_owner, with a pinned search_path (and the via_proc marker on the two that write); the audit tables belong
to backlog_owner; every baseline ticket has a BACKFILL status event; the history up to the baseline ids is identical; the writer role lost NOTHING (A-1 is
additive) and cannot write the audit tables; the guard trigger exists. --verify a2 requires: exit 0; the writer can no longer update the governed columns or
insert tickets or write the audit tables; it keeps exactly the enumerated exceptions (E1 logs, E2 link events, E3 the descriptive columns) and may still execute the
routines; the author-check triggers exist; the history is identical. Both write out/audit/fnd0331_acceptance_<step>_<UTC>.txt and exit 0 only when every check
passes (1 otherwise, 2 on usage or connection errors). It never writes to the database. No SQL here: every read goes through shared/backlog_client.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "out" / "audit"
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2
SEARCH_PATH = "search_path=pg_catalog,backlog"
WRITING_ROUTINES = ("sp_open_ticket", "sp_transition")
HARDENED = ("sp_open_ticket", "sp_transition", "sp_close_release", "trg_governed_writes", "fn_actor_authorized")
DESCRIPTIVE_FIELDS = ("title", "nature", "schema_name", "object_type", "object_name", "priority", "session_id", "scenario_description")      # exception E3 (tests tie it to the client)


def _norm(cfg) -> list:
    return [c.replace(" ", "") for c in (cfg or [])]


def _same_history(baseline: dict, after: dict) -> tuple:
    keys = ("logs", "logs_md5", "events", "events_md5")
    ok = all(baseline.get(k) == after.get(k) for k in keys)
    return ok, (f"logs {baseline.get('logs')}->{after.get('logs')} md5 {'=' if baseline.get('logs_md5') == after.get('logs_md5') else '!='}; "
                f"events {baseline.get('events')}->{after.get('events')} md5 {'=' if baseline.get('events_md5') == after.get('events_md5') else '!='}")


def evaluate_a1(migration_rc: int, baseline: dict, after: dict, catalog: dict) -> list:
    """[(check, passed, detail)] for step A-1. Pure."""
    role, routines, owners = catalog["owner_role"], {r["name"]: r for r in catalog["routines"]}, catalog["audit_table_owners"]
    writer = catalog["writer"] or {}
    bad_routines = []
    for name in HARDENED:
        r = routines.get(name)
        cfg = _norm(r["config"]) if r else []
        need_marker = name in WRITING_ROUTINES
        if not (r and r["secdef"] and r["owner"] == "backlog_owner" and SEARCH_PATH in cfg and (not need_marker or "backlog.via_proc=on" in cfg)):
            bad_routines.append(name)
    history_ok, history_detail = _same_history(baseline, after)
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("backlog_owner exists, is not a superuser and cannot log in", bool(role) and not role["superuser"] and not role["can_login"], f"{role}"),
        ("routines are SECURITY DEFINER, owned by backlog_owner, search_path pinned", not bad_routines, f"not conforming: {bad_routines}"),
        ("the three audit tables belong to backlog_owner", owners == {"raw_update_event": "backlog_owner", "status_event": "backlog_owner", "wait_event": "backlog_owner"}, f"{owners}"),
        ("every ticket has a status event (BACKFILL for those that pre-date A-1, LIVE for later ones)",
         catalog.get("tickets_without_status_event") == 0 and (catalog["backfill_events"] or 0) > 0,
         f"tickets without a status event {catalog.get('tickets_without_status_event')}, backfill events {catalog['backfill_events']}, tickets {catalog['tickets']}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
        ("A-1 is additive: the writer lost nothing and cannot write the audit tables",
         bool(writer) and writer["can_update_status"] and writer["can_insert_ticket"] and writer["can_insert_log"] and writer["can_execute_transition"]
         and not writer["can_write_audit_tables"], f"{writer}"),
        ("the shadow-stage trigger exists", "governed_writes" in catalog["triggers"], f"triggers {catalog['triggers']}"),
    ]


def evaluate_a2(migration_rc: int, baseline: dict, after: dict, catalog: dict) -> list:
    """[(check, passed, detail)] for step A-2. Pure."""
    w = catalog["writer"] or {}
    history_ok, history_detail = _same_history(baseline, after)
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("the writer cannot update status, release_version or assignee, nor insert tickets, nor write the audit tables",
         bool(w) and not w["can_update_status"] and not w["can_update_release_version"] and not w["can_update_assignee"] and not w["can_insert_ticket"]
         and not w["can_write_audit_tables"], f"{w}"),
        ("the writer keeps exactly the enumerated exceptions E1 (log INSERT), E2 (link event INSERT), E3 (descriptive UPDATE) and the routines",
         bool(w) and w["can_insert_log"] and w["can_insert_link_event"] and w["can_update_title"] and w["can_execute_transition"], f"{w}"),
        ("the author-check triggers exist on logs and link events", {"logs_actor", "link_event_actor"} <= set(catalog["triggers"]), f"triggers {catalog['triggers']}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
    ]


def evaluate_human(migration_rc: int, baseline: dict, after: dict, catalog: dict, ident: dict) -> list:
    """[(check, passed, detail)] for the owner-only role of FND-0337 (step 32 plus the re-applied A-1 routines). Pure."""
    role, w, uses = ident["human_role"], catalog["writer"] or {}, ident["calls_authorization"]
    history_ok, history_detail = _same_history(baseline, after)
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("backlog_human exists, is not a superuser, can log in and inherits the writer's privileges",
         bool(role) and not role["superuser"] and role["can_login"] and role["member_of_writer"] is True, f"{role}"),
        ("the authorization rule exists and sp_open_ticket and sp_transition call it", ident["authorization_function"] and bool(uses.get("sp_open_ticket")) and bool(uses.get("sp_transition")),
         f"function={ident['authorization_function']}, calls={uses}"),
        ("the shared writer can still execute the routines (agents are not locked out)", bool(w) and w["can_execute_transition"], f"{w}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
    ]


def evaluate_iam(migration_rc: int, baseline: dict, after: dict, cat: dict) -> list:
    """[(check, passed, detail)] for the IAM step of FND-0354 (50_iam.sql): the three roles and exactly what each can do. Bindings and the retirement of the shared writer are NOT criteria
    of this step (the owner does them afterwards, see evaluate_retire). Pure."""
    roles, rt = cat["roles"], cat["routines"]
    history_ok, history_detail = _same_history(baseline, after)

    def scoped(name, executes):
        r = roles.get(name)
        return bool(r) and r["can_login"] is not None and not r["superuser"] and r["select_all"] and r["write_tables"] == [] and r["executes"] == executes

    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("the routines exist (hook, bind, scoped append, scoped incident)", all(rt.get(n) for n in ("fn_actor_bound", "sp_bind_login", "sp_append_log", "sp_report_incident")), f"{rt}"),
        ("backlog_reader reads everything and can execute nothing, write nothing", scoped("backlog_reader", []), f"{roles.get('backlog_reader')}"),
        ("backlog_logger can only execute sp_append_log and writes no table", scoped("backlog_logger", ["sp_append_log"]), f"{roles.get('backlog_logger')}"),
        ("backlog_incident can only execute sp_report_incident and writes no table", scoped("backlog_incident", ["sp_report_incident"]), f"{roles.get('backlog_incident')}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
    ]


REQUIRED_OPERATOR_PERSONS = 3          # owner decision 2026-10-29: three distinct HUMAN persons (the two Cormenzana logins are one person; the third is the QA lead)


def evaluate_retire(migration_rc: int, baseline: dict, after: dict, cat: dict) -> list:
    """[(check, passed, detail)] for the retirement of the shared writer login and the A-2 validation pool (FND-0354, owner decisions 2026-10-29): no login can still open or move a ticket
    unless it is bound to an identity; at least REQUIRED_OPERATOR_PERSONS distinct PERSONS are bound to human identities (several logins of one person count once; agents never count);
    and transitions have actually been made by that many distinct bound persons (not just logins that exist). Pure."""
    humans = [b for b in cat["bindings"] if b["active"] and b["kind"] == "HUMAN"]
    persons = sorted({b["person"] for b in humans})
    moved = cat.get("transition_persons") or []
    history_ok, history_detail = _same_history(baseline, after)
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("no login that can open or move a ticket is unbound", cat["unbound_writers"] == [], f"unbound writers: {cat['unbound_writers']}"),
        (f"at least {REQUIRED_OPERATOR_PERSONS} distinct persons are bound to human identities", len(persons) >= REQUIRED_OPERATOR_PERSONS,
         f"{len(persons)} person(s) {persons} through {[(b['login_name'], b['actor']) for b in humans]}"),
        (f"transitions were made by at least {REQUIRED_OPERATOR_PERSONS} distinct bound human persons", len(moved) >= REQUIRED_OPERATOR_PERSONS,
         f"{len(moved)} person(s) {cat.get('transitions_by_person') or {}}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
    ]


def evaluate_history(migration_rc: int, baseline: dict, after: dict, catalog: dict, ident: dict) -> list:
    """[(check, passed, detail)] for the descriptive history of FND-0338 (step 33): COVERAGE of the eight columns, append-only, ownership, closed to the writer. Pure."""
    h = ident["history"] or {}
    trig = h.get("trigger_def") or ""
    missing = [c for c in DESCRIPTIVE_FIELDS if c not in trig]
    history_ok, history_detail = _same_history(baseline, after)
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("the history table exists, belongs to backlog_owner and is append-only", bool(h) and h.get("owner") == "backlog_owner" and h.get("append_only") is True, f"{h}"),
        ("the trigger watches all eight descriptive columns (coverage)", bool(trig) and not missing and "AFTER UPDATE OF" in trig, f"missing columns {missing}"),
        ("the attribution view exists (the measure of effectiveness)", bool(h) and h.get("view") is True, f"view={h.get('view')}"),
        ("the shared writer cannot write the history", bool(h) and h.get("writer_can_insert") is False, f"writer_can_insert={h.get('writer_can_insert')}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
    ]


PHASE_B_TABLES = {"gate_audit_event": "backlog_owner", "gate_switch": "backlog_owner", "hold_event": "backlog_owner", "review_flag": "backlog_owner"}


def evaluate_relations(migration_rc: int, baseline: dict, after: dict, cat: dict) -> list:
    """[(check, passed, detail)] for Phase B (FND-0333, 40_relations_v2.sql): the objects and their hardening, append-only tables, the link vocabulary and the cycle
    guard, the hook in sp_transition, the writer's rights, the gate being enabled, and the history untouched. The replay over real history is a separate
    artefact (backlog_phase_b_replay.py --check). Pure."""
    from shared.backlog_client import PHASE_B_ROUTINES
    routines = {r["name"]: r for r in cat["routines"]}
    bad = [n for n in PHASE_B_ROUTINES if not (n in routines and routines[n]["secdef"] and routines[n]["owner"] == "backlog_owner" and SEARCH_PATH in _norm(routines[n]["config"]))]
    check = cat.get("link_type_check") or ""
    history_ok, history_detail = _same_history(baseline, after)
    writer = cat.get("writer") or {}
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        ("the Phase B routines exist, SECURITY DEFINER, owned by backlog_owner, search_path pinned", not bad, f"not conforming: {bad}"),
        ("the new tables belong to backlog_owner and are append-only", cat["table_owners"] == PHASE_B_TABLES and cat["append_only"] == sorted(PHASE_B_TABLES), f"owners {cat['table_owners']}, append-only {cat['append_only']}"),
        ("the link vocabulary accepts IMPACTS and SUPERSEDES and the column origin exists", "IMPACTS" in check and "SUPERSEDES" in check and cat["origin_column"] is True, f"origin column {cat['origin_column']}"),
        ("the DEPENDS_ON cycle guard is installed and no cycle exists today", cat["cycle_guard"] is True and cat["existing_depends_on_cycles"] == 0, f"guard {cat['cycle_guard']}, cycles {cat['existing_depends_on_cycles']}"),
        ("sp_transition calls the delivery gate", cat["sp_transition_calls_gate"] is True, f"{cat['sp_transition_calls_gate']}"),
        ("the shared writer can execute the gate but cannot write the new tables", bool(writer) and writer["can_execute_gate"] and not writer["can_write_new_tables"], f"{writer}"),
        ("the delivery gate is in AUDIT mode (the rollout default) or ENFORCE, not OFF", cat["gate_mode"] in ("AUDIT", "ENFORCE"), f"gate_mode {cat['gate_mode']}"),
        ("gate_switch has the executive-override column (FND-0350)", cat.get("override_column") is True, f"override column {cat.get('override_column')}"),
        ("history up to the baseline ids is identical", history_ok, history_detail),
    ]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--verify", choices=("a1", "a2", "human", "history", "relations", "iam", "retire"))
    ap.add_argument("--baseline-file", help="--verify: the JSON written by --baseline")
    ap.add_argument("--migration-rc", type=int, help="--verify: exit code the owner saw from migrate_backlog_v2.py --apply")
    args = ap.parse_args(argv)
    if args.verify and (not args.baseline_file or args.migration_rc is None):
        print("error: --verify needs --baseline-file and --migration-rc", file=sys.stderr)
        return RC_USAGE

    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads the repo .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if args.baseline:
            fp = bc.history_fingerprint()
            fp["tickets"] = bc.phase_a_catalog()["tickets"]
            fp["taken_at_utc"] = _stamp()
            path = OUT_DIR / f"fnd0331_baseline_{fp['taken_at_utc']}.json"
            path.write_text(json.dumps(fp, indent=2), encoding="utf-8")
            print(f"baseline written: {path}\n{json.dumps(fp)}")
            return RC_OK
        baseline = json.loads(Path(args.baseline_file).read_text(encoding="utf-8"))
        after = bc.history_fingerprint(max_log_id=baseline["max_log_id"], max_event_id=baseline["max_event_id"])
        catalog = bc.phase_a_catalog()
        if args.verify == "relations":
            results = evaluate_relations(args.migration_rc, baseline, after, bc.phase_b_catalog())
        elif args.verify in ("iam", "retire"):
            iam = bc.iam_catalog()
            results = (evaluate_iam if args.verify == "iam" else evaluate_retire)(args.migration_rc, baseline, after, iam)
        elif args.verify in ("human", "history"):
            ident = bc.identity_history_catalog()
            results = (evaluate_human if args.verify == "human" else evaluate_history)(args.migration_rc, baseline, after, catalog, ident)
        else:
            results = (evaluate_a1 if args.verify == "a1" else evaluate_a2)(args.migration_rc, baseline, after, catalog)
        info = []
        if args.verify == "history" and ident["history"] and ident["history"].get("view"):
            att = {r["field"]: r for r in bc.field_change_attribution()}["ALL"]
            info.append(f"INFO - effectiveness today: {att['n_with_actor']} of {att['n_changes']} recorded descriptive changes carry an actor ({att['pct_with_actor']} %); "
                        "the limitation is resolved only when real edits have been recorded and this share has been measured and logged")
        if args.verify == "relations" and bc.phase_b_catalog()["audit_window"]:
            w = bc.phase_b_catalog()["audit_window"]
            hours = "-" if w["hours_in_audit"] is None else f"{float(w['hours_in_audit']):.1f}"
            info.append(f"INFO - gate audit window: mode {w['mode']}, {hours} h in audit, {w['evaluated']} gated moves evaluated, {w['tickets_48h']} of {w['required_tickets']} required different tickets moved in the last 48 h, "
                        f"{w['violations_since']} violations logged ({w['violations_48h']} in the last 48 h); enforcement needs 48 h, {w['required_tickets']} different tickets and 0 violations (clear = {w['clear']})")
        if args.verify in ("iam", "retire"):
            info.append(f"INFO - bindings: {iam['active_logins']} active login(s), {len(iam['active_persons'])} distinct human person(s) {iam['active_persons']}; "
                        f"logins that can still open or move a ticket: {[w['login'] + (' (bound)' if w['bound'] else ' (UNBOUND)') for w in iam['writers']]}")
        if bc.transitions_store_present():
            sp = bc.shadow_progress()
            info.append(f"INFO - shadow stage: {sp['live_non_system_since_last_v00']} of 50 non-system transitions since the last V-00 "
                        f"(V-00 total {sp['v00_total']}, last {sp['last_v00_ts']}); this is the gate of step A-2, not a criterion of {args.verify.upper()}")
    except bc.BacklogUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    lines = [f"FND-0331 acceptance of step {args.verify.upper()}, {_stamp()}, baseline {args.baseline_file}"]
    lines += [f"{'PASS' if ok else 'FAIL'} - {name}: {detail}" for name, ok, detail in results] + info
    verdict = all(ok for _, ok, _ in results)
    lines.append(f"RESULT: {'ACCEPTED (all criteria met)' if verdict else 'NOT ACCEPTED'}")
    text = "\n".join(lines)
    (OUT_DIR / f"fnd0331_acceptance_{args.verify}_{_stamp()}.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return RC_OK if verdict else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
