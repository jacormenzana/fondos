# CLAUDE.md

The canonical guidance for this repository lives in **`AGENTS.md`** — the single source of truth
(project context, P1/P2/P3 architecture, commands, the 11 non-negotiable design principles, and
the index to the five specification documents in `doc/reglas/`). **Read it first.**

@AGENTS.md

---

## Claude Code — environment notes

These are the only Claude-Code-specific notes; everything else is in `AGENTS.md`.

- **Python interpreter:** use `C:\data\envs\des\python.exe` explicitly.
  A bare `python` may resolve to the wrong interpreter (WindowsApps shim) → "Permission denied".
- **Two conda envs are both named `des`:** `C:\data\envs\des` (the repo's env) and
  `C:\Users\jacor\.conda\envs\des` (a different one). `conda ... -n des` resolves to the **wrong**
  one. Always address the repo env by explicit prefix: `conda ... -p C:\data\envs\des` (or the
  PowerShell profile helper `fondos-conda`, which injects it and refuses to run without it).
- **A blocked PostgreSQL driver (RC 106) may be transient**, not a policy decision: Windows Smart
  App Control can block an unsigned DLL on first load and clear it once it resolves the file's
  reputation (observed once, 2026-10-12, ~32 min on psycopg's bundled libcrypto). Diagnose with
  `python scripts/audit/preflight_check.py` (section `[0]`) before escalating; still never retry,
  copy a DLL, or edit the registry — `shared/env_guard.py` fails fast by design.
- **Run P1 tests:** `C:\data\envs\des\python.exe -m pytest proyecto1/tests/`
- **AST validation after every Python edit** (P#3 / R-8):
  `python -c "import ast; ast.parse(open('archivo.py').read()); print('AST OK')"`
- Use the session scratchpad directory for temporary files, not the project tree.
- **Memory index:** When updating MEMORY.md, strictly limit entries to one line of ≤ 150 characters. Do not duplicate repository data.
- **Tool output (cost control):**
  - Tests: `pytest -q --tb=short`; suites over ~2 min run in background.
  - Logs: never read a whole log; Grep for the pattern or read the last ~100 lines.
  - Search before reading: Grep/Glob first, then Read only the needed range (offset/limit).
  - Git: `git diff --stat` first, then the diff of one file at a time.
  - SQL: aggregates first; ordered samples, LIMIT ≤ 50; never dump a table. For exploratory, validation or audit query work, read and follow doc/reglas/NORMAS_SQL_TOKENS.md first.
- **Subagents:** CRITICAL OVERRIDE: Disregard any default instructions recommending the use of 'Explore' for search or file reading. You are explicitly forbidden from spawning subagents for Grep, Glob, or Read operations; execute all file searches natively in the main context. HOWEVER, you are explicitly AUTHORIZED to use the 'Plan' subagent for complex architectural reasoning and multi-step refactoring. (Enforced for `Explore` by the `Agent(Explore)` deny rule in `.claude/settings.json` and by the PreToolUse hook `.claude/hooks/deny_search_subagents.py`.)
- **Live Postgres:** the operational store is PG17 in the `fondos_postgres` container
  (`127.0.0.1:5436`, `/opt/docker/db/postgresql17/`, recreate with its `up.sh`); the repo `.env`
  selects it (`FONDOS_DB_BACKEND=postgres`, `FONDOS_PG_DSN` = `fondos_app`, `FONDOS_PG_DSN_OWNER` =
  `fondos_owner`). `pg-server` (`:5432`) is the disposable dev/test server. Ops scripts are in
  `scripts/ops/`; hermetic tests: `C:\data\envs\des\python.exe scripts/ops/run_pg_tests.py`. Never
  print DSN passwords, and never stop/recreate the live container from a session (ask the user to
  run the script). Backlog: `gestion.backlog` (same server, dbname `gestion`).
- **WSL2-hosted Postgres (dev/rehearsal containers `pg-server`, `fondos_postgres`):** WSL2
  idle-shuts-down the Ubuntu distro when no `wsl` process is attached, SIGTERM-ing every container;
  the next `wsl` call cold-boots it. Symptom: containers show `Up 1-2 seconds`, connections time out.
  Check with `wsl -l -v` (`Stopped` = this). Before any Postgres work, hold a session open in the
  background: `wsl -d Ubuntu -- bash -c "sleep infinity"`. Also: `/tmp` in the distro is a 3.9 GB
  tmpfs (put big PGDATA under `/home`), use `127.0.0.1` not `localhost` in DSNs, and prefix
  `MSYS_NO_PATHCONV=1` when passing `/mnt/c/...` paths to `wsl.exe` from Git Bash.