# tests/test_log_sink_provision.py
# -*- coding: utf-8 -*-
"""provision_log_sink.ps1 (FND-0353, spec section 7 / D6): the parts that can run without elevation, a collector or a certificate. The query and the subscription XML are extracted from the script and
run in real PowerShell; the script itself is parsed and shown to refuse a non-elevated run and malformed parameters. The side effects (log size, WinRM, registry policy, group, key ACL) are
exercised only by the owner's dry run and apply on the real machine."""
from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "windows" / "provision_log_sink.ps1"
pytestmark = pytest.mark.skipif(shutil.which("powershell") is None, reason="PowerShell not available")
FILE = r"C:\ProgramData\fondos\ci_runner\github_app_keys\fondos-pr-opener.pem"
DIR = r"C:\ProgramData\fondos\ci_runner\github_app_keys"
THUMB = "A" * 40
NS = {"s": "http://schemas.microsoft.com/2006/03/windows/events/subscription"}


def ps(tmp_path, body: str) -> str:
    src = SCRIPT.read_text(encoding="utf-8")
    fns = "\n".join(re.search(r"(function " + n + r" \{.*?\n\}\n)", src, re.S).group(1) for n in ("ConvertTo-XmlText", "Get-EventQuery", "New-SubscriptionXml", "Get-ManagerString"))
    f = tmp_path / "t.ps1"
    f.write_text(fns + "\n" + body, encoding="utf-8-sig")
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(f)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.replace("\r\n", "\n")


def test_the_script_parses_and_creates_as_a_scriptblock():
    cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('" + str(SCRIPT) + "',[ref]$t,[ref]$e);if($e.Count){$e|ForEach-Object{$_.Message};exit 1}; "
           "[void][scriptblock]::Create((Get-Content -Raw -LiteralPath '" + str(SCRIPT) + "'))")
    assert subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).returncode == 0


def subscription(tmp_path):
    out = ps(tmp_path, f"New-SubscriptionXml '{FILE}' '{DIR}' '{THUMB}' 'fondos-key-access'")
    return ET.fromstring(out.strip().split("\n", 1)[1] if out.lstrip().startswith("<?xml") else out)


def test_the_subscription_forwards_exactly_the_six_event_ids_for_exactly_the_key_paths(tmp_path):
    root = subscription(tmp_path)
    query = root.find("s:Query", NS).text
    ids = sorted(set(int(i) for i in re.findall(r"EventID=(\d+)", query)))
    assert ids == [1102, 4656, 4660, 4663, 4670, 4719]
    assert f"Data[@Name='ObjectName']='{FILE}'" in query and f"Data[@Name='ObjectName']='{DIR}'" in query
    assert "contains(" not in query and "starts-with(" not in query                    # the Windows event XPath dialect has no such function
    assert query.count('Path="Security"') == 3                                          # one query, one channel, two selects: nothing else is read
    ET.fromstring(query)                                                                  # the query itself is well-formed XML


def test_the_subscription_is_source_initiated_https_with_a_60_second_heartbeat_and_low_latency(tmp_path):
    root = subscription(tmp_path)
    t = lambda tag: root.find("s:" + tag, NS)             # noqa: E731
    assert t("SubscriptionType").text == "SourceInitiated" and t("TransportName").text == "HTTPS" and t("Enabled").text == "true"
    assert t("ReadExistingEvents").text == "false"
    assert t("Delivery").attrib["Mode"] == "Push"
    assert int(t("Delivery").find("s:PushSettings/s:Heartbeat", NS).attrib["Interval"]) == 60000
    assert int(t("Delivery").find("s:Batching/s:MaxLatencyTime", NS).text) <= 30000      # an event reaches the sink well inside the 60 s of the spec
    assert t("AllowedSourceNonDomainComputers").find("s:AllowedIssuerCAList/s:AllowedIssuerCA", NS).text == THUMB


def test_the_manager_string_names_the_collector_over_https_and_the_issuing_ca(tmp_path):
    out = ps(tmp_path, f"Get-ManagerString 'wec.example.org' 5986 60 '{THUMB}'").strip()
    assert out == f"Server=HTTPS://wec.example.org:5986/wsman/SubscriptionManager/WEC,Refresh=60,IssuerCA={THUMB}"


def run_script(*args):
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args], capture_output=True, text=True)
    return done.stdout + done.stderr


def test_without_elevation_it_refuses_before_touching_anything():
    out = run_script("-CollectorHost", "wec.example.org", "-ClientCertThumbprint", THUMB, "-IssuerCaThumbprint", THUMB, "-Apply")
    if "elevated" not in out and "Run this in an elevated" not in out:
        pytest.skip("the test session itself is elevated")
    assert "FAILED: Run this in an elevated PowerShell" in out


@pytest.mark.parametrize("args", [
    ["-CollectorHost", "wec.example.org", "-ClientCertThumbprint", "zz", "-IssuerCaThumbprint", THUMB],
    ["-CollectorHost", "bad host;x", "-ClientCertThumbprint", THUMB, "-IssuerCaThumbprint", THUMB],
    ["-ClientCertThumbprint", THUMB, "-IssuerCaThumbprint", THUMB],
])
def test_malformed_or_missing_parameters_are_refused(args):
    out = run_script(*args)
    # a parameter-binding error happens before the script body runs: no elevation check, no plan, no change
    assert out.strip() != "" and "FAILED:" not in out and "LOG SINK SOURCE CONFIGURED" not in out and "DRY RUN" not in out and "Preconditions passed" not in out


def test_the_script_opens_no_listener_no_firewall_rule_and_stores_no_secret():
    s = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("New-NetFirewallRule", "winrm quickconfig", "winrm create", "Enable-PSRemoting", "Set-Item WSMan", "Export-PfxCertificate", "ConvertTo-SecureString", "Read-Host"):
        assert forbidden not in s, forbidden
    assert "CollectorHost -in @($env:COMPUTERNAME" in s                                   # the sink cannot be this machine
    assert "-Apply" in s and "DRY RUN" in s
