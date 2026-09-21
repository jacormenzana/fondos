"""
sync_agents_md.py — Dynamic re-sync of AUTO sentinel blocks in AGENTS.md
                     and other governed markdown files.

Sentinel format:
    <!-- AUTO:BEGIN <name> -->
    ...auto-generated content...
    <!-- AUTO:END <name> -->

Implemented sentinels (fully regenerated from code — all enforced at commit):
  AGENTS.md:
    schema-version          shared/config.py  SCHEMA_VERSION
    kill-switches-line      shared/config.py  all *_ENABLED bool constants (source order)
    skills-table            .claude/skills/*.md  names + extracted description (alpha order)
    p1-module-map           proyecto1/{core,blocks}/*.py  canonical source modules
    p2-module-map           proyecto2/src/**/*.py + tests/**/*.py  canonical modules + tests
    p3-module-map           proyecto3/src/*.py  canonical source modules
    db-tables               db/schema_fondos.sql  all CREATE TABLE declarations, bucketed by domain
    launchers               scripts/launch/*.bat  canonical operational launchers
    macro-factors           proyecto2/src/calculations/macro_sensitivity.py  _FACTOR_TO_METRIC
    data-sources            proyecto2/src/discovery/macro_discovery.py  SOURCES registry

  doc/reglas/SCHEMA_REFERENCE.md:
    schema-reference-tables db/schema_fondos.sql  same table roster as db-tables

Advisory checks (warn only — non-blocking):
  calc-version        CALC_VERSION in run_pipeline.py vs mention in AGENTS.md

Non-canonical files are excluded by _is_noncanonical():
  - dated variants: name contains _YYYYMMDD
  - _prod / _last suffixes
  - private/init files starting with _
  - in-tree test_* files outside a proper tests/ directory
  - BackUp / Backup in name

Usage:
  python scripts/audit/sync_agents_md.py           # report mode (default)
  python scripts/audit/sync_agents_md.py --check   # exit 1 on sentinel drift
  python scripts/audit/sync_agents_md.py --write   # update all governed files in-place

Multi-file enforcement: drift in any governed file (AGENTS.md or SCHEMA_REFERENCE.md)
fails --check with exit code 1.

Pre-commit hook installer: scripts/audit/pre-commit.sh
"""
import ast
import re
import sys
from pathlib import Path

ROOT             = Path(__file__).resolve().parents[2]
AGENTS           = ROOT / "AGENTS.md"
SCHEMA_REFERENCE = ROOT / "doc/reglas/SCHEMA_REFERENCE.md"

_SENTINEL_RE = re.compile(
    r"<!-- AUTO:BEGIN (\S+) -->\n(.*?)\n<!-- AUTO:END \1 -->",
    re.DOTALL,
)

# Domain mapping for DB tables (in schema order: P1 → P2 → P3)
_TABLE_DOMAIN: dict[str, str] = {
    # P1
    "fund_master":             "P1",
    "fund_cost_schedule":      "P1",
    "fund_kiid_metadata":      "P1",
    "ingestion_log":           "P1",
    "fund_data_quality_issues":"P1",
    "fund_families":           "P1",
    "fund_benchmarks":         "P1",
    # P2
    "series_macro":            "P2",
    "series_benchmark":        "P2",
    "series_inflation":        "P2",
    "fund_metrics":            "P2",
    "p2_pipeline_log":         "P2",
    "nav_sources":             "P2",
    "fund_nav_monthly":        "P2",
    "fund_nav_daily":          "P2",
    "fund_metric_timeseries":  "P2",
    "fund_metric_alerts":      "P2",
    "fund_metric_state":       "P2",
    # P3
    "fund_scores":             "P3",
    "portfolio_scenarios":     "P3",
    "portfolio_weights":       "P3",
    "rotation_costs":          "P3",
    # P1
    "fund_cost_corrections":   "P1",
    # Cross-domain (statistical audit engine — doc/reglas/AUDITORIA_ESTADISTICA.md)
    "audit_statistic":         "P1/P2",
    "audit_finding":           "P1/P2",
}


# ── Non-canonical exclusion helper ────────────────────────────────────────────

