# proyecto2/tests/pipeline/test_refresh_matviews_and_update_isin.py
# -*- coding: utf-8 -*-
"""_refresh_gold_matviews (FND-0077 hook) y _restrict_to_isins (nav_discovery update --isin).
Cumple R-7: sin importar pipeline.py de P1 ni core.io."""

import logging

import src.pipeline.run_pipeline as rp
from proyecto2.src.discovery.nav_discovery import _restrict_to_isins


class _Conn:
    def __init__(self, fail=False):
        self.fail, self.executed, self.commits, self.rollbacks = fail, [], 0, 0

    def execute(self, sql, params=()):
        self.executed.append(sql)
        if self.fail:
            raise RuntimeError("boom")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _log():
    return logging.getLogger("test_refresh_matviews")


def test_refresh_is_a_noop_on_sqlite(monkeypatch):
    monkeypatch.setattr(rp, "is_postgres_connection", lambda c: False)
    conn = _Conn()
    assert rp._refresh_gold_matviews(conn, _log()) is False
    assert conn.executed == []


def test_refresh_calls_the_function_once_and_commits(monkeypatch):
    monkeypatch.setattr(rp, "is_postgres_connection", lambda c: True)
    conn = _Conn()
    assert rp._refresh_gold_matviews(conn, _log()) is True
    assert conn.executed == ["SELECT control.refresh_gold_matviews()"] and conn.commits == 1


def test_refresh_failure_is_non_fatal_and_rolls_back(monkeypatch, caplog):
    monkeypatch.setattr(rp, "is_postgres_connection", lambda c: True)
    conn = _Conn(fail=True)
    with caplog.at_level(logging.WARNING):
        assert rp._refresh_gold_matviews(conn, _log()) is False
    assert conn.rollbacks == 1 and "non-fatal" in caplog.text


def test_restrict_to_isins():
    rows = [("AAA", 1), ("BBB", 2), ("CCC", 3)]
    assert _restrict_to_isins(rows, None) == rows
    assert _restrict_to_isins(rows, []) == rows
    assert _restrict_to_isins(rows, ["bbb"]) == [("BBB", 2)]
    assert _restrict_to_isins(rows, ["AAA", "ZZZ"]) == [("AAA", 1)]
