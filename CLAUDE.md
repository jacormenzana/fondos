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
- **Run P1 tests:** `C:\data\envs\des\python.exe -m pytest proyecto1/tests/`
- **AST validation after every Python edit** (P#3 / R-8):
  `python -c "import ast; ast.parse(open('archivo.py').read()); print('AST OK')"`
- Use the session scratchpad directory for temporary files, not the project tree.
- **WSL2-hosted Postgres (dev/rehearsal containers `pg-server`, `fondos_postgres`):** WSL2
  idle-shuts-down the Ubuntu distro when no `wsl` process is attached, SIGTERM-ing every container;
  the next `wsl` call cold-boots it. Symptom: containers show `Up 1-2 seconds`, connections time out.
  Check with `wsl -l -v` (`Stopped` = this). Before any Postgres work, hold a session open in the
  background: `wsl -d Ubuntu -- bash -c "sleep infinity"`. Also: `/tmp` in the distro is a 3.9 GB
  tmpfs (put big PGDATA under `/home`), use `127.0.0.1` not `localhost` in DSNs, and prefix
  `MSYS_NO_PATHCONV=1` when passing `/mnt/c/...` paths to `wsl.exe` from Git Bash.