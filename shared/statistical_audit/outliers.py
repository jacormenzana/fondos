"""Function #7 (AUDITORIA_ESTADISTICA.md §4): detect_outliers.

IQR fence and MAD robust-z share the same degenerate-population failure mode:
when the population is zero-inflated (or otherwise concentrated), Q1==Q3==0
or MAD==0, and every non-zero value would report as an outlier — an artifact,
not a signal (AUDITORIA_ESTADISTICA.md §3, "zero%" / "MAD robust-z"). Both
guards return an explicit unavailable_reason rather than a silently empty
result, per the skills' Method Control "prefer evidence over magnitude".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

Method = Literal["IQR", "MAD_Z"]

_MAD_CONSTANT = 0.6745


@dataclass
class OutlierResult:
    method: str
    available: bool
    unavailable_reason: str | None
    flags: pd.DataFrame


def detect_outliers(
    series: pd.Series,
    method: Method = "IQR",
    *,
    iqr_multiplier: float = 1.5,
    mad_warn_z: float = 3.5,
    mad_extreme_z: float = 5.0,
    zero_pct_guard: float = 0.70,
    min_n: int = 8,
) -> OutlierResult:
    valid = series.dropna()
    n = len(valid)

    if n < min_n:
        return OutlierResult(method, False, "N_TOO_SMALL", pd.DataFrame())

    zero_pct = float((valid == 0).mean())
    if zero_pct >= zero_pct_guard:
        return OutlierResult(method, False, "ZERO_INFLATED", pd.DataFrame())

    median = float(valid.median())
    q1, q3 = float(valid.quantile(0.25)), float(valid.quantile(0.75))
    iqr = q3 - q1
    mad = float((valid - median).abs().median())

    if method == "IQR":
        if iqr == 0:
            return OutlierResult(method, False, "IQR_ZERO", pd.DataFrame())
        lower, upper = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
        mask = (valid < lower) | (valid > upper)
        flags = pd.DataFrame({
            "value": valid[mask],
            "q1": q1, "q3": q3, "iqr": iqr,
            "lower_fence": lower, "upper_fence": upper,
            "severity": "OUTLIER",
        })
        return OutlierResult(method, True, None, flags)

    if method == "MAD_Z":
        if mad == 0:
            return OutlierResult(method, False, "MAD_ZERO", pd.DataFrame())
        robust_z = _MAD_CONSTANT * (valid - median) / mad
        severity = pd.Series("OK", index=valid.index)
        severity[robust_z.abs() >= mad_warn_z] = "WARN"
        severity[robust_z.abs() >= mad_extreme_z] = "ALARM"
        mask = severity != "OK"
        flags = pd.DataFrame({
            "value": valid[mask],
            "median": median, "mad": mad,
            "robust_z": robust_z[mask],
            "severity": severity[mask],
        })
        return OutlierResult(method, True, None, flags)

    raise ValueError(f"Unknown method: {method!r}")
