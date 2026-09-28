"""
check_audit_skill_sync.py — Advisory drift checker between the statistical-audit catalogs
(shared/statistical_audit/) and the two skill .md files that describe them
(.claude/skills/auditStatisticalDataDistribution{P2Metrics,CostAttributes}.md).

C3 (FND-0132, 2026-09-28, audit-skill-alignment plan Wave 3).

Scope note (discovered while building this, not assumed going in): both skill files describe
every rule in PROSE / mathematical-expression form (e.g. "max_dd ∈ [−1, 0]", "Exit ==
Management_Fee"), never by the catalog's own rule_id strings (e.g. MAX_DD_RANGE,
Exit_Fee_Pct_Max__EQUALS__Management_Fee_Pct) -- the skill text predates the catalog's naming
convention and the two were never coordinated. A literal rule_id-in-skill-text substring check
would therefore report the entire catalog as "missing" from the skill, which is noise, not a
signal -- confirmed by hand-checking several rules before writing this. So this script does NOT
attempt that. What it actually checks:

  1. Self-consistency WITHIN the catalogs (mechanical, 100% reliable, zero false positives): a
     RETIRED_RULES entry should be either (a) fully removed (replaced_by=None) and genuinely
     absent from every active catalog dict, or (b) self-corrected under the same id
     (replaced_by == rule_id) and genuinely present. Anything else is a bug in the registry
     itself, not a documentation problem.
  2. A side-by-side rule-count summary per domain (every catalog dict + _PROCEDURAL_RULES vs the
     raw count of markdown-table-row lines in the corresponding skill file) for a human to
     eyeball. NOT asserted equal -- see the scope note above. This is the honest ceiling for
     automated drift detection against prose documentation without NLP; `--list-catalog` (C2)
     plus a human diff against the skill text is the real mechanism for keeping Wave 3 C4's
     update, and any future skill-doc change, in sync.

Advisory only: always exits 0. Promote to a blocking --check mode (mirroring
scripts/audit/sync_agents_md.py's two-tier report/--check pattern) only if a future, more
reliable check is added -- there is nothing here worth blocking a commit on yet.

Dependency note: imports the real catalog dicts (needs pandas, same as every other
shared/statistical_audit module) and run_statistical_audit.py itself, for its _PROCEDURAL_RULES
registry, via importlib -- the same pattern proyecto1/tests/test_statistical_audit_runner_dialect.py
already uses. Not dependency-free like sync_agents_md.py: getting the real rule_id strings by
import is far more reliable than re-parsing them out of source with regex/ast, and this script
never needs a live DB connection either way.

Usage:
    python scripts/audit/check_audit_skill_sync.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

P2_SKILL = ROOT / ".claude" / "skills" / "auditStatisticalDataDistributionP2Metrics.md"
COST_SKILL = ROOT / ".claude" / "skills" / "auditStatisticalDataDistributionCostAttributes.md"

from shared.statistical_audit.catalog_cost_columns import COST_COLUMNS
from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS
from shared.statistical_audit.catalog_invariants import COST_INVARIANTS, P2_INVARIANTS
from shared.statistical_audit.catalog_metric_bounds import METRIC_BOUNDS
from shared.statistical_audit.catalog_pairs import COST_PAIRS, P2_PAIRS
from shared.statistical_audit.catalog_retired import RETIRED_RULES

_spec = importlib.util.spec_from_file_location(
    "run_statistical_audit", ROOT / "scripts" / "audit" / "run_statistical_audit.py")
_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_runner)


def _check_retired_self_consistency() -> list[str]:
    active_ids = set()
    active_ids |= set(COST_PAIRS.keys()) | set(P2_PAIRS.keys())
    active_ids |= {r.rule_id for r in COST_INVARIANTS} | {r.rule_id for r in P2_INVARIANTS}
    active_ids |= {r.rule_id for r in COST_GROUP_CHECKS}

    problems = []
    for rule_id, entry in RETIRED_RULES.items():
        is_active = rule_id in active_ids
        if entry.replaced_by is None and is_active:
            problems.append(f"{rule_id}: marked fully removed (replaced_by=None) but is STILL an "
                             f"active catalog rule_id")
        if entry.replaced_by == rule_id and not is_active:
            problems.append(f"{rule_id}: marked self-corrected (replaced_by={rule_id}) but is NOT "
                             f"an active catalog rule_id anywhere")
    return problems


def _rule_count_summary(domain: str) -> int:
    n = 0
    if domain == "costs":
        n += len(COST_PAIRS) + len(COST_INVARIANTS) + len(COST_GROUP_CHECKS)
        n += sum(1 for spec in COST_COLUMNS.values()
                  for b in (spec.hard_bound, spec.plausibility_bound) if b is not None)
    else:
        n += len(P2_PAIRS) + len(P2_INVARIANTS) + len(METRIC_BOUNDS)
    n += sum(1 for rule in _runner._PROCEDURAL_RULES if rule["domain"] in (domain, "both"))
    return n


def _skill_table_row_count(skill_path: Path) -> int:
    text = skill_path.read_text(encoding="utf-8")
    return sum(
        1 for line in text.splitlines()
        if line.strip().startswith("|") and "---" not in line
    )


def main() -> int:
    print("=== check_audit_skill_sync (advisory -- always exits 0) ===\n")

    print("-- RETIRED_RULES self-consistency --")
    problems = _check_retired_self_consistency()
    if problems:
        for p in problems:
            print(f"  ! {p}")
    else:
        print("  clean")
    print()

    for domain, skill_path in (("costs", COST_SKILL), ("p2", P2_SKILL)):
        catalog_n = _rule_count_summary(domain)
        skill_n = _skill_table_row_count(skill_path)
        print(f"-- {domain}: {catalog_n} catalog rules vs {skill_n} skill markdown-table-row "
              f"lines ({skill_path.name}) --")
    print("\n(Counts are informational, not asserted equal -- see this script's docstring for "
          "why a literal or count-exact comparison against these two prose-style skill files "
          "would be noise. Review --list-catalog output (C2) against the skill text by hand "
          "before editing it.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
