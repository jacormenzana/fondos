# tests/test_backlog_actor_alias_acceptance.py
# -*- coding: utf-8 -*-
"""Acceptance logic of the actor-alias phase (FND-0330; scripts/audit/backlog_actor_alias_acceptance.py). Pure: no database (R-7)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_actor_alias_acceptance as acc  # noqa: E402
import migrate_backlog_v2 as mig  # noqa: E402

SEED = mig.seed_aliases()
FP = {"logs": 1265, "logs_md5": "a" * 32, "events": 265, "events_md5": "b" * 32}


def _checks(**kw):
    args = dict(migration_rc=0, baseline=FP, after=dict(FP), db_aliases=dict(SEED), seed=SEED, unmapped=[])
    args.update(kw)
    return {name: ok for name, ok, _ in acc.evaluate(**args)}


def test_seed_is_the_thirteen_equivalences():
    assert len(SEED) == acc.EXPECTED_ALIASES == 13


def test_everything_met_is_accepted():
    assert all(_checks().values())


def test_a_nonzero_migration_exit_code_fails_only_that_check():
    r = _checks(migration_rc=1)
    assert [k for k, v in r.items() if not v] == ["migration exit code is 0"]


def test_a_missing_or_different_equivalence_fails():
    short = dict(SEED)
    short.pop("jacor")
    assert not _checks(db_aliases=short)["13 equivalences in the database equal the DDL seed"]
    changed = dict(SEED, claude="agent:other")
    assert not _checks(db_aliases=changed)["13 equivalences in the database equal the DDL seed"]
    extra = dict(SEED, stranger="agent:x")
    assert not _checks(db_aliases=extra)["13 equivalences in the database equal the DDL seed"]


def test_an_unmapped_author_fails_two_checks():
    r = _checks(unmapped=[{"raw_actor": "stranger"}])
    assert sorted(k for k, v in r.items() if not v) == ["--check-actors would exit 0", "no author string is unmapped"]


def test_any_change_of_history_fails():
    for key, val in (("logs", 1264), ("logs_md5", "c" * 32), ("events", 266), ("events_md5", "d" * 32)):
        assert not _checks(after=dict(FP, **{key: val}))["history up to the baseline ids is identical"], key


def test_verify_without_baseline_or_rc_is_a_usage_error():
    assert acc.main(["--verify"]) == acc.RC_USAGE
    assert acc.main(["--verify", "--baseline-file", "x.json"]) == acc.RC_USAGE