def _is_noncanonical(name: str, is_test: bool = False) -> bool:
    """Return True for files that are variants/backups and should not be inventoried.

    is_test=True relaxes the dated-pattern check: test files in a proper tests/
    directory may carry a date-stamp in their name (regression test for a fix)
    and are still canonical.
    """
    # Private / init (starts with _)
    if name.startswith("_"):
        return True
    # BackUp / Backup copies
    if "BackUp" in name or "Backup" in name:
        return True
    # _prod suffix  (e.g. fund_scorer_prod.py)
    stem = Path(name).stem
    if stem.endswith("_prod"):
        return True
    # _last suffix  (e.g. dla_table_serializer_last.py)
    if stem.endswith("_last"):
        return True
    # Dated variants: _YYYYMMDD anywhere in the stem (e.g. run_pipeline_20260605_00.py)
    if not is_test and re.search(r"_\d{8}", stem):
        return True
    # In-tree test files: test_* only non-canonical when NOT inside a tests/ directory
    if not is_test and name.startswith("test_"):
        return True
    return False


# ── Extractors (shared utilities) ─────────────────────────────────────────────

def _config_assigns() -> dict:
    """Return {name: constant_value} for Assign and AnnAssign nodes in shared/config.py."""
    src = (ROOT / "shared/config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and isinstance(node.value, ast.Constant):
                    result[tgt.id] = node.value.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.value is not None
                and isinstance(node.value, ast.Constant)
            ):
                result[node.target.id] = node.value.value
    return result


def _kill_switches_ordered() -> list[tuple[str, bool]]:
    """Return [(name, value), ...] in source-file order for all *_ENABLED booleans."""
    src = (ROOT / "shared/config.py").read_text(encoding="utf-8")
    return [
        (m.group(1), m.group(2) == "True")
        for m in re.finditer(
            r"^(\w+_ENABLED)\s*(?::\s*\w+)?\s*=\s*(True|False)",
            src,
            re.MULTILINE,
        )
    ]


