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
    n_outliers: int = 0


def splice_check(primary: Mapping[str, float], fallback: Mapping[str, float], *, min_overlap: int = 3,
                 abs_tol: float | None = None, rel_tol: float | None = None, window: int = 24, min_share: float = 1.0) -> SpliceResult:
    """Do `primary` and `fallback` measure the same thing? Both map 'YYYY-MM-DD' (month start) -> value.

    Compared on the LAST `window` months they share. Needs >= min_overlap shared months and, for the shared months,
    |p - f| <= abs_tol (if given) and |p - f| / |p| <= rel_tol (if given). At least one tolerance is required: a splice
    with no stated tolerance would accept anything.

    min_share (default 1.0 = every month) is the fraction of shared months that must be within tolerance. Below 1.0 it admits ISOLATED
    outliers (a single revised or mistyped month, e.g. China M2 2017-12: 1 month of 236 differs by 0.8%, the other 235 agree to six digits)
    while a different measure, which deviates everywhere, still fails. The count of outliers is returned and should be printed.
    """
    if abs_tol is None and rel_tol is None:
        raise ValueError("splice_check needs abs_tol and/or rel_tol")
    if not 0.0 < min_share <= 1.0:
        raise ValueError("min_share must be in (0, 1]")
    shared = sorted(set(primary) & set(fallback))[-window:]
    if len(shared) < min_overlap:
        return SpliceResult(False, len(shared), None, None, f"overlap {len(shared)} < {min_overlap} months")
    abs_d = [abs(primary[m] - fallback[m]) for m in shared]
    rel_d = [abs(primary[m] - fallback[m]) / abs(primary[m]) if primary[m] else float("inf") for m in shared]
    max_abs, max_rel = max(abs_d), max(rel_d)
    outliers = sum(1 for a, r in zip(abs_d, rel_d) if (abs_tol is not None and a > abs_tol) or (rel_tol is not None and r > rel_tol))
    if outliers > int((1.0 - min_share) * len(shared) + 1e-9):
        if abs_tol is not None and max_abs > abs_tol:
            why = f"max |diff| {max_abs:.6g} > abs_tol {abs_tol}"
        else:
            why = f"max rel diff {max_rel:.4g} > rel_tol {rel_tol}"
        if min_share < 1.0:
            why += f" in {outliers} of {len(shared)} months (allowed {int((1.0 - min_share) * len(shared) + 1e-9)})"
        return SpliceResult(False, len(shared), max_abs, max_rel, why, outliers)
    return SpliceResult(True, len(shared), max_abs, max_rel, "ok", outliers)


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


def _month_start(p: str) -> str:
    """'YYYY-MM' -> 'YYYY-MM-01'; anything else (quarters, years, bad months) raises, so a layout change cannot load shifted dates."""
    if len(p) != 7 or p[4] != "-" or not (p[:4].isdigit() and p[5:].isdigit() and 1 <= int(p[5:]) <= 12):
        raise ValueError(f"unexpected monthly TIME_PERIOD {p!r}")
    return p + "-01"


def oecd_csv_series(text: str, *, area_col: str = "REF_AREA", where: Mapping[str, str] | None = None) -> dict[str, dict[str, float]]:
    """{area: {'YYYY-MM-01': value}} from an OECD SDMX 'csvfilewithlabels' response (one row per area x month).
    Rows without a value are skipped, and `where` keeps only the rows whose columns equal the given values (e.g. {"ADJUSTMENT": "N"}),
    so two variants of one series can never be mixed into a month."""
    import csv
    import io
    out: dict[str, dict[str, float]] = {}
    for r in csv.DictReader(io.StringIO(text)):
        if where and any(r.get(k) != v for k, v in where.items()):
            continue
        v, p = r.get("OBS_VALUE"), r.get("TIME_PERIOD", "")
        if v in (None, ""):
            continue
        out.setdefault(r[area_col], {})[_month_start(p)] = float(v)
    return out


def bis_csv_series(text: str) -> dict[str, float]:
    """{'YYYY-MM-01': value} from a BIS data-portal CSV (TIME_PERIOD, OBS_VALUE); rows without a value are skipped."""
    import csv
    import io
    return {_month_start(r["TIME_PERIOD"]): float(r["OBS_VALUE"]) for r in csv.DictReader(io.StringIO(text)) if r.get("OBS_VALUE")}


def boj_series(payload: Mapping, *, scale: float = 1.0) -> dict[str, float]:
    """{'YYYY-MM-01': value * scale} from ONE page of a Bank of Japan time-series API response (SURVEY_DATES as YYYYMM).
    Months the BoJ publishes as null are skipped, never zero-filled."""
    out: dict[str, float] = {}
    for res in payload.get("RESULTSET", []):
        vals = res["VALUES"]
        for t, v in zip(vals["SURVEY_DATES"], vals["VALUES"]):
            if v is None:
                continue
            s = str(t)
            out[_month_start(f"{s[:4]}-{s[4:]}")] = float(v) * scale
    return out


def monthly_gaps(values: Mapping[str, float]) -> list[str]:
    """Month starts missing between the first and the last key of `values` (a series that is allowed to replace another must have none)."""
    if not values:
        return []
    ms = sorted(values)
    y0, m0 = int(ms[0][:4]), int(ms[0][5:7])
    y1, m1 = int(ms[-1][:4]), int(ms[-1][5:7])
    out = []
    for k in range(y0 * 12 + m0 - 1, y1 * 12 + m1):
        d = f"{k // 12:04d}-{k % 12 + 1:02d}-01"
        if d not in values:
            out.append(d)
    return out


def pct_change(values: Mapping[str, float], months: int = 1) -> dict[str, float]:
    """{'date': 100 * (v / v[date - months] - 1)} for the dates whose month-start predecessor `months` earlier exists."""
    out: dict[str, float] = {}
    for d, v in values.items():
        y, m = int(d[:4]), int(d[5:7])
        k = y * 12 + (m - 1) - months
        prev = f"{k // 12:04d}-{k % 12 + 1:02d}-01"
        if prev in values and values[prev]:
            out[d] = 100.0 * (v / values[prev] - 1.0)
    return out


def rows_to_values(rows: list[dict], indicator: str, geography: str) -> dict[str, float]:
    """{'date': value} of the loader rows that belong to (indicator, geography)."""
    return {r["date"]: float(r["value"]) for r in rows if r["indicator"] == indicator and r["geography"] == geography}
