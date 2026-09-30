# proyecto3/tests/test_regime_semaforo.py
# -*- coding: utf-8 -*-
"""FND-0154: semaforo(n_last) multi-month confirmation, plus the per-indicator signal thresholds.

Pure: the classifier is built with __new__ (no DB) and fed a synthetic macro frame (R-7).
"""
from __future__ import annotations

import pandas as pd
import pytest

from proyecto3.src.regime_classifier import (
    RegimeClassifier,
    SEMAFORO_IPC_ACCEL,
    _semaforo_indicator_signals,
)


def _clf(spread):
    """Classifier with a stable regime history and only spread_hy varying."""
    idx = pd.date_range("2026-01-31", periods=len(spread), freq="ME")
    clf = RegimeClassifier.__new__(RegimeClassifier)
    clf._macro = pd.DataFrame({"spread_hy": spread}, index=idx)
    clf._historical_cache = pd.DataFrame({"regime": ["Expansion"] * len(spread)}, index=idx)
    return clf


def test_default_reads_only_the_latest_month():
    clf = _clf([3.0, 3.0, 5.0])          # amber spread (>4) only in the last month
    assert clf.semaforo().color == "Ambar"


def test_n_last_requires_the_signal_in_every_month():
    clf = _clf([3.0, 3.0, 5.0])
    sem = clf.semaforo(n_last=3)
    assert sem.color == "Verde" and sem.signals == []


def test_n_last_confirms_a_persistent_signal_at_its_weakest_severity():
    # red in the last month, only amber before: confirmed, but as amber
    clf = _clf([3.0, 5.0, 7.0])
    assert clf.semaforo(n_last=1).color == "Rojo"
    assert clf.semaforo(n_last=2).color == "Ambar"
    assert clf.semaforo(n_last=3).color == "Verde"   # month 1 is calm


def test_n_last_larger_than_history_does_not_crash():
    assert _clf([5.0]).semaforo(n_last=3).color in ("Verde", "Ambar")


def test_regime_change_is_an_event_not_windowed():
    clf = _clf([3.0, 3.0, 3.0])
    clf._historical_cache["regime"] = ["Expansion", "Expansion", "Contraccion"]
    assert clf.semaforo(n_last=3).color == "Rojo"


@pytest.mark.parametrize("accel,fires", [(SEMAFORO_IPC_ACCEL * 1.5, True), (SEMAFORO_IPC_ACCEL * 0.5, False)])
def test_ipc_acceleration_uses_the_constant_unscaled(accel, fires):
    # Regression: the call site used to compare against SEMAFORO_IPC_ACCEL * 2.
    idx = pd.date_range("2026-01-31", periods=2, freq="ME")
    filled = pd.DataFrame({"ipc_yoy_avg": [0.020, 0.020 + accel]}, index=idx)
    assert ("ipc" in _semaforo_indicator_signals(filled)) is fires
