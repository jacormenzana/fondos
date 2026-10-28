#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
migrate_backlog_v2.py -- apply (or roll back) ONE phase of the Backlog v2 store in the `gestion` database
(doc/especificacion/PROPUESTA_BACKLOG_V2.md; owner action O-2).

    python scripts/ops/migrate_backlog_v2.py --phase alias                    # DRY RUN: read-only, reports state, no DDL
    python scripts/ops/migrate_backlog_v2.py --phase alias --apply            # the phase's block in ONE transaction
    python scripts/ops/migrate_backlog_v2.py --phase alias --down              # DRY RUN of the staged rollback
    python scripts/ops/migrate_backlog_v2.py --phase alias --down --apply      # rollback in ONE transaction

Phases: `alias` (FND-0330), `transitions` (FND-0331 step A-1, additive: creates the non-superuser role backlog_owner, the audit tables, sp_open_ticket,
sp_transition, the rebuilt sp_close_release and the shadow-stage trigger; revokes nothing) and `revoke` (step A-2: removes the direct INSERT/UPDATE
privileges of backlog_writer and adds the author check; --apply is REFUSED unless the shadow stage shows 50 non-system transitions since the last V-00),
`human` (FND-0337, owner-only role backlog_human for human actors; created WITHOUT a password: the owner sets it in his own session and keeps its DSN out of .env) and `history`
(FND-0338, append-only history of the eight descriptive columns). `human` and `history` need step A-1 re-applied with FND-0337 (the routines they use).

One phase per transaction (Postgres DDL is transactional: a failure rolls back whole). The DDL is NOT duplicated here: it is the block
between `-- BEGIN <name>` and `-- END <name>` in the phase's file under db/pg_backlog/ (single source of truth, P#11); the rollback is
the block of its file under db/pg_backlog/down/. Every phase is idempotent. Target, DSN handling and exit codes are those of
migrate_backlog_links.py (owner role of FONDOS_PG_DSN_OWNER pointed at `gestion`; the DSN is never printed).
After an apply or a rollback, record it in the phase's backlog ticket (who applied, when, the printed state): the script persists
nothing itself, the ticket log is the evidence.

Exit codes: 0 ok (or dry run), 1 apply failed (rolled back), 2 usage or connection error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import migrate_backlog_links as base  # noqa: E402  (ddl_block, gestion_dsn, LOCK_TIMEOUT, RC_*)

DDL_DIR = ROOT / "db" / "pg_backlog"
RC_OK, RC_FAILED, RC_USAGE = base.RC_OK, base.RC_FAILED, base.RC_USAGE

