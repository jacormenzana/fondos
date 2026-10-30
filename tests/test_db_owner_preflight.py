# tests/test_db_owner_preflight.py
# -*- coding: utf-8 -*-
"""Step 3 of the Backlog v2 deployment (FND-0354): the manifest of the db_owner tool tree and the hard-stop pre-flight. Pure: no database, no account, no elevation.
The PowerShell block that WRITES the manifest is extracted from provision_db_owner.ps1 and run for real, so writer and reader cannot drift apart."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_OPS = _ROOT / "scripts" / "ops"
if str(_OPS) not in sys.path:
    sys.path.insert(0, str(_OPS))

import db_owner_manifest as mf  # noqa: E402
import db_owner_preflight as pf  # noqa: E402

COMMIT = "a" * 40
DSN = "postgresql://owner:s3cret@127.0.0.1:5436/gestion"
GOOD = {"system_identifier": 7688429679929688102, "database_oid": 34094, "dbname": "gestion", "major": 17}


def tree(tmp_path):
    (tmp_path / "app" / "scripts").mkdir(parents=True)
    (tmp_path / "app" / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "python.exe").write_bytes(b"MZ\x00\x01")
    (tmp_path / "vault").mkdir()
    (tmp_path / "vault" / "owner_dsn.conf").write_text(f"FONDOS_PG_DSN_OWNER={DSN}\n", encoding="utf-8")
    (tmp_path / "receipts").mkdir()
    (tmp_path / mf.MANIFEST_NAME).write_text(mf.build(tmp_path, COMMIT), encoding="utf-8", newline="\n")
    return tmp_path


# --- manifest ----------------------------------------------------------------------------------------------------------------------------------------

def test_a_fresh_tree_verifies_and_vault_and_receipts_are_not_covered(tmp_path):
    root = tree(tmp_path)
    assert mf.verify(root) == (COMMIT, [])
    (root / "receipts" / "x.txt").write_text("written at every run\n", encoding="utf-8")
    (root / "vault" / "owner_dsn.conf").write_text("rotated\n", encoding="utf-8")
    assert mf.verify(root)[1] == []


@pytest.mark.parametrize("mutate,expected", [
    (lambda r: (r / "app" / "scripts" / "a.py").write_text("print(2)\n", encoding="utf-8"), "CHANGED app/scripts/a.py"),
    (lambda r: (r / "runtime" / "python.exe").unlink(), "MISSING runtime/python.exe"),
    (lambda r: (r / "app" / "b.py").write_text("x\n", encoding="utf-8"), "EXTRA   app/b.py"),
    (lambda r: (r / "app" / "__pycache__").mkdir() or (r / "app" / "__pycache__" / "a.pyc").write_bytes(b"\x00"), "EXTRA   app/__pycache__/a.pyc"),
])
def test_any_change_missing_or_extra_file_is_a_problem(tmp_path, mutate, expected):
    root = tree(tmp_path)
    mutate(root)
    assert expected in mf.verify(root)[1]


def test_a_missing_or_malformed_manifest_fails_closed(tmp_path):
    root = tree(tmp_path)
    (root / mf.MANIFEST_NAME).write_text("# commit " + COMMIT + "\nnot a hash line\n", encoding="utf-8")
    assert "malformed" in mf.verify(root)[1][0]
    (root / mf.MANIFEST_NAME).unlink()
    assert mf.verify(root) == (None, [f"{mf.MANIFEST_NAME} is missing"])
    with pytest.raises(ValueError):
        mf.build(root, "NOTASHA")


@pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell not available")
def test_the_manifest_written_by_the_provisioning_script_is_read_by_the_verifier(tmp_path):
    root = tree(tmp_path)
    (root / mf.MANIFEST_NAME).unlink()
    (root / "app" / "dir with space").mkdir()
    (root / "app" / "dir with space" / "f.txt").write_text("spaces\n", encoding="utf-8")
    src = (_OPS / "windows" / "provision_db_owner.ps1").read_text(encoding="utf-8")
    block = re.search(r"(\$lines = Get-ChildItem.*?)\n    # --- 8\.", src, re.S).group(1)
    (tmp_path / "w.ps1").write_text(f"$root = '{root}'\n$Commit = '{COMMIT}'\n$manifest = Join-Path $root 'MANIFEST.sha256'\n{block}\n", encoding="utf-8-sig")
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "w.ps1")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert mf.verify(root) == (COMMIT, [])


# --- pre-flight ----------------------------------------------------------------------------------------------------------------------------------

def test_the_vault_must_be_exactly_one_line_for_exactly_this_server():
    assert pf.parse_vault(f"FONDOS_PG_DSN_OWNER={DSN}\n") == DSN
    for bad in ("", f"FONDOS_PG_DSN_OWNER={DSN}\nEXTRA=1\n", f"OTHER={DSN}\n",
                "FONDOS_PG_DSN_OWNER=postgresql://o:p@127.0.0.1:5433/des\n", "FONDOS_PG_DSN_OWNER=postgresql://o:p@localhost:5436/gestion\n",
                "FONDOS_PG_DSN_OWNER=postgresql://o:p@127.0.0.1:5436/fondos\n"):
        with pytest.raises(pf.HardStop):
            pf.parse_vault(bad)


def test_the_fingerprint_names_every_fact_that_differs():
    assert pf.fingerprint_problems(GOOD) == []
    assert any("system_identifier" in p for p in pf.fingerprint_problems({**GOOD, "system_identifier": 1}))
    assert any("database_oid" in p for p in pf.fingerprint_problems({**GOOD, "database_oid": 16384}))
    assert any("major" in p for p in pf.fingerprint_problems({**GOOD, "major": 15}))
    assert len(pf.fingerprint_problems({"system_identifier": 0, "database_oid": 0, "dbname": "des", "major": 15})) == 4


class Conn:
    def __init__(self, row):
        self.row, self.closed = row, False

    def execute(self, sql):
        return self

    def fetchone(self):
        return self.row

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def go(root, argv, *, row=(7688429679929688102, 34094, "gestion", 17), who="FONDOS_DB_OWNER", typed="", children=None, connect_error=None):
    logs, calls = [], children if children is not None else []

    def connect(dsn):
        if connect_error:
            raise connect_error
        return Conn(row)

    def child(cmd, env, out):
        calls.append((cmd, env))
        out("migrate output")
        return 0
    rc = pf.run(argv, root=root, account=lambda: who, connect=connect, ask=lambda p: typed, child=child, log=logs.append)
    return rc, logs, calls


def deployed(tmp_path):
    return tree(tmp_path)


def test_the_first_line_is_the_target_and_a_check_changes_nothing(tmp_path):
    root = deployed(tmp_path)
    rc, logs, calls = go(root, ["--check"])
    assert rc == 0 and logs[0] == "target: host=127.0.0.1 port=5436 dbname=gestion" and calls == []
    assert any("server verified: system_identifier 7688429679929688102" in line for line in logs)
    receipt = next((root / "receipts").glob("*_check_check.txt"), None) or next((root / "receipts").glob("*check*.txt"))
    assert "s3cret" not in receipt.read_text(encoding="utf-8")


@pytest.mark.parametrize("kwargs,needle", [
    ({"who": "jacor"}, "runs only as fondos_db_owner"),
    ({"row": (1, 34094, "gestion", 17)}, "WRONG SERVER"),
    ({"row": (7688429679929688102, 99, "gestion", 17)}, "WRONG SERVER"),
    ({"connect_error": ConnectionError("s3cret in the message")}, "connection failed: ConnectionError"),
])
def test_every_hard_stop_exits_1_and_never_reaches_the_migration(tmp_path, kwargs, needle):
    root = deployed(tmp_path)
    rc, logs, calls = go(root, ["--phase", "relations", "--apply"], typed="APPLY relations ON gestion 127.0.0.1:5436", **kwargs)
    assert rc == 1 and calls == [] and any(needle in line for line in logs)
    assert not any("s3cret" in line for line in logs)


def test_a_changed_tool_file_stops_before_the_vault_is_read(tmp_path):
    root = deployed(tmp_path)
    (root / "app" / "scripts" / "a.py").write_text("import os; os.system('x')\n", encoding="utf-8")
    rc, logs, calls = go(root, ["--check"])
    assert rc == 1 and calls == [] and any("MANIFEST.sha256 does not match" in line and "CHANGED app/scripts/a.py" in line for line in logs)
    assert not any(line.startswith("target:") for line in logs)


def test_a_dry_run_needs_no_confirmation_and_apply_needs_the_exact_sentence(tmp_path):
    root = deployed(tmp_path)
    rc, logs, calls = go(root, ["--phase", "relations"])
    assert rc == 0 and len(calls) == 1 and "--apply" not in calls[0][0]
    cmd, env = calls[0]
    assert cmd[1:3] == ["-I", "-B"] and cmd[-2:] == ["--phase", "relations"] and env["FONDOS_PG_DSN_OWNER"] == DSN
    assert set(env) == {"SYSTEMROOT", "FONDOS_PG_DSN_OWNER", "FONDOS_ENV_FILE", "PYTHONDONTWRITEBYTECODE"}
    rc, logs, calls = go(root, ["--phase", "iam", "--apply"], typed="yes")
    assert rc == 1 and calls == [] and any("confirmation not typed" in line for line in logs)
    rc, logs, calls = go(root, ["--phase", "iam", "--apply"], typed="APPLY iam ON gestion 127.0.0.1:5436")
    assert rc == 0 and "--apply" in calls[0][0]
    rc, logs, calls = go(root, ["--phase", "iam", "--down", "--apply"], typed="APPLY ROLLBACK iam ON gestion 127.0.0.1:5436")
    assert rc == 0 and "--down" in calls[0][0]


def test_the_receipt_never_holds_the_dsn_even_if_a_child_echoes_it(tmp_path):
    root = deployed(tmp_path)
    logs = []

    def child(cmd, env, out):
        out(f"oops {env['FONDOS_PG_DSN_OWNER']}")
        return 0
    pf.run(["--phase", "relations"], root=root, account=lambda: "fondos_db_owner", connect=lambda d: Conn((7688429679929688102, 34094, "gestion", 17)),
           ask=lambda p: "", child=child, log=logs.append)
    receipts = list((root / "receipts").glob("*.txt"))
    assert receipts and all("s3cret" not in r.read_text(encoding="utf-8") for r in receipts)


def test_bad_arguments_are_usage_errors(tmp_path):
    root = deployed(tmp_path)
    for argv in ([], ["--check", "--apply"], ["--down"], ["--phase", "alias"], ["--check", "--phase", "iam"]):
        with pytest.raises(SystemExit) as e:
            go(root, argv)
        assert e.value.code == 2


# --- runtime integrity (the interpreter copied into runtime\) -----------------------------------------------------------------------------------

ENV_DIR = Path.home() / ".conda" / "envs" / "des"


def _functions(*names):
    src = (_OPS / "windows" / "provision_db_owner.ps1").read_text(encoding="utf-8")
    return "\n".join(re.search(r"(function " + n + r" \{.*?\n\}\n)", src, re.S).group(1) for n in names)


@pytest.mark.skipif(shutil.which("powershell") is None or not (ENV_DIR / "conda-meta").is_dir(), reason="PowerShell or the project environment not available")
def test_the_copied_interpreter_is_checked_against_its_install_time_hashes(tmp_path):
    rt = tmp_path / "runtime"
    sp = ENV_DIR / "Lib" / "site-packages"
    rc = ["/NJH", "/NJS", "/NP", "/NFL", "/NDL", "/R:1", "/W:1"]
    steps = [[str(ENV_DIR), str(rt), "python.exe", "*.dll"], [str(ENV_DIR / "DLLs"), str(rt / "DLLs"), "/E"], [str(ENV_DIR / "Library" / "bin"), str(rt / "Library" / "bin"), "/E"],
             [str(ENV_DIR / "Lib"), str(rt / "Lib"), "/E", "/XD", "site-packages", "__pycache__", "test", "tests", "idlelib", "turtledemo"]]
    steps += [[str(sp / p), str(rt / "Lib" / "site-packages" / p), "/E", "/XD", "__pycache__"] for p in ("psycopg", "psycopg_binary", "psycopg_binary.libs", "tzdata")]
    for s in steps:
        assert subprocess.run(["robocopy", *s, *rc], capture_output=True).returncode < 8
    shutil.copy(sp / "typing_extensions.py", rt / "Lib" / "site-packages" / "typing_extensions.py")

    def check():
        ps = tmp_path / "chk.ps1"
        ps.write_text(_functions("Get-RecordedHashes", "Test-RuntimeIntegrity") + f"\n$r = Test-RuntimeIntegrity '{ENV_DIR}' '{rt}'\n'VERIFIED ' + $r.Verified\n$r.Problems\n", encoding="utf-8-sig")
        done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        return done.stdout.splitlines()

    clean = check()
    assert clean[0].startswith("VERIFIED ") and int(clean[0].split()[1]) > 1000 and len(clean) == 1
    (rt / "Lib" / "os.py").write_text("import evil\n", encoding="utf-8")
    (rt / "Lib" / "site-packages" / "evil.pth").write_text("import evil\n", encoding="utf-8")
    (rt / "Lib" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    (rt / "Lib" / "__pycache__").mkdir()
    (rt / "Lib" / "__pycache__" / "os.cpython-313.pyc").write_bytes(b"\x00")
    bad = check()[1:]
    assert "MISMATCH lib/os.py" in bad and "FORBIDDEN lib/site-packages/evil.pth" in bad and "UNRECORDED lib/extra.py" in bad
    assert "FORBIDDEN lib/__pycache__/os.cpython-313.pyc" in bad
