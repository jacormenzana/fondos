"""
sync_agents_md.py — Dynamic re-sync of AUTO sentinel blocks in AGENTS.md.

Sentinel format used in AGENTS.md:
    <!-- AUTO:BEGIN <name> -->
    ...auto-generated content...
    <!-- AUTO:END <name> -->

Implemented sentinels (fully regenerated from code):
  schema-version      shared/config.py  SCHEMA_VERSION
  kill-switches-line  shared/config.py  all *_ENABLED bool constants (source order)
  skills-table        .claude/skills/*.md  names + extracted description (alpha order)

Advisory checks (warn, never fail — just inform the developer):
  p2-new-modules   new *.py files in proyecto2/src not mentioned in AGENTS.md
  p2-new-tables    new CREATE TABLE names in schema_fondos.sql not mentioned in AGENTS.md
  calc-version     CALC_VERSION in run_pipeline.py vs mention in AGENTS.md

Usage:
  python scripts/audit/sync_agents_md.py           # report mode (default)
  python scripts/audit/sync_agents_md.py --check   # exit 1 on sentinel drift
  python scripts/audit/sync_agents_md.py --write   # update AGENTS.md in-place

Pre-commit hook (run once):
  Copy scripts/audit/pre-commit.sh to .git/hooks/pre-commit and chmod +x
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"

_SENTINEL_RE = re.compile(
    r"<!-- AUTO:BEGIN (\S+) -->\n(.*?)\n<!-- AUTO:END \1 -->",
    re.DOTALL,
)

# ── Extractors ────────────────────────────────────────────────────────────────

def _config_assigns() -> dict:
    """Return {name: constant_value} for Assign and AnnAssign nodes in shared/config.py."""
    src = (ROOT / "shared/config.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    result = {}
    for node in ast.walk(tree):
        # NAME = value
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and isinstance(node.value, ast.Constant):
                    result[tgt.id] = node.value.value
        # NAME: type = value  (annotated assignment, Python 3.6+)
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.value is not None
                and isinstance(node.value, ast.Constant)
            ):
                result[node.target.id] = node.value.value
    return result


def _kill_switches_ordered() -> list[tuple[str, bool]]:
    """Return [(name, value), ...] in source-file order for all *_ENABLED booleans.

    Handles both plain assignment (NAME = True) and annotated assignment
    (NAME: bool = True) forms used throughout shared/config.py.
    """
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
    # 1. frontmatter description: field
    fm = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if fm:
        m = re.search(r"^description:\s*(.+)", fm.group(1), re.MULTILINE)
        if m:
            return m.group(1).strip()[:150]
    # 2. **Description:** bold label
    m = re.search(r"\*\*Description:\*\*\s*(.+)", text)
    if m:
        return m.group(1).strip()[:150]
    # 3. first non-header, non-empty line
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


GENERATORS: dict = {
    "schema-version": gen_schema_version,
    "kill-switches-line": gen_kill_switches_line,
    "skills-table": gen_skills_table,
}

# ── Advisory checks ───────────────────────────────────────────────────────────

def _advisory_checks(agents_text: str) -> list[str]:
    warnings: list[str] = []

    # 1. New P2 modules (skip cache dirs, private files, and dated backups like *_20260605_00.py)
    src_dir = ROOT / "proyecto2/src"
    _backup_re = re.compile(r"_\d{8}(_\d+)?\.py$")
    for f in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in f.parts or f.name.startswith("_"):
            continue
        if _backup_re.search(f.name):
            continue  # versioned backup — not a canonical module
        if f.name not in agents_text:
            rel = str(f.relative_to(src_dir)).replace("\\", "/")
            warnings.append(f"P2 module not in AGENTS.md: {rel}")

    # 2. New DB tables
    sql_path = ROOT / "db/schema_fondos.sql"
    if sql_path.exists():
        sql = sql_path.read_text(encoding="utf-8", errors="ignore")
        for table in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql):
            if f"`{table}`" not in agents_text:
                warnings.append(f"DB table not in AGENTS.md: {table}")

    # 3. CALC_VERSION drift
    pipeline = ROOT / "proyecto2/src/pipeline/run_pipeline.py"
    if pipeline.exists():
        src = pipeline.read_text(encoding="utf-8")
        m = re.search(r'CALC_VERSION\s*(?::\s*\w+)?\s*=\s*["\'](\d{8})["\']', src)
        if m and m.group(1) not in agents_text:
            warnings.append(f"CALC_VERSION '{m.group(1)}' not mentioned in AGENTS.md")

    return warnings


# ── Core sync logic ───────────────────────────────────────────────────────────

def sync(mode: str) -> int:
    """
    mode: 'report' | 'check' | 'write'
    Returns: 0 = in sync (or report), 1 = drift detected (check mode only).
    """
    if not AGENTS.exists():
        print(f"ERROR: {AGENTS} not found", file=sys.stderr)
        return 2

    text = AGENTS.read_text(encoding="utf-8")
    drift_names: list[str] = []
    new_text = text

    def _replacer(m: re.Match) -> str:
        name = m.group(1)
        current = m.group(2)
        gen = GENERATORS.get(name)
        if gen is None:
            return m.group(0)
        try:
            fresh = gen()
        except Exception as exc:
            print(f"  ERROR generating [{name}]: {exc}", file=sys.stderr)
            return m.group(0)
        if fresh.rstrip() != current.rstrip():
            drift_names.append(name)
            if mode in ("report", "check"):
                print(f"\nDRIFT in sentinel [{name}]:")
                print(f"  CURRENT:\n    " + current.replace("\n", "\n    "))
                print(f"  FRESH:\n    " + fresh.replace("\n", "\n    "))
        return f"<!-- AUTO:BEGIN {name} -->\n{fresh}\n<!-- AUTO:END {name} -->"

    new_text = _SENTINEL_RE.sub(_replacer, new_text)

    # Advisory checks
    advisories = _advisory_checks(text)
    if advisories:
        print("\nAdvisories (non-blocking, update AGENTS.md manually):")
        for a in advisories:
            print(f"  ! {a}")

    if mode == "write":
        if drift_names:
            AGENTS.write_text(new_text, encoding="utf-8")
            print(f"\nAGENTS.md updated. Sentinel(s) refreshed: {', '.join(drift_names)}")
        else:
            print("\nAGENTS.md already in sync — no changes written.")
        return 0

    if mode == "check":
        if drift_names:
            print(
                f"\nFAIL: {len(drift_names)} sentinel(s) out of sync: "
                f"{', '.join(drift_names)}. Run --write to fix.",
                file=sys.stderr,
            )
            return 1
        print("OK: all AGENTS.md sentinels in sync.")
        return 0

    # report mode
    if not drift_names:
        print("AGENTS.md sentinel blocks: IN SYNC")
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
