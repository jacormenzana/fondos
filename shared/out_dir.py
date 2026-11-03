# shared/out_dir.py
# -*- coding: utf-8 -*-
"""out_dir.py -- where the repository's `out/` directory is (FND-0379). Normally `<repo>/out`. The autopilot hook runs the autopilot from a CLEAN CLONE of origin/master kept under out/ (so the state of the
working tree never decides whether it runs); that copy must read and write the MAIN tree's out/ (the weekly digest, the module-proof cache, the test receipts), so the hook sets FONDOS_OUT_DIR and every
script that keeps files there asks this module. Pure apart from reading the environment; no repository imports (the receipt command uses it and must not load the .env)."""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "FONDOS_OUT_DIR"


def out_root(repo_root: Path) -> Path:
    """`$FONDOS_OUT_DIR` when it is set and not empty, else `<repo_root>/out`."""
    override = (os.environ.get(ENV_VAR) or "").strip()
    return Path(override) if override else Path(repo_root) / "out"
