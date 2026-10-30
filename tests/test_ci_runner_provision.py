# tests/test_ci_runner_provision.py
# -*- coding: utf-8 -*-
"""provision_ci_runner.ps1 (FND-0353, Step 2): the parts that can run without elevation. The PEM validator is extracted from the script and run in real PowerShell on
generated keys; the script itself is only parsed (it creates accounts and ACLs, which only the owner's elevated session does)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "windows" / "provision_ci_runner.ps1"
pytestmark = pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell not available")


def pem(bits: int = 2048, fmt: str = "pkcs1") -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    f = serialization.PrivateFormat.TraditionalOpenSSL if fmt == "pkcs1" else serialization.PrivateFormat.PKCS8
    return key.private_bytes(serialization.Encoding.PEM, f, serialization.NoEncryption()).decode("ascii")


def validate(tmp_path, text: str) -> str:
    fn = re.search(r"(function Test-AppKey \{.*?\n\})\n", SCRIPT.read_text(encoding="utf-8"), re.S).group(1)
    (tmp_path / "k.pem").write_bytes(text.encode("ascii"))
    ps = tmp_path / "t.ps1"
    ps.write_text(fn + "\n$r = Test-AppKey ([IO.File]::ReadAllText('" + str(tmp_path / "k.pem") + "'))\nif ($r) { $r } else { 'VALID' }\n", encoding="utf-8-sig")
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_the_script_parses():
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('" + str(SCRIPT) + "',[ref]$t,[ref]$e);"
           "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}")
    assert subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).returncode == 0


def test_an_unmodified_2048_bit_github_style_key_is_accepted(tmp_path):
    assert validate(tmp_path, pem()) == "VALID"
    assert validate(tmp_path, pem().replace("\n", "\r\n")) == "VALID"            # a browser or Windows may change the line endings


@pytest.mark.parametrize("make,reason", [
    (lambda: pem(fmt="pkcs8"), "not exactly one unmodified RSA PRIVATE KEY PEM"),
    (lambda: pem() + pem(), "not exactly one unmodified RSA PRIVATE KEY PEM"),
    (lambda: "note\n" + pem(), "not exactly one unmodified RSA PRIVATE KEY PEM"),
    (lambda: pem() + "trailing text\n", "not exactly one unmodified RSA PRIVATE KEY PEM"),
    (lambda: "", "not exactly one unmodified RSA PRIVATE KEY PEM"),
    (lambda: pem(4096), "not a 2048-bit RSA key"),
    (lambda: pem(1024), "not a 2048-bit RSA key"),
])
def test_anything_else_is_refused_with_a_reason(tmp_path, make, reason):
    assert reason in validate(tmp_path, make())


def test_the_script_keeps_its_isolation_promises_in_text():
    s = SCRIPT.read_text(encoding="utf-8")
    assert "#Requires" not in s                                          # it runs in memory: the check is explicit
    assert s.count("IsInRole") == 1
    assert "SeDenyInteractiveLogonRight" in s and "SeDenyRemoteInteractiveLogonRight" in s and "SeBatchLogonRight" in s
    assert "Credential" not in s.replace("NetworkCredential", "")        # the Credential Manager is not used (spec D7)
    assert "/savecred" not in s


# --- the audit trail of the staged key ----------------------------------------------------------------------------------------------------------

STAGED = r"C:\ProgramData\fondos\key_staging\fondos-pr-opener.2026-10-30.private-key.pem"


def ev(process: str, mask: str, obj: str = STAGED, user: str = "jacor") -> str:
    return ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventID>4663</EventID></System><EventData>'
            f'<Data Name="SubjectUserName">{user}</Data><Data Name="ObjectName">{obj}</Data><Data Name="ProcessName">{process}</Data><Data Name="AccessMask">{mask}</Data></EventData></Event>')


def access(tmp_path, events):
    src = SCRIPT.read_text(encoding="utf-8")
    fn = re.search(r"(function Get-ForeignKeyAccess \{.*?\n\}\n)", src, re.S).group(1)
    allowed = re.search(r"\$allowedReaders = (@\(.*?\))\n", src).group(1)
    xml = "@(\n" + ",\n".join("@'\n" + e + "\n'@" for e in events) + "\n)" if events else "@()"
    ps = tmp_path / "a.ps1"
    ps.write_text(f"{fn}\n$allowedReaders = {allowed}\n$x = {xml}\n$r = Get-ForeignKeyAccess $x '{STAGED}' $allowedReaders\n'RELEVANT ' + $r.Relevant\n$r.Foreign | ForEach-Object {{ 'FOREIGN ' + $_ }}\n",
                  encoding="utf-8-sig")
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def test_only_the_browser_defender_and_the_script_may_touch_the_staged_key(tmp_path):
    out = access(tmp_path, [ev(r"C:\Program Files\Google\Chrome\Application\chrome.exe", "0x2"), ev(r"C:\ProgramData\Microsoft\Windows Defender\Platform\4.18\MsMpEng.exe", "0x1", user="SYSTEM"),
                            ev(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "0x1", user="admin")])
    assert out == ["RELEVANT 3"]


def test_any_other_process_reading_the_staged_key_is_named(tmp_path):
    out = access(tmp_path, [ev(r"C:\Program Files\Google\Chrome\Application\chrome.exe", "0x2"), ev(r"C:\Users\jacor\AppData\Local\Temp\updater.exe", "0x1"),
                            ev(r"C:\Windows\explorer.exe", "0x80"),                                   # attribute read only: not the content
                            ev(r"C:\Users\jacor\x\other.exe", "0x1", obj=r"C:\ProgramData\fondos\key_staging\another-file.txt"),   # another object
                            ev(r"C:\Users\jacor\y\deleter.exe", "0x10000")])
    assert out[0] == "RELEVANT 3" and sorted(out[1:]) == ["FOREIGN deleter.exe (jacor)", "FOREIGN updater.exe (jacor)"]


def test_no_event_at_all_is_reported_as_zero_relevant_so_the_main_run_fails_closed(tmp_path):
    assert access(tmp_path, []) == ["RELEVANT 0"]


def test_the_script_has_a_stage_mode_and_requires_the_key_inside_it():
    s = SCRIPT.read_text(encoding="utf-8")
    assert "[switch]$Stage" in s and "KeySource must be a file directly inside $staging" in s and "Remove-Item -LiteralPath $staging" in s
    assert "Downloads" in s                                      # the message that names the folder the key must NOT be in
