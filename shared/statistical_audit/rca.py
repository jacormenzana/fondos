"""
shared/statistical_audit/rca.py -- deterministic root-cause tag for audit findings (FND-0251, Commit C).

Only ONE cause class is ever assigned automatically, and only from a mechanical, verifiable state of the funds involved:

  RCA-E  (state / life cycle)  EVERY violating ISIN of the finding is
           * stale NAV: its newest monthly NAV is older than P3_FUND_NAV_MAX_AGE_DAYS, or
           * stale metric version: its newest fund_metrics.algorithm_version is older than the newest in the table
             (the fund was left out of the last recompute, so the audit applies today's conventions to old numbers).

Everything else stays UNCLASSIFIED -- a human reads it. Market (RCA-M) and engineering (RCA-I) causes are NEVER inferred: a MAD-z score
cannot tell a market episode from a defect, and a label that looks like analysis but is a guess is worse than no label.

The tag is a prefix of `root_cause_candidate` ("RCA-E: ..."); severity, rule_class and every gate decision are untouched, and no schema
changes. `nav_sources.status` is deliberately not used: it is 'OK' for the frozen funds (FND-0041), so it would not detect them.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Optional

RCA_E = "RCA-E"
RCA_E_PREFIX = f"{RCA_E}:"


def stale_states(nav_last: Mapping[str, Any], algo_by_isin: Mapping[str, Optional[str]], current_version: Optional[str],
                 as_of: date, max_age_days: int) -> dict:
    """{isin: mechanical reason} for the funds in a stale state. Pure. A fund with no NAV row (or no metrics row) makes no claim:
    absence of data is not a state this module can name."""
    out: dict = {}
    for isin, last in nav_last.items():
        if last is not None and (as_of - last).days > max_age_days:
            out[isin] = f"NAV {(as_of - last).days} d old (limit {max_age_days} d)"
    if current_version:
        for isin, version in algo_by_isin.items():
            if version is not None and str(version) < str(current_version) and isin not in out:
                out[isin] = f"metrics at algorithm_version {version} < {current_version}"
    return out


def tag_findings(findings: Iterable[dict], states: Mapping[str, str]) -> int:
    """Prefix `root_cause_candidate` with RCA-E for every finding whose violating ISINs are ALL in `states`. Mutates the dicts, returns the
    number tagged. A finding with no violating ISINs, with any ISIN outside `states`, or already tagged is left alone."""
    n = 0
    for f in findings:
        isins = tuple(f.get("violating_isins") or ())
        if not isins or any(i not in states for i in isins):
            continue
        current = str(f.get("root_cause_candidate") or "")
        if current.startswith(RCA_E_PREFIX):
            continue
        kinds = sorted({"stale NAV" if states[i].startswith("NAV") else "stale metric version" for i in isins})
        f["root_cause_candidate"] = (f"{RCA_E_PREFIX} all {len(isins)} violating funds are in a mechanical stale state ({', '.join(kinds)}; "
                                     f"e.g. {isins[0]}: {states[isins[0]]})" + (f". {current}" if current else ""))
        n += 1
    return n


def load_stale_states(conn, isins: Iterable[str], max_age_days: int, as_of: Optional[date] = None) -> dict:
    """Read-only. {isin: reason} for the given funds. Three short queries; nothing is written."""
    isins = sorted(set(isins))
    if not isins:
        return {}
    as_of = as_of or date.today()
    nav_last = {r[0]: r[1] for r in conn.execute(
        "SELECT isin, MAX(date) FROM fund_nav_monthly WHERE isin = ANY(%s) GROUP BY isin", (isins,)).fetchall()}
    algo = {r[0]: r[1] for r in conn.execute(
        "SELECT isin, MAX(algorithm_version) FROM fund_metrics WHERE isin = ANY(%s) GROUP BY isin", (isins,)).fetchall()}
    current = conn.execute("SELECT MAX(algorithm_version) FROM fund_metrics").fetchone()[0]
    return stale_states(nav_last, algo, current, as_of, max_age_days)
