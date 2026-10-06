"""Accepted-residual baseline (FND-0234(e)): findings the owner has decided to live with, until an expiry.

A hard-invariant violation that the pipeline cannot repair yet (e.g. the 92 funds whose ongoing charge was copied from the
ACI row, FND-0034) used to raise the same ALARM in every audit run. This module lets such a (domain, rule, ISIN) be ACCEPTED
with the owning AP and a mandatory expiry:

* a finding whose violating ISINs are ALL accepted is downgraded to INFO / rule_class ACCEPTED_RESIDUAL (still reported and
  persisted, so nothing is hidden, and --mode check no longer blocks on it);
* a finding with ANY not-accepted ISIN stays as it was, restricted to the new ISINs (distance = how many are new);
* an expired acceptance counts as not accepted, so a forgotten residual comes back by itself.

Only findings that carry `violating_isins` can be accepted; group-level statistics (BLOCK1-3) cannot. The acceptance key is
set membership, not a value: a violation that gets worse for an already-accepted ISIN is not detected (the expiry bounds it).

Pure functions plus one read-only loader; the writer lives in persistence.py (the package's only DB writer).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

ACCEPTED_CLASS = "ACCEPTED_RESIDUAL"

# {rule_id: {isin: (ap_id, expires_at)}}
Accepted = Mapping[str, Mapping[str, tuple]]


def load_accepted(conn: "psycopg.Connection", domain: str, today: date | None = None) -> dict:
    """Active (not expired) acceptances for `domain`. Read-only. Raises if the table is missing: the caller decides
    whether that means "nothing accepted" (see run_statistical_audit._apply_accepted_residuals)."""
    today = today or date.today()
    rows = conn.execute(
        "SELECT rule_id, isin, ap_id, expires_at FROM audit_accepted_finding "
        "WHERE domain = %s AND expires_at >= %s",
        (domain, today),
    ).fetchall()
    out: dict = {}
    for rule_id, isin, ap_id, expires_at in rows:
        out.setdefault(rule_id, {})[isin] = (ap_id, expires_at)
    return out


def apply_accepted(findings: Sequence[Mapping[str, Any]], accepted: Accepted) -> tuple:
    """Returns (new_findings, notes). `findings` is not mutated; each changed finding is a copy. `notes` are one-line
    human summaries for the report (empty when nothing was accepted)."""
    new_findings: list = []
    notes: list = []
    for f in findings:
        isins = tuple(f.get("violating_isins") or ())
        by_isin = accepted.get(f.get("rule_id"), {}) if isins else {}
        hit = [i for i in isins if i in by_isin]
        if not hit:
            new_findings.append(f)
            continue
        remaining = [i for i in isins if i not in by_isin]
        aps = sorted({by_isin[i][0] for i in hit})
        until = min(by_isin[i][1] for i in hit)
        g = dict(f)
        if not remaining:
            g["severity"] = "INFO"
            g["rule_class"] = ACCEPTED_CLASS
            g["evidence"] = (f"ACCEPTED RESIDUAL ({', '.join(aps)}, until {until}): {len(hit)}/{len(isins)} ISINs; "
                             f"was: {f.get('evidence')}")
            notes.append(f"{f['rule_id']}: {len(hit)} ISINs accepted ({', '.join(aps)}, until {until}) -> INFO")
        else:
            g["violating_isins"] = tuple(remaining)
            g["distance"] = float(len(remaining))
            g["evidence"] = (f"{len(remaining)} NEW ISINs violate ({len(hit)} more accepted under "
                             f"{', '.join(aps)}); was: {f.get('evidence')}")
            notes.append(f"{f['rule_id']}: {len(hit)} accepted, {len(remaining)} NEW ISINs still violate")
        new_findings.append(g)
    return new_findings, notes


def expiry_after(days: int, today: date | None = None, max_days: int = 180) -> date:
    """Expiry date for an acceptance, capped so that no residual can be accepted for ever."""
    from datetime import timedelta
    if days < 1:
        raise ValueError("days must be >= 1")
    return (today or date.today()) + timedelta(days=min(days, max_days))
