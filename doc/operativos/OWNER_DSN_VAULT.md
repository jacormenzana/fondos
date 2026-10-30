# Runbook: the Owner DSN vault, owned by an isolated local account (FND-0354, Step 3 of the resumption sequence)

Written for: the repository owner, who runs the elevated block of section 3 once and the checks of section 4. No agent session creates, reads, tests or runs anything here. Decisions (owner, 2026-10-30): the canonical owner DSN lives in one file outside the repository, **owned by a dedicated local account `fondos_db_owner`, which no agent session can reach**; the deployment tool that uses it runs only as that account. This supersedes the first draft (a file in the owner's profile), because the agent sessions run as the owner's daily Windows user and a file that user can open is not isolated.

## 1. The boundary, as technical facts
| Fact | Enforced by |
|---|---|
| The vault `C:\ProgramData\fondos\db_owner\vault\` has exactly two access entries: `fondos_db_owner` read, and `NT AUTHORITY\SYSTEM` denied. Inheritance is removed. The owner's daily user, `Users`, `Authenticated Users`, `Everyone` and `Administrators` hold no entry. | `icacls` (section 3), asserted at the end of the block and again by section 4 |
| `fondos_db_owner` is a local account that is not in `Administrators` and not in `Remote Desktop Users`, is hidden from the sign-in screen, and has a password that only the owner knows. | `New-LocalUser` plus the assertions of section 3 |
| An agent process cannot become `fondos_db_owner`: `runas` and `Start-Process -Credential` need its password, and `runas /savecred` is never used, because it would write the password to the Credential Manager. | the password exists only in the owner's memory |
| An agent process cannot obtain an elevated token to take the vault over: UAC is fixed at *Prompt for credentials on the secure desktop*, which no process can reach or answer. | registry values `EnableLUA`=1, `ConsentPromptBehaviorAdmin`=1, `PromptOnSecureDesktop`=1, set and asserted by the block |
| The code that runs as `fondos_db_owner` cannot be altered by an agent: it is a deployed copy under `C:\ProgramData\fondos\db_owner\app\` and a virtual environment under `...\venv\`, writable only by `fondos_db_owner` and by elevated administrators; the owner's daily user has read and execute only. | ACL of section 3; the copy and its `MANIFEST.sha256` are produced from a reviewed commit by the Step 3 deploy script, which also checks the manifest at every start |
| Every read, write, delete or permission change on the vault leaves a Security event (4663, 4660, 4670) with the process name; a failed attempt leaves 4656. | `auditpol` plus a SACL (section 3) |
| The `.env` of the repository holds no owner credential: its keys are `FONDOS_AGENT_ACTOR`, `FONDOS_BACKLOG_PG_DSN`, `FONDOS_DB_BACKEND`, `FONDOS_PG_DSN` and `FRED_API_KEY` (key names read 2026-10-30; values not read). | checked by section 4 |

## 2. File format
`C:\ProgramData\fondos\db_owner\vault\owner_dsn.conf`, UTF-8 without BOM, one line, no quotes, no spaces around `=`:
`FONDOS_PG_DSN_OWNER=postgresql://<owner role>:<password>@127.0.0.1:5436/gestion`
The block refuses any DSN that does not target exactly `127.0.0.1:5436/gestion`. A password that contains `@`, `:`, `/` or `%` is percent-encoded.

## 3. Create the account and the vault (elevated PowerShell, once)
Open *Windows PowerShell* with *Run as administrator* (UAC asks for the administrator password) and paste the whole block. It prompts for the new account's password (twice) and for the DSN (no echo), changes nothing if the account already exists, and stops with an error at the first assertion that fails.
```powershell
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$acct  = 'fondos_db_owner'
$me    = "$env:COMPUTERNAME\$acct"
$root  = 'C:\ProgramData\fondos\db_owner'
$vault = Join-Path $root 'vault'
$file  = Join-Path $vault 'owner_dsn.conf'
$sidSystem = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$sidEveryone = New-Object Security.Principal.SecurityIdentifier('S-1-1-0')
$sidAdmins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$sidUsers  = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-545')
$sidRdp    = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-555')
$daily = "$env:COMPUTERNAME\jacor"   # the owner's daily Windows user (profile C:\Users\jacor), also the user agent sessions run as

# 1. UAC: credentials on the secure desktop (no process can answer that prompt)
$sys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
New-ItemProperty -Path $sys -Name ConsentPromptBehaviorAdmin -Value 1 -PropertyType DWord -Force | Out-Null
New-ItemProperty -Path $sys -Name PromptOnSecureDesktop -Value 1 -PropertyType DWord -Force | Out-Null
if ((Get-ItemProperty $sys).EnableLUA -ne 1) { throw 'EnableLUA is not 1: UAC is off. Turn it on and restart before continuing.' }

# 2. The account
if (Get-LocalUser -Name $acct -ErrorAction SilentlyContinue) { throw "$acct already exists: stop and review it by hand." }
$pw1 = Read-Host "Password for $acct" -AsSecureString
$pw2 = Read-Host "Repeat the password" -AsSecureString
if ([Net.NetworkCredential]::new('', $pw1).Password -cne [Net.NetworkCredential]::new('', $pw2).Password) { throw 'The two passwords differ.' }
New-LocalUser -Name $acct -Password $pw1 -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires -Description 'Runs the Backlog DDL tool and holds the owner DSN vault. Not an administrator.' | Out-Null
Add-LocalGroupMember -SID $sidUsers -Member $acct
$ul = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
New-Item -Path $ul -Force | Out-Null
New-ItemProperty -Path $ul -Name $acct -Value 0 -PropertyType DWord -Force | Out-Null

# 3. Directories (created while this elevated session still holds the default ACL)
New-Item -ItemType Directory -Path $vault, (Join-Path $root 'app'), (Join-Path $root 'venv') -Force | Out-Null

# 4. The DSN, written once (no echo, no command line, no history)
$dsnSecure = Read-Host 'Owner DSN (postgresql://role:password@127.0.0.1:5436/gestion)' -AsSecureString
$dsn = [Net.NetworkCredential]::new('', $dsnSecure).Password
if ($dsn -notmatch '^postgresql://[^:@/\s]+:[^@\s]+@127\.0\.0\.1:5436/gestion$') { throw 'The DSN must target exactly 127.0.0.1:5436/gestion.' }
[IO.File]::WriteAllText($file, "FONDOS_PG_DSN_OWNER=$dsn`n", (New-Object Text.UTF8Encoding($false)))
$dsn = $null; $dsnSecure = $null

# 5. Audit: every access to the vault leaves a Security event
auditpol /set /subcategory:"{0CCE921D-69AE-11D9-BED3-505054503030}" /success:enable /failure:enable | Out-Null
$rights = [Security.AccessControl.FileSystemRights]'ReadData, WriteData, Delete, ChangePermissions, TakeOwnership'
$aud = New-Object Security.AccessControl.FileSystemAuditRule($sidEveryone, $rights, 'ContainerInherit, ObjectInherit', 'None', 'Success, Failure')
$acl = Get-Acl -Path $vault -Audit
$acl.AddAuditRule($aud)
Set-Acl -Path $vault -AclObject $acl

# 6. Lock the vault: inheritance removed, fondos_db_owner read, SYSTEM denied, nobody else
function Invoke-Icacls { icacls @args | Out-Null; if ($LASTEXITCODE -ne 0) { throw "icacls failed: $args" } }
Invoke-Icacls $vault /inheritance:r /grant:r "${me}:(OI)(CI)(R)" /deny '*S-1-5-18:(OI)(CI)(F)'
# code and environment the account runs: the owner's daily user can read and execute, never write
foreach ($d in (Join-Path $root 'app'), (Join-Path $root 'venv')) {
    Invoke-Icacls $d /inheritance:r /grant:r "${me}:(OI)(CI)(M)" '*S-1-5-32-544:(OI)(CI)(F)' "${daily}:(OI)(CI)(RX)"
}

# 7. Assertions: any failure stops here with a message
$a = Get-Acl -Path $vault
$rules = @($a.Access)
if (-not $a.AreAccessRulesProtected) { throw 'ASSERT: vault inherits from its parent.' }
if ($rules.Count -ne 2) { throw "ASSERT: the vault has $($rules.Count) entries, expected 2." }
if (@($rules | Where-Object { $_.IsInherited }).Count -ne 0) { throw 'ASSERT: an inherited entry remains.' }
$allow = @($rules | Where-Object { $_.AccessControlType -eq 'Allow' })
$deny  = @($rules | Where-Object { $_.AccessControlType -eq 'Deny' })
if ($allow.Count -ne 1 -or $allow[0].IdentityReference.Value -ne $me) { throw 'ASSERT: the only allow entry must be fondos_db_owner.' }
if ($deny.Count -ne 1 -or $deny[0].IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -ne $sidSystem) { throw 'ASSERT: the only deny entry must be SYSTEM.' }
$adm = @(Get-LocalGroupMember -SID $sidAdmins | ForEach-Object { $_.Name })
if ($adm -contains $me) { throw 'ASSERT: fondos_db_owner is an administrator.' }
$rdp = @(Get-LocalGroupMember -SID $sidRdp | ForEach-Object { $_.Name })
if ($rdp -contains $me) { throw 'ASSERT: fondos_db_owner can use Remote Desktop.' }
if ((Get-ItemProperty $sys).ConsentPromptBehaviorAdmin -ne 1 -or (Get-ItemProperty $sys).PromptOnSecureDesktop -ne 1) { throw 'ASSERT: UAC is not on the secure-desktop credential prompt.' }
Write-Host 'VAULT OK: account created, vault locked, UAC on the secure desktop. Content was never printed.'
icacls $vault
```
Reading the block: step 6 removes the elevated session's own access to the vault (that is intended); the vault owner stays `BUILTIN\Administrators`, so a later change of the ACL needs an elevated session and leaves event 4670. Step 7 uses SIDs, so it does not depend on the Windows display language.

## 4. Verify from the owner's normal (non-elevated) cmd.exe
```
type C:\ProgramData\fondos\db_owner\vault\owner_dsn.conf
dir C:\ProgramData\fondos\db_owner\vault
icacls C:\ProgramData\fondos\db_owner\vault
net localgroup Administrators
findstr /b /i "FONDOS_PG_DSN_OWNER" "C:\desarrollo\fondos\.env"
```
Expected, in order: `Access is denied`; `Access is denied`; `Access is denied` (the daily user cannot even read the ACL); the list does not contain `fondos_db_owner`; `findstr` prints nothing. Anything else means the boundary is broken: run section 3 again after deleting the account (`net user fondos_db_owner /delete`) and the folder, from an elevated session. An agent session that runs the same three first commands gets the same result, because it has the same token.

## 5. How the deployment tool uses it (Step 3, written after the CI/CD validation)
1. The owner starts it from his terminal with `runas /user:%COMPUTERNAME%\fondos_db_owner "C:\ProgramData\fondos\db_owner\venv\Scripts\python.exe -I C:\ProgramData\fondos\db_owner\app\scripts\ops\owner_preflight.py"` and types the account's password at the prompt. The tool refuses to run under any other account.
2. At start it verifies `MANIFEST.sha256` of `app\` and the ACL of the vault; any difference exits with code 1.
3. It reads `owner_dsn.conf` into memory, never prints it, never exports it, and hands it only to the migration process it launches.
4. Hard-stop assertions before the migration tool: host `127.0.0.1`; port `5436`; `system_identifier` `7688429679929688102`; database `gestion` with oid `34094`. A mismatch exits 1 and names the fact that differs.
5. The first output line of the migration tool reads `target: host=127.0.0.1 port=5436 dbname=gestion`.

## 6. Rotation and removal (elevated session)
- Rotate the DSN after any Postgres password change: delete `owner_dsn.conf` and write the new one through steps 4 to 7 of the block (the elevated session first re-grants itself the directory with `icacls <vault> /grant:r "%USERNAME%:(OI)(CI)(M)"`, which leaves event 4670).
- Retire: `net user fondos_db_owner /delete`, then delete `C:\ProgramData\fondos\db_owner`.
