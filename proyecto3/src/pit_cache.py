# proyecto3/src/pit_cache.py
# -*- coding: utf-8 -*-
"""
Parquet cache for the heavy point-in-time stages of the P3 backtester -- FND-0159 d3 / FND-0189 (N3).

The PIT metric frames (expanding risk metrics, peer metrics, short gates) cost minutes to hours on the full
universe and only change when their inputs change, so each stage is cached as parquet files keyed by a
CONTENT hash of everything that determines it: the full input frames (every row, not just the last date --
P2's utils/fingerprint.py only hashes each fund's last date/row-count/last value, which would miss a
historical NAV correction and silently serve stale history), the parameters, and CODE_VERSION (bump it when a
PIT calculation changes). No DB table is involved (runtime code never issues DDL).

  * `content_hash(*parts)`: SHA-1 over DataFrames/Series (index + values + columns), dicts, scalars.
  * `ParquetCache(directory, max_bytes, enabled)`: size-capped (oldest-access purge) and switchable
    (enabled=False is the --no-cache behaviour: nothing read, nothing written).
  * `cached_frames(cache, stage, key, compute)`: dict of DataFrames from cache or computed and stored.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CODE_VERSION = "pit-1"                       # bump when a PIT calculation changes (forces a cache miss)
DEFAULT_MAX_BYTES = 2 * 1024 ** 3            # 2 GiB


def content_hash(*parts) -> str:
    """SHA-1 of the full content of DataFrames / Series / dicts / scalars, order-sensitive."""
    h = hashlib.sha1()
    for part in parts:
        if isinstance(part, (pd.DataFrame, pd.Series)):
            frame = part.to_frame() if isinstance(part, pd.Series) else part
            h.update(b"F")
            h.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
            h.update(json.dumps([str(c) for c in frame.columns]).encode())
            h.update(str(frame.shape).encode())
        elif isinstance(part, dict):
            h.update(b"D")
            h.update(json.dumps(part, sort_keys=True, default=str).encode())
        elif isinstance(part, (np.ndarray, list, tuple)):
            h.update(b"A")
            h.update(json.dumps([str(x) for x in np.asarray(part).ravel().tolist()]).encode())
        else:
            h.update(b"S")
            h.update(repr(part).encode())
    return h.hexdigest()


class ParquetCache:
    def __init__(self, directory, max_bytes: int = DEFAULT_MAX_BYTES, enabled: bool = True):
        self.dir = Path(directory)
        self.max_bytes = max_bytes
        self.enabled = enabled
        self.hits = 0
        self.misses = 0
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.parquet"

    def get(self, name: str) -> "pd.DataFrame | None":
        if not self.enabled:
            return None
        p = self._path(name)
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p)
        except Exception:                                    # corrupt/partial file: treat as a miss
            logger.warning("pit cache: unreadable %s -- recomputing", p.name)
            return None
        os.utime(p, None)                                    # LRU: mark as recently used
        return df

    def put(self, name: str, df: pd.DataFrame) -> None:
        if not self.enabled:
            return
        p = self._path(name)
        tmp = p.with_suffix(".tmp")
        df.to_parquet(tmp)
        os.replace(tmp, p)                                   # atomic: never leave a half-written entry
        self.purge()

    def size(self) -> int:
        return sum(f.stat().st_size for f in self.dir.glob("*.parquet")) if self.enabled else 0

    def purge(self) -> int:
        """Delete least-recently-used entries until the cache is within max_bytes. Returns files removed."""
        if not self.enabled:
            return 0
        files = sorted(self.dir.glob("*.parquet"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        removed = 0
        while files and total > self.max_bytes:
            victim = files.pop(0)
            total -= victim.stat().st_size
            victim.unlink()
            removed += 1
        if removed:
            logger.info("pit cache: purged %d least-recently-used files (cap %d MiB)", removed, self.max_bytes // 1024 ** 2)
        return removed

    def clear(self) -> None:
        if self.enabled:
            for f in self.dir.glob("*.parquet"):
                f.unlink()


def _frame_to_store(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c) for c in out.columns]
    out.index.name = "idx"
    return out


def cached_frames(cache: ParquetCache, stage: str, key: str, compute) -> "tuple[dict, bool, float]":
    """dict[name -> DataFrame] for (stage, key): from the cache when every member is present, else
    `compute()` (returning that dict) and stored. Returns (frames, from_cache, seconds)."""
    t0 = time.perf_counter()
    names_path = f"{stage}_{key}__names"
    names_df = cache.get(names_path)
    if names_df is not None:
        frames = {}
        for n in names_df["name"]:
            f = cache.get(f"{stage}_{key}__{n}")
            if f is None:
                frames = None
                break
            frames[n] = f.rename_axis(None)
        if frames is not None:
            cache.hits += 1
            return frames, True, time.perf_counter() - t0
    cache.misses += 1
    frames = compute()
    if cache.enabled:
        for n, f in frames.items():
            cache.put(f"{stage}_{key}__{n}", _frame_to_store(f))
        cache.put(names_path, pd.DataFrame({"name": list(frames)}))
    return frames, False, time.perf_counter() - t0