# phase -> files and block names, the objects it creates, and the tables it needs first
PHASES: dict = {
    "alias": {
        "up": ("25_actor_alias.sql", "backlog_actor_alias"),
        "down": ("down/25_actor_alias_down.sql", "backlog_actor_alias_down"),
        "tables": ("actor_alias",),
        "views": ("v_backlog_log_actor", "v_actor_unmapped"),
        "requires": ("backlog", "backlog_logs", "backlog_link_event"),
    },
    # Phase A step A-1 (additive). Its rollback first restores the LEGACY sp_close_release from 10_kpis_and_release.sql, then runs its own block.
    "transitions": {
        "up": ("30_transitions.sql", "backlog_transitions"),
        "down": [("10_kpis_and_release.sql", "backlog_sp_close_release"), ("down/30_transitions_down.sql", "backlog_transitions_down")],
        "tables": ("status_event", "wait_event", "raw_update_event"),
        "views": ("v_shadow_stage",),
        "routines": ("sp_open_ticket", "sp_transition", "fn_actor_ok", "fn_actor_authorized", "fn_transition_legal", "trg_governed_writes", "fn_gate_blockers", "fn_raise_review_flags", "fn_gate_mode", "fn_gate_record"),
        "requires": ("backlog", "backlog_logs", "backlog_link_event", "actor_alias", "v_backlog_link"),
    },
    # Phase A step A-2 (revoke). REFUSED unless the shadow stage shows SHADOW_REQUIRED non-system status changes since the last V-00.
    "revoke": {
        "up": ("31_revoke_raw_update.sql", "backlog_revoke_raw_update"),
        "down": ("down/31_revoke_raw_update_down.sql", "backlog_revoke_raw_update_down"),
        "tables": (),
        "views": (),
        "routines": ("trg_actor_known",),
        "requires": ("status_event", "wait_event", "raw_update_event", "v_shadow_stage"),
        "gate": "shadow",
    },
    # Identity (FND-0337): the owner-only role for human actors. Needs step A-1 re-applied with FND-0337 (fn_actor_authorized). The owner sets the role's password himself.
    "human": {
        "up": ("32_human_role.sql", "backlog_human_role"),
        "down": ("down/32_human_role_down.sql", "backlog_human_role_down"),
        "tables": (),
        "views": (),
        "routines": ("fn_actor_authorized",),
        "requires": ("status_event",),
        "requires_routines": ("fn_actor_authorized",),
        "roles": ("backlog_human",),
    },
    # Descriptive change history (FND-0338): append-only trigger on the eight E3 columns. Needs A-1 re-applied with FND-0337 (trg_append_only, fn_actor_authorized).
    "history": {
        "up": ("33_descriptive_history.sql", "backlog_descriptive_history"),
        "down": ("down/33_descriptive_history_down.sql", "backlog_descriptive_history_down"),
        "tables": ("field_change_event",),
        "views": ("v_field_change_attribution",),
        "routines": ("trg_field_changes",),
        "requires": ("backlog", "status_event"),
        "requires_routines": ("trg_append_only", "fn_actor_ok", "fn_actor_authorized"),
    },
    # Phase B (FND-0333): relationship semantics and the delivery gate (deployed in AUDIT mode). ONE command, ONE transaction: the block of step A-1 first (idempotent: it carries
    # the gate hooks of sp_transition and the transition matrix) and then Phase B, so there is no separate "re-apply A-1" step and a failure in either changes nothing.
    # Step human is not required. Its rollback goes back to step A-1 (the hooks become no-op stubs).
    "relations": {
        "up": [("30_transitions.sql", "backlog_transitions"), ("40_relations_v2.sql", "backlog_relations_v2")],
        "down": ("down/40_relations_v2_down.sql", "backlog_relations_v2_down"),
        "tables": ("review_flag", "hold_event", "gate_switch", "gate_audit_event"),
        "views": ("v_open_review_flag", "v_open_hold", "v_supersede_suggestion", "v_gate_audit_violation", "v_gate_audit_window"),
        "routines": ("trg_link_cycle", "fn_gate_mode", "fn_gate_record", "fn_gate_blockers", "fn_raise_review_flags", "sp_ack_review", "sp_raise_hold", "sp_release_hold", "sp_set_gate", "sp_annotate_wait"),
        "requires": ("backlog", "backlog_logs", "backlog_link_event", "actor_alias", "v_backlog_link"),      # those of step A-1, which this phase applies itself
    },
}
SHADOW_REQUIRED = 50          # owner decision D2 (2026-10-26): 50 consecutive non-system transitions with no direct update


def _blocks(entry) -> list:
    """A phase entry is one (file, block) pair or a list of them; always a list."""
    return [entry] if isinstance(entry[0], str) else list(entry)


def ddl_block(phase: str, down: bool = False) -> str:
    """The DDL (or rollback) block(s) of `phase` exactly as written in their files, markers included, in execution order."""
    return "\n".join(base.ddl_block(DDL_DIR / fname, name) for fname, name in _blocks(PHASES[phase]["down" if down else "up"]))


def shadow_stage(conn) -> dict:
    """{'last_v00_ts', 'v00_total', 'live_non_system_since_last_v00'} from backlog.v_shadow_stage (A-1 must be applied)."""
    row = conn.execute("SELECT last_v00_ts, v00_total, live_non_system_since_last_v00 FROM backlog.v_shadow_stage").fetchone()
    return {"last_v00_ts": row[0], "v00_total": int(row[1]), "live_non_system_since_last_v00": int(row[2])}


def revoke_gate(stage: dict, required: int = SHADOW_REQUIRED) -> tuple:
    """(allowed, message): the A-2 revoke needs `required` non-system status changes since the last direct write (V-00). Pure."""
    n = stage["live_non_system_since_last_v00"]
    msg = (f"{n} of {required} required non-system transitions since the last V-00 "
           f"(V-00 total {stage['v00_total']}, last {stage['last_v00_ts'] or 'never'})")
    return n >= required, msg


