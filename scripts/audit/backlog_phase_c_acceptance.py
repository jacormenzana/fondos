#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_phase_c_acceptance.py -- acceptance checks of Backlog v2 Phase C (FND-0334), read-only, evidence persisted.

    python scripts/audit/backlog_phase_c_acceptance.py --verify hook        # AFTER the owner applied --phase hook      (slice 2: gate_check, hook_bypass, the login backlog_gate)
    python scripts/audit/backlog_phase_c_acceptance.py --verify collision   # AFTER --phase collision                   (slice 3: the rule, adjudication, the modes, backlog_adjudicator)
    python scripts/audit/backlog_phase_c_acceptance.py --verify attest      # AFTER --phase attest                      (slice 4: the branch-protection facts and the V-GATE-* codes)
    python scripts/audit/backlog_phase_c_acceptance.py --verify all         # the three, one report

What each phase must satisfy (every check prints PASS or FAIL with its detail):
  * the tables, views and routines of the phase exist (the lists are the ones scripts/ops/migrate_backlog_v2.py applies: one source); every table is owned by backlog_owner and carries the
    append-only trigger, ENABLED (a disabled trigger is a failure); every routine is SECURITY DEFINER, owned by backlog_owner, with a pinned search_path, and PUBLIC cannot execute it;
  * the scoped logins are scoped: backlog_gate can log in, is no superuser, cannot create roles or databases, has its connection limit and statement timeout, belongs to no other role, holds NO
    privilege on any table or view of the backlog schema (the whole schema is read, not only the phase's tables), and executes exactly the routines of its design and none of the privileged ones; backlog_adjudicator (the owner's: this tool never creates it) is no superuser, executes
    exactly the adjudication routine and the read of the undecided pairs and has no write privilege; the shared writer reads the tables, writes none directly and can execute what the tools
    need but not the adjudication; the catalogue view v_backlog_violations contains the phase's violations.
It never writes to the database: every read goes through shared/backlog_client. Evidence: out/audit/fnd0334_acceptance_<phase>_<UTC>.txt. Exit codes: 0 every check passed, 1 a check failed,
2 usage or connection error. The password of a role is not readable by a non-superuser: that the owner set one is shown by a login that works (scripts/audit/gate_mode.py, gate_adjudicate.py).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
OUT_DIR = ROOT / "out" / "audit"
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2
OWNER = "backlog_owner"
ROLES = ["backlog_gate", "backlog_adjudicator", "backlog_writer", "backlog_human", OWNER]
GATE_HOOK = {"sp_gate_check", "sp_gate_forward", "fn_gate_statuses"}
GATE_COLLISION = {"sp_gate_collision", "fn_collision_state", "fn_gate_undecided"}
ADJUDICATOR = {"sp_adjudicate_overlap", "fn_gate_undecided"}
PRIVILEGED = ("sp_transition", "sp_open_ticket", "sp_raise_hold", "sp_release_hold", "sp_record_hook_bypass", "sp_set_collision_mode", "sp_adjudicate_overlap", "sp_record_gate_protection",
              "sp_close_release", "sp_set_gate", "sp_ack_review", "sp_bind_login")           # what a scoped login must NOT execute (unless it is its own design)
PURE_HELPERS = ("fn_mode_rank",)     # an IMMUTABLE SQL function with no table access: owned by backlog_owner and not executable by PUBLIC, but not SECURITY DEFINER (it has nothing to elevate)
VIOLATION_VIEW = {"hook": "v_violations_c2", "attest": "v_violations_c3"}


def _c(check: str, ok: bool, detail: str = "") -> tuple:
    return (check, bool(ok), detail)


