# shared/governance/lineage.py
# -*- coding: utf-8 -*-
"""
Metrics lineage fingerprint (FND-0306, scope items 2 and 5): WHICH stored P2 metrics a DRAFT or an audit run was built on.

The fund_metrics writer (proyecto2/src/writers/metrics_writer.py) stamps every row it writes or rewrites with the run's algorithm_version and a unique
batch_id (an upsert always re-stamps), so the distinct (algorithm_version, batch_id) pairs of a fund say which runs produced its stored rows. A pair
alone cannot see a DELETION (replace_beta_set, delete_unproduced and an owner clean-up remove rows without re-stamping the others), so the digest also
takes the ROW COUNT of each (fund, pair). Measured on the live store 2026-11-13: 1,608,730 rows, 3,707 funds, 0 NULL stamps, 831 rows of 190 old or
retired funds carrying the sentinel PRE_V26_UNKNOWN.

Pure functions over aggregate rows (isin, algorithm_version, batch_id, n_rows) plus one loader whose errors propagate: a lineage query that fails is
INCOMPLETE evidence, and the caller refuses; it never skips (FND-0306 item 5). Scope: fund_metrics only. fund_metric_timeseries (rewritten only when the
row differs) and fund_metric_alerts (no batch) are NOT covered, on purpose and stated: the P3 selection reads fund_metrics, and the alarm watch reads the
alerts live at promotion time.

    fingerprint(rows)            -> LineageFingerprint (deterministic, independent of row order)
    compute(conn, isins=None)    -> the same from the database (universe, or the funds of one scenario)
    verdict(stamped, current)    -> (MATCH | MISMATCH | UNKNOWN | AMBIGUOUS, detail); a missing or older-format stamp is UNKNOWN, never a pass
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Optional

LINEAGE_VERSION = "v1"                 # changes if the definition of the digest changes: an older stamp then reads UNKNOWN
UNSTAMPED_SENTINEL = "PRE_V26_UNKNOWN"
NULL_TOKEN = "<NULL>"
MAX_UNSTAMPED_SHARE = 0.02             # above this share of rows without a real stamp the scenario is AMBIGUOUS, not hashed over (live: 0.05 %)
LISTED_UNSTAMPED_FUNDS = 20            # the detail lists at most this many funds; the count is always exact

# Unqualified table name on purpose: the connection's search_path resolves it (as in proyecto3/src/data_freshness.py).
LINEAGE_UNIVERSE_SQL = ("SELECT isin, algorithm_version, batch_id, COUNT(*) FROM fund_metrics "
                        "GROUP BY isin, algorithm_version, batch_id")
LINEAGE_SCOPE_SQL = ("SELECT isin, algorithm_version, batch_id, COUNT(*) FROM fund_metrics WHERE isin = ANY(%s) "
                     "GROUP BY isin, algorithm_version, batch_id")

MATCH, MISMATCH, UNKNOWN, AMBIGUOUS = "MATCH", "MISMATCH", "UNKNOWN", "AMBIGUOUS"


@dataclass(frozen=True)
class LineageFingerprint:
    version: str
    sha256: str
    funds: int
    pairs: int
    rows: int
    unstamped_rows: int
    unstamped_funds: int
    unstamped_isins: tuple             # at most LISTED_UNSTAMPED_FUNDS, sorted
    ambiguous: bool

    def token(self) -> str:
        return f"{self.version}:{self.sha256[:16]}"

    def as_dict(self) -> dict:
        return {"version": self.version, "sha256": self.sha256, "funds": self.funds, "pairs": self.pairs, "rows": self.rows,
                "unstamped_rows": self.unstamped_rows, "unstamped_funds": self.unstamped_funds,
                "unstamped_isins": list(self.unstamped_isins), "ambiguous": self.ambiguous}

    @classmethod
    def from_dict(cls, d) -> "LineageFingerprint":
        """Inverse of as_dict. Raises ValueError on anything that is not a complete stamp: the caller reads that as UNKNOWN."""
        try:
            return cls(str(d["version"]), str(d["sha256"]), int(d["funds"]), int(d["pairs"]), int(d["rows"]), int(d["unstamped_rows"]),
                       int(d["unstamped_funds"]), tuple(str(i) for i in d["unstamped_isins"]), bool(d["ambiguous"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"not a lineage stamp: {type(exc).__name__}: {exc}") from exc


def _unstamped(av: str, batch: str) -> bool:
    return av in (NULL_TOKEN, UNSTAMPED_SENTINEL) or batch in (NULL_TOKEN, UNSTAMPED_SENTINEL)


def fingerprint(rows: Iterable, max_unstamped_share: float = MAX_UNSTAMPED_SHARE) -> LineageFingerprint:
    """Digest of (isin, algorithm_version, batch_id, n_rows) aggregate rows. Order-independent: the rows are sorted before hashing.
    A row with a NULL or sentinel stamp still enters the digest (as its token) and is counted; with no rows at all, or more than
    `max_unstamped_share` of the rows unstamped, the fingerprint is flagged ambiguous (verdict AMBIGUOUS)."""
    canon = sorted((str(i), NULL_TOKEN if av is None else str(av), NULL_TOKEN if b is None else str(b), int(n)) for i, av, b, n in rows)
    text = "\n".join("|".join(str(x) for x in r) for r in canon)
    sha = hashlib.sha256(f"{LINEAGE_VERSION}\n{text}".encode("utf-8")).hexdigest()
    total = sum(r[3] for r in canon)
    bad = [r for r in canon if _unstamped(r[1], r[2])]
    bad_rows = sum(r[3] for r in bad)
    bad_funds = sorted({r[0] for r in bad})
    ambiguous = total == 0 or (bad_rows / total) > max_unstamped_share
    return LineageFingerprint(LINEAGE_VERSION, sha, len({r[0] for r in canon}), len(canon), total, bad_rows, len(bad_funds),
                              tuple(bad_funds[:LISTED_UNSTAMPED_FUNDS]), ambiguous)


def load_lineage_rows(conn, isins: Optional[Iterable] = None) -> list:
    """[(isin, algorithm_version, batch_id, n_rows)] for the universe, or only for `isins`. A database error propagates (INCOMPLETE evidence)."""
    if isins is None:
        rows = conn.execute(LINEAGE_UNIVERSE_SQL).fetchall()
    else:
        rows = conn.execute(LINEAGE_SCOPE_SQL, (sorted({str(i) for i in isins}),)).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def compute(conn, isins: Optional[Iterable] = None, max_unstamped_share: float = MAX_UNSTAMPED_SHARE) -> LineageFingerprint:
    return fingerprint(load_lineage_rows(conn, isins), max_unstamped_share)


def verdict(stamped: Optional[LineageFingerprint], current: Optional[LineageFingerprint]) -> tuple:
    """(state, detail). UNKNOWN when either side is missing or the definition changed (never a silent pass); AMBIGUOUS when either side has
    no rows or too many unstamped ones (refused rather than hashed over); MATCH when the digests are equal; MISMATCH otherwise."""
    if stamped is None:
        return UNKNOWN, "no lineage stamp: built before the feature, or the evidence header does not carry it"
    if current is None:
        return UNKNOWN, "the current lineage could not be computed"
    if stamped.version != current.version:
        return UNKNOWN, f"the fingerprint definition changed ({stamped.version} -> {current.version}): re-stamp"
    for label, fp in (("stamped", stamped), ("current", current)):
        if fp.ambiguous:
            why = "no metrics rows" if fp.rows == 0 else f"{fp.unstamped_rows} of {fp.rows} rows ({fp.unstamped_funds} funds) without a real stamp"
            return AMBIGUOUS, f"{label} lineage is ambiguous: {why}"
    if stamped.sha256 == current.sha256:
        return MATCH, f"{current.funds} funds, {current.pairs} (fund, run) pairs, {current.rows} rows: identical to the stamp"
    return MISMATCH, (f"the stored metrics changed after the stamp: funds {stamped.funds} -> {current.funds}, pairs {stamped.pairs} -> {current.pairs}, "
                      f"rows {stamped.rows} -> {current.rows} (a P2 recompute, a rewrite or a deletion)")
