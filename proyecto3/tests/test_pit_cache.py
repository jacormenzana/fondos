# proyecto3/tests/test_pit_cache.py
# -*- coding: utf-8 -*-
"""
Parquet cache for the PIT stages (proyecto3/src/pit_cache.py) -- FND-0159 d3 / FND-0189.

    python -m pytest proyecto3/tests/test_pit_cache.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_cache import ParquetCache, cached_frames, content_hash


def _frame(seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-31", periods=12, freq=pd.offsets.MonthEnd())
    return pd.DataFrame(rng.normal(size=(12, 3)), index=idx, columns=["A", "B", "C"])


# ---------------- content hash ----------------

def test_hash_is_stable_and_covers_every_row_not_just_the_last():
    a = _frame()
    assert content_hash(a, {"k": 1}) == content_hash(a.copy(), {"k": 1})
    old_edit = a.copy()
    old_edit.iloc[2, 1] += 1e-9                                  # a historical correction far from the last row
    assert content_hash(old_edit, {"k": 1}) != content_hash(a, {"k": 1})


def test_hash_changes_with_parameters_index_columns_and_version():
    a = _frame()
    base = content_hash(a, {"max_stale_days": 45}, "pit-1")
    assert content_hash(a, {"max_stale_days": 60}, "pit-1") != base
    assert content_hash(a, {"max_stale_days": 45}, "pit-2") != base
    assert content_hash(a.rename(columns={"A": "Z"}), {"max_stale_days": 45}, "pit-1") != base
    shifted = a.copy()
    shifted.index = shifted.index + pd.Timedelta(days=1)
    assert content_hash(shifted, {"max_stale_days": 45}, "pit-1") != base
    assert content_hash(a, {"x": 1, "y": 2}) == content_hash(a, {"y": 2, "x": 1})       # dict order irrelevant


# ---------------- cache behaviour ----------------

def test_roundtrip_hit_after_miss_and_values_equal(tmp_path):
    cache = ParquetCache(tmp_path)
    calls = []

    def compute():
        calls.append(1)
        return {"max_dd": _frame(1), "sharpe": _frame(2)}

    frames1, from_cache1, _ = cached_frames(cache, "risk", "k1", compute)
    frames2, from_cache2, _ = cached_frames(cache, "risk", "k1", compute)
    assert not from_cache1 and from_cache2 and len(calls) == 1
    assert (cache.hits, cache.misses) == (1, 1)
    for name in frames1:
        pd.testing.assert_frame_equal(frames1[name], frames2[name].rename_axis(None), check_freq=False)


def test_different_key_is_a_miss(tmp_path):
    cache = ParquetCache(tmp_path)
    cached_frames(cache, "risk", "k1", lambda: {"x": _frame()})
    _, from_cache, _ = cached_frames(cache, "risk", "k2", lambda: {"x": _frame()})
    assert not from_cache


def test_disabled_cache_never_reads_or_writes(tmp_path):
    cache = ParquetCache(tmp_path / "off", enabled=False)
    calls = []
    for _ in range(2):
        cached_frames(cache, "risk", "k", lambda: calls.append(1) or {"x": _frame()})
    assert len(calls) == 2 and not (tmp_path / "off").exists()


def test_missing_member_file_forces_recompute(tmp_path):
    cache = ParquetCache(tmp_path)
    cached_frames(cache, "risk", "k", lambda: {"a": _frame(), "b": _frame(1)})
    (tmp_path / "risk_k__b.parquet").unlink()
    calls = []
    _, from_cache, _ = cached_frames(cache, "risk", "k", lambda: calls.append(1) or {"a": _frame(), "b": _frame(1)})
    assert not from_cache and calls


def test_corrupt_file_is_treated_as_a_miss(tmp_path):
    cache = ParquetCache(tmp_path)
    cached_frames(cache, "risk", "k", lambda: {"a": _frame()})
    (tmp_path / "risk_k__a.parquet").write_bytes(b"not parquet")
    _, from_cache, _ = cached_frames(cache, "risk", "k", lambda: {"a": _frame()})
    assert not from_cache


def test_size_cap_purges_least_recently_used(tmp_path):
    import os, time
    cache = ParquetCache(tmp_path, max_bytes=10 ** 9)
    for i in range(4):
        cache.put(f"s{i}", _frame(i))
        os.utime(tmp_path / f"s{i}.parquet", (1000 + i, 1000 + i))      # s0 oldest ... s3 newest
    one = (tmp_path / "s0.parquet").stat().st_size
    cache.get("s0")                                                      # touch s0 -> now the MOST recent
    cache.max_bytes = one * 2 + 10
    removed = cache.purge()
    left = sorted(p.stem for p in tmp_path.glob("*.parquet"))
    assert removed == 2 and left == ["s0", "s3"]
    assert cache.size() <= cache.max_bytes


def test_clear_empties_the_cache(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.put("a", _frame())
    cache.clear()
    assert cache.size() == 0 and cache.get("a") is None
