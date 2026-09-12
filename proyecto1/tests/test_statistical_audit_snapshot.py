# proyecto1/tests/test_statistical_audit_snapshot.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/snapshot.py (build_population,
build_snapshot — funciones #1-#2, doc/reglas/AUDITORIA_ESTADISTICA.md §4).

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sqlite3
import sys

import pandas as pd
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.snapshot import (
    UniverseFilterMissingError,
    build_population,
    build_snapshot,
)


def _memory_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE fund_master (ISIN TEXT, In_Current_Universe INTEGER, "
        "Ongoing_Charge_Recurrent REAL)"
    )
    conn.executemany(
        "INSERT INTO fund_master VALUES (?, ?, ?)",
        [("LU1", 1, 0.015), ("LU2", 0, 0.020)],
    )
    conn.commit()
    return conn


class TestBuildPopulation:
    def test_raises_without_universe_filter(self):
        conn = _memory_conn()
        with pytest.raises(UniverseFilterMissingError):
            build_population(conn, "SELECT * FROM fund_master")

    def test_passes_with_universe_filter(self):
        conn = _memory_conn()
        df = build_population(conn, "SELECT * FROM fund_master WHERE In_Current_Universe=1")
        assert len(df) == 1
        assert df.iloc[0]["ISIN"] == "LU1"

    def test_bypass_flag_for_universe_less_tables(self):
        conn = _memory_conn()
        df = build_population(conn, "SELECT * FROM fund_master", require_universe_filter=False)
        assert len(df) == 2


def _timeseries_df():
    return pd.DataFrame([
        # ISIN A: two rows, latest 2026-08-31
        {"isin": "A", "metric": "vol_ann", "window": "rolling_1y", "real_flag": 0,
         "date": "2026-07-31", "value": 0.10},
        {"isin": "A", "metric": "vol_ann", "window": "rolling_1y", "real_flag": 0,
         "date": "2026-08-31", "value": 0.11},
        # ISIN B: stale — latest is 11 days behind A's slice max
        {"isin": "B", "metric": "vol_ann", "window": "rolling_1y", "real_flag": 0,
         "date": "2026-08-20", "value": 0.20},
    ])


class TestBuildSnapshot:
    def test_selects_true_latest_row_per_entity(self):
        r = build_snapshot(
            _timeseries_df(), entity_key="isin",
            slice_keys=["metric", "window", "real_flag"],
            date_column="date", tolerance_days=30,
        )
        a_row = r.eligible[r.eligible["isin"] == "A"].iloc[0]
        assert a_row["value"] == 0.11  # the later of A's two rows, not the first

    def test_holds_out_stale_entity_beyond_tolerance(self):
        r = build_snapshot(
            _timeseries_df(), entity_key="isin",
            slice_keys=["metric", "window", "real_flag"],
            date_column="date", tolerance_days=5,
        )
        assert list(r.eligible["isin"]) == ["A"]
        assert list(r.held_out["isin"]) == ["B"]

    def test_within_tolerance_both_eligible(self):
        r = build_snapshot(
            _timeseries_df(), entity_key="isin",
            slice_keys=["metric", "window", "real_flag"],
            date_column="date", tolerance_days=15,
        )
        assert set(r.eligible["isin"]) == {"A", "B"}
        assert r.held_out.empty

    def test_empty_input_returns_empty_result(self):
        empty = pd.DataFrame(columns=["isin", "metric", "window", "real_flag", "date", "value"])
        r = build_snapshot(
            empty, entity_key="isin", slice_keys=["metric", "window", "real_flag"],
            date_column="date",
        )
        assert r.eligible.empty
        assert r.held_out.empty
        assert r.date_spread_days == 0.0

    def test_no_slice_keys_is_a_global_latest(self):
        df = pd.DataFrame([
            {"isin": "A", "date": "2026-01-31", "value": 1.0},
            {"isin": "A", "date": "2026-02-28", "value": 2.0},
        ])
        r = build_snapshot(df, entity_key="isin", slice_keys=[], date_column="date")
        assert r.eligible.iloc[0]["value"] == 2.0
