# -*- coding: utf-8 -*-
"""
Relationships between action points: discovery, review and standard procedure checks (doc/reglas/NORMAS_BACKLOG.md).

Why: a fix designed in isolation duplicates signals and leaves scope for the related ticket (FND-0243 was built without
integrating FND-0246). This tool finds the relations the free-text backlog does not state, proposes them for a HUMAN decision and
records only decided links (append-only `backlog.backlog_link_event`, through shared/backlog_client.py -- the only gestion path).

Signals (each pair of tickets can carry several; none decides alone):
  S1 explicit   an FND id mentioned in a ticket's title / description / logs; the wording around it suggests the link type
  S2 footprint  files touched by the commits that name the ticket (+ paths cited in its text), IDF-weighted overlap
  S3 vocabulary repo identifiers (config switches, DDL tables/columns, module and function names) shared by two tickets, IDF-weighted
  S4 text       TF-IDF cosine of title + description + logs (stdlib, shared/backlog_overlap.py; scikit-learn / rich are not installed in env `des`)

    python scripts/audit/backlog_links.py --all                       # clusters + ranked unreviewed candidates (read-only)
    python scripts/audit/backlog_links.py --for FND-0246              # neighbours; exit 1 while a confirmed DEPENDS_ON target is open
    python scripts/audit/backlog_links.py --new "text of the new ticket"   # the 3 open tickets closest to it (informational, exit 0)
    python scripts/audit/backlog_links.py --evaluate                  # recall of S2-S4 on the explicit links, precision on the reviewed set
    python scripts/audit/backlog_links.py --mermaid                   # out/reports/backlog_links/<stamp>.md
    python scripts/audit/backlog_links.py --review-out                # out/reports/backlog_links/review_<stamp>.{md,csv}
    python scripts/audit/backlog_links.py --apply-review FILE.csv [--confirm] [--force-row FROM:TO:TYPE]

Read-only except `--apply-review --confirm`, which inserts link EVENTS (never updates, never deletes). Uses FONDOS_BACKLOG_PG_DSN
(autoloaded from the repo .env by shared.config); never prints it. The pure core below takes plain lists of dicts, so every signal
is unit-tested on fixtures (tests/test_backlog_links_signals.py).
"""
from __future__ import annotations

import argparse
import ast
import csv
import getpass
import math
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Iterable, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config  # noqa: E402,F401  (side effect: autoloads the repo .env, FONDOS_BACKLOG_PG_DSN)
from shared import backlog_client as bc  # noqa: E402
from shared.backlog_overlap import tfidf_cosine  # noqa: E402  (S4; shared with report_incident)

OUT_DIR = _ROOT / "out" / "reports" / "backlog_links"

# Candidate thresholds. Calibrated with `--evaluate` on live data (recall of each signal on the S1 links); a candidate needs S1,
# or S2 / S3 on their own, or S4 with corroboration. Pure constants: changing one is a code review, not a runtime flag.
T_S2 = 0.30                        # weighted file overlap
T_S3 = 0.25                        # weighted identifier overlap
T_S4 = 0.35                        # TF-IDF cosine, alone (31% of the pairs it fires on are stated links)
T_S4_WITH_OTHER = 0.20             # TF-IDF cosine when S2 / S3 corroborate it
T_CORROBORATE = (0.10, 0.20)       # S2 alone is a co-commit: it needs S3 >= first or S4 >= second
STRONG = 0.60                      # combined confidence from which --new exits 1
MAX_DF_SHARE = 0.10                # a term / file in more than this share of the tickets carries no information (hub modules)
SMOOTH = 2.0                       # additive smoothing of the weighted Jaccard: one shared term in two tiny sets is not "1.00"
MAX_PER_TICKET = 5                 # non-explicit candidates kept per pending ticket (S1 links are never capped)

# Files the backlog procedure itself makes every ticket cite (see footprints()): never footprint evidence.
PROCEDURE_PATHS = frozenset({
    "scripts/audit/backlog_links.py",
    "scripts/audit/backlog_links_calibration.py",
    "shared/backlog_client.py",
    "shared/backlog_overlap.py",
    "doc/reglas/NORMAS_BACKLOG.md",
    "db/pg_backlog/20_links.sql",
})
# ... and their identifiers, for the same reason, on the S3 side (vocabulary_terms).
PROCEDURE_TERMS = frozenset(p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() for p in PROCEDURE_PATHS)

_ID_RX = re.compile(r"\bFND-0*(\d{1,4})\b")
_PATH_RX = re.compile(r"(?<![\w/\\])((?:[\w.\-]+[/\\])*[\w.\-]+\.(?:py|sql|bat|md|yaml|yml|json|toml|ps1|sh))\b")
_IDENT_RX = re.compile(r"[A-Za-z][A-Za-z0-9_]{5,}")