def structure(phase: str, cat: dict, spec: dict) -> list:
    """The checks every phase shares: it exists, is owned by backlog_owner, append-only, SECURITY DEFINER with a pinned search_path, not executable by PUBLIC. Pure."""
    out = []
    for t in spec["tables"]:
        f = cat["tables"].get(t)
        out.append(_c(f"table {t} exists, owned by {OWNER}, append-only trigger", f is not None and f["owner"] == OWNER and f["append_only"],
                      "absent" if f is None else f"owner={f['owner']} append_only={f['append_only']}"))
    for v in spec["views"]:
        out.append(_c(f"view {v} exists", cat["views"].get(v) is True))
    for r in spec["routines"]:
        f = cat["routines"].get(r)
        if r in PURE_HELPERS:
            out.append(_c(f"routine {r} (pure helper) exists, owned by {OWNER}, PUBLIC cannot execute", f is not None and f["owner"] == OWNER and not f["public"],
                          "absent" if f is None else f"owner={f['owner']} public={f['public']}"))
            continue
        out.append(_c(f"routine {r}: SECURITY DEFINER, owned by {OWNER}, pinned search_path, PUBLIC cannot execute",
                      f is not None and f["secdef"] and f["owner"] == OWNER and f["search_path_pinned"] and not f["public"],
                      "absent" if f is None else f"secdef={f['secdef']} owner={f['owner']} pinned={f['search_path_pinned']} public={f['public']}"))
    return out


def _executes(cat: dict, role: str) -> set:
    return {n for n, f in cat["routines"].items() if role in f["executes"]}


def _no_table_writes(cat: dict, role: str) -> bool:
    """True when `role` can write NO relation of the whole backlog schema (not only the tables of the phase). Pure."""
    return not cat.get("scoped", {}).get(role, {"write": ["?"]})["write"]


def _scoped_login(cat: dict, role: str, allowed: set, connlimit=None, timeout=False) -> list:
    """The checks of a scoped login: it exists with the right flags and executes exactly `allowed` among the routines read. Pure."""
    f = cat["roles"].get(role)
    out = [_c(f"role {role} exists, can log in", f is not None and f["login"], "absent" if f is None else f"login={f['login']}")]
    if f is None:
        return out
    out.append(_c(f"{role} is no superuser and cannot create roles or databases", not (f["super"] or f["createrole"] or f["createdb"]),
                  f"super={f['super']} createrole={f['createrole']} createdb={f['createdb']}"))
    out.append(_c(f"{role} belongs to no other role", not f["member_of"], f"member_of={f['member_of']}"))
    if connlimit is not None:
        out.append(_c(f"{role} connection limit is {connlimit}", f["connlimit"] == connlimit, f"connlimit={f['connlimit']}"))
    if timeout:
        out.append(_c(f"{role} has a statement timeout", any(x.startswith("statement_timeout=") for x in f["config"]), f"config={f['config']}"))
    got = _executes(cat, role)
    out.append(_c(f"{role} executes exactly its routines and none of the privileged ones", got == allowed, f"executes={sorted(got)} expected={sorted(allowed)}"))
    return out


def evaluate_hook(cat: dict, spec: dict, collision_applied: bool = False) -> list:
    """Slice 2 (--phase hook). Pure."""
    allowed = GATE_HOOK | (GATE_COLLISION if collision_applied else set())
    out = structure("hook", cat, spec)
    out += _scoped_login(cat, "backlog_gate", allowed, connlimit=8, timeout=True)
    g = cat.get("scoped", {}).get("backlog_gate")
    out.append(_c("backlog_gate holds no privilege on ANY table or view of the backlog schema", g is not None and not g["read"] and not g["write"],
                  "not read" if g is None else f"read={g['read'][:6]} write={g['write'][:6]}"))
    w = _executes(cat, "backlog_writer")
    out.append(_c("the shared writer executes sp_gate_check and sp_record_hook_bypass", {"sp_gate_check", "sp_record_hook_bypass"} <= w, f"writer executes {sorted(w & set(spec['routines']))}"))
    out.append(_c("the shared writer reads the tables and writes none directly", all(f["select"].get("backlog_writer", False) and not f["insert"].get("backlog_writer", False)
                                                                                 for f in cat["tables"].values())))
    out.append(_c(f"v_backlog_violations contains {VIOLATION_VIEW['hook']}", cat["violations_view"] is not None and VIOLATION_VIEW["hook"] in cat["violations_view"]))
    return out


