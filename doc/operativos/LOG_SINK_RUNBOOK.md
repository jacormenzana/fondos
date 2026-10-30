# Runbook: forwarding the key-access events off the machine (FND-0353, spec section 7 external part, decision D6)

Written for: the repository owner and the DevOps lead. It is an addition to `PROVISIONING_RUNBOOK.md` (that runbook is not edited): run it after Step 3 of that runbook (the key directory and its audit rule exist) and before acceptance check 5. Acceptance 5 cannot pass without it, and the runner's telemetry depends on it.

## 1. What this script does and does not do
- **Does:** turns this machine into a Windows Event Forwarding source over HTTPS with certificates (the machine is in a workgroup, so Kerberos is not available). It forwards exactly: 4656, 4663, 4660, 4670 for the App key file and its directory, 1102 (audit log cleared), 4719 (audit policy changed). Batching latency is at most 30 s and the subscription heartbeat is 60 s. It sets the Security log to at least 256 MB, turns on the audit subcategories that produce those events, starts WinRM (no listener, no firewall rule: the machine only connects out), lets NETWORK SERVICE read the Security log and the client certificate's private key, and writes the SubscriptionManager policy. The forwarding identity is NETWORK SERVICE, not `fondos_ci_runner` and not the owner (the script refuses if either is in Event Log Readers).
- **Does not:** create the collector, its certificates, or the immutable store. Spec section 7 requires a store on ANOTHER machine that this machine's administrators cannot rewrite, kept at least a year, with an alert when the heartbeat is silent for more than 5 minutes. Windows Event Forwarding delivers events and heartbeats to the collector; WORM retention and the silence alert are collector/SIEM functions (for example the collector relaying to an append-only syslog or SIEM sink, as the spec allows). **No collector has been designated in the repository or in the specification, so none is assumed:** the script takes the collector's name as a mandatory parameter.

## 2. What you must provide before running it (the script refuses without them)
| Item | Requirement |
|---|---|
| Collector host | another machine, reachable on TCP 5986 from this one, with the Windows Event Collector service and an HTTPS WinRM listener |
| CA | a certificate authority both machines trust; its certificate in `LocalMachine\Root` (or `\CA`) on this machine |
| Collector server certificate | issued by that CA, CN = the collector's name, Server Authentication |
| Client certificate for this machine | issued by that CA, Client Authentication, with its private key, in `LocalMachine\My`, valid for more than 30 days |
| Thumbprints | the client certificate's and the CA's, 40 hex characters each |

On the collector (DevOps): `wecutil qc /q`; an HTTPS listener bound to the server certificate; inbound 5986 allowed from this machine only; after this script's apply, import the subscription it writes with `wecutil cs <fondos-key-access.xml>`; then `wecutil gr fondos-key-access` lists this machine as a source once it connects.

## 3. Run (elevated PowerShell on this machine)
Dry run first: it checks every precondition (certificates, CA, usage, expiry, reachability of the collector, no other SubscriptionManager already configured, the runner and owner accounts not in Event Log Readers) and prints the plan, changing nothing.
```powershell
$collector = 'wec.example.org'                                   # EDIT: the collector's name
$client    = '0000000000000000000000000000000000000000'          # EDIT: client certificate thumbprint
$issuer    = '0000000000000000000000000000000000000000'          # EDIT: CA certificate thumbprint
function Invoke-Pinned { param([string]$Commit, [string]$Path, [string]$Blob, [hashtable]$Params)
  $repo = 'C:\desarrollo\fondos'; $git = (Get-Command git.exe).Source
  if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
  $g = @('-c', "safe.directory=$repo", '-C', $repo)
  if ((& $git @g rev-parse "${Commit}:${Path}") -ne $Blob) { throw "The script in $Commit is not the reviewed one: stop." }
  $text = (& $git @g show "${Commit}:${Path}") -join "`n"
  & ([scriptblock]::Create($text)) @Params }
Invoke-Pinned '9a8f1b00d0a647de490b3cf67649903025e4816c' 'scripts/ops/windows/provision_log_sink.ps1' '873ef36318916e466447d95db5ce762e2e7c3f6b' @{ CollectorHost = $collector; ClientCertThumbprint = $client; IssuerCaThumbprint = $issuer }
```
Expected: `Preconditions passed. Plan:` with six lines, then `DRY RUN: nothing was changed`. Any `FAILED:` line names the missing precondition. To apply, add `; Apply = $true` inside the hashtable of the last line and run it again. Success ends with `LOG SINK SOURCE CONFIGURED` and writes `C:\ProgramData\fondos\log_sink\fondos-key-access.xml`, which DevOps imports on the collector. A failure after the first change undoes the log size, the WinRM settings, the group membership, the key ACL and the registry value (the two audit subcategories stay on: enabling auditing is harmless).

## 4. Prove it works (acceptance 5; nothing here is simulated)
1. On this machine, from a normal (non-elevated) terminal: `type C:\ProgramData\fondos\ci_runner\github_app_keys\fondos-pr-opener.pem` → access denied. Within 60 seconds the collector's `ForwardedEvents` log shows a 4656 failure event for that object from this computer.
2. Heartbeat: on the collector, `wecutil gr fondos-key-access` shows the source with a recent heartbeat; stop WinRM on this machine (`Stop-Service WinRM`) and confirm that the sink's silence alert fires after 5 minutes, then start it again.
3. 1102 and 4719: the spec runs these on a TEST machine only (`wevtutil cl Security` clears the audit log and must never be run here). On this machine the check is that the subscription's query contains both ids (the pytest suite asserts it).
4. Report the collector-side output only; never paste a private key.

## 5. Residual risks stated
- Source-initiated forwarding pushes events out, but an attacker holding SYSTEM on this machine can stop WinRM or alter the registry policy; that is detected, not prevented, by the collector's heartbeat alert, which must exist for acceptance 5 to be meaningful.
- The script asserts the local configuration. Whether events really arrive is proven only by section 4 against the real collector.
