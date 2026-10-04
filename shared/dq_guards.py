# shared/dq_guards.py
# -*- coding: utf-8 -*-
"""
Generic numerical-sanity guards for regression models (FND-0208). No project imports (R-7).

    scaled_condition_number   Belsley-Kuh-Welsch condition index of a design matrix
    most_collinear_column     which column drives the near-dependency (never the intercept)
    bound_or_none             post-calculation circuit breaker for one coefficient

Why the BKW index and not np.linalg.cond(X): columns are scaled to unit length first, so the result does not
depend on the units of each regressor (raw YoY factors live on very different scales), and they are NOT centred,
so a (near-)constant regressor shows up as collinear with the intercept column -- the FND-0176 failure mode.
Reference level: index > 30 is "severe" (Belsley, Kuh & Welsch 1980).
"""

import numpy as np


def _unit_scaled(X) -> np.ndarray:
    """Columns scaled to unit Euclidean length; a zero column stays zero (its index is then infinite)."""
    A = np.asarray(X, dtype=float)
    norms = np.linalg.norm(A, axis=0)
    safe = np.where(norms > 0, norms, 1.0)
    return A / safe


def scaled_condition_number(X) -> float:
    """BKW condition index (sigma_max / sigma_min of the unit-scaled design). inf if singular or non-finite."""
    A = np.asarray(X, dtype=float)
    if A.ndim != 2 or A.shape[1] == 0 or not np.all(np.isfinite(A)):
        return float("inf")
    s = np.linalg.svd(_unit_scaled(A), compute_uv=False)
    if s[-1] <= 0 or not np.isfinite(s[-1]):
        return float("inf")
    return float(s[0] / s[-1])


def most_collinear_column(X, skip: tuple = (0,)) -> int:
    """Index of the column with the largest loading on the smallest singular direction, excluding `skip`
    (the intercept by default). Raises ValueError if every column is skipped."""
    A = _unit_scaled(X)
    candidates = [j for j in range(A.shape[1]) if j not in skip]
    if not candidates:
        raise ValueError("no droppable column")
    _, _, vt = np.linalg.svd(A, full_matrices=False)
    loadings = np.abs(vt[-1])
    return max(candidates, key=lambda j: loadings[j])


def bound_or_none(value, limit: float):
    """Return (value, False) if finite and |value| <= limit, else (None, True)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, True
    if not np.isfinite(v) or abs(v) > limit:
        return None, True
    return v, False
