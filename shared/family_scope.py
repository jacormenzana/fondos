"""Evaluate by FUND FAMILY, not by share class (FND-0392, WS0.5 of FND-0391; plan amendment A5).

A fund is sold as several share classes (EUR hedged, USD, accumulating, institutional...) that hold the same portfolio. Three of the 21 funds with a robust deviation >= 50 on 2026-11-07 belong to one family
(Man Dynamic Income): counting each class would open three incidents, three exclusions and three "independent" confirmations for one phenomenon. The suspicion predicate (WS2, FND-0394) and the escalation
engine (WS3, FND-0395) therefore reduce their findings to families through this module.

The family key is NOT defined here (P#11): `fund_master.fund_family_id` is written by proyecto1/core/fund_family_builder.compute_family_groups (FND-0246, the one definition). A fund with no family id is a
family of one, keyed `ISIN:<isin>`, so a missing id never merges two unrelated funds. Pure except load_family_map, which only reads.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

SOLO_PREFIX = "ISIN:"


def family_key(isin: str, family_of: Mapping) -> str:
    """The family key of `isin`: its persisted family id, else `ISIN:<isin>` (a family of one)."""
    fam = family_of.get(isin)
    return str(fam) if fam not in (None, "") else f"{SOLO_PREFIX}{isin}"


def group_by_family(isins: Iterable[str], family_of: Mapping) -> dict:
    """{family key: sorted tuple of the given ISINs that belong to it}, families in sorted key order. Duplicated ISINs count once."""
    groups: dict = {}
    for isin in sorted(set(isins)):
        groups.setdefault(family_key(isin, family_of), []).append(isin)
    return {k: tuple(v) for k, v in sorted(groups.items())}


def collapse_flags(flags: Mapping, family_of: Mapping, magnitude=abs) -> dict:
    """Reduce per-ISIN flags to per-family flags. `flags` = {isin: score} (e.g. the largest robust deviation of the fund). Returns {family key: {"isins": flagged members, "representative": the member with
    the largest magnitude(score) (ties: the first ISIN), "score": its score, "n_flagged": how many members are flagged}}. One entry per family, so one incident / one exclusion decision per family."""
    out: dict = {}
    for key, members in group_by_family(flags.keys(), family_of).items():
        rep = max(members, key=lambda i: (magnitude(flags[i]), -members.index(i)))
        out[key] = {"isins": members, "representative": rep, "score": flags[rep], "n_flagged": len(members)}
    return out


def load_family_map(conn: "psycopg.Connection", isins: Optional[Iterable[str]] = None) -> dict:
    """{isin: fund_family_id} for the funds that have one (all of them, or only `isins`). Read-only."""
    if isins is None:
        rows = conn.execute("SELECT isin, fund_family_id FROM fund_master WHERE fund_family_id IS NOT NULL").fetchall()
    else:
        rows = conn.execute("SELECT isin, fund_family_id FROM fund_master WHERE fund_family_id IS NOT NULL AND isin = ANY(%s)", (list(isins),)).fetchall()
    return {r[0]: r[1] for r in rows}