def _skill_description(path: Path) -> str:
    """Extract the best one-line description from a skill .md file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    fm = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if fm:
        m = re.search(r"^description:\s*(.+)", fm.group(1), re.MULTILINE)
        if m:
            return m.group(1).strip()[:150]
    m = re.search(r"\*\*Description:\*\*\s*(.+)", text)
    if m:
        return m.group(1).strip()[:150]
    in_fm = False
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if i == 0 and stripped == "---":
            in_fm = True
            continue
        if in_fm:
            if stripped == "---":
                in_fm = False
            continue
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:150]
    return ""


def _py_modules_in(
    directory: Path,
    recursive: bool = False,
    is_test_dir: bool = False,
) -> list[Path]:
    """Return canonical .py files under directory, non-canonical excluded."""
    pattern = "**/*.py" if recursive else "*.py"
    result = []
    # key=lambda p: p.name, NOT bare sorted(paths) — pathlib.Path.__lt__ compares
    # case-INsensitively on Windows (WindowsPath) but case-sensitively on POSIX (PosixPath), so
    # the same code silently produced a different table order per OS (found live 2026-09-21: this
    # generator's own p1-module-map sentinel passed locally on Windows but failed for real on its
    # first-ever Linux CI run, e.g. PATCHES_pipeline.py's uppercase P sorts before all lowercase
    # names case-sensitively but lands mid-alphabet case-insensitively). An explicit string key
    # makes the sort — and the generated AGENTS.md content — deterministic across platforms.
    for f in sorted(directory.glob(pattern), key=lambda p: p.name):
        if "__pycache__" in f.parts:
            continue
        if _is_noncanonical(f.name, is_test=is_test_dir):
            continue
        result.append(f)
    return result


def _ast_dict_items(src: str, var_name: str) -> list[tuple[str, str]]:
    """
    AST-parse src and return [(key, value), ...] for a module-level dict assignment
    (Assign or AnnAssign) whose target name matches var_name.
    Keys must be string Constants; values may be Constants, Name (function reference),
    or Attribute nodes.  Returns empty list if the variable is not found.
    """
    tree = ast.parse(src)

    def _extract_dict(dict_node: ast.Dict) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for k, v in zip(dict_node.keys, dict_node.values):
            if not isinstance(k, ast.Constant):
                continue
            if isinstance(v, ast.Constant):
                val_str = str(v.value)
            elif isinstance(v, ast.Name):
                val_str = v.id
            elif isinstance(v, ast.Attribute):
                val_str = v.attr
            else:
                val_str = "?"
            pairs.append((str(k.value), val_str))
        return pairs

    for node in ast.walk(tree):
        # Plain assignment:  VAR = {...}
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == var_name:
                    if isinstance(node.value, ast.Dict):
                        return _extract_dict(node.value)
        # Annotated assignment:  VAR: type = {...}
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == var_name
                and node.value is not None
                and isinstance(node.value, ast.Dict)
            ):
                return _extract_dict(node.value)
    return []


# ── Generators ────────────────────────────────────────────────────────────────

def gen_schema_version() -> str:
    assigns = _config_assigns()
    ver = assigns.get("SCHEMA_VERSION", "???")
    return (
        f"DB: `db/fondos.sqlite` (schema {ver}). "
        r"Master list: `c:\data\fondos\in\GestoresDeFondosv1.xlsx`."
    )


def gen_kill_switches_line() -> str:
    assigns = _config_assigns()
    ver = assigns.get("SCHEMA_VERSION", "???")
    switches = _kill_switches_ordered()
    names = ", ".join(f"`{n}`" for n, _ in switches)
    return (
        f"- `shared/config.py` — all constants: `DB_PATH`, `SCHEMA_VERSION` (`\"{ver}\"`), "
        f"`DOMAIN_VALUES`, `ATTRIBUTE_CATALOG`, kill-switches ({names})"
    )


def gen_skills_table() -> str:
    skills_dir = ROOT / ".claude/skills"
    skills = sorted(
        [(f.stem, _skill_description(f)) for f in skills_dir.glob("*.md")],
        key=lambda x: x[0].lower(),
    )
    header = "| Skill | Purpose |\n|-------|---------|"
    rows = "\n".join(f"| `{name}` | {desc} |" for name, desc in skills)
    return f"{header}\n{rows}"


def gen_p1_module_map() -> str:
    """Table of all canonical P1 source modules (core + blocks)."""
    core_dir   = ROOT / "proyecto1/core"
    blocks_dir = ROOT / "proyecto1/blocks"

    core_files   = _py_modules_in(core_dir)
    blocks_files = _py_modules_in(blocks_dir)

    header = "| Module | Folder |\n|--------|--------|"
    rows = []
    for f in core_files:
        rows.append(f"| `{f.name}` | `proyecto1/core` |")
    for f in blocks_files:
        rows.append(f"| `{f.name}` | `proyecto1/blocks` |")
    return f"{header}\n" + "\n".join(rows)


def _tree_block(root_label: str, files: list[Path], src_root: Path) -> str:
    """Render a set of paths as an indented tree code block."""
    from collections import defaultdict
    tree: dict[str, list[str]] = defaultdict(list)
    for f in files:
        rel = f.relative_to(src_root)
        parts = rel.parts
        if len(parts) == 1:
            tree[""].append(parts[0])
        else:
            tree["/".join(parts[:-1])].append(parts[-1])

    lines = [f"{root_label}/"]
    # Sort directories, then files in root
    dirs = sorted(k for k in tree if k)
    for d in dirs:
        lines.append(f"  {d}/")
        for fname in sorted(tree[d]):
            lines.append(f"    {fname}")
    for fname in sorted(tree[""]):
        lines.append(f"  {fname}")

    return "```\n" + "\n".join(lines) + "\n```"


def gen_p2_module_map() -> str:
    """Tree of all canonical P2 source modules and tests."""
    src_root   = ROOT / "proyecto2/src"
    tests_root = ROOT / "proyecto2/tests"

    src_files   = _py_modules_in(src_root, recursive=True, is_test_dir=False)
    test_files  = _py_modules_in(tests_root, recursive=True, is_test_dir=True) if tests_root.exists() else []

    # Build tree: group by sub-path under src/ for source, under tests/ for tests
    from collections import defaultdict

    def _build_tree(files: list[Path], base: Path, label: str) -> list[str]:
        tree: dict[str, list[str]] = defaultdict(list)
        for f in files:
            rel = f.relative_to(base)
            parts = rel.parts
            if len(parts) == 1:
                tree[""].append(parts[0])
            else:
                sub = "/".join(parts[:-1])
                tree[sub].append(parts[-1])
        out = [f"  {label}/"]
        for d in sorted(k for k in tree if k):
            out.append(f"    {d}/")
            for fname in sorted(tree[d]):
                out.append(f"      {fname}")
        for fname in sorted(tree[""]):
            out.append(f"    {fname}")
        return out

    lines = ["proyecto2/"]
    lines += _build_tree(src_files, src_root, "src")
    if test_files:
        lines += _build_tree(test_files, tests_root, "tests")

    return "```\n" + "\n".join(lines) + "\n```"


def gen_p3_module_map() -> str:
    """Table of all canonical P3 source modules."""
    src_dir = ROOT / "proyecto3/src"
    files   = _py_modules_in(src_dir)

    header = "| Module | Folder |\n|--------|--------|"
    rows   = [f"| `{f.name}` | `proyecto3/src` |" for f in files]
    return f"{header}\n" + "\n".join(rows)


def gen_db_tables() -> str:
    """Table of all CREATE TABLE declarations in schema_fondos.sql, bucketed by domain."""
    sql_path = ROOT / "db/schema_fondos.sql"
    sql = sql_path.read_text(encoding="utf-8", errors="ignore")
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)

    header = "| Table | Domain |\n|-------|--------|"
    rows = []
    for t in tables:
        domain = _TABLE_DOMAIN.get(t, "?")
        rows.append(f"| `{t}` | {domain} |")
    return f"{header}\n" + "\n".join(rows)


def gen_launchers() -> str:
    """Table of all canonical operational launchers in scripts/launch/."""
    launch_dir = ROOT / "scripts/launch"
    # key=lambda p: p.name — same cross-platform sort fix as _py_modules_in() above; a bare
    # sorted(paths) here has the identical Windows-vs-POSIX Path.__lt__ discrepancy.
    bat_files  = sorted(
        (f for f in launch_dir.glob("*.bat") if not _is_noncanonical(f.name)),
        key=lambda p: p.name,
    )

    def _domain(name: str) -> str:
        for prefix in ("P1_", "P2_", "P3_", "P4_"):
            if name.startswith(prefix):
                return prefix.rstrip("_")
        return "—"

    header = "| Script | Domain |\n|--------|--------|"
    rows   = [f"| `{f.name}` | {_domain(f.name)} |" for f in bat_files]
    return f"{header}\n" + "\n".join(rows)


def gen_macro_factors() -> str:
    """Two-column table of OLS macro betas from _FACTOR_TO_METRIC in macro_sensitivity.py."""
    src_path = ROOT / "proyecto2/src/calculations/macro_sensitivity.py"
    src = src_path.read_text(encoding="utf-8")
    pairs = _ast_dict_items(src, "_FACTOR_TO_METRIC")
    if not pairs:
        raise ValueError("_FACTOR_TO_METRIC not found in macro_sensitivity.py")
    header = "| Factor key | Metric |\n|------------|--------|"
    rows = "\n".join(f"| `{fk}` | `{mv}` |" for fk, mv in pairs)
    return f"{header}\n{rows}"


def gen_data_sources() -> str:
    """Two-column table of macro data sources from SOURCES registry in macro_discovery.py."""
    src_path = ROOT / "proyecto2/src/discovery/macro_discovery.py"
    src = src_path.read_text(encoding="utf-8")
    pairs = _ast_dict_items(src, "SOURCES")
    if not pairs:
        raise ValueError("SOURCES not found in macro_discovery.py")
    header = "| Source | Loader |\n|--------|--------|"
    rows = "\n".join(f"| `{src_name}` | `{loader}` |" for src_name, loader in pairs)
    return f"{header}\n{rows}"


def gen_schema_reference_tables() -> str:
    """Table-name roster for SCHEMA_REFERENCE.md — same content as gen_db_tables()."""
    return gen_db_tables()


# ── GENERATORS registry ───────────────────────────────────────────────────────
# Values are either a plain callable (target = AGENTS) or a (callable, Path) tuple.

GENERATORS: dict = {
    # AGENTS.md sentinels (10)
    "schema-version":           gen_schema_version,
    "kill-switches-line":       gen_kill_switches_line,
    "skills-table":             gen_skills_table,
    "p1-module-map":            gen_p1_module_map,
    "p2-module-map":            gen_p2_module_map,
    "p3-module-map":            gen_p3_module_map,
    "db-tables":                gen_db_tables,
    "launchers":                gen_launchers,
    "macro-factors":            gen_macro_factors,
    "data-sources":             gen_data_sources,
    # doc/reglas/SCHEMA_REFERENCE.md sentinels (1)
    "schema-reference-tables":  (gen_schema_reference_tables, SCHEMA_REFERENCE),
}

# ── Advisory checks ───────────────────────────────────────────────────────────

def _advisory_checks(agents_text: str) -> list[str]:
    warnings: list[str] = []

    # CALC_VERSION drift (module maps + DB tables now enforced via sentinels;
    # only CALC_VERSION still needs an advisory since it's an inline value)
    pipeline = ROOT / "proyecto2/src/pipeline/run_pipeline.py"
    if pipeline.exists():
        src = pipeline.read_text(encoding="utf-8")
        m = re.search(r'CALC_VERSION\s*(?::\s*\w+)?\s*=\s*["\'](\d{8})["\']', src)
        if m and m.group(1) not in agents_text:
            warnings.append(f"CALC_VERSION '{m.group(1)}' not mentioned in AGENTS.md")

    # Domain-doc table row count vs non-legacy doc/reglas/*.md file count.
    # Catches prose/table drift when docs are added without updating AGENTS.md.
    reglas_dir = ROOT / "doc/reglas"
    if reglas_dir.exists():
        canonical_docs = [
            f for f in reglas_dir.glob("*.md")
            if not f.name.startswith("legacy_")
        ]
        fs_count = len(canonical_docs)
        # Count rows in the domain-doc table (lines of the form "| `*.md` |")
        table_rows = re.findall(r"^\|\s*`[^`]+\.md`\s*\|", agents_text, re.MULTILINE)
        table_count = len(table_rows)
        if fs_count != table_count:
            doc_names = ", ".join(sorted(f.name for f in canonical_docs))
            warnings.append(
                f"Domain-doc table has {table_count} row(s) but {fs_count} canonical "
                f"doc/reglas/*.md file(s) exist ({doc_names}). "
                f"Update the domain-doc table in AGENTS.md."
            )

    return warnings


# ── Core sync logic ───────────────────────────────────────────────────────────

def _make_replacer(
    gens: dict,
    drift_accumulator: list,
    mode: str,
    target_name: str,
):
    """Return a re.sub callback that replaces sentinel blocks using the given generators."""
    def _replacer(m: re.Match) -> str:
        name    = m.group(1)
        current = m.group(2)
        gen = gens.get(name)
        if gen is None:
            return m.group(0)
        try:
            fresh = gen()
        except Exception as exc:
            print(f"  ERROR generating [{name}]: {exc}", file=sys.stderr)
            return m.group(0)
        if fresh.rstrip() != current.rstrip():
            drift_accumulator.append(name)
            if mode in ("report", "check"):
                print(f"\nDRIFT in sentinel [{name}] ({target_name}):")
                print("  CURRENT:\n    " + current.replace("\n", "\n    "))
                print("  FRESH:\n    "   + fresh.replace("\n", "\n    "))
        return f"<!-- AUTO:BEGIN {name} -->\n{fresh}\n<!-- AUTO:END {name} -->"
    return _replacer


def sync(mode: str) -> int:
    """
    mode: 'report' | 'check' | 'write'
    Processes all governed files (AGENTS.md + SCHEMA_REFERENCE.md).
    Returns: 0 = in sync (or write/report), 1 = drift detected (check mode only), 2 = file missing.
    """
    # Group generators by target file
    by_target: dict = {}
    for name, entry in GENERATORS.items():
        if callable(entry):
            fn, target = entry, AGENTS
        else:
            fn, target = entry
        by_target.setdefault(target, {})[name] = fn

    all_drift: list[str] = []

    for target, gens in by_target.items():
        if not target.exists():
            print(f"ERROR: {target} not found", file=sys.stderr)
            return 2

        text     = target.read_text(encoding="utf-8")
        drift_names: list[str] = []
        replacer = _make_replacer(gens, drift_names, mode, target.name)
        new_text = _SENTINEL_RE.sub(replacer, text)

        all_drift.extend(drift_names)

        if mode == "write" and drift_names:
            target.write_text(new_text, encoding="utf-8")
            print(f"\n{target.name} updated. Sentinel(s) refreshed: {', '.join(drift_names)}")

    if mode == "write":
        if not all_drift:
            print("\nAll files already in sync — no changes written.")
        return 0

    # For check and report modes run advisories
    advisories = _advisory_checks(AGENTS.read_text(encoding="utf-8"))
    if advisories:
        print("\nAdvisories (non-blocking, update AGENTS.md manually):")
        for a in advisories:
            print(f"  ! {a}")

    if mode == "check":
        if all_drift:
            print(
                f"\nFAIL: {len(all_drift)} sentinel(s) out of sync: "
                f"{', '.join(all_drift)}. Run --write to fix.",
                file=sys.stderr,
            )
            return 1
        print("OK: all sentinels in sync.")
        return 0

    # report mode
    if not all_drift:
        print("All sentinel blocks: IN SYNC")
    return 0


def main() -> None:
    mode = "report"
    if "--check" in sys.argv:
        mode = "check"
    elif "--write" in sys.argv:
        mode = "write"
    sys.exit(sync(mode))


if __name__ == "__main__":
    main()
