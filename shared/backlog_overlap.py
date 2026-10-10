# -*- coding: utf-8 -*-
"""
shared/backlog_overlap.py -- text similarity between action points, the single implementation behind both consumers
(P#11 / R-1): scripts/audit/backlog_links.py (S4 signal of the relationship discovery) and shared/backlog_client.report_incident
(two-tier overlap policy of the automatic incident capture, NORMAS_BACKLOG.md §7).

Pure and stdlib-only: report_incident runs inside the exception handler of an already failing pipeline, so this module must
import and run anywhere the pipelines run (no numpy, no database, no git).

TF-IDF: sublinear tf (1 + ln tf) x smoothed idf (ln((1 + n) / (1 + df)) + 1), L2-normalised rows over ALL the document's terms (a
term unique to one text still lowers its similarity to every other), cosine = dot product.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Optional

_TOKEN_RX = re.compile(r"[a-z0-9_]{3,}")
_FND_RX = re.compile(r"\bFND-0*(\d{1,4})\b")
# Exit codes are IDENTITY, unlike durations and counts, which vary run to run: "failed with RC=2" and "failed with RC=101" read almost
# the same to a similarity measure and are different defects.
_EXIT_CODE_RX = re.compile(r"\bRC\s*=\s*(\d+)|\bexit(?:ed)?(?: with)?(?: code| status)?\s*[:=]?\s*(\d+)", re.I)
_STOP = frozenset("""
the and for with that this from are was were not but have has had its into when then than also any all can will would should
may might per via out one two new old use used using see set get got run runs ran
los las del una uno unos unas para por con que como pero más mas sus son está esta este esto
""".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RX.findall(text.lower()) if t not in _STOP and not t.isdigit()]


def exit_codes(text: str) -> set[str]:
    return {a or b for a, b in _EXIT_CODE_RX.findall(text)}


def mentioned_ids(text: str) -> set[str]:
    return {"FND-%04d" % int(m) for m in _FND_RX.findall(text)}


def _vectors(docs: dict[str, str]) -> dict[str, dict[str, float]]:
    counts = {i: Counter(tokens(t)) for i, t in docs.items()}
    df = Counter(t for c in counts.values() for t in c)
    n = len(docs)
    idf = {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}
    vec: dict[str, dict[str, float]] = {}
    for i, c in counts.items():
        v = {t: (1.0 + math.log(k)) * idf[t] for t, k in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vec[i] = {t: x / norm for t, x in v.items()}
    return vec


def tfidf_cosine(docs: dict[str, str], min_cos: float = 0.10) -> dict[tuple, float]:
    """Pairwise cosine above `min_cos`: {(a, b): cosine} with a < b (an inverted index, so cost follows shared terms)."""
    if len(docs) < 2:
        return {}
    vec = _vectors(docs)
    inverted: dict[str, list] = defaultdict(list)
    for i, v in vec.items():
        for t, w in v.items():
            inverted[t].append((i, w))
    acc: dict[tuple, float] = defaultdict(float)
    for postings in inverted.values():
        postings.sort()
        for x in range(len(postings)):
            ia, wa = postings[x]
            for y in range(x + 1, len(postings)):
                ib, wb = postings[y]
                acc[(ia, ib)] += wa * wb
    return {k: v for k, v in acc.items() if v >= min_cos}


def cosine_to_query(query: str, docs: dict[str, str]) -> dict[str, float]:
    """Cosine of one text against each document (the idf is computed over the documents plus the query)."""
    key = "\x00query"
    vec = _vectors({**docs, key: query})
    q = vec.pop(key)
    return {i: sum(w * v.get(t, 0.0) for t, w in q.items()) for i, v in vec.items()}


def classify_incident(*, scenario: str, object_name: str, candidates: list[dict], high_cosine: float,
                      medium_cosine: float) -> dict:
    """Two-tier overlap policy for an automatic incident (owner safeguard S-2).

    `candidates`: the OPEN tickets, each {action_point_id, title, object_name, scenario_description}.
    HIGH   -> the incident is appended to that ticket (a log note saying why): the ticket tracks the same object_name AND its text
              is at least `high_cosine` similar AND the exit codes (if both state one) agree.
    MEDIUM -> a new ticket is opened and its first log only SUGGESTS the related one (no merge): the same object_name with a
              weaker or contradicting match, the scenario naming the ticket, or similarity >= `medium_cosine`.
    NONE   -> unrelated.
    Calibration (D-1, 2026-10-11): "the scenario names an open ticket" was tried as a HIGH rule and made 53 false merges on the 218
    historical tickets -- people cite related tickets constantly, a citation is not a duplicate -- so it is a suggestion only.
    Returns {"tier", "ticket", "reason", "score"}."""
    none = {"tier": "NONE", "ticket": None, "reason": "", "score": 0.0}
    if not candidates:
        return none
    docs = {c["action_point_id"]: " ".join(x for x in (c.get("title"), c.get("object_name"), c.get("scenario_description")) if x)
            for c in candidates}
    by_id = {c["action_point_id"]: c for c in candidates}
    cos = cosine_to_query(f"{object_name} {scenario}", docs)

    def hit(tier: str, tid: str, reason: str) -> dict:
        return {"tier": tier, "ticket": tid, "reason": reason, "score": round(cos.get(tid, 0.0), 3)}

    same = [i for i, c in by_id.items() if c.get("object_name") == object_name]
    if same:
        best = max(same, key=lambda i: cos.get(i, 0.0))
        theirs, mine = exit_codes(docs[best]), exit_codes(scenario)
        if theirs and mine and theirs != mine:
            return hit("MEDIUM", best, f"same object_name but exit code {sorted(mine)} differs from {sorted(theirs)}")
        if cos.get(best, 0.0) >= high_cosine:
            return hit("HIGH", best, f"same object_name and text similarity {cos[best]:.2f} >= {high_cosine:.2f}")
    named = sorted(mentioned_ids(scenario) & set(docs))
    if named:
        best = max(named, key=lambda i: cos.get(i, 0.0))
        return hit("MEDIUM", best, f"the scenario names {best}")
    if same:
        best = max(same, key=lambda i: cos.get(i, 0.0))
        return hit("MEDIUM", best, f"same object_name, text similarity {cos.get(best, 0.0):.2f}")
    top: Optional[str] = max(cos, key=lambda i: cos[i]) if cos else None
    if top is not None and cos[top] >= medium_cosine:
        return hit("MEDIUM", top, f"text similarity {cos[top]:.2f} >= {medium_cosine:.2f}")
    return none