# (link type, regex over the text around a mention). First match in this order wins; no cue = RELATED.
_CUES = (
    ("DUPLICATES", re.compile(r"duplicate[sd]? of|dup of|duplicado de", re.I)),
    ("SPLIT_FROM", re.compile(r"split (?:from|out of)|carved out of|spun off from|derived from|child of|desglosado de"
                             r"|\bfiled\b[^.\n]{0,40}\bfrom\b", re.I)),
    ("RELEASE_WITH", re.compile(r"releas\w+ (?:together )?with|ships? with|same release|together with|junto con"
                               r"|\bcloses? with\b|grouped (?:into|with)", re.I)),
    ("DEPENDS_ON", re.compile(r"depends? on|blocked by|requires|prerequisite|waits? for|depende de|bloqueado por", re.I)),
    ("OVERLAPS", re.compile(r"overlaps?|absorb\w*|integrat\w+|same (?:column|signal|module|code)|solapa", re.I)),
)

# Cues that read the other way round: "<this ticket> split out to FND-x" means FND-x is SPLIT_FROM this one. Anchored at the end of
# the text just before the mention, so a stray "blocks" earlier in the sentence does not count.
_REVERSE_CUES = (
    ("SPLIT_FROM", re.compile(r"split (?:out )?(?:to|into)\W*$|spun off to\W*$|desglosado en\W*$", re.I)),
    ("DEPENDS_ON", re.compile(r"\bblocks\W*$|\bbloquea\W*$|\bprerequisite (?:for|of)\W*$|\bneeded by\W*$", re.I)),
    ("DUPLICATES", re.compile(r"duplicated by\W*$|duplicado por\W*$", re.I)),
)


# ─── pure core ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def norm_id(raw: str) -> str:
    return "FND-%04d" % int(re.sub(r"\D", "", raw))


def ticket_text(ticket: dict, logs: Iterable[dict] = ()) -> str:
    parts = [ticket.get("title") or "", ticket.get("object_name") or "", ticket.get("scenario_description") or ""]
    parts += [l.get("comment") or "" for l in logs]
    return "\n".join(p for p in parts if p)


def _pair(a: str, b: str) -> tuple:
    return (a, b) if a <= b else (b, a)


def signal_explicit(docs: dict[str, str], known: set[str]) -> dict[tuple, dict]:
    """S1: {(from, to): {type, n, evidence}} for every FND id a ticket's text mentions (other than itself, and known ids only)."""
    out: dict[tuple, dict] = {}
    for src, text in docs.items():
        cues: Counter = Counter()
        first: dict[str, str] = {}
        count: Counter = Counter()
        reverse: Counter = Counter()          # (dst, type) -> mentions where dst is the "from" end
        prev_end = 0
        for m in _ID_RX.finditer(text):
            # the cue is read from the words BEFORE the mention, back to the previous mention or sentence break, so
            # "depends on FND-1. Linked: FND-2" does not give FND-2 the cue of FND-1
            before_all = text[max(prev_end, m.start() - 90): m.start()]
            prev_end = m.end()
            before_sentence = re.split(r"[.;\n]\s", before_all)[-1]
            dst = norm_id(m.group(0))
            if dst == src or dst not in known:
                continue
            window = text[max(0, m.start() - 90): m.end() + 25]
            rev = next((t for t, rx in _REVERSE_CUES if rx.search(before_sentence[-40:])), None)
            cue = next((t for t, rx in _CUES if rx.search(before_sentence)), None)
            if rev:
                reverse[(dst, rev)] += 1
            elif cue:
                cues[(dst, cue)] += 1
            count[dst] += 1
            first.setdefault(dst, " ".join(window.split()))
        for dst, n in count.items():
            typed = [(c, k) for (d, k), c in cues.items() if d == dst]
            ltype = max(typed)[1] if typed else "RELATED"
            rev = [(c, k) for (d, k), c in reverse.items() if d == dst]
            if rev and (not typed or max(rev)[0] > max(typed)[0]):
                # src says "split out to / blocks / duplicated by dst": the link runs dst -> src
                out[(dst, src)] = {"type": max(rev)[1], "n": n, "evidence": first[dst][:160]}
            else:
                out[(src, dst)] = {"type": ltype, "n": n, "evidence": first[dst][:160]}
    return out


def extract_paths(text: str) -> set[str]:
    return {re.sub(r"^(?:\./)+", "", m.group(1).replace("\\", "/")) for m in _PATH_RX.finditer(text)}


def resolve_paths(paths: Iterable[str], known_files: Iterable[str]) -> set[str]:
    """Map a cited path to repo files: exact, else a unique basename / path-suffix match; unresolved paths are dropped."""
    known = set(known_files)
    by_base: dict[str, list[str]] = defaultdict(list)
    for f in known:
        by_base[f.rsplit("/", 1)[-1]].append(f)
    out = set()
    for p in paths:
        if p in known:
            out.add(p)
            continue
        cands = [f for f in by_base.get(p.rsplit("/", 1)[-1], ()) if f.endswith("/" + p) or f == p]
        if len(cands) == 1:
            out.add(cands[0])
    return out


