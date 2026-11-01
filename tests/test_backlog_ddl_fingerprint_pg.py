# tests/test_backlog_ddl_fingerprint_pg.py
# -*- coding: utf-8 -*-
"""The DDL fingerprint of Backlog v2 (FND-0351 family): the phases of scripts/ops/migrate_backlog_v2.py applied to the hermetic container in the order the live database received them, and a fingerprint of what
they leave (md5 of every routine definition and view, the columns of every relation). scripts/ops/backlog_drift.py compares it with the LIVE catalog (read-only) to find a phase that master has but the live
database lacks. With FONDOS_DRIFT_OUT set it writes the fingerprint there; without it, it only checks that the fingerprint is stable (two builds are identical), so CI still exercises it.

    python scripts/ops/backlog_drift.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_drift as bd  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402
from test_backlog_transitions_pg import _base  # noqa: E402


def build(conn) -> dict:
    _base(conn)
    for ph in bd.PHASES_IN_ORDER:
        mig.migrate(conn, ph)
    return bd.fingerprint(conn)


def test_the_fingerprint_is_stable_and_covers_every_phase(pg_conn):
    fp = build(pg_conn)
    assert len(fp["funcs"]) > 30 and len(fp["cols"]) > 30 and len(fp["views"]) > 15
    again = bd.fingerprint(pg_conn)
    assert again == fp                                                                     # reading the catalog twice gives the same answer
    for fn in ("sp_transition", "sp_gate_check", "sp_gate_collision", "sp_record_gate_protection", "sp_set_gate", "sp_agent_transition", "sp_auto_transition"):
        assert any(k.startswith(fn + "(") for k in fp["funcs"]), fn                       # a routine of each of A-1, hook, collision, attest, Phase B, autonomy (W2 and W3)
    out = os.environ.get("FONDOS_DRIFT_OUT")
    if out:
        Path(out).write_text(json.dumps(fp, sort_keys=True), encoding="utf-8")
