#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_phase_a_acceptance.py -- acceptance checks of Backlog v2 Phase A (FND-0331), read-only, evidence persisted.

    python scripts/audit/backlog_phase_a_acceptance.py --baseline                                     # BEFORE the owner applies a step
    python scripts/audit/backlog_phase_a_acceptance.py --verify a1 --baseline-file F --migration-rc 0   # AFTER step A-1 (30_transitions.sql)
    python scripts/audit/backlog_phase_a_acceptance.py --verify a2 --baseline-file F --migration-rc 0   # AFTER step A-2 (31_revoke_raw_update.sql)

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
HARDENED = ("sp_open_ticket", "sp_transition", "sp_close_release", "trg_governed_writes")


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
        ("every baseline ticket has a BACKFILL status event", (catalog["backfill_events"] or 0) >= baseline.get("tickets", 10 ** 9),
         f"backfill events {catalog['backfill_events']}, baseline tickets {baseline.get('tickets')}"),
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


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--verify", choices=("a1", "a2"))
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
        results = (evaluate_a1 if args.verify == "a1" else evaluate_a2)(args.migration_rc, baseline, after, catalog)
        info = []
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
