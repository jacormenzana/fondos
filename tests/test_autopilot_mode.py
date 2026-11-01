# tests/test_autopilot_mode.py
# -*- coding: utf-8 -*-
"""scripts/audit/autopilot_mode.py: the owner's command for the autopilot mode (Backlog v3.1). The summary, the lowest-of-three rule and the guards of the write, with the client faked: no database,
no network (R-7)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import autopilot_mode as cmd  # noqa: E402
from shared import backlog_client as bc  # noqa: E402
from shared import owner_vault as ov  # noqa: E402

REC = {"mode": "SHADOW", "decisions": 7, "contradicted": 0, "reopened_closures": 0}


@pytest.mark.parametrize("ceiling, stored, expected", [("ON", "ON", "ON"), ("ON", "SHADOW", "SHADOW"), ("SHADOW", "ON", "SHADOW"), ("ON", "OFF", "OFF"), ("OFF", "ON", "OFF"),
                                                      ("on", "ON", "OFF"), ("ON", "bogus", "SHADOW")])
def test_the_effective_mode_is_the_lowest_of_the_ceiling_and_the_stored_mode(ceiling, stored, expected):
    assert cmd.effective(ceiling, stored) == expected


def test_the_summary_shows_the_modes_the_evidence_and_what_each_mode_means():
    text = "\n".join(cmd.show_lines("ON", REC, 20, 2, 30))
    assert "ceiling ON" in text and "database mode SHADOW" in text and "effective SHADOW" in text and "7 SHADOW decision(s)" in text and "promotion at 20" in text and "moves nothing" in text
    on = "\n".join(cmd.show_lines("ON", {**REC, "mode": "ON"}, 20, 2, 30))
    assert "effective ON" in on and "it acts" in on
    off = "\n".join(cmd.show_lines("ON", {**REC, "mode": "OFF"}, 20, 2, 30))
    assert "only a person lifts it" in off


def test_the_tool_is_in_the_vault_manifest_with_the_owner_only_login():
    assert ov.OWNER_TOOLS["autopilot_mode.py"] == ("FONDOS_BACKLOG_PG_DSN_HUMAN",)


@pytest.fixture
def fake_client(monkeypatch):
    calls = {"set": []}
    monkeypatch.setattr(bc, "events_store_present", lambda **kw: True)
    monkeypatch.setattr(bc, "autopilot_track_record", lambda window_days=30, **kw: dict(REC))
    monkeypatch.setattr(bc, "dsn_for_actor", lambda actor: "postgresql://x")
    monkeypatch.setattr(bc, "current_actor", lambda: "human:owner")
    monkeypatch.setattr(bc, "require_live_write_cli", lambda dsn=None, **kw: None)
    monkeypatch.setattr(bc, "set_autopilot_mode", lambda mode, reason, **kw: calls["set"].append((mode, reason)) or 41)
    return calls


def test_showing_changes_nothing(fake_client, capsys):
    assert cmd.main([]) == cmd.RC_OK and fake_client["set"] == []
    assert "database mode SHADOW" in capsys.readouterr().out


def test_set_on_with_a_reason_appends_the_event_and_reports_the_effective_mode(fake_client, capsys):
    assert cmd.main(["--set", "ON", "--reason", "owner decision 2026-11-01: switch on"]) == cmd.RC_OK
    assert fake_client["set"] == [("ON", "owner decision 2026-11-01: switch on")]
    assert "SHADOW -> ON" in capsys.readouterr().out


def test_set_needs_a_reason_and_does_nothing_when_the_mode_is_already_that(fake_client):
    assert cmd.main(["--set", "ON"]) == cmd.RC_USAGE and cmd.main(["--set", "ON", "--reason", "short"]) == cmd.RC_USAGE
    assert cmd.main(["--set", "SHADOW", "--reason", "already in this mode, nothing to write"]) == cmd.RC_OK
    assert fake_client["set"] == []


def test_a_shell_that_is_not_a_console_or_a_refused_write_changes_nothing(fake_client, monkeypatch, capsys):
    monkeypatch.setattr(bc, "require_live_write_cli", lambda dsn=None, **kw: 1)                 # what the live-write guard returns without a typed confirmation
    assert cmd.main(["--set", "ON", "--reason", "owner decision, no confirmation given"]) == 1 and fake_client["set"] == []
    monkeypatch.setattr(bc, "require_live_write_cli", lambda dsn=None, **kw: None)

    def refuse(mode, reason, **kw):
        raise RuntimeError("only a person or the autopilot sets the mode")

    monkeypatch.setattr(bc, "set_autopilot_mode", refuse)
    assert cmd.main(["--set", "ON", "--reason", "owner decision, the database refuses"]) == cmd.RC_FAILED
    assert "REFUSED" in capsys.readouterr().err


def test_without_the_events_ddl_it_says_what_to_apply(monkeypatch, capsys):
    monkeypatch.setattr(bc, "events_store_present", lambda **kw: False)
    assert cmd.main([]) == cmd.RC_USAGE and "events store is not applied" in capsys.readouterr().err
