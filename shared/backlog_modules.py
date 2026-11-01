# -*- coding: utf-8 -*-
"""
backlog_modules.py -- which modules a ticket is about, derived (Backlog v3 W4b, FND-0360; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md section W4b).

Pure rules; the runner (scripts/audit/backlog_modules.py) gathers the facts from git and the backlog and records what is new through backlog.sp_record_module. No database, no git, no network here.

Three things are decided here and nowhere else (P#11):
  1. what counts as a MODULE: a tracked file, with a test mapped to the file it tests and the registries and documents left out (tools and registries are mentioned in ticket logs all the time and
     would form false clusters: on 2026-11-01, 70 of 81 open tickets cited a tracked file, and the most cited were backlog_links.py, close_release.py and AGENTS.md);
  2. what a ticket's TEXT can claim: only CITED, which proposes and never forms a cluster; the membership signals are TARGET (declared) and TOUCHED (a commit changed it);
  3. the set of facts to record: the difference between what the sources say now and what the current set already holds.
Nothing here is a gate and nothing needs a person's approval.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

# A path under one of these prefixes, or one of these exact files, is a registry or a document: it records decisions of many tickets and is not logic that one fix changes.
REGISTRY_PREFIXES = ("doc/", "docs/", "deployments/", "out/", "backlog_gate/", ".github/")
REGISTRY_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "README.md", ".gitignore", "shared/config.py", "scripts/ops/migrate_backlog_v2.py"})
REGISTRY_SUFFIXES = (".md", ".txt", ".rst", ".json", ".lock", ".csv", ".yml", ".yaml", ".toml", ".ini", ".cfg")
MODULE_SUFFIXES = (".py", ".sql", ".bat", ".cmd", ".ps1", ".sh")
_TOKEN = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:py|sql|bat|cmd|ps1|sh|yml|yaml|md|json|txt)\b")
_TEST = re.compile(r"(?:^|/)tests?/(?:.*/)?test_(?P<name>[A-Za-z0-9_]+)\.py$")
_OBJECT_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")


def clean_path(path: str) -> str:
    """The path with forward slashes and without a leading `./` (and nothing else: `.github/x` keeps its dot). Pure."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def is_registry(path: str) -> bool:
    """True for a path that is a registry, a document or a configuration file, never a module. Pure."""
    p = clean_path(path)
    return p in REGISTRY_FILES or p.startswith(REGISTRY_PREFIXES) or p.lower().endswith(REGISTRY_SUFFIXES)


def index_tracked(tracked: list) -> tuple:
    """(set of tracked paths, {file name: [paths]}) from `git ls-files`. Pure."""
    paths = {p.replace("\\", "/") for p in tracked if p}
    by_base: dict = defaultdict(list)
    for p in sorted(paths):
        by_base[p.rsplit("/", 1)[-1]].append(p)
    return paths, by_base


def logical_module(path: str, tracked: set, by_base: dict) -> Optional[str]:
    """The module a path stands for, or None when it stands for none. A test maps to the one non-test file it is named after (tests/test_backlog_client.py -> shared/backlog_client.py); a test with
    no such unique file, a registry, a document, a file that is not tracked and a file that is not code are None. Pure."""
    p = clean_path(path)
    if p not in tracked or is_registry(p) or not p.endswith(MODULE_SUFFIXES):
        return None
    m = _TEST.search(p)
    if m:
        base = m.group("name") + ".py"
        targets = [c for c in by_base.get(base, []) if not _TEST.search(c) and not is_registry(c)]
        return targets[0] if len(targets) == 1 else None
    if p.rsplit("/", 1)[-1] in ("conftest.py", "__init__.py"):
        return None
    return p


def extract_citations(text: str, tracked: set, by_base: dict) -> set:
    """The modules a text mentions: a path that is tracked, or a bare file name that is unique in the repository, normalised by logical_module. Pure."""
    found = set()
    for tok in _TOKEN.findall(text or ""):
        tok = clean_path(tok)
        if tok not in tracked and "/" not in tok and len(by_base.get(tok, [])) == 1:
            tok = by_base[tok][0]
        mod = logical_module(tok, tracked, by_base)
        if mod:
            found.add(mod)
    return found


def object_module(object_name: Optional[str], tracked: set, by_base: dict) -> Optional[str]:
    """The module an `object_name` names, when it is a tracked file (most object names are tables or jobs, which are not modules). Pure."""
    name = (object_name or "").strip()
    if not name or not _OBJECT_PATH.match(name):
        return None
    return extract_citations(name, tracked, by_base).pop() if extract_citations(name, tracked, by_base) else None


def text_facts(ticket: dict, logs: list, tracked: set, by_base: dict) -> list:
    """[{module, role, source, evidence}] a ticket's own words propose: CITED from the title and description (TEXT), from its progress logs (TEXT) and from its object name (OBJECT_NAME). One fact
    per (module, source); the evidence says where it was first seen. Pure."""
    seen: dict = {}
    for where, text in (("title", ticket.get("title")), ("description", ticket.get("scenario_description"))):
        for mod in sorted(extract_citations(text, tracked, by_base)):
            seen.setdefault((mod, "TEXT"), f"named in the {where}")
    for text in logs:
        for mod in sorted(extract_citations(text, tracked, by_base)):
            seen.setdefault((mod, "TEXT"), "named in a progress log")
    obj = object_module(ticket.get("object_name"), tracked, by_base)
    if obj:
        seen.setdefault((obj, "OBJECT_NAME"), "the ticket's object name")
    return [{"module": m, "role": "CITED", "source": s, "evidence": why} for (m, s), why in sorted(seen.items())]


def commit_facts(files_by_commit: dict, tracked: set, by_base: dict) -> list:
    """[{module, role, source, evidence}] one TOUCHED per module changed by the commits of ONE ticket. `files_by_commit` = {sha: [paths]}; the evidence names the first commit that touched it. Pure."""
    seen: dict = {}
    for sha in sorted(files_by_commit):
        for path in files_by_commit[sha]:
            mod = logical_module(path, tracked, by_base)
            if mod:
                seen.setdefault(mod, f"commit {sha[:12]}")
    return [{"module": m, "role": "TOUCHED", "source": "COMMIT", "evidence": why} for m, why in sorted(seen.items())]


def new_facts(desired: dict, current: set) -> list:
    """[(ticket, fact)] to record: what the sources say now (`desired` = {ticket: [fact, ...]}) minus what the current set already holds (`current` = {(ticket, module, symbol, role, source)}). A fact the
    database already holds is not sent again; one it refuses (a rejected proposal) answers NULL there. Pure."""
    out = []
    for ap in sorted(desired):
        for f in desired[ap]:
            if (ap, f["module"], None, f["role"], f["source"]) not in current:
                out.append((ap, f))
    return out