def evaluate_collision(cat: dict, spec: dict) -> list:
    """Slice 3 (--phase collision). Pure."""
    out = structure("collision", cat, spec)
    adj = cat["roles"].get("backlog_adjudicator")
    out += _scoped_login(cat, "backlog_adjudicator", ADJUDICATOR)
    if adj is not None:
        a = cat.get("scoped", {}).get("backlog_adjudicator")
        out.append(_c("backlog_adjudicator holds no write privilege on any table or view of the backlog schema", _no_table_writes(cat, "backlog_adjudicator"),
                      "not read" if a is None else f"write={a['write'][:6]}"))
    gate = _executes(cat, "backlog_gate")
    out.append(_c("backlog_gate executes the rule, the state and the undecided pairs, and cannot adjudicate or set a mode", GATE_COLLISION <= gate and not ({"sp_adjudicate_overlap", "sp_set_collision_mode",
                                                                                                                                   "fn_collision_pairs"} & gate), f"executes={sorted(gate)}"))
    w = _executes(cat, "backlog_writer")
    out.append(_c("the shared writer can set a mode (the calibrator) but cannot adjudicate", "sp_set_collision_mode" in w and "sp_adjudicate_overlap" not in w, f"writer executes {sorted(w & set(spec['routines']))}"))
    out.append(_c("the shared writer writes no table directly", all(not f["insert"].get("backlog_writer", False) for f in cat["tables"].values())))
    return out


def evaluate_attest(cat: dict, spec: dict) -> list:
    """Slice 4 (--phase attest). Pure."""
    out = structure("attest", cat, spec)
    ex = cat["routines"].get("sp_record_gate_protection", {}).get("executes", [])
    out.append(_c("sp_record_gate_protection is executable by the shared writer and the owner-only login, not by the scoped logins", {"backlog_writer"} <= set(ex) and not ({"backlog_gate", "backlog_adjudicator"} & set(ex)),
                  f"executes={ex}"))
    out.append(_c(f"v_backlog_violations contains {VIOLATION_VIEW['attest']}", cat["violations_view"] is not None and VIOLATION_VIEW["attest"] in cat["violations_view"]))
    out.append(_c("the shared writer writes no table directly", all(not f["insert"].get("backlog_writer", False) for f in cat["tables"].values())))
    return out


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", required=True, choices=("hook", "collision", "attest", "all"))
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads .env: the ordinary read login)
    from shared import backlog_client as bc
    import migrate_backlog_v2 as mig

    phases = ("hook", "collision", "attest") if args.verify == "all" else (args.verify,)
    lines, ok_all = [f"Backlog v2 Phase C acceptance ({', '.join(phases)}) at {_stamp()}"], True
    try:
        for phase in phases:
            spec = mig.PHASES[phase]
            cat = bc.phase_c_catalog(list(spec["tables"]) + [t for p in ("hook", "collision", "attest") for t in mig.PHASES[p]["tables"]],
                                     list(spec["views"]), list(spec["routines"]) + [r for p in ("hook", "collision", "attest") for r in mig.PHASES[p]["routines"]] + list(PRIVILEGED), ROLES)
            results = {"hook": lambda: evaluate_hook(cat, spec, collision_applied="sp_gate_collision" in cat["routines"]),
                       "collision": lambda: evaluate_collision(cat, spec), "attest": lambda: evaluate_attest(cat, spec)}[phase]()
            lines.append(f"\n== {phase} ==")
            lines += [f"{'PASS' if ok else 'FAIL'} - {name}" + (f": {detail}" if detail and not ok else "") for name, ok, detail in results]
            ok_all = ok_all and all(ok for _, ok, _ in results)
    except Exception as exc:                                  # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    lines.append("\nRESULT: " + ("ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED"))
    text = "\n".join(lines)
    print(text)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"fnd0334_acceptance_{args.verify}_{_stamp()}.txt").write_text(text + "\n", encoding="utf-8")
    return RC_OK if ok_all else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
