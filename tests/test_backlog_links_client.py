# tests/test_backlog_links_client.py
# -*- coding: utf-8 -*-
"""Relationship API of shared/backlog_client.py (list_tickets / list_logs / list_links / record_link_event(s)): pure checks.

No database and no pipeline import (R-7). The behaviour against the real DDL is in test_backlog_links_client_pg.py.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from shared import backlog_client as bc

_DDL = (Path(__file__).resolve().parents[1] / "db" / "pg_backlog" / "20_links.sql").read_text(encoding="utf-8")


def _check_values(column: str) -> tuple:
    m = re.search(rf"{column}\s+TEXT NOT NULL\s+CHECK \({column} IN \(([^)]*)\)\)", _DDL)
    assert m, f"CHECK list of {column} not found in 20_links.sql"
    return tuple(v.strip().strip("'") for v in m.group(1).split(","))


def test_link_vocabulary_is_the_ddl_check_list():
    # one vocabulary (P#11): the Python constants are exactly the CHECK lists of the table
    assert bc.LINK_TYPES == _check_values("link_type")
    assert bc.LINK_STATUSES == _check_values("status")


@pytest.mark.parametrize("call", [
    lambda: bc.list_tickets(),
    lambda: bc.list_logs(),
    lambda: bc.list_links(),
    lambda: bc.record_link_event(from_id="FND-1", to_id="FND-2", link_type="RELATED", status="PROPOSED",
                                 evidence="x", username="u"),
], ids=["list_tickets", "list_logs", "list_links", "record_link_event"])
def test_fails_loud_without_a_dsn(monkeypatch, call):
    # unlike report_incident(): an unreachable backlog must never read as "no tickets / no links"
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    with pytest.raises(bc.BacklogUnavailable):
        call()


_GOOD = {"from_id": "FND-3", "to_id": "FND-4", "link_type": "OVERLAPS", "status": "PROPOSED", "evidence": "x", "username": "u"}


@pytest.mark.parametrize("bad", [
    dict(_GOOD, link_type="BLOCKS"),
    dict(_GOOD, status="MAYBE"),
    dict(_GOOD, to_id="FND-3"),
    dict(_GOOD, evidence="   "),
    dict(_GOOD, username=""),
], ids=["type", "status", "self-link", "evidence", "username"])
def test_events_are_validated_before_any_connection(monkeypatch, bad):
    # ValueError (not BacklogUnavailable) with no DSN proves the batch is rejected before a connection is attempted
    monkeypatch.delenv("FONDOS_BACKLOG_PG_DSN", raising=False)
    with pytest.raises(ValueError):
        bc.record_link_events([_GOOD, bad])


def test_argument_validation():
    with pytest.raises(ValueError):
        bc.list_tickets(closed_since=datetime(2026, 1, 1))          # only meaningful with open_only=True
    with pytest.raises(ValueError):
        bc.list_links(status="MAYBE")
