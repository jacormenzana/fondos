# tests/test_assert_boundaries.py
# -*- coding: utf-8 -*-
"""assert_boundaries.ps1 (FND-0353/0354): the non-elevated isolation checks and their structured receipt. A throwaway machine cannot DENY access to its own user, so these tests prove the other half:
the script fails closed (a readable path, a missing path and a missing .env are never a pass), writes a well-formed receipt and exits 1, and never prints a value from .env."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "windows" / "assert_boundaries.ps1"
pytestmark = pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell not available")


def run(tmp_path, *, build_dirs: bool, env_text: str | None):
    ci, db, out = tmp_path / "ci", tmp_path / "db", tmp_path / "out"
    if build_dirs:
        for d in ("github_app_keys", "outbox", "app", "inbox"):
            (ci / d).mkdir(parents=True)
        (ci / "github_app_keys" / "fondos-pr-opener.pem").write_text("not a key\n", encoding="ascii")
        (db / "vault").mkdir(parents=True)
        (db / "vault" / "owner_dsn.conf").write_text("FONDOS_PG_DSN_OWNER=postgresql://o:TOPSECRET@127.0.0.1:5436/gestion\n", encoding="ascii")
        (db / "app").mkdir()
    env = tmp_path / ".env"
    if env_text is not None:
        env.write_text(env_text, encoding="ascii")
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), "-CiRoot", str(ci), "-DbRoot", str(db), "-RepoRoot", str(tmp_path),
                           "-EnvFile", str(env), "-OutDir", str(out)], capture_output=True, text=True)
    return done, out


def receipt(out):
    files = list(out.glob("*_boundary-assertions_*.json"))
    assert len(files) == 1
    return files[0], json.loads(files[0].read_text(encoding="utf-8"))


def test_readable_paths_and_a_dsn_key_in_env_are_failures_not_passes(tmp_path):
    done, out = run(tmp_path, build_dirs=True, env_text="FONDOS_PG_DSN=x\nFONDOS_PG_DSN_OWNER=postgresql://o:TOPSECRET@h/d\n")
    if done.returncode == 2:
        pytest.skip("the test session itself is elevated")
    assert done.returncode == 1, done.stdout + done.stderr
    f, r = receipt(out)
    by = {x["id"]: x for x in r["results"]}
    assert r["schema"] == "fondos.boundary-receipt/1" and r["overall"] == "FAIL" and r["elevated"] is False and len(r["results"]) == 10
    for i in ("B01", "B02", "B03", "B04", "B06", "B07", "B08"):
        assert by[i]["observed"] == "OK" and by[i]["pass"] is False, i               # the user can do it, so the isolation claim is false
    assert by["B05"]["pass"] is True and by["B09"]["pass"] is True                    # the inbox is writable by design; the accounts do not exist, so they are not administrators
    assert by["B10"]["observed"] == "PRESENT" and by["B10"]["pass"] is False
    assert "TOPSECRET" not in f.read_text(encoding="utf-8") and "TOPSECRET" not in done.stdout + done.stderr


def test_missing_paths_and_a_missing_env_file_never_count_as_denied(tmp_path):
    done, out = run(tmp_path, build_dirs=False, env_text=None)
    if done.returncode == 2:
        pytest.skip("the test session itself is elevated")
    assert done.returncode == 1
    f, r = receipt(out)
    by = {x["id"]: x for x in r["results"]}
    for i in ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08"):
        assert by[i]["pass"] is False and by[i]["observed"] not in ("DENIED", "OK"), (i, by[i])
    assert by["B10"]["observed"] == "ENV_FILE_MISSING" and by["B10"]["pass"] is False


def test_the_receipt_has_a_matching_sha256_sidecar_and_the_hash_of_the_script(tmp_path):
    done, out = run(tmp_path, build_dirs=True, env_text="FONDOS_PG_DSN=x\n")
    if done.returncode == 2:
        pytest.skip("the test session itself is elevated")
    f, r = receipt(out)
    side = Path(str(f) + ".sha256").read_text(encoding="ascii").split()
    assert side[0] == hashlib.sha256(f.read_bytes()).hexdigest() and side[1] == f.name
    assert r["script_sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()


def test_the_script_refuses_an_elevated_token_in_text():
    s = SCRIPT.read_text(encoding="utf-8")
    assert "IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {" in s and "exit 2" in s
