# proyecto2/tests/utils/test_fingerprint.py
# -*- coding: utf-8 -*-
"""
Tests for src/utils/fingerprint.compute_input_hash.

R-7: these tests import ONLY the fingerprint module — no pipeline.py, no core.io,
no DB connection required. Run from the repo root or proyecto2/ as:

    python -m pytest proyecto2/tests/utils/test_fingerprint.py -v

or:
    cd proyecto2
    python -m pytest tests/utils/test_fingerprint.py -v
"""

import sys
from pathlib import Path

# Ensure proyecto2/src is importable regardless of cwd
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd
import pytest

from utils.fingerprint import compute_input_hash  # noqa: E402


# ============================================================
# Fixtures
# ============================================================

def _nav(n: int = 60, last_val: float = 100.0) -> pd.DataFrame:
    """Synthetic monthly NAV DataFrame (column contract: 'date', 'nav')."""
    dates = pd.date_range("2018-01-31", periods=n, freq="ME")
    vals  = [last_val - (n - 1 - i) * 0.1 for i in range(n)]
    return pd.DataFrame({"date": dates, "nav": vals})


def _ipc(n: int = 60) -> pd.DataFrame:
    """Synthetic monthly IPC DataFrame (column contract: 'date', 'ipc_index')."""
    dates = pd.date_range("2018-01-31", periods=n, freq="ME")
    vals  = [100.0 + i * 0.25 for i in range(n)]
    return pd.DataFrame({"date": dates, "ipc_index": vals})


MV   = "v1"
CALC = "20260727"


# ============================================================
# Hash stability
# ============================================================

class TestHashStability:
    """Same inputs → identical hash (determinism)."""

    def test_stable_on_identical_call(self):
        nav = _nav()
        ipc = _ipc()
        h1  = compute_input_hash(nav, ipc, MV, CALC)
        h2  = compute_input_hash(nav, ipc, MV, CALC)
        assert h1 == h2, "Hash must be deterministic for identical inputs"

    def test_stable_on_dataframe_copy(self):
        """A copy of the DataFrames must yield the same hash."""
        nav = _nav()
        ipc = _ipc()
        h1  = compute_input_hash(nav, ipc, MV, CALC)
        h2  = compute_input_hash(nav.copy(), ipc.copy(), MV, CALC)
        assert h1 == h2

    def test_returns_40_char_hex(self):
        """SHA-1 digest is 40 lowercase hex characters."""
        h = compute_input_hash(_nav(), _ipc(), MV, CALC)
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)


# ============================================================
# Hash sensitivity — NAV changes
# ============================================================

class TestHashSensitivityNav:
    """Any meaningful change to NAV produces a different hash."""

    def test_changes_on_new_nav_row(self):
        """Adding one more month to NAV must flip the hash."""
        nav_60 = _nav(60)
        nav_61 = _nav(61)
        h60    = compute_input_hash(nav_60, _ipc(), MV, CALC)
        h61    = compute_input_hash(nav_61, _ipc(), MV, CALC)
        assert h60 != h61, "Extra NAV row must change hash"

    def test_changes_on_corrected_last_value(self):
        """Correcting the latest NAV value (data quality fix) must flip the hash."""
        nav_orig   = _nav(60, last_val=100.0)
        nav_fixed  = _nav(60, last_val=101.5)
        h_orig     = compute_input_hash(nav_orig,  _ipc(), MV, CALC)
        h_fixed    = compute_input_hash(nav_fixed, _ipc(), MV, CALC)
        assert h_orig != h_fixed, "Corrected last NAV must change hash"

    def test_changes_on_different_max_date(self):
        """A newer max date (later data vintage) must flip the hash."""
        nav_old = _nav(60)
        nav_new = pd.concat(
            [nav_old, pd.DataFrame({"date": [nav_old["date"].max() + pd.DateOffset(months=1)],
                                    "nav": [nav_old["nav"].iloc[-1] * 1.01]})],
            ignore_index=True,
        )
        h_old = compute_input_hash(nav_old, _ipc(), MV, CALC)
        h_new = compute_input_hash(nav_new, _ipc(), MV, CALC)
        assert h_old != h_new

    def test_changes_on_empty_nav(self):
        """Empty NAV produces a distinct hash (EMPTY sentinel)."""
        h_full  = compute_input_hash(_nav(), _ipc(), MV, CALC)
        h_empty = compute_input_hash(pd.DataFrame({"date": [], "nav": []}),
                                     _ipc(), MV, CALC)
        assert h_full != h_empty

    def test_none_nav_accepted(self):
        """None NAV is allowed and produces a stable hash."""
        h1 = compute_input_hash(None, _ipc(), MV, CALC)
        h2 = compute_input_hash(None, _ipc(), MV, CALC)
        assert h1 == h2
        assert len(h1) == 40


