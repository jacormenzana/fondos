# Runbook: the Owner DSN vault (FND-0354, Step 3 of the resumption sequence)

Written for: the repository owner, who runs every command below in his own cmd.exe terminal. Nothing here is executed by an agent session, and no agent session reads, creates or tests the file. Decision (owner, 2026-10-30): the canonical owner DSN lives in one restricted file outside the repository; it is not in `.env`, in an environment variable of a shell that launches agents, or in the repository.

## 1. Location and format
- File: `%USERPROFILE%\.fondos_keys\owner_dsn.conf` (outside every Git working tree).
- One line, UTF-8 without BOM, `KEY=VALUE`, no quotes, no spaces around `=`:
  `FONDOS_PG_DSN_OWNER=postgresql://<owner role>:<password>@127.0.0.1:5436/gestion`
- The password never appears on a command line, in `.env`, in shell history or in a log (the creation command below prompts for it with no echo).

## 2. Create it (owner terminal, cmd.exe, once)
Run in order. The first `icacls` removes every inherited entry before the secret exists.
```
mkdir "%USERPROFILE%\.fondos_keys"
icacls "%USERPROFILE%\.fondos_keys" /inheritance:r /grant:r "%USERNAME%:(OI)(CI)(M)"
powershell -NoProfile -Command "$s = Read-Host 'Owner DSN' -AsSecureString; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); $t = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b); [IO.File]::WriteAllText($env:USERPROFILE + '\.fondos_keys\owner_dsn.conf', 'FONDOS_PG_DSN_OWNER=' + $t + [Environment]::NewLine); [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)"
icacls "%USERPROFILE%\.fondos_keys\owner_dsn.conf" /inheritance:r /grant:r "%USERNAME%:(R)" /deny "NT AUTHORITY\SYSTEM:(F)"
icacls "%USERPROFILE%\.fondos_keys" /grant:r "%USERNAME%:(OI)(CI)(RX)" /deny "NT AUTHORITY\SYSTEM:(OI)(CI)(F)"
attrib +R "%USERPROFILE%\.fondos_keys\owner_dsn.conf"
```
Paste the DSN at the prompt (it is not echoed). The file is born inside a directory that already has no inherited entries, so no other principal ever holds a grant on it.

## 3. Verify (owner terminal)
```
icacls "%USERPROFILE%\.fondos_keys\owner_dsn.conf"
icacls "%USERPROFILE%\.fondos_keys"
```
Expected, for each of the two paths, exactly two entries and no `(I)` (inherited) flag: the owner account with `(R)` (file) / `(OI)(CI)(RX)` (directory), and `NT AUTHORITY\SYSTEM` with `(DENY)(F)`. No `Users`, `Authenticated Users`, `Everyone` or `Administrators` entry. Paste only that listing, never the file content.

Notes on the two denials the owner ordered:
- **Other local users** need no explicit deny: with inheritance removed they hold no grant. An explicit deny for `BUILTIN\Users` or `Everyone` must NOT be added: Windows evaluates a deny before any grant, and the owner is a member of both groups, so he would lock himself out.
- **SYSTEM** is denied explicitly as ordered. It limits services running as SYSTEM (backup, antivirus scans); an administrator can still take ownership and change the ACL, which is why the audit rule in section 5 exists.

## 4. How the pre-flight wrapper uses it (to be written in Step 3, after the CI/CD validation)
1. Run only in the owner's terminal, by the owner. It refuses to start if `icacls` on the file lists anything other than the two entries above, or if the file has an inherited entry.
2. Parse `owner_dsn.conf` (`KEY=VALUE`, exactly one key `FONDOS_PG_DSN_OWNER`), keep the value in memory, never print it, never export it to the environment of a child other than the migration tool it launches.
3. Hard-stop assertions, in this order, before the migration tool is invoked: host `127.0.0.1`; port `5436`; `system_identifier` `7688429679929688102`; database `gestion` with oid `34094`. Any mismatch aborts with exit 1 and a message naming the fact that differs (a port check alone cannot catch another server that answers on 5436).
4. Print the first output line of the migration tool as `target: host=127.0.0.1 port=5436 dbname=gestion`.

## 5. Limit of this design, and how it is covered
The agent sessions on this machine run as the owner's own Windows user. A file ACL that admits "the owner account only" therefore admits an agent session too: the file is protected from every other account, not from the owner's own processes. Agents are forbidden by rule to search for, read or reuse it (`AGENTS.md`, standing credential rules), and that rule is the control; the ACL is not. To make a breach visible instead of silent:
- Add an audit entry on the file and run once, elevated: `auditpol /set /subcategory:"File System" /success:enable /failure:enable`, then a SACL on `owner_dsn.conf` for `Everyone`, rights `ReadData`, flags `Success, Failure`. Each read leaves a Security event 4663 whose `ProcessName` identifies the reader. The only legitimate reader is the pre-flight wrapper's interpreter started from the owner's terminal.
- If the owner wants a hard boundary, the upgrade is a second local account (for example `fondos_owner_ops`) that owns the file and runs the wrapper, with the owner's daily account absent from the ACL, exactly as the key file of `CICD_SERVICE_ACCOUNT_SPEC.md` section 4. Not required by the current decision; recorded so the trade-off is explicit.

## 6. Rotation and removal
- Rotate after any password change in Postgres: `icacls "%USERPROFILE%\.fondos_keys" /grant:r "%USERNAME%:(OI)(CI)(M)"`, `attrib -R` on the file, repeat the `powershell` step of section 2, then tighten again as in section 2.
- Retire: `del /f "%USERPROFILE%\.fondos_keys\owner_dsn.conf"` then `rmdir "%USERPROFILE%\.fondos_keys"`.
