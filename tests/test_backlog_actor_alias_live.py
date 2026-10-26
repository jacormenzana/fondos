# tests/test_backlog_actor_alias_live.py
# -*- coding: utf-8 -*-
"""Live coverage guard for FND-0330: every author string in the live backlog has a canonical identity.

Read-only against the real `gestion` database; SKIPPED (never failed) when FONDOS_BACKLOG_PG_DSN is unset, the server is unreachable or
the alias store has not been applied yet -- so CI and a cold WSL cannot turn it red, but a developer's run after the owner applied the
phase fails the moment a new raw spelling (a new agent name, a script's login) has no alias.

shared/config.py never loads the repo .env under pytest (a developer .env must not redirect tests), so this guard runs only when
FONDOS_BACKLOG_PG_DSN is exported in the shell. The same check without that step: `python -m shared.backlog_client --check-actors`
(loads .env, exit 1 on a missing alias or an unapplied store).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config  # noqa: E402,F401  (loads the repo .env)
from shared import backlog_client as bc  # noqa: E402


def test_every_live_author_string_has_an_alias():
    try:
        present = bc.actor_store_present()
    except Exception as exc:                                     # no DSN / no driver / server down
        pytest.skip(f"live backlog not reachable ({type(exc).__name__}); pytest does not load .env: export FONDOS_BACKLOG_PG_DSN to run this guard")
    if not present:
        pytest.skip("backlog.actor_alias not applied yet (owner: scripts/ops/migrate_backlog_v2.py --phase alias --apply)")
    missing = bc.unmapped_actors()
    assert not missing, f"author strings with no alias in backlog.actor_alias: {[m['raw_actor'] for m in missing]}"