# ============================================================
# Hash sensitivity — IPC changes
# ============================================================

class TestHashSensitivityIpc:
    """IPC updates must flip the hash."""

    def test_changes_on_new_ipc_row(self):
        """Adding one more month of IPC must flip the hash."""
        ipc_60 = _ipc(60)
        ipc_61 = _ipc(61)
        h60    = compute_input_hash(_nav(), ipc_60, MV, CALC)
        h61    = compute_input_hash(_nav(), ipc_61, MV, CALC)
        assert h60 != h61

    def test_changes_on_none_vs_ipc(self):
        """None IPC vs real IPC must produce different hashes."""
        h_with    = compute_input_hash(_nav(), _ipc(), MV, CALC)
        h_without = compute_input_hash(_nav(), None,   MV, CALC)
        assert h_with != h_without

    def test_none_ipc_stable(self):
        """None IPC must hash stably."""
        h1 = compute_input_hash(_nav(), None, MV, CALC)
        h2 = compute_input_hash(_nav(), None, MV, CALC)
        assert h1 == h2


# ============================================================
# Hash sensitivity — version bumps
# ============================================================

class TestHashSensitivityVersions:
    """Bumping metric_version or calc_version forces a cache miss."""

    def test_changes_on_metric_version_bump(self):
        h_v1 = compute_input_hash(_nav(), _ipc(), "v1", CALC)
        h_v2 = compute_input_hash(_nav(), _ipc(), "v2", CALC)
        assert h_v1 != h_v2, "metric_version bump must change hash"

    def test_changes_on_calc_version_bump(self):
        h_old = compute_input_hash(_nav(), _ipc(), MV, "20260727")
        h_new = compute_input_hash(_nav(), _ipc(), MV, "20260801")
        assert h_old != h_new, "calc_version bump must change hash"

    def test_all_versions_independent(self):
        """Each component of the version tuple contributes independently."""
        base = compute_input_hash(_nav(), _ipc(), "v1", "A")
        mv_  = compute_input_hash(_nav(), _ipc(), "v2", "A")
        cv_  = compute_input_hash(_nav(), _ipc(), "v1", "B")
        assert base != mv_
        assert base != cv_
        assert mv_  != cv_


# ============================================================
# Empty / degenerate inputs
# ============================================================

class TestDegenerateInputs:
    """Boundary conditions that must not raise exceptions."""

    def test_both_none_is_stable(self):
        h1 = compute_input_hash(None, None, MV, CALC)
        h2 = compute_input_hash(None, None, MV, CALC)
        assert h1 == h2
        assert len(h1) == 40

    def test_empty_nav_empty_ipc(self):
        nav_e = pd.DataFrame({"date": [], "nav": []})
        ipc_e = pd.DataFrame({"date": [], "ipc_index": []})
        h     = compute_input_hash(nav_e, ipc_e, MV, CALC)
        assert len(h) == 40

    def test_single_row_nav(self):
        nav = pd.DataFrame({
            "date": [pd.Timestamp("2024-01-31")],
            "nav": [100.0],
        })
        h = compute_input_hash(nav, None, MV, CALC)
        assert len(h) == 40

    def test_empty_string_versions_accepted(self):
        """Degenerate version strings should not raise."""
        h = compute_input_hash(_nav(), _ipc(), "", "")
        assert len(h) == 40
