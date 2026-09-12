"""Functions #3-#6 (AUDITORIA_ESTADISTICA.md §4):
profile_coverage, profile_location, profile_moments, profile_mass_points.

Fisher excess-kurtosis convention throughout (normal = 0) via pandas'
.skew()/.kurt() — matches AUDITORIA_ESTADISTICA.md §3 and avoids the
scipy dependency (installed but unused elsewhere in this repo).
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

_LOCATION_QUANTILES: dict[str, float] = {
    "min": 0.0, "p05": 0.05, "p25": 0.25, "p50": 0.50, "p75": 0.75, "p95": 0.95, "max": 1.0,
}


def profile_coverage(series: pd.Series, n_expected: int | None = None) -> dict[str, Any]:
    n_total = int(len(series))
    n_valid = int(series.notna().sum())
    n_null = n_total - n_valid
    return {
        "n_total": n_total,
        "n_valid": n_valid,
        "n_null": n_null,
        "null_pct": (n_null / n_total) if n_total else math.nan,
        "n_expected": n_expected,
        "coverage_pct": (n_valid / n_expected) if n_expected else math.nan,
    }


def profile_location(series: pd.Series) -> dict[str, float]:
    valid = series.dropna()
    if valid.empty:
        return {k: math.nan for k in _LOCATION_QUANTILES}
    return {k: float(valid.quantile(q)) for k, q in _LOCATION_QUANTILES.items()}


def profile_moments(
    series: pd.Series,
    min_n: int = 30,
    cv_epsilon: float = 1e-6,
) -> dict[str, Any]:
    valid = series.dropna()
    n = int(len(valid))
    if n == 0:
        return {
            "n": 0, "mean": math.nan, "sd": math.nan, "cv": math.nan,
            "cv_status": "NO_DATA", "skew": math.nan, "kurtosis": math.nan,
            "shape_status": "NO_DATA",
        }

    mean = float(valid.mean())
    sd = float(valid.std(ddof=1)) if n > 1 else 0.0

    if abs(mean) < cv_epsilon:
        cv, cv_status = math.nan, "MEAN_NEAR_ZERO"
    else:
        cv, cv_status = sd / mean, "OK"

    if n < min_n:
        skew, kurtosis, shape_status = math.nan, math.nan, "N_TOO_SMALL"
    else:
        skew = float(valid.skew())
        kurtosis = float(valid.kurt())
        shape_status = "OK"

    return {
        "n": n, "mean": mean, "sd": sd, "cv": cv, "cv_status": cv_status,
        "skew": skew, "kurtosis": kurtosis, "shape_status": shape_status,
    }


def profile_mass_points(
    series: pd.Series,
    rounding_digits: int | None = None,
    dominant_threshold: float = 0.40,
    zero_inflated_threshold: float = 0.70,
    top_k: int = 5,
) -> dict[str, Any]:
    valid = series.dropna()
    n = int(len(valid))
    if n == 0:
        return {
            "n": 0, "zero_pct": math.nan, "dominant_value": None,
            "dominant_pct": math.nan, "top_k": [], "mass_class": "NO_DATA",
        }

    zero_pct = float((valid == 0).mean())
    rounded = valid.round(rounding_digits) if rounding_digits is not None else valid
    counts = rounded.value_counts()
    dominant_value = counts.index[0]
    dominant_pct = float(counts.iloc[0] / n)
    top_k_list = [(v, float(c / n)) for v, c in counts.head(top_k).items()]

    if zero_pct >= zero_inflated_threshold:
        mass_class = "ZERO_INFLATED"
    elif dominant_pct >= dominant_threshold:
        mass_class = "TEMPLATE_OR_DEFAULT"
    else:
        mass_class = "NORMAL"

    return {
        "n": n, "zero_pct": zero_pct, "dominant_value": dominant_value,
        "dominant_pct": dominant_pct, "top_k": top_k_list, "mass_class": mass_class,
    }
