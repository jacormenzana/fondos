# proyecto2/src/discovery/source_chain.py
# -*- coding: utf-8 -*-
"""
Source chain for macro series whose provider changed or stopped (FND-0256 euro-area HICP, FND-0293 JP / CN / M2 / CN rate).

Why: the Feb-2026 HICP overhaul discontinued the ECB `ICP` dataset, and OECD stopped the MEI feed FRED mirrored, so a series
froze at a past date while the provider's NEW feed carries the same measure. A fallback may only continue a series once it has
proved it is the SAME measure: `splice_check` compares both feeds on the months they share, and `extend_series` then emits rows
strictly AFTER the primary's last month (the primary always wins where it has data), tagged with the fallback's own source so
a downstream reader can tell. An unproven splice emits nothing -- a stale series is visible (TTL, FND-0256); a spliced wrong one is not.

Pure module: no HTTP, no DB, no pipeline imports (R-7). The loaders in macro_discovery fetch and call these.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SpliceResult:
    ok: bool
    n_overlap: int
    max_abs_diff: float | None
    max_rel_diff: float | None
    reason: str


def splice_check(primary: Mapping[str, float], fallback: Mapping[str, float], *, min_overlap: int = 3,
                 abs_tol: float | None = None, rel_tol: float | None = None, window: int = 24) -> SpliceResult:
    """Do `primary` and `fallback` measure the same thing? Both map 'YYYY-MM-DD' (month start) -> value.

    Compared on the LAST `window` months they share. Needs >= min_overlap shared months and, for every shared month,
    |p - f| <= abs_tol (if given) and |p - f| / |p| <= rel_tol (if given). At least one tolerance is required: a splice
    with no stated tolerance would accept anything.
    """
    if abs_tol is None and rel_tol is None:
        raise ValueError("splice_check needs abs_tol and/or rel_tol")
    shared = sorted(set(primary) & set(fallback))[-window:]
    if len(shared) < min_overlap:
        return SpliceResult(False, len(shared), None, None, f"overlap {len(shared)} < {min_overlap} months")
    abs_d = [abs(primary[m] - fallback[m]) for m in shared]
    rel_d = [abs(primary[m] - fallback[m]) / abs(primary[m]) if primary[m] else float("inf") for m in shared]
    max_abs, max_rel = max(abs_d), max(rel_d)
    if abs_tol is not None and max_abs > abs_tol:
        return SpliceResult(False, len(shared), max_abs, max_rel, f"max |diff| {max_abs:.6g} > abs_tol {abs_tol}")
    if rel_tol is not None and max_rel > rel_tol:
        return SpliceResult(False, len(shared), max_abs, max_rel, f"max rel diff {max_rel:.4g} > rel_tol {rel_tol}")
    return SpliceResult(True, len(shared), max_abs, max_rel, "ok")


def extend_series(primary: Mapping[str, float], fallback: Mapping[str, float], *, indicator: str, geography: str, unit: str,
                  source: str, scale: float = 1.0) -> list[dict]:
    """series_macro rows for the fallback months strictly AFTER the primary's last month (empty primary -> every fallback month).
    `scale` converts the fallback's unit to the primary's (e.g. 1e6 for millions -> simple units). The caller has already
    run splice_check; this function never validates."""
    last = max(primary) if primary else ""
    return [{"date": d, "indicator": indicator, "geography": geography, "value": float(v) * scale, "unit": unit, "source": source}
            for d, v in sorted(fallback.items()) if d > last]


def jsonstat_series(data: Mapping, *, time_dim: str = "time") -> dict[str, float]:
    """{'YYYY-MM-01': value} from an Eurostat JSON-stat response whose non-time dimensions all have size 1 (the caller filters
    unit/geo/item in the request). Raises ValueError when a non-time dimension has several categories, because the flat
    value index would then mix series silently."""
    ids, sizes = data.get("id", []), data.get("size", [])
    extra = [i for i, s in zip(ids, sizes) if i != time_dim and s != 1]
    if extra:
        raise ValueError(f"JSON-stat response has several categories in {extra}: filter the request")
    periods = list(data["dimension"][time_dim]["category"]["index"])
    values = data.get("value", {})
    out: dict[str, float] = {}
    for idx, period in enumerate(periods):
        v = values.get(str(idx))
        if v is None:
            continue
        out[period + "-01" if len(period) == 7 else period] = float(v)
    return out


def rows_to_values(rows: list[dict], indicator: str, geography: str) -> dict[str, float]:
    """{'date': value} of the loader rows that belong to (indicator, geography)."""
    return {r["date"]: float(r["value"]) for r in rows if r["indicator"] == indicator and r["geography"] == geography}
