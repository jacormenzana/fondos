# scripts/audit — governed-document sync (area guide)

Loaded by Claude Code the first time a file under `scripts/audit/` is read (`scripts/audit/CLAUDE.md` imports this file); any other agent reads it before changing code here. Cross-project rules (design principles P#1-P#11, restrictions R-1..R-8, backlog, live-store safety) are in the root `AGENTS.md` and apply here.

## Maintenance — Dynamic Sync

Mechanical sections are kept in sync by `scripts/audit/sync_agents_md.py`. Hand-written prose
(regime tables, scoring weights, principles, rationale) stays outside sentinel markers and is
never touched by the generator. The generator is **dependency-free** (AST-parses only — no
project imports), so it runs on bare `python3` (CI, developer machines, Linux runners).

**Implemented sentinels (all enforced at commit):**

| Sentinel | Target file | Source of truth |
|----------|-------------|----------------|
| `schema-version` | `AGENTS.md` | `SCHEMA_VERSION` in `shared/config.py` |
| `skills-table` | `AGENTS.md` | `.claude/skills/*/SKILL.md` (alpha order) |
| `kill-switches-line` | `doc/reglas/NORMAS_IMPLEMENTACION.md` | `*_ENABLED` booleans in `shared/config.py` (source order) |
| `p1-module-map` | `proyecto1/AGENTS.md` | `proyecto1/{core,blocks}/*.py` minus non-canonical |
| `p2-module-map` | `proyecto2/AGENTS.md` | `proyecto2/src/**/*.py` + `tests/**/*.py` minus non-canonical |
| `macro-factors` | `proyecto2/AGENTS.md` | `_FACTOR_TO_METRIC` dict in `macro_sensitivity.py` |
| `data-sources` | `proyecto2/AGENTS.md` | `SOURCES` registry in `macro_discovery.py` |
| `p3-module-map` | `proyecto3/AGENTS.md` | `proyecto3/src/*.py` minus non-canonical |
| `launchers` | `scripts/launch/AGENTS.md` | `scripts/launch/*.bat` minus non-canonical |
| `schema-reference-tables` | `doc/reglas/SCHEMA_REFERENCE.md` | All `CREATE TABLE` in `db/schema_fondos.sql`, bucketed P1/P2/P3 |

**Non-canonical exclusion rule** — files matching any of these patterns are excluded from module
maps and the launchers table: dated suffix `_YYYYMMDD`, `_prod` suffix, `_last` suffix,
`_` prefix (private/init), `BackUp`/`Backup` in name, `test_*` outside a proper `tests/`
directory. These files are operational noise; their canonical replacements are listed instead.

**Structure is enforced too:** a registered sentinel must occur exactly once, as a well-formed BEGIN/END pair, in the file it is
registered for; an unknown sentinel and a generator that raises are failures (`--check` and `--write` exit 1; `--write` then writes nothing).

**Generator modes:**
```bash
python scripts/audit/sync_agents_md.py            # report mode — show drift
python scripts/audit/sync_agents_md.py --check    # exit 1 on drift (used by pre-commit hook + CI)
python scripts/audit/sync_agents_md.py --write    # update all governed files in-place
```

**Two-tier enforcement:**
- **Local** — `.git/hooks/pre-commit` runs `--check`; any sentinel drift blocks the commit.
  Fix with `--write`, then re-stage the modified file(s).
- **Hosted CI** — `.github/workflows/agents-sync.yml` mirrors `--check` on every push/PR.
  Server-side gate is independent of local hook installation state.

**Hook installer:** `scripts/audit/pre-commit.sh` — portable, resolves Python via Conda env `des`.

**Splitting or moving governed text:** `python scripts/audit/check_agents_md_split.py --base HEAD --receivers <files> --ledger scripts/audit/agents_md_split_ledger.txt` must report 0 unaccounted lines (every line the source lost is in a receiver or in the ledger with a reason). Calibrated by `tests/test_check_agents_md_split.py`.
