# tests/test_out_dir.py
# -*- coding: utf-8 -*-
"""FND-0379: shared/out_dir.py. The autopilot hook runs the autopilot from a clean clone of origin/master kept under out/; every script that keeps files in out/ asks out_root() so that copy reads and
writes the MAIN tree's out/ (digest, module-proof cache, test receipts, publisher state). R-7: no database, no network."""
from __future__ import annotations

import os
from pathlib import Path

from shared.out_dir import ENV_VAR, out_root

_ROOT = Path(__file__).resolve().parents[1]


def test_out_root_is_the_repo_out_unless_the_environment_names_another(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert out_root(tmp_path) == tmp_path / "out"
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "main" / "out"))
    assert out_root(tmp_path) == tmp_path / "main" / "out"
    monkeypatch.setenv(ENV_VAR, "   ")
    assert out_root(tmp_path) == tmp_path / "out"                                                    # an empty value is no override


def test_every_script_that_keeps_files_in_out_asks_out_root_so_the_clean_clone_shares_the_main_out():
    for rel, marker in (("scripts/audit/backlog_digest.py", 'OUT_DIR = out_root(ROOT) / "backlog_digest"'),
                        ("scripts/audit/backlog_modules.py", 'PROOF_CACHE = out_root(ROOT) / "backlog_modules" / "proof.json"'),
                        ("scripts/audit/delivery_attest.py", 'RECEIPTS = out_root(ROOT) / "audit" / "delivery_receipts.jsonl"'),
                        ("scripts/audit/auto_publish.py", 'STATE = out_root(ROOT) / "audit" / "auto_publish_state.json"')):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert marker in text, rel
        assert 'ROOT / "out"' not in text.replace('out_root(ROOT)', ""), rel                          # no path of its own under <repo>/out is left in it


def test_the_scripts_really_move_with_the_override(monkeypatch, tmp_path):
    import importlib
    import sys
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "elsewhere"))
    for p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
        if p not in sys.path:
            sys.path.insert(0, p)
    for name in ("backlog_digest", "backlog_modules", "delivery_attest", "auto_publish"):
        mod = importlib.reload(importlib.import_module(name))
        paths = [str(getattr(mod, a)) for a in ("OUT_DIR", "PROOF_CACHE", "RECEIPTS", "LOCK", "STATE") if hasattr(mod, a)]
        assert paths and all(str(tmp_path / "elsewhere") in p for p in paths), (name, paths)
    monkeypatch.delenv(ENV_VAR, raising=False)
    for name in ("backlog_digest", "backlog_modules", "delivery_attest", "auto_publish"):
        importlib.reload(importlib.import_module(name))                                              # leave the modules as the rest of the suite expects them
