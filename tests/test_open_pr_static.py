# tests/test_open_pr_static.py
# -*- coding: utf-8 -*-
"""Acceptance 12 of CICD_SERVICE_ACCOUNT_SPEC.md (FND-0353): the static guarantees of the pull-request runner. Written BEFORE the code (owner order, 2026-10-30)."""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
RUNNER = OPS / "open_pr.py"
AGENT_SIDE = OPS / "open_pr_request.py"
FORBIDDEN_IMPORTS = {"subprocess", "pickle", "marshal", "shelve", "yaml", "pty", "commands", "popen2"}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_OS = {"system", "popen", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp", "execlpe", "execv", "execve",
                "execvp", "execvpe", "startfile"}
CREDENTIAL_STORE_WORDS = ("cmdkey", "CredWrite", "CredRead", "keyring", "win32cred", "CredEnumerate", "CryptProtectData")
ALLOWED_IMPORTS = {"__future__", "base64", "dataclasses", "hashlib", "json", "os", "re", "time", "uuid", "pathlib", "urllib", "ssl", "ctypes", "typing", "datetime", "http", "collections",
                   "cryptography", "open_pr_schema", "db_owner_manifest", "sys", "contextlib", "tempfile", "threading"}


def tree():
    return ast.parse(RUNNER.read_text(encoding="utf-8"))


def test_the_runner_has_no_process_call_and_no_dynamic_execution():
    t = tree()
    bad = []
    for node in ast.walk(t):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] in FORBIDDEN_IMPORTS]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
            bad.append(node.module)
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in FORBIDDEN_CALLS:
                bad.append(f.id)
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os" and f.attr in FORBIDDEN_OS:
                bad.append(f"os.{f.attr}")
            if any(k.arg == "shell" and getattr(k.value, "value", None) is True for k in node.keywords):
                bad.append("shell=True")
            if isinstance(f, ast.Attribute) and f.attr == "load" and isinstance(f.value, ast.Name) and f.value.id == "yaml":
                bad.append("yaml.load")
    assert bad == []


def test_the_runner_imports_only_what_the_pinned_environment_carries():
    names = set()
    for node in ast.walk(tree()):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add((node.module or "").split(".")[0])
    assert names - ALLOWED_IMPORTS == set()


def test_there_is_no_credential_store_and_no_token_cache_construct():
    for path in (RUNNER, OPS / "open_pr_request.py", OPS / "open_pr_schema.py"):
        text = path.read_text(encoding="utf-8")
        assert [w for w in CREDENTIAL_STORE_WORDS if w in text] == [], path.name
        assert not re.search(r"(write_text|write_bytes|open\([^)]*[\"']w)[^\n]*(token|jwt)", text, re.I), path.name       # a token or JWT is never written to a file


def tracked_files():
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    return [ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]


def test_only_the_runner_refers_to_the_key_file():
    needles = ("fondos-pr-opener.pem", "github_app_keys")
    offenders = []
    for p in tracked_files():
        if p.suffix not in (".py", ".ps1", ".cmd", ".bat") or p.is_dir() or not p.exists():
            continue
        if p == RUNNER or p == AGENT_SIDE or p.parts[-2] == "tests" or p.parts[-2] == "windows":      # tests describe the path; the provisioning scripts create it; the agent side is checked below
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if any(n in text for n in needles):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_no_tracked_file_contains_a_private_key():
    block = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{100,}")
    offenders = []
    for p in tracked_files():
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "PRIVATE KEY" in text and block.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_gitignore_covers_pem_files_and_the_key_directory():
    lines = {ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert "*.pem" in lines and "github_app_keys/" in lines


def test_the_agent_side_module_holds_no_secret_and_no_key_path():
    text = (OPS / "open_pr_request.py").read_text(encoding="utf-8")
    assert "BEGIN" not in text and "load_pem_private_key" not in text and "access_tokens" not in text


def test_the_agent_side_names_the_key_path_only_for_the_isolation_probe():
    tree_ = ast.parse(AGENT_SIDE.read_text(encoding="utf-8"))
    holders = []
    for node in ast.walk(tree_):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and ("github_app_keys" in node.value or "fondos-pr-opener.pem" in node.value):
            holders.append(node.lineno)
    lines = AGENT_SIDE.read_text(encoding="utf-8").splitlines()
    assert len(holders) == 2 and all(lines[n - 1].startswith(("KEY_DIR", "KEY_FILE")) for n in holders)     # the two constants, nothing else
    uses = [ln for ln in lines if ("KEY_FILE" in ln or "KEY_DIR" in ln) and not ln.startswith(("KEY_DIR", "KEY_FILE"))]
    assert all("check_isolation" in ln or "for name, probe, target in" in ln for ln in uses)          # used only by the probe and its caller
