"""Verified-genuine exemptions for the data-suspicion predicate (FND-0394, plan amendments A3 G-08 and A4 #4, owner order 2026-11-09).

A statistical outlier is not always a defect: a fund can really have a +75.7% year (ES0116848013, FND-0403: Morningstar, Finect and the manager's own web agree with the stored NAV). The predicate (`shared/data_suspect.py`) would flag such a fund
for ever and, once its selection effect is on, keep a genuine performer out of every portfolio. An exemption is the recorded, reviewed decision "this extreme quantity of this fund was verified as real". It is the reverse of an accepted audit residual
and of a P3 data hold (`shared/data_hold.py`): the evidence stays visible, only the SUSPICION is withdrawn, for exactly the quantities named.

Design, in the order the owner's rules give it:
  * SCOPED: an entry exempts named QUANTITIES (`metric|horizon`, e.g. `sortino|crisis_2022`) of one ISIN and of its fund family (share classes carry the same series), never the whole fund: a new extreme quantity, or a NAV-level flag
    (`validate_nav_unshared_jump`), is still reported.
  * TICKET + EXPIRY: every entry names the ticket that holds the evidence (`ap`), `since` / `until` (at most EXEMPTION_MAX_DAYS: a verification does not stay true for ever; the reviewer re-checks), a reason and the evidence text.
  * EXPIRY GUARD (A4 #4), derived from dates and from the scenarios that exist, with no stored state to forget: ACTIVE until `until`; EXPIRING in the last `warn_days` (the cycle report and the promotion output print a line);
    after `until` the fund is in GRACE for exactly one P3 build (the first scenario created after `until`: still exempt, flagged `exemption_grace`); from the build after that it is LAPSED and the quantity counts as suspect again.
  * VISIBLE: `attention_lines` is what the cycle report and the promotion output print; `exempted_hits` (data_suspect) lists what was withdrawn, so no exemption is silent.
Pure functions; the registry is `shared.config.DATA_SUSPECT_EXEMPTIONS` (git history is its event log). `load_build_dates` is the only database read.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional

from shared.data_hold import parse_day

EXEMPTION_MAX_DAYS = 180
STATES_THAT_EXEMPT = ("ACTIVE", "EXPIRING", "GRACE")
_AP_RE = re.compile(r"^[A-Z]{2,4}-\d{4}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_QUANTITY_RE = re.compile(r"^[a-z0-9_]+\|[a-z0-9_]+$")          # 'metric|horizon' (shared.data_suspect.quantity_of)


def validate_exemptions(registry: Mapping) -> list:
    """Problems with the registry (empty list = fine): malformed ISIN or ticket id, a reason shorter than 20 characters, no evidence text, quantities that are not `metric|horizon`, dates that are not ISO dates,
    `until` not after `since`, or a window longer than EXEMPTION_MAX_DAYS."""
    problems = []
    for isin, e in sorted(registry.items()):
        if not _ISIN_RE.match(str(isin)):
            problems.append(f"{isin!r} is not an ISIN")
        if not _AP_RE.match(str(e.get("ap", ""))):
            problems.append(f"{isin}: 'ap' {e.get('ap')!r} is not a ticket id like FND-0403")
        if len(str(e.get("reason", "")).strip()) < 20:
            problems.append(f"{isin}: 'reason' must say why, in at least 20 characters")
        if len(str(e.get("evidence", "")).strip()) < 20:
            problems.append(f"{isin}: 'evidence' must say what was verified and against what, in at least 20 characters")
        quantities = e.get("quantities")
        if not quantities or not all(isinstance(q, str) and _QUANTITY_RE.match(q) for q in quantities):
            problems.append(f"{isin}: 'quantities' must be a non-empty list of 'metric|horizon' names (no real_flag, no version)")
        since, until = parse_day(e.get("since")), parse_day(e.get("until"))
        if since is None or until is None:
            problems.append(f"{isin}: 'since' and 'until' must be ISO dates")
        elif until <= since:
            problems.append(f"{isin}: 'until' must be after 'since'")
        elif (until - since) > timedelta(days=EXEMPTION_MAX_DAYS):
            problems.append(f"{isin}: an exemption lasts at most {EXEMPTION_MAX_DAYS} days ({since} -> {until})")
    return problems


def exemption_state(entry: Mapping, as_of: date, build_dates: Iterable[date] = (), warn_days: int = 7) -> str:
    """NOT_STARTED | ACTIVE | EXPIRING | GRACE | LAPSED | INVALID (unreadable dates).
    After `until` the first scenario creation date strictly before `as_of` that is also after `until` ends the grace: with none the build running now is the one grace build (GRACE), with one it is the next (LAPSED)."""
    since, until = parse_day(entry.get("since")), parse_day(entry.get("until"))
    if since is None or until is None:
        return "INVALID"
    if as_of < since:
        return "NOT_STARTED"
    if as_of <= until:
        return "EXPIRING" if (until - as_of).days <= warn_days else "ACTIVE"
    return "LAPSED" if any(until < d < as_of for d in build_dates) else "GRACE"


def effective_exemptions(registry: Mapping, as_of: date, build_dates: Iterable[date] = (), warn_days: int = 7) -> dict:
    """{isin: frozenset(quantities)} of the entries that exempt on `as_of` (ACTIVE, EXPIRING or GRACE). Entries in any other state exempt nothing."""
    build_dates = list(build_dates)
    return {str(isin): frozenset(e.get("quantities") or ()) for isin, e in registry.items()
            if exemption_state(e, as_of, build_dates, warn_days) in STATES_THAT_EXEMPT}


def grace_isins(registry: Mapping, as_of: date, build_dates: Iterable[date] = (), warn_days: int = 7) -> dict:
    """{isin: note} of the entries in GRACE: the P3 notes and the selection-impact KPIs flag them `exemption_grace`."""
    build_dates = list(build_dates)
    return {str(isin): f"exemption_grace: {e.get('ap')} expired {e.get('until')}; exempt for this build only"
            for isin, e in registry.items() if exemption_state(e, as_of, build_dates, warn_days) == "GRACE"}


def attention_lines(registry: Mapping, as_of: date, build_dates: Iterable[date] = (), warn_days: int = 7) -> list:
    """Sentences for the cycle report and the promotion output: exemptions about to expire, in their grace build, lapsed, or unreadable. Pure."""
    build_dates = list(build_dates)
    lines = []
    for isin, e in sorted(registry.items()):
        state = exemption_state(e, as_of, build_dates, warn_days)
        qty = ", ".join(sorted(e.get("quantities") or ())) or "?"
        if state == "EXPIRING":
            lines.append(f"verified-genuine exemption of {isin} ({qty}; {e.get('ap')}) expires on {e.get('until')} "
                         f"({(parse_day(e.get('until')) - as_of).days} days): re-verify and renew it in shared.config.DATA_SUSPECT_EXEMPTIONS, or it lapses")
        elif state == "GRACE":
            lines.append(f"verified-genuine exemption of {isin} ({qty}; {e.get('ap')}) EXPIRED on {e.get('until')}: it still exempts THIS build only (exemption_grace); "
                         "from the next build the quantities count as suspect again unless it is renewed")
        elif state == "LAPSED":
            lines.append(f"verified-genuine exemption of {isin} ({qty}; {e.get('ap')}) LAPSED (expired {e.get('until')}, its grace build is over): the quantities count as suspect again")
        elif state == "INVALID":
            lines.append(f"verified-genuine exemption of {isin}: its dates cannot be read, it exempts nothing")
    return lines


def load_build_dates(conn: "psycopg.Connection") -> list:
    """Creation dates of every portfolio scenario (the builds that count for the grace). Read-only; an unreadable table gives no dates, which keeps an expired exemption in GRACE rather than lapsing it unseen."""
    from shared.db import fail_soft_block                          # a SAVEPOINT: a failed read must not leave the caller's transaction aborted (tests/test_dialect_coverage.py)
    try:
        with fail_soft_block(conn):
            return [r[0] for r in conn.execute("SELECT created_at FROM gold.portfolio_scenarios").fetchall() if r[0] is not None]
    except Exception:                                              # noqa: BLE001
        return []