def footprints(docs: dict[str, str], commits: list[dict]) -> dict[str, set[str]]:
    """S2 input: per ticket, the files of the commits whose message names it plus the repo files its own text cites.

    `PROCEDURE_PATHS` are dropped: the start-of-ticket dependency scan (NORMAS_BACKLOG.md §4) makes EVERY ticket's first log cite
    this tooling, so those paths say nothing about what the ticket is about. Measured 2026-10-11: the four tickets filed with a scan
    note (FND-0255..0258) came out pairwise linked at 0.64-0.70 on `backlog_links.py` alone. MAX_DF_SHARE would only filter them
    once ~22 tickets carried the note."""
    files: dict[str, set[str]] = defaultdict(set)
    known = set()
    for c in commits:
        known.update(c["files"])
        for m in _ID_RX.finditer(c["message"]):
            files[norm_id(m.group(0))].update(c["files"])
    for tid, text in docs.items():
        files[tid].update(resolve_paths(extract_paths(text), known))
    return {t: f - PROCEDURE_PATHS for t, f in files.items() if t in docs and f - PROCEDURE_PATHS}


def idf_overlap(sets: dict[str, set[str]], *, max_df_share: float = MAX_DF_SHARE) -> dict[tuple, tuple]:
    """Weighted Jaccard of term sets, shared by S2 (files) and S3 (identifiers): {(a, b): (score, top shared terms)}.
    A term present in one set only cannot overlap; one present in more than `max_df_share` of the sets is noise and is dropped."""
    n = len(sets)
    if n < 2:
        return {}
    df = Counter(t for s in sets.values() for t in s)
    limit = max(2, int(max_df_share * n))
    w = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items() if 2 <= c <= limit}
    total = {a: sum(w.get(t, 0.0) for t in s) for a, s in sets.items()}
    inverted: dict[str, list[str]] = defaultdict(list)
    for a, s in sets.items():
        for t in s:
            if t in w:
                inverted[t].append(a)
    shared: dict[tuple, float] = defaultdict(float)
    terms: dict[tuple, list] = defaultdict(list)
    for t, owners in inverted.items():
        for a, b in combinations(sorted(owners), 2):
            shared[(a, b)] += w[t]
            terms[(a, b)].append((w[t], t))
    out = {}
    for (a, b), sh in shared.items():
        den = total[a] + total[b] - sh + SMOOTH
        if den > 0:
            top = [t for _, t in sorted(terms[(a, b)], reverse=True)[:5]]
            out[(a, b)] = (sh / den, top)
    return out


def vocabulary_terms(doc: str, vocab: set[str]) -> set[str]:
    """S3 input: the repo identifiers a ticket names, minus PROCEDURE_TERMS (see footprints(): the dependency-scan note names
    this tooling in every ticket, so it is not evidence of a shared concern)."""
    return {t.lower() for t in _IDENT_RX.findall(doc) if t.lower() in vocab} - PROCEDURE_TERMS


def build_candidates(*, s1: dict, s2: dict, s3: dict, s4: dict, existing: set[tuple], scope: Optional[set[str]] = None) -> list[dict]:
    """Merge the signals per unordered pair, drop pairs that already carry a link (any state: a REJECTED pair is not re-proposed),
    keep those that clear the thresholds and rank them. `scope` = the pending tickets: at least one end must be in it, and each
    of them keeps at most MAX_PER_TICKET non-explicit candidates (the review load stays bounded; a stated mention never is)."""
    pairs: dict[tuple, dict] = {}

    def slot(a, b):
        return pairs.setdefault(_pair(a, b), {"signals": {}, "terms": {}})

    for (a, b), v in s1.items():
        e = slot(a, b)
        if "S1" not in e["signals"] or v["n"] > e["signals"]["S1"]["n"]:
            e["signals"]["S1"] = dict(v, src=a, dst=b)
    for name, src in (("S2", s2), ("S3", s3)):
        for (a, b), (score, top) in src.items():
            e = slot(a, b)
            e["signals"][name] = score
            e["terms"][name] = top
    for (a, b), score in s4.items():
        slot(a, b)["signals"]["S4"] = score
    out = []
    for (a, b), e in pairs.items():
        if (a, b) in existing or (b, a) in existing:
            continue
        if scope is not None and a not in scope and b not in scope:
            continue
        sg = e["signals"]
        s2v, s3v, s4v = sg.get("S2", 0.0), sg.get("S3", 0.0), sg.get("S4", 0.0)
        hit = ("S1" in sg
               or (s2v >= T_S2 and (s3v >= T_CORROBORATE[0] or s4v >= T_CORROBORATE[1]))
               or (s3v >= T_S3 and s4v >= T_S4_WITH_OTHER)
               or s4v >= T_S4)
        if not hit:
            continue
        if "S1" in sg:
            frm, to, ltype = sg["S1"]["src"], sg["S1"]["dst"], sg["S1"]["type"]
            conf = 0.75 + 0.25 * max(s2v, s3v, s4v)
            if ltype == "RELATED" and max(s2v, s3v) >= 0.5 * min(T_S2, T_S3):
                ltype = "OVERLAPS"        # a stated reference backed by shared code is a real overlap
        else:
            frm, to = a, b
            ltype = "OVERLAPS" if max(s2v, s3v) >= min(T_S2, T_S3) else "RELATED"
            conf = min(0.7, 0.45 * s2v + 0.35 * s3v + 0.30 * s4v + 0.1 * (sum(1 for x in (s2v, s3v, s4v) if x) - 1))
        evid = []
        if "S1" in sg:
            evid.append(f"mention x{sg['S1']['n']}: {sg['S1']['evidence']}")
        for name in ("S2", "S3"):
            if name in e["terms"]:
                evid.append(f"{name} {sg[name]:.2f}: " + ", ".join(e["terms"][name]))
        if s4v:
            evid.append(f"S4 {s4v:.2f}")
        out.append({"from_id": frm, "to_id": to, "type": ltype, "conf": round(conf, 3), "signals": sorted(sg),
                    "scores": {k: (v if isinstance(v, float) else v["n"]) for k, v in sg.items()}, "evidence": " | ".join(evid)})
    out.sort(key=lambda c: (-c["conf"], c["from_id"], c["to_id"]))
    if scope is None:
        return out
    kept, load = [], Counter()
    for c in out:
        if "S1" not in c["signals"]:
            ends = [e for e in (c["from_id"], c["to_id"]) if e in scope]
            if all(load[e] >= MAX_PER_TICKET for e in ends):
                continue
            for e in ends:
                load[e] += 1
        kept.append(c)
    return kept


