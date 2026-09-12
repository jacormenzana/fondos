# proyecto1/tests/test_statistical_audit_timeseries.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/timeseries.py
(check_timeseries_integrity — funcion #11, doc/reglas/AUDITORIA_ESTADISTICA.md
§4). Los huecos solo se evaluan cuando el llamador aporta el calendario real
(expected_dates); sin el, la funcion no fabrica huecos falsos.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.timeseries import check_timeseries_integrity


def test_duplicate_dates_detected():
    df = pd.DataFrame({
        "isin": ["A", "A", "B"],
        "date": ["2026-01-31", "2026-01-31", "2026-01-31"],
        "value": [1.0, 1.1, 2.0],
    })
    r = check_timeseries_integrity(df, entity_keys=["isin"], date_column="date")
    assert len(r.duplicates) == 2  # both A rows, not the lone B row
    assert r.n_series == 2


def test_no_gaps_reported_without_expected_dates():
    df = pd.DataFrame({
        "isin": ["A", "A"],
        "date": ["2026-01-31", "2026-03-31"],  # a real gap, but undeclared
        "value": [1.0, 1.2],
    })
    r = check_timeseries_integrity(df, entity_keys=["isin"], date_column="date")
    assert r.gaps.empty  # never fabricate a gap from an assumed calendar


def test_gaps_detected_against_supplied_calendar():
    df = pd.DataFrame({
        "isin": ["A", "A"],
        "date": ["2026-01-31", "2026-03-31"],
        "value": [1.0, 1.2],
    })
    expected = pd.DatetimeIndex(["2026-01-31", "2026-02-28", "2026-03-31"])
    r = check_timeseries_integrity(
        df, entity_keys=["isin"], date_column="date", expected_dates=expected,
    )
    assert len(r.gaps) == 1
    assert r.gaps.iloc[0]["date"] == pd.Timestamp("2026-02-28")


def test_per_entity_calendar_mapping():
    df = pd.DataFrame({
        "isin": ["A", "B"],
        "date": ["2026-01-31", "2026-01-31"],
        "value": [1.0, 2.0],
    })
    expected = {
        ("A",): pd.DatetimeIndex(["2026-01-31", "2026-02-28"]),
        ("B",): pd.DatetimeIndex(["2026-01-31"]),
    }
    r = check_timeseries_integrity(
        df, entity_keys=["isin"], date_column="date", expected_dates=expected,
    )
    assert len(r.gaps) == 1
    assert r.gaps.iloc[0]["isin"] == "A"


def test_empty_frame_handled():
    df = pd.DataFrame(columns=["isin", "date", "value"])
    r = check_timeseries_integrity(df, entity_keys=["isin"], date_column="date")
    assert r.n_series == 0
    assert r.duplicates.empty
    assert r.gaps.empty
