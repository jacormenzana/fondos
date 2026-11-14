"""The layout of scripts/ is a rule, not a habit (scripts/AGENTS.md): five domains, an object folder under each, every folder described, and no living file citing a path from before the 2026-11-14 reorganisation.

Pure stdlib. It walks the working tree (never calls git: the delivery receipt runs it on an exported copy of the commit) and reads the old -> new map from scripts/AGENTS.md itself, so the document
and the guard cannot drift apart.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
GUIDE = SCRIPTS / "AGENTS.md"
DOMAINS = {"audit", "gov", "launch", "mig", "mnt"}
FLAT = {"launch"}                                    # launch/ is flat by design (launch/lib holds its helpers)

# Dated records and generated mirrors keep the paths of their day (scripts/AGENTS.md "Old -> new path map").
RECORD_PREFIXES = ("deployments/", "doc/decisiones/", "doc/memoria/", "doc/analisis/", "doc/especificacion/", "doc/instrucciones/", "doc/situacion/", "doc/skill/", "doc/api_key/",
                   "db/", "upload/", "out/")
LIVING_BACKLOG = ("doc/backlog/PKG_", "doc/backlog/HANDOFF_S0_PACKAGE_1.md", "doc/backlog/HANDOFF_CLUSTERS_C_D_E_G.md")
RECORD_FILES = {"scripts/AGENTS.md", "scripts/gov/repo/agents_md_split_ledger.txt", "scripts/mig/backlog/backlog_v3_manifest.yaml", "tests/test_scripts_layout.py"}
TEXT_EXT = (".py", ".sh", ".bat", ".ps1", ".md", ".yml", ".yaml", ".json", ".txt", ".cmd", ".toml", ".cfg", ".ini")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", "out", "upload", "log", "logs"}      # generated and ignored: runtime logs and outputs are not source
# scripts/diag held tools that were retired long before the reorganisation; these living files still tell that history. Nothing else may name the folder.
HISTORICAL_DIAG = {
    "proyecto1/core/classify_utils.py", "proyecto1/core/cost_arbitration.py", "proyecto1/core/cost_format_router.py", "proyecto1/core/cost_format_signals.py",
    "proyecto1/core/cost_table_parser.py", "proyecto1/tests/test_cost_arbitration_primitives_resolve.py", "proyecto1/tests/test_statistical_audit_persistence_pg.py",
    "shared/config.py", "doc/reglas/AUDITORIA_ESTADISTICA.md",
}


def _old_paths() -> list:
    """The first column of the map table in scripts/AGENTS.md: the paths (under scripts/) that existed before the reorganisation."""
    text = GUIDE.read_text(encoding="utf-8")
    section = text.split("## Old -> new path map", 1)[1]
    return re.findall(r"^\| `([^`|]+)` \| `[^`|]+` \|$", section, flags=re.M)


def _living_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not rel.lower().endswith(TEXT_EXT) or rel in RECORD_FILES or rel.startswith(RECORD_PREFIXES):
                continue
            if rel.startswith("doc/backlog/") and not rel.startswith(LIVING_BACKLOG):
                continue
            yield rel


def _dirs_with_files(domain: str) -> set:
    out = set()
    for dirpath, dirnames, filenames in os.walk(SCRIPTS / domain):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        if filenames:
            out.add(Path(dirpath).relative_to(SCRIPTS).as_posix())
    return out


def test_the_five_domains_and_nothing_else():
    tops = {p.name for p in SCRIPTS.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert tops == DOMAINS, f"top-level folders of scripts/: {sorted(tops)}; the model has exactly {sorted(DOMAINS)} (scripts/AGENTS.md)"
    loose = sorted(p.name for p in SCRIPTS.iterdir() if p.is_file())
    assert loose == ["AGENTS.md", "CLAUDE.md"], f"files directly in scripts/: {loose}"


def test_no_file_sits_directly_in_a_domain_folder():
    for domain in sorted(DOMAINS - FLAT):
        loose = sorted(p.name for p in (SCRIPTS / domain).iterdir() if p.is_file())
        assert not loose, f"scripts/{domain}/ holds files at its root ({loose}): put them in an object folder"


def test_every_folder_that_holds_files_is_described_in_the_guide():
    guide = GUIDE.read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z0-9_/]+)/` \|", guide.split("## The folders", 1)[1].split("## Rules", 1)[0], flags=re.M))
    present = {"launch"}
    for domain in sorted(DOMAINS - FLAT):
        present |= _dirs_with_files(domain)
    assert present - listed == set(), f"folders with files that scripts/AGENTS.md does not describe: {sorted(present - listed)}"
    assert listed - present == set(), f"folders described in scripts/AGENTS.md that do not exist or are empty: {sorted(listed - present)}"


def test_the_guide_carries_the_old_to_new_map():
    olds = _old_paths()
    assert len(olds) >= 100, "the old -> new table of scripts/AGENTS.md is missing or truncated"
    assert "audit/delivery_attest.py" in olds and "ops/close_release.py" in olds


def test_no_living_file_cites_a_path_from_before_the_reorganisation():
    olds = _old_paths() + ["launch/redireccion", "launch/P2_LoadMetrics.txt"]
    alts = []
    for p in olds:
        parts = p.split("/")
        alts += [re.escape(("scripts/" + "/".join(parts))), re.escape("scripts\\" + "\\".join(parts)), re.escape("scripts\\\\" + "\\\\".join(parts))]
        if p.endswith(".py"):
            alts.append(re.escape("scripts." + ".".join(parts)[:-3]) + r"(?![\w])")        # dotted module path, without the .py
    cites = re.compile("|".join(alts).encode())
    dead = re.compile(rb"scripts[/\\]+(?:ops|debug)\b|scripts[/\\]+diag\b|\bscripts\.(?:ops|debug|diag)\b")
    offenders = []
    for rel in _living_files():
        try:
            data = (ROOT / rel).read_bytes()
        except OSError:
            continue
        m = cites.search(data)
        if m:
            offenders.append(f"{rel}: {m.group(0).decode('utf-8', 'replace')} (see the map in scripts/AGENTS.md)")
            continue
        m = dead.search(data)
        if m and not (rel in HISTORICAL_DIAG and b"diag" in m.group(0)):
            offenders.append(f"{rel}: {m.group(0).decode('utf-8', 'replace')} (a folder that no longer exists)")
    assert not offenders, "living files cite a path from before the reorganisation:\n  " + "\n  ".join(offenders[:40])