def clusters(edges: Iterable[tuple]) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, list[str]] = defaultdict(list)
    for x in list(parent):
        groups[find(x)].append(x)
    return sorted((sorted(g) for g in groups.values() if len(g) > 1), key=lambda g: (-len(g), g[0]))


# ─── review file (S-1, D-2, D-3) ───────────────────────────────────────────────────────────────────────────────────────────

REVIEW_COLS = ["row", "from_id", "to_id", "type", "conf", "signals", "evidence", "from_status", "from_update_ts", "to_status",
               "to_update_ts", "decision", "absorbs", "note"]
DECISIONS = ("confirm", "reject")


def parse_decision(raw: str) -> Optional[tuple]:
    """'confirm' | 'reject' | 'retype:<TYPE>' | '' -> (status, forced_type|None), or None to skip the row."""
    d = (raw or "").strip().lower()
    if not d:
        return None
    if d == "confirm":
        return ("CONFIRMED", None)
    if d == "reject":
        return ("REJECTED", None)
    if d.startswith("retype:"):
        t = d.split(":", 1)[1].strip().upper()
        if t not in bc.LINK_TYPES:
            raise ValueError(f"unknown link type in decision {raw!r}")
        return ("CONFIRMED", t)
    raise ValueError(f"unknown decision {raw!r} (use confirm / reject / retype:<TYPE>)")


def stale_rows(rows: list[dict], live: dict[str, dict]) -> dict[int, str]:
    """S-1: row index -> reason, for every row whose ticket vanished or whose status changed since the review file was written.
    A closed ticket at generation time stays valid (SPLIT_FROM / DUPLICATES targets usually are); only a CHANGE is flagged."""
    out: dict[int, str] = {}
    for i, r in enumerate(rows):
        for side in ("from", "to"):
            tid, was = r[f"{side}_id"], r[f"{side}_status"]
            cur = live.get(tid)
            if cur is None:
                out[i] = f"{tid} no longer exists"
                break
            if cur["status"] != was:
                out[i] = f"{tid} changed status {was} -> {cur['status']}"
                break
    return out


def build_events(rows: list[dict], flagged: dict[int, str], forced: set[tuple], username: str, source: str) -> tuple[list[dict], list[str]]:
    """Decided rows -> link events. Flagged rows are skipped unless forced (`--force-row`), and a forced row says so in its evidence."""
    events, notes = [], []
    for i, r in enumerate(rows):
        dec = parse_decision(r.get("decision", ""))
        if dec is None:
            continue
        status, forced_type = dec
        ltype = forced_type or r["type"]
        key = (r["from_id"], r["to_id"], ltype)
        ev = f"review {source}: {r.get('evidence', '')}".strip()
        if i in flagged:
            if key not in forced:
                notes.append(f"SKIPPED row {r['row']} {r['from_id']}->{r['to_id']} {ltype}: {flagged[i]}")
                continue
            ev += f" | FORCED despite: {flagged[i]}"
            notes.append(f"FORCED  row {r['row']} {r['from_id']}->{r['to_id']} {ltype}: {flagged[i]}")
        events.append({"from_id": r["from_id"], "to_id": r["to_id"], "link_type": ltype, "status": status,
                       "evidence": ev, "absorbs": (r.get("absorbs") or None), "username": username})
    return events, notes


# ─── repository inputs (impure; injected in the tests) ─────────────────────────────────────────────────────────────────────