_SEED_ROW = re.compile(r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(?:NULL|'[^']*')\s*\)")


def seed_aliases(phase: str = "alias") -> dict:
    """{raw_name: canonical} rows the phase's DDL block seeds (the acceptance check compares the database with exactly this)."""
    return {m.group(1): m.group(2) for m in _SEED_ROW.finditer(ddl_block(phase))}


def existing(conn, phase: str) -> dict:
    """{'tables': [present], 'views': [present], 'missing_requires': [...]} for `phase` (read-only)."""
    spec = PHASES[phase]
    names = list(spec["tables"]) + list(spec["views"]) + list(spec["requires"])
    rows = conn.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'backlog' "
                        "AND table_name = ANY(%s)", ([n.lower() for n in names],)).fetchall()
    have = {r[0].lower() for r in rows}
    out = {
        "tables": sorted(t for t in spec["tables"] if t in have),
        "views": sorted(v for v in spec["views"] if v in have),
        "missing_requires": [r for r in spec["requires"] if r not in have],
    }
    if "routines" in spec or "requires_routines" in spec:       # phases that create or need routines also report them (the alias phase keeps its exact shape)
        procs = {r[0] for r in conn.execute("SELECT proname FROM pg_proc WHERE pronamespace = 'backlog'::regnamespace").fetchall()}
        out["routines"] = sorted(r for r in spec.get("routines", ()) if r in procs)
        out["missing_requires"] += [r for r in spec.get("requires_routines", ()) if r not in procs]
    if "roles" in spec:
        present = {r[0] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(spec["roles"]),)).fetchall()}
        out["roles"] = sorted(present)
    if phase == "revoke":
        can = conn.execute("SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') "
                           "THEN has_column_privilege('backlog_writer', 'backlog.backlog', 'status', 'UPDATE') END").fetchone()[0]
        out["writer_can_update_status"] = can
    return out


def migrate(conn, phase: str, down: bool = False, lock_timeout: str = base.LOCK_TIMEOUT) -> None:
    """The phase's block (or its rollback) in ONE transaction. Raises on any failure (the caller rolls back)."""
    if not down:
        missing = existing(conn, phase)["missing_requires"]
        if missing:
            raise RuntimeError(f"phase {phase!r} needs backlog.{', backlog.'.join(missing)} first (apply the earlier steps; for the routines re-apply step A-1 as updated by FND-0337)")
    conn.execute(f"SET LOCAL lock_timeout = '{lock_timeout}'")
    conn.execute(ddl_block(phase, down))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", required=True, choices=sorted(PHASES))
    ap.add_argument("--down", action="store_true", help="the staged rollback of the phase instead of the phase")
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from shared.env_guard import require_db_driver     # RC 106 up front, not a traceback mid-run
    require_db_driver()
    import os
    import shared.config  # noqa: F401  (autoloads .env: FONDOS_PG_DSN_OWNER)
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    owner = os.environ.get("FONDOS_PG_DSN_OWNER")
    if not owner:
        print("FONDOS_PG_DSN_OWNER is not set (it is in .env); DDL needs the owner role.", file=sys.stderr)
        return RC_USAGE
    dsn = base.gestion_dsn(owner)
    d = conninfo_to_dict(dsn)
    print(f"target: host={d.get('host')} port={d.get('port')} dbname={d.get('dbname')} user={d.get('user')}")
    try:
        conn = psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:            # never echo the DSN
        print(f"connection failed: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    what = f"phase {args.phase} {'ROLLBACK' if args.down else 'DDL'}"
    try:
        have = existing(conn, args.phase)
        conn.rollback()
        extra = {k: v for k, v in have.items() if k not in ("tables", "views", "missing_requires")}
        print(f"{what}: tables present {have['tables']} of {list(PHASES[args.phase]['tables'])}; "
              f"views present {have['views']} of {list(PHASES[args.phase]['views'])}; "
              f"missing prerequisites {have['missing_requires']}" + (f"; {extra}" if extra else ""))
        files = ", ".join(f for f, _ in _blocks(PHASES[args.phase]["down" if args.down else "up"]))
        gate_msg = None
        if PHASES[args.phase].get("gate") == "shadow" and not args.down and not have["missing_requires"]:
            allowed, gate_msg = revoke_gate(shadow_stage(conn))
            conn.rollback()
            print(f"shadow stage: {gate_msg} -> {'OPEN' if allowed else 'CLOSED'}")
            if args.apply and not allowed:
                print("REFUSED: the A-2 revoke is conditioned on the shadow stage (owner decision D2). Nothing was changed.", file=sys.stderr)
                return RC_FAILED
        if not args.apply:
            print(f"\nDRY RUN: nothing changed. --apply would run the {len(ddl_block(args.phase, args.down).splitlines())}-line block(s) "
                  f"of {files} in one transaction.")
            return RC_OK
        try:
            migrate(conn, args.phase, args.down)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"FAILED, rolled back (nothing committed): {type(exc).__name__}: {exc}", file=sys.stderr)
            return RC_FAILED
        after = existing(conn, args.phase)
        conn.rollback()
        extra = {k: v for k, v in after.items() if k not in ("tables", "views", "missing_requires")}
        print(f"\nAPPLIED ({what}): tables {after['tables']}; views {after['views']}" + (f"; {extra}" if extra else "")
              + ". Record it in the phase ticket log.")
        return RC_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
