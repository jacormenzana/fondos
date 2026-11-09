"""Temporary, ticket-bound P3 data hold: a fund whose published numbers are under human review is kept out of portfolio construction until the review has a verdict (FND-0403, owner order 2026-11-08).

Why a registry of its own and not an accepted audit residual: a residual is granted for the violators of one audit RULE, it downgrades the fund's alarms to INFO (the very evidence under review would go quiet) and its grant
is a two-step owner command that needs a finding to point at. A hold is the opposite: the evidence stays loud, the fund is simply not selectable. Same safeguards as a residual: an owning ticket, a mandatory expiry
(at most HOLD_MAX_DAYS), and a lapse by itself, so a forgotten hold comes back and cannot park a fund forever.

Pure functions. The registry is `shared.config.P3_DATA_HOLDS` ({isin: {"ap", "since", "until", "reason"}}, git history is its event log); the scorer reads it through `hold_reasons` and excludes the fund with
`exclusion_reason = 'data_hold: ...'`. The point-in-time backtest does not carry the column, so it is unaffected (a hold is today's operational decision, not history: documented, like every static attribute).
Releasing a hold when the ticket gets its verdict is a one-line registry change (or it lapses at `until`).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Mapping, Optional

HOLD_MAX_DAYS = 90
_AP_RE = re.compile(r"^[A-Z]{2,4}-\d{4}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")


def _day(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


parse_day = _day          # public alias: shared/data_exemption.py reads dates the same way (P#11)


def validate_holds(holds: Mapping) -> list:
    """Problems with the registry (empty list = fine): a malformed ISIN or ticket id, a reason shorter than 20 characters, dates that are not ISO dates, until not after since, or a hold longer than HOLD_MAX_DAYS."""
    problems = []
    for isin, h in sorted(holds.items()):
        if not _ISIN_RE.match(str(isin)):
            problems.append(f"{isin!r} is not an ISIN")
        if not _AP_RE.match(str(h.get("ap", ""))):
            problems.append(f"{isin}: 'ap' {h.get('ap')!r} is not a ticket id like FND-0403")
        if len(str(h.get("reason", "")).strip()) < 20:
            problems.append(f"{isin}: 'reason' must say why, in at least 20 characters")
        since, until = _day(h.get("since")), _day(h.get("until"))
        if since is None or until is None:
            problems.append(f"{isin}: 'since' and 'until' must be ISO dates")
        elif until <= since:
            problems.append(f"{isin}: 'until' must be after 'since'")
        elif (until - since) > timedelta(days=HOLD_MAX_DAYS):
            problems.append(f"{isin}: a hold lasts at most {HOLD_MAX_DAYS} days ({since} -> {until})")
    return problems


def hold_reasons(holds: Mapping, as_of: date) -> dict:
    """{isin: exclusion text} of the holds that are ACTIVE on `as_of` (since <= as_of <= until). An expired or not-yet-started hold is not active, and an entry whose dates cannot be read never holds; any other formatting slip (ticket text, reason) still holds, so a typo cannot release a
    fund under review (`validate_holds` reports the slip, and the registry test fails on it)."""
    out = {}
    for isin, h in holds.items():
        since, until = _day(h.get("since")), _day(h.get("until"))
        if since is None or until is None or not (since <= as_of <= until):
            continue
        out[str(isin)] = f"data_hold: {h.get('ap')} until {until} ({str(h.get('reason', '')).strip()})"
    return out
