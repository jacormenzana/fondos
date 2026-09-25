# proyecto1/tests/test_statistical_audit_runner_dialect.py
# -*- coding: utf-8 -*-
"""Helpers de dialecto de scripts/audit/run_statistical_audit.py (puerto a Postgres).
Cumple R-7: sin importar pipeline.py ni core.io."""

import datetime as dt
import importlib.util
import os
import sqlite3
import sys

import pandas as pd

_ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit", os.path.join(_ROOT_DIR, "scripts", "audit", "run_statistical_audit.py"))
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _as_postgres(monkeypatch):
    monkeypatch.setattr(runner, "is_postgres_connection", lambda conn: True)


def test_sql_sqlite_keeps_qmark_and_window():
    conn = sqlite3.connect(":memory:")
    out = runner._sql(conn, "SELECT 1 WHERE metric = ? AND {window} = ?")
    assert out == "SELECT 1 WHERE metric = ? AND window = ?"


def test_sql_postgres_uses_percent_s_and_window_label(monkeypatch):
    _as_postgres(monkeypatch)
    out = runner._sql(object(), "SELECT 1 WHERE metric = ? AND {window} = ?")
    assert out == "SELECT 1 WHERE metric = %s AND window_label = %s"


def test_timeseries_queries_never_leak_bare_window_into_postgres(monkeypatch):
    _as_postgres(monkeypatch)
    for q in (runner._TS_LATEST_QUERY, runner._TS_SERIES_SUMMARY_QUERY):
        adapted = runner._sql(object(), q)
        assert "{window}" not in adapted and "?" not in adapted
        assert "window_label" in adapted
        assert " window " not in adapted.replace("window_label", "")


def test_restore_case_maps_lowercased_postgres_columns_back():
    df = pd.DataFrame(columns=["isin", "ongoing_charge_recurrent", "extra"])
    out = runner._restore_case(df, ("ISIN", "Ongoing_Charge_Recurrent"))
    assert list(out.columns) == ["ISIN", "Ongoing_Charge_Recurrent", "extra"]


def test_restore_case_is_identity_on_sqlite_spelling():
    df = pd.DataFrame(columns=list(runner._P2_METRICS_COLS))
    assert list(runner._restore_case(df, runner._P2_METRICS_COLS).columns) == list(runner._P2_METRICS_COLS)


def test_month_span_accepts_date_objects_and_strings():
    assert runner._month_span(dt.date(2024, 1, 31), dt.date(2024, 3, 31)) == 3
    assert runner._month_span("2024-01-31", "2024-03-31") == 3
