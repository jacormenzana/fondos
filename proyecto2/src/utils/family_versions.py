# proyecto2/src/utils/family_versions.py
# -*- coding: utf-8 -*-
"""
Per-metric-family calculation versions and the per-fund "which families must run" decision (FND-0236).

Problem (2026-10-05 cycle): CALC_VERSION is global, so a macro-only change (bundle FND-0196/0226/0200/0202)
recomputed all nine families of every fund -- 16,158 s -- when the macro pass alone takes about 517 s.

Model
-----
* CALC_VERSION stays the GLOBAL epoch: bump it only for a change that affects every family.
* FAMILY_CALC_OVERRIDES[family] is the per-family epoch: bump only that family's entry when only that family's
  logic changed. A family's *token* is CALC_VERSION, plus "." + its override when it has one, so
  - a global bump changes every token (everything recomputes, as today), and
  - an override bump changes one token (one family recomputes).
  A family without an override has token == CALC_VERSION, i.e. the value already stored in algorithm_version.
* A family's input hash = data fingerprint (NAV + IPC, utils.fingerprint.data_fingerprint) + metric version +
  the family's token + the P2 bundle flags that belong to that family (FLAG_FAMILIES). Flipping a bundle flag
  therefore stales one family, not all of them.
* decide_fund() turns stored vs current family hashes into the set of families to run for one fund.

Dormant: nothing here is read by the pipeline until shared.config.FAMILY_VERSIONING_ENABLED is switched on
(wired in run_pipeline). Pure module: no pipeline, DB or core.io imports (R-7).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping

# The nine families selectable with --metrics (single definition; run_pipeline imports it).
ALL_FAMILIES: tuple = (
    "risk", "macro", "momentum", "capture", "persistence", "fx", "regime", "rolling", "short",
)

# Per-family epoch overrides. EMPTY by design: today every family follows CALC_VERSION.
# Example (a macro-only change): FAMILY_CALC_OVERRIDES = {"macro": "20261101"}.
FAMILY_CALC_OVERRIDES: dict = {}

# shared.config.P2_BUNDLE_FLAGS -> the one family each flag changes. A test asserts every bundle flag is mapped
# here, so a new flag cannot silently fall back to "recompute everything" or "recompute nothing".
FLAG_FAMILIES: dict = {
    "MACRO_VIF_ITERATIVE_ENABLED": "macro",
    "MACRO_FACTOR_CLEAN_ENABLED": "macro",
    "PERSISTENCE_FIRST_LAST_NAV_ENABLED": "persistence",
    "CAPTURE_MONTH_END_ENABLED": "capture",
}


def family_token(family: str, calc_version: str, overrides: Mapping | None = None) -> str:
    """The version string stored in algorithm_version / last_ols_calc_version for `family`."""
    _check_family(family)
    ov = (FAMILY_CALC_OVERRIDES if overrides is None else overrides).get(family)
    return f"{calc_version}.{ov}" if ov else calc_version


def family_flags(family: str, flags_on: Iterable[str]) -> str:
    """'+'-joined, sorted names of the enabled bundle flags that belong to `family` ('' when none)."""
    _check_family(family)
    return "+".join(sorted(f for f in flags_on if FLAG_FAMILIES.get(f) == family))


def family_hash(data_fp: str, metric_version: str, family: str, calc_version: str,
                flags_on: Iterable[str] = (), overrides: Mapping | None = None) -> str:
    """SHA-1 over data + metric version + family token + the family's own enabled flags."""
    raw = (f"{data_fp}||{metric_version}||{family_token(family, calc_version, overrides)}"
           f"||{family}||{family_flags(family, flags_on)}")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def current_family_hashes(data_fp: str, metric_version: str, calc_version: str,
                          flags_on: Iterable[str] = (), overrides: Mapping | None = None) -> dict:
    """{family: hash} for all nine families, for one fund's current data state."""
    flags_on = tuple(flags_on)
    return {f: family_hash(data_fp, metric_version, f, calc_version, flags_on, overrides) for f in ALL_FAMILIES}


def composite_hash(family_hashes: Mapping) -> str:
    """One stable hash over every family's hash. Stamped in fund_metric_state.input_hash while family versioning is
    on, so readers of that column (recompute_gate, audit tooling) still see a value that changes iff anything did."""
    raw = "||".join(f"{f}={family_hashes[f]}" for f in sorted(family_hashes))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FundDecision:
    """What to do for one fund. `run` = families to compute; `stamp` = families whose state row is written after
    a successful per-fund transaction; `cache_hit` = nothing to compute; `seed` = lazily adopted from the legacy
    global hash (no recompute)."""
    run: frozenset
    stamp: frozenset
    cache_hit: bool
    seed: bool
    reason: str


def decide_fund(wanted: Iterable[str], stored: Mapping, current: Mapping, *, force: bool = False,
                legacy_stored: str | None = None, legacy_current: str | None = None) -> FundDecision:
    """Which families to run for one fund.

    wanted         families requested (--metrics filter; all nine when unscoped)
    stored         {family: hash} read from the per-family state table ({} when the fund has no rows)
    current        {family: hash} from current_family_hashes()
    force          --force / force_recalc: run every wanted family regardless of state
    legacy_stored  fund_metric_state.input_hash as stored before family versioning
    legacy_current the same hash computed the pre-versioning way (global CALC_VERSION + all enabled flags)

    Lazy seeding: a fund with NO family rows whose legacy hash still matches was fully up to date under the old
    scheme, so every family is adopted as current without recomputing -- the first run after enabling the switch
    costs nothing. Any other fund without rows computes the wanted families.
    """
    wanted = frozenset(wanted)
    unknown = wanted - set(ALL_FAMILIES)
    if unknown:
        raise ValueError(f"unknown metric families: {sorted(unknown)}")
    all_f = frozenset(ALL_FAMILIES)
    if force:
        return FundDecision(wanted, wanted, False, False, "force")
    if not stored:
        if legacy_stored is not None and legacy_stored == legacy_current:
            return FundDecision(frozenset(), all_f, True, True, "seeded from legacy hash")
        return FundDecision(wanted, wanted, False, False, "no family state")
    stale = frozenset(f for f in wanted if stored.get(f) != current[f])
    if not stale:
        return FundDecision(frozenset(), frozenset(), True, False, "all wanted families current")
    return FundDecision(stale, stale, False, False, "stale: " + ",".join(sorted(stale)))


def _check_family(family: str) -> None:
    if family not in ALL_FAMILIES:
        raise ValueError(f"unknown metric family: {family!r}")
