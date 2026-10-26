#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_actor_alias_acceptance.py -- acceptance check of the actor-alias phase (FND-0330), read-only, evidence persisted.

    python scripts/audit/backlog_actor_alias_acceptance.py --baseline                       # BEFORE the owner applies the phase
    python scripts/audit/backlog_actor_alias_acceptance.py --verify --baseline-file FILE --migration-rc 0   # AFTER

--baseline fingerprints the append-only history (count + md5 of every progress log and link event up to the current maximum ids) and
writes out/audit/fnd0330_baseline_<UTC>.json. --verify, after the phase is applied, requires ALL of:
  1. the migration exited 0 (reported with --migration-rc: the script cannot observe another process' exit code);
  2. backlog.actor_alias holds exactly the aliases the DDL seeds (13 today) with the same canonical identity each;
  3. no author string of logs or link events is unmapped;
  4. the history up to the baseline ids has the SAME count and md5 (nothing rewritten, deleted or inserted below the baseline);
  5. `backlog_client --check-actors` would exit 0.
It prints every check and writes out/audit/fnd0330_acceptance_<UTC>.txt; exit 0 only when all pass, 1 otherwise, 2 on usage/connection
error. It never writes to the database. No SQL here: every read goes through shared/backlog_client (the only door to `gestion`).
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
EXPECTED_ALIASES = 13


def evaluate(migration_rc: int, baseline: dict, after: dict, db_aliases: dict, seed: dict, unmapped: list, expected: int = EXPECTED_ALIASES) -> list:
    """[(check, passed, detail)] for the five acceptance criteria. Pure."""
    diff = sorted(set(db_aliases) ^ set(seed)) + sorted(k for k in set(db_aliases) & set(seed) if db_aliases[k] != seed[k])
    same_history = all(baseline.get(k) == after.get(k) for k in ("logs", "logs_md5", "events", "events_md5"))
    return [
        ("migration exit code is 0", migration_rc == 0, f"reported rc={migration_rc}"),
        (f"{expected} equivalences in the database equal the DDL seed", len(db_aliases) == expected == len(seed) and not diff,
         f"database {len(db_aliases)}, seed {len(seed)}, differences {diff}"),
        ("no author string is unmapped", not unmapped, f"unmapped {[u.get('raw_actor') for u in unmapped]}"),
        ("history up to the baseline ids is identical", same_history,
         f"logs {baseline.get('logs')}->{after.get('logs')} md5 {'=' if baseline.get('logs_md5') == after.get('logs_md5') else '!='}; "
         f"events {baseline.get('events')}->{after.get('events')} md5 {'=' if baseline.get('events_md5') == after.get('events_md5') else '!='}"),
        ("--check-actors would exit 0", not unmapped and len(db_aliases) > 0, "store present, every author string mapped"),
    ]


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline", action="store_true")
    g.add_argument("--verify", action="store_true")
    ap.add_argument("--baseline-file", help="--verify: the JSON written by --baseline")
    ap.add_argument("--migration-rc", type=int, help="--verify: exit code the owner saw from migrate_backlog_v2.py --apply")
    args = ap.parse_args(argv)
    if args.verify and (not args.baseline_file or args.migration_rc is None):
        print("error: --verify needs --baseline-file and --migration-rc", file=sys.stderr)
        return RC_USAGE

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    import shared.config  # noqa: F401  (loads the repo .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if args.baseline:
            fp = bc.history_fingerprint()
            fp["taken_at_utc"] = _stamp()
            path = OUT_DIR / f"fnd0330_baseline_{fp['taken_at_utc']}.json"
            path.write_text(json.dumps(fp, indent=2), encoding="utf-8")
            print(f"baseline written: {path}\n{json.dumps(fp)}")
            return RC_OK
        import migrate_backlog_v2 as mig
        baseline = json.loads(Path(args.baseline_file).read_text(encoding="utf-8"))
        if not bc.actor_store_present():
            print("FAIL - backlog.actor_alias is not applied")
            return RC_FAIL
        after = bc.history_fingerprint(max_log_id=baseline["max_log_id"], max_event_id=baseline["max_event_id"])
        results = evaluate(args.migration_rc, baseline, after, bc.list_actor_aliases(), mig.seed_aliases(), bc.unmapped_actors())
    except bc.BacklogUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    lines = [f"FND-0330 acceptance, {_stamp()}, baseline {args.baseline_file}"]
    lines += [f"{'PASS' if ok else 'FAIL'} - {name}: {detail}" for name, ok, detail in results]
    verdict = all(ok for _, ok, _ in results)
    lines.append(f"RESULT: {'ACCEPTED (all criteria met)' if verdict else 'NOT ACCEPTED'}")
    text = "\n".join(lines)
    (OUT_DIR / f"fnd0330_acceptance_{_stamp()}.txt").write_text(text + "\n", encoding="utf-8")
    print(text)
    return RC_OK if verdict else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