def load_commits(root: Path = _ROOT) -> list[dict]:
    out = subprocess.run(["git", "log", "--format=%x1e%H%x1f%s%x1f%b%x1f", "--name-only"], cwd=root, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", check=True).stdout
    commits = []
    for chunk in out.split("\x1e")[1:]:
        parts = chunk.split("\x1f")           # sha, subject, body, name-only file list
        if len(parts) < 4:
            continue
        commits.append({"sha": parts[0], "message": parts[1] + "\n" + parts[2],
                        "files": [f.strip() for f in parts[3].splitlines() if f.strip()]})
    return commits


def build_vocabulary(root: Path = _ROOT) -> set[str]:
    """Identifiers of the repo that a ticket can name: config constants and switches, DDL tables/columns, module stems, top-level
    function / class names. Lower-cased; >= 6 chars with an underscore (a plain word is not an identifier)."""
    vocab: set[str] = set()
    cfg = root / "shared" / "config.py"
    if cfg.exists():
        vocab.update(m.lower() for m in re.findall(r"^([A-Z][A-Z0-9_]{5,})\s*[:=]", cfg.read_text(encoding="utf-8", errors="replace"), re.M))
    for sql in (root / "db").rglob("*.sql"):
        if sql.parent.name == "pg_backlog":     # the backlog's own columns (creation_ts, ...) are meta-terms, not code a ticket is about
            continue
        vocab.update(t.lower() for t in _IDENT_RX.findall(sql.read_text(encoding="utf-8", errors="replace")))
    for folder in ("shared", "proyecto1", "proyecto2", "proyecto3", "scripts"):
        for py in (root / folder).rglob("*.py"):
            if "__pycache__" in py.parts or "tests" in py.parts:
                continue
            vocab.add(py.stem.lower())
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            vocab.update(n.name.lower() for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
    return {v for v in vocab if len(v) >= 6 and "_" in v and not v.startswith("__")}


class Analysis:
    """Everything derived from one read of the backlog: documents, signals, existing links, candidates."""

    def __init__(self, tickets, logs, links, commits, vocab, *, extra_docs: Optional[dict[str, str]] = None,
                 max_df_share: float = MAX_DF_SHARE):
        self.tickets = {t["action_point_id"]: t for t in tickets}
        by_logs: dict[str, list] = defaultdict(list)
        for l in logs:
            by_logs[l["action_point_id"]].append(l)
        self.docs = {i: ticket_text(t, by_logs[i]) for i, t in self.tickets.items()}
        if extra_docs:
            self.docs.update(extra_docs)
        known = set(self.tickets)
        self.s1 = signal_explicit(self.docs, known)
        self.s2 = idf_overlap(footprints(self.docs, commits), max_df_share=max_df_share)
        self.s3 = idf_overlap({i: vocabulary_terms(d, vocab) for i, d in self.docs.items()}, max_df_share=max_df_share)
        self.s4 = tfidf_cosine(self.docs)
        self.links = links
        self.existing = {(l["from_id"], l["to_id"]) for l in links}
        self.open_ids = {i for i, t in self.tickets.items() if t["status"] != "CLOSED"}

    def scope(self) -> set[str]:
        """Sweep targets: the pending tickets. A pair is a candidate when at least one end is pending; the other end may be any
        ticket (a closed one is what SPLIT_FROM / DUPLICATES / an absorbed scope point at). Two closed tickets are not reviewed:
        the goal is the relations that affect the work still to do."""
        return set(self.open_ids)

    def candidates(self, scope: Optional[set[str]] = None) -> list[dict]:
        return build_candidates(s1=self.s1, s2=self.s2, s3=self.s3, s4=self.s4, existing=self.existing, scope=scope)


def _load(extra_docs: Optional[dict[str, str]] = None, max_df_share: float = MAX_DF_SHARE) -> Analysis:
    tickets = bc.list_tickets()
    logs = bc.list_logs()
    if bc.links_store_present():
        links = bc.list_links()
    else:                       # read-only analysis still works; recording needs the owner's migration first
        print("WARNING: relationship store not applied on this backlog (run scripts/ops/migrate_backlog_links.py --apply): "
              "no link has been reviewed, every candidate is unreviewed.", file=sys.stderr)
        links = []
    return Analysis(tickets, logs, links, load_commits(), build_vocabulary(), extra_docs=extra_docs, max_df_share=max_df_share)


# ─── modes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _short(t: dict, n: int = 70) -> str:
    return (t.get("title") or "")[:n]


def cycle_summary(an: Optional[Analysis] = None) -> dict:
    """Counts for the cycle report's one line (p1p2_cycle_report.py): read-only, no printing. `blocked_in_progress` lists the
    tickets already being worked on whose CONFIRMED DEPENDS_ON prerequisite is still not closed (the attention item)."""
    an = an or _load()
    cands = an.candidates(an.scope())
    confirmed = [l for l in an.links if l["status"] == "CONFIRMED"]
    pending = an.open_ids
    cl = clusters([(a, b) for (a, b) in an.s1 if a in pending and b in pending]
                  + [(l["from_id"], l["to_id"]) for l in confirmed if l["from_id"] in pending and l["to_id"] in pending])
    blocked = [l for l in confirmed if l["link_type"] == "DEPENDS_ON" and l["from_id"] in pending and l["to_id"] in pending]
    return {
        "open": len(pending), "links_confirmed": len(confirmed),
        "candidates_unreviewed": len(cands), "candidates_stated": sum("S1" in c["signals"] for c in cands),
        "clusters": len(cl), "largest_cluster": len(cl[0]) if cl else 0,
        "open_depends_on": len(blocked),
        "blocked_in_progress": sorted({l["from_id"] for l in blocked
                                       if an.tickets[l["from_id"]]["status"] in ("IN_PROGRESS", "READY_FOR_DEPLOY")}),
        "store_applied": bool(an.links) or bc.links_store_present(),
    }


def mode_all(an: Analysis, top: int) -> int:
    scope = an.scope()
    cands = an.candidates(scope)
    confirmed = [(l["from_id"], l["to_id"]) for l in an.links if l["status"] == "CONFIRMED"]
    explicit_open = [(a, b) for (a, b) in an.s1 if a in an.open_ids and b in an.open_ids]
    print(f"tickets {len(an.tickets)}  open {len(an.open_ids)}  sweep scope {len(scope)}  links on record {len(an.links)} "
          f"(confirmed {len(confirmed)})  explicit mentions {len(an.s1)}")
    print(f"unreviewed candidates in scope: {len(cands)}  (S1 {sum('S1' in c['signals'] for c in cands)}, "
          f"S2 {sum('S2' in c['signals'] for c in cands)}, S3 {sum('S3' in c['signals'] for c in cands)}, "
          f"S4 {sum('S4' in c['signals'] for c in cands)})")
    decided = [l for l in an.links if l["status"] in ("CONFIRMED", "REJECTED")]
    forced = [l for l in decided if "FORCED despite" in (l.get("evidence") or "")]
    if forced:      # D-3: systematic overriding of the staleness pre-flight must be visible
        print(f"forced rows (kept despite a stale ticket): {len(forced)} of {len(decided)} decided links "
              f"({len(forced) / len(decided):.0%}) -- each needs its one-line reason in the backlog log")
    cl = clusters(explicit_open + [(a, b) for a, b in confirmed if a in an.open_ids and b in an.open_ids])
    print(f"\nclusters among open tickets (explicit mentions + confirmed links): {len(cl)}")
    for g in cl[:15]:
        print(f"  [{len(g)}] " + ", ".join(g))
    print(f"\ntop {min(top, len(cands))} candidates (conf, type, from -> to, signals):")
    for c in cands[:top]:
        print(f"  {c['conf']:.2f} {c['type']:<12} {c['from_id']} -> {c['to_id']}  {','.join(c['signals'])}")
        print(f"        {_short(an.tickets.get(c['from_id'], {}), 55)} // {_short(an.tickets.get(c['to_id'], {}), 55)}")
    return 0


def mode_for(an: Analysis, tid: str) -> int:
    tid = norm_id(tid)
    if tid not in an.tickets:
        print(f"{tid}: no such ticket")
        return 2
    t = an.tickets[tid]
    print(f"{tid} [{t['status']}] {_short(t, 100)}")
    rc = 0
    mine = [l for l in an.links if tid in (l["from_id"], l["to_id"])]
    if mine:
        print("\nlinks on record (current state):")
        for l in mine:
            other = l["to_id"] if l["from_id"] == tid else l["from_id"]
            arrow = "->" if l["from_id"] == tid else "<-"
            print(f"  {l['status']:<9} {l['link_type']:<12} {arrow} {other} [{an.tickets[other]['status']}]  {l.get('absorbs') or ''}")
            if l["status"] == "CONFIRMED" and l["link_type"] == "DEPENDS_ON" and l["from_id"] == tid \
                    and an.tickets[other]["status"] != "CLOSED":
                print(f"    UNCLOSED PREREQUISITE: {other} must be finished first")
                rc = 1
    cands = [c for c in an.candidates() if tid in (c["from_id"], c["to_id"])]
    if cands:
        print("\nunreviewed candidates:")
        for c in cands[:15]:
            other = c["to_id"] if c["from_id"] == tid else c["from_id"]
            print(f"  {c['conf']:.2f} {c['type']:<12} {other} [{an.tickets[other]['status']}] {','.join(c['signals'])}  {_short(an.tickets[other], 50)}")
            print(f"        {c['evidence'][:200]}")
    if not mine and not cands:
        print("\nno links and no candidates")
    return rc


NEW_TOP = 3                        # --new lists this many open tickets: RANK is informative, the absolute score is not
NEW_FLOOR = 0.03                   # (measured on 36 open tickets with a stated neighbour: the neighbour is in the top 3 for 72%,
                                   #  but neighbours and non-neighbours have the same score range, so no threshold can gate)


def new_overlaps(an: Analysis, new_id: str = "NEW") -> list[tuple]:
    """Open tickets that overlap a not-yet-filed text: [(score, ticket_id, signals, evidence)], best first. A short text must not be
    judged by the thresholds of full tickets, so it is ranked by the weighted sum of the raw signal scores."""
    parts: dict[str, dict] = defaultdict(lambda: {"s": 0.0, "sig": [], "ev": []})

    def other(pair):
        return pair[1] if pair[0] == new_id else pair[0]

    for pair, v in an.s1.items():
        if new_id in pair:
            o = other(pair)
            parts[o]["s"] += 0.75
            parts[o]["sig"].append("S1")
            parts[o]["ev"].append(f"mentioned: {v['evidence']}")
    for name, src, w in (("S2", an.s2, 0.45), ("S3", an.s3, 0.35)):
        for pair, (sc, top) in src.items():
            if new_id in pair:
                o = other(pair)
                parts[o]["s"] += w * sc
                parts[o]["sig"].append(name)
                parts[o]["ev"].append(f"{name} {sc:.2f}: " + ", ".join(top))
    for pair, sc in an.s4.items():
        if new_id in pair:
            o = other(pair)
            parts[o]["s"] += 0.30 * sc
            parts[o]["sig"].append("S4")
            parts[o]["ev"].append(f"S4 {sc:.2f}")
    rows = [(min(1.0, v["s"]), o, v["sig"], " | ".join(v["ev"])) for o, v in parts.items() if o in an.open_ids]
    return sorted(rows, reverse=True)


def mode_new(text: str, top: int) -> int:
    """Pre-filing check: the open tickets closest to a text that is about to become a ticket. Informational (exit 0): the
    operator reads their logs and decides between appending to one and opening a new ticket linked to it."""
    an = _load(extra_docs={"NEW": text}, max_df_share=0.35)
    rows = [r for r in new_overlaps(an) if r[0] >= NEW_FLOOR][:min(top, NEW_TOP)]
    if not rows:
        print("no open ticket resembles this text")
        return 0
    print("closest OPEN tickets (read their logs before opening a new one; a rank, not a verdict):")
    for score, tid, sig, ev in rows:
        print(f"  {score:.2f} {tid} [{an.tickets[tid]['status']}] {_short(an.tickets[tid], 60)}  ({','.join(sig)})")
        print(f"        {ev[:200]}")
    return 0


def mode_evaluate(an: Analysis) -> int:
    explicit = {_pair(a, b) for (a, b) in an.s1 if a in an.tickets and b in an.tickets}
    print(f"explicit (S1) ticket pairs: {len(explicit)}")
    print("recall of each other signal on those pairs (NOT precision: a mention is not always a dependency):")
    for name, src, thr in (("S2 footprint", an.s2, T_S2), ("S3 vocabulary", an.s3, T_S3), ("S4 text", an.s4, T_S4)):
        keys = {_pair(a, b) for (a, b), v in src.items() if (v[0] if isinstance(v, tuple) else v) >= thr}
        hit = len(explicit & keys)
        print(f"  {name:<14} fires on {len(keys):5d} pairs (threshold {thr}); recovers {hit}/{len(explicit)} = "
              f"{hit / max(1, len(explicit)):.0%}")
    cands = an.candidates()
    nonexp = [c for c in cands if "S1" not in c["signals"]]
    reviewed = defaultdict(int)
    for l in an.links:
        reviewed[l["status"]] += 1
    judged = reviewed["CONFIRMED"] + reviewed["REJECTED"]
    print(f"\nreviewed links on record: confirmed {reviewed['CONFIRMED']}, rejected {reviewed['REJECTED']}, proposed {reviewed['PROPOSED']}")
    if judged:
        print(f"  precision of past proposals = confirmed / judged = {reviewed['CONFIRMED'] / judged:.0%} (n={judged})")
    else:
        print("  precision: n/a until a review has been applied (plan step 5)")
    print(f"unreviewed candidates: {len(cands)} total, {len(nonexp)} from S2-S4 alone (the review load beyond the stated links)")
    return 0


def _safe(i: str) -> str:
    return i.replace("-", "_")


def mode_mermaid(an: Analysis, include_candidates: bool) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"{stamp}.md"
    conf = [l for l in an.links if l["status"] == "CONFIRMED"
            and (l["from_id"] in an.open_ids or l["to_id"] in an.open_ids)]
    cand = [c for c in an.candidates(an.scope()) if c["conf"] >= 0.5] if include_candidates else []
    nodes = {x for l in conf for x in (l["from_id"], l["to_id"])} | {x for c in cand for x in (c["from_id"], c["to_id"])}
    lines = ["```mermaid", "graph LR"]
    for n in sorted(nodes):
        t = an.tickets.get(n, {})
        label = f"{n}<br/>{_short(t, 34).replace(chr(34), chr(39))}"
        lines.append(f'  {_safe(n)}["{label}"]:::{"open" if n in an.open_ids else "closed"}')
    for l in conf:
        lines.append(f"  {_safe(l['from_id'])} -->|{l['link_type']}| {_safe(l['to_id'])}")
    for c in cand:
        lines.append(f"  {_safe(c['from_id'])} -.->|{c['type']}?| {_safe(c['to_id'])}")
    lines += ["  classDef open fill:#fde68a,stroke:#92400e", "  classDef closed fill:#e5e7eb,stroke:#6b7280", "```"]
    path.write_text(f"# Backlog links - {stamp}\n\nSolid = confirmed; dashed = unreviewed candidate (conf >= 0.5).\n\n"
                    + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def mode_review_out(an: Analysis) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = OUT_DIR / f"review_{stamp}"
    if base.with_suffix(".csv").exists():
        raise SystemExit(f"{base.with_suffix('.csv')} exists; refusing to overwrite (D-2)")
    cands = an.candidates(an.scope())
    rows = []
    for k, c in enumerate(cands, 1):
        f, t = an.tickets.get(c["from_id"], {}), an.tickets.get(c["to_id"], {})
        rows.append({"row": k, "from_id": c["from_id"], "to_id": c["to_id"], "type": c["type"], "conf": c["conf"],
                     "signals": ",".join(c["signals"]), "evidence": c["evidence"], "from_status": f.get("status", ""),
                     "from_update_ts": f.get("update_ts").isoformat() if f.get("update_ts") else "",
                     "to_status": t.get("status", ""), "to_update_ts": t.get("update_ts").isoformat() if t.get("update_ts") else "",
                     "decision": "", "absorbs": "", "note": ""})
    with base.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_COLS)
        w.writeheader()
        w.writerows(rows)
    md = [f"# Link review {stamp}", "", f"{len(rows)} candidates. Fill `decision` in the CSV with `confirm`, `reject` or "
          f"`retype:<{'|'.join(bc.LINK_TYPES)}>`; blank = skip. Optional `absorbs` (OVERLAPS: the scope taken over) and `note`.", "",
          "| row | from | to | type | conf | signals | titles |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['row']} | {r['from_id']} | {r['to_id']} | {r['type']} | {r['conf']} | {r['signals']} | "
                  f"{_short(an.tickets.get(r['from_id'], {}), 40)} // {_short(an.tickets.get(r['to_id'], {}), 40)} |")
    base.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return base.with_suffix(".csv")


def mode_apply_review(path: Path, confirm: bool, forced: set[tuple], username: str) -> int:
    path = path.resolve()
    if path.parent.name == "archived" or (path.parent / "archived" / path.name).exists():
        print(f"REFUSED: {path.name} was already applied (archived) -- generate a new review (D-2)")
        return 2
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    decided = [r for r in rows if (r.get("decision") or "").strip()]
    live = {t["action_point_id"]: t for t in bc.list_tickets(ids=sorted({r[k] for r in decided for k in ("from_id", "to_id")}))}
    flagged = stale_rows(decided, live)
    events, notes = build_events(decided, flagged, forced, username, path.name)
    print(f"{path.name}: {len(rows)} rows, {len(decided)} decided -> {len(events)} events")
    for n in notes:
        print("  " + n)
    if forced:
        print(f"forced rows: {sum(1 for n in notes if n.startswith('FORCED'))}/{len(decided)} decided "
              f"(a forced row needs a one-line reason in the session's backlog log -- NORMAS_BACKLOG.md)")
    if not confirm:
        print("DRY RUN: nothing written (add --confirm to insert the events)")
        return 0
    ids = bc.record_link_events(events)
    arch = path.parent / "archived"
    arch.mkdir(exist_ok=True)
    for sib in (path, path.with_suffix(".md")):
        if sib.exists():
            shutil.move(str(sib), str(arch / sib.name))
    print(f"inserted {len(ids)} events; review archived to {arch}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--for", dest="for_id", metavar="FND-nnnn")
    g.add_argument("--new", metavar="TEXT")
    g.add_argument("--evaluate", action="store_true")
    g.add_argument("--mermaid", action="store_true")
    g.add_argument("--review-out", action="store_true")
    g.add_argument("--apply-review", metavar="FILE.csv")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--with-candidates", action="store_true", help="--mermaid: also draw unreviewed candidates (dashed)")
    p.add_argument("--confirm", action="store_true", help="--apply-review: actually insert the events")
    p.add_argument("--force-row", action="append", default=[], metavar="FROM:TO:TYPE",
                   help="--apply-review: keep a flagged (stale) row; repeatable")
    p.add_argument("--user", default=None, help="username recorded on the events (default: OS user)")
    a = p.parse_args(argv)
    try:
        if a.new is not None:
            return mode_new(a.new, a.top)
        if a.apply_review:
            forced = set()
            for spec in a.force_row:
                parts = spec.split(":")
                if len(parts) != 3:
                    p.error(f"--force-row expects FROM:TO:TYPE, got {spec!r}")
                forced.add((norm_id(parts[0]), norm_id(parts[1]), parts[2].upper()))
            return mode_apply_review(Path(a.apply_review), a.confirm, forced, a.user or getpass.getuser())
        an = _load()
        if a.all:
            return mode_all(an, a.top)
        if a.for_id:
            return mode_for(an, a.for_id)
        if a.evaluate:
            return mode_evaluate(an)
        if a.mermaid:
            print(mode_mermaid(an, a.with_candidates))
            return 0
        if a.review_out:
            print(mode_review_out(an))
            return 0
    except bc.BacklogUnavailable as exc:
        print(f"BACKLOG UNAVAILABLE: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
