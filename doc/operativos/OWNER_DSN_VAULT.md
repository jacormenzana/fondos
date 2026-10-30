# Runbook: the Owner DSN vault and the isolated DDL launcher (FND-0354, Step 3 of the resumption sequence)

Written for: the repository owner, who runs the elevated block of section 3 once and the checks of section 4. No agent session creates, reads, tests or runs anything here. Decisions (owner, 2026-10-30): the canonical owner DSN lives in one file outside the repository, **owned by a dedicated local account `fondos_db_owner` that no agent session can reach**, and the DDL tool that uses it runs only as that account, from a read-only copy that is deployed in the same step as the vault. Vault and tool are created by ONE script in ONE elevated run and either both exist or neither does.

## 1. The boundary, as technical facts
| Fact | Enforced by |
|---|---|
| The vault `C:\ProgramData\fondos\db_owner\vault\` has exactly two entries: `fondos_db_owner` read, `NT AUTHORITY\SYSTEM` denied; inheritance removed. The owner's daily user, `Users`, `Authenticated Users`, `Everyone` and `Administrators` hold none. | `icacls`; the script asserts it and undoes the whole run if it differs |
| `fondos_db_owner` is a local account outside `Administrators` and `Remote Desktop Users`, hidden from the sign-in screen; only the owner knows its password. | `New-LocalUser` and the script's assertions |
| No agent process can become that account: `runas` needs its password, and `/savecred` is never used (it would store the password in the Credential Manager). | the password exists only in the owner's memory |
| No agent process can obtain an elevated token to take the vault over: UAC is fixed at *Prompt for credentials on the secure desktop*. | registry `EnableLUA`=1, `ConsentPromptBehaviorAdmin`=1, `PromptOnSecureDesktop`=1, set and asserted by the script |
| The code that runs as `fondos_db_owner` is a copy of ONE pushed commit under `...\db_owner\app\`, with its own Python interpreter under `...\runtime\`. The account has read and execute only (it cannot rewrite its own code); the owner's daily user has read and execute only; only elevated administrators can write. | ACLs set by the script; the script asserts that neither principal holds a write right |
| Any change to any file of `app\` or `runtime\`, a missing file or an extra file (bytecode included) stops the tool before the vault is read. | `MANIFEST.sha256` (sha256 of every file plus the commit), checked at every start by `db_owner_preflight.py` |
| The launcher runs the DDL only against the real server: host `127.0.0.1` and port `5436` are fixed by the vault format; `system_identifier` 7688429679929688102, database `gestion` with oid 34094 and PostgreSQL 17 are read through a read-only session and must match. A stand-in answering on 5436 stops the run. | `db_owner_preflight.py`, tested with a wrong identifier, a wrong oid, a wrong version and a wrong account |
| Every read, write, delete or permission change on the vault leaves a Security event (4663, 4660, 4670) with the process name; a refused attempt leaves 4656. | `auditpol` and a SACL, set by the script |
| `.env` holds no owner credential: its keys are `FONDOS_AGENT_ACTOR`, `FONDOS_BACKLOG_PG_DSN`, `FONDOS_DB_BACKEND`, `FONDOS_PG_DSN`, `FRED_API_KEY` (names read 2026-10-30, values not read). | section 4 |

## 2. Vault format
`C:\ProgramData\fondos\db_owner\vault\owner_dsn.conf`, UTF-8 without BOM, exactly one line: `FONDOS_PG_DSN_OWNER=postgresql://<role>:<password>@127.0.0.1:5436/gestion`. The script and the launcher both refuse any other host, port or database. A password that contains `@`, `:`, `/` or `%` is percent-encoded.

## 3. Provision (elevated PowerShell, once)
The commands and the pinned commit and blob hashes live in ONE place, `PROVISIONING_RUNBOOK.md` (Step 5, with the CI runner steps around it); they are not repeated here so they cannot drift. The block reads the script out of the git object store (never from the working tree, which an agent can edit), refuses it if its blob hash differs from the pinned one and runs it in memory.

What the script does, in this order (any failure undoes what the run created and prints `FAILED`):
1. Checks that git is under Program Files and the commit exists on an `origin` branch.
2. Resolves the daily user from the logged-on session (`Win32_ComputerSystem.UserName`, which is correct even when the elevation used another administrator's credentials), shows it and lets you correct it (`DOMAIN\user`). Nothing is hard-coded.
3. Fixes UAC on the secure desktop; locks the root `C:\ProgramData\fondos\db_owner` to administrators before anything is staged in it.
4. Creates `fondos_db_owner` (password typed twice, no echo), not an administrator, hidden from sign-in.
5. Writes the vault from the DSN you type (no echo, never a command line), turns on the audit policy and the SACL, locks the vault.
6. Extracts `shared/`, `db/pg_backlog/`, `migrate_backlog_links.py`, `migrate_backlog_v2.py`, `db_owner_preflight.py`, `db_owner_manifest.py` and `run_db_owner.cmd` from the commit into `app\` (git archive and tar) and copies the Python interpreter with `psycopg` and its libraries into `runtime\` (about 84 MB), then checks every copied file against the hash recorded when it was installed (conda-meta, pip RECORD) and refuses bytecode, `.pth` pointers, startup hooks and unrecorded files.
7. Writes `MANIFEST.sha256`, sets the final ACLs (the account reads and executes; the daily user reads and executes; `receipts\` is the only place the account can write) and asserts them.
8. Prints the SHA-256 of `MANIFEST.sha256`: record it in `deployments/`.

Stated limit: the install-time records live in the same environment directory, so the check proves the copy equals what was installed and catches added or edited files; it does not stop an attacker who rewrote both a file and its record. Run the block after a restart with no agent session running. After the copy the tree is immutable for every non-elevated process and any later change is caught by the manifest.

## 4. Verify (owner's normal, non-elevated cmd.exe)
```
type C:\ProgramData\fondos\db_owner\vault\owner_dsn.conf
dir C:\ProgramData\fondos\db_owner\vault
net localgroup Administrators
findstr /b /i "FONDOS_PG_DSN_OWNER" "C:\desarrollo\fondos\.env"
echo x> C:\ProgramData\fondos\db_owner\app\probe.txt
```
Expected, in order: `Access is denied`; `Access is denied`; a list without `fondos_db_owner`; no output; `Access is denied`. An agent session gets exactly the same answers, because it holds the same token. Then run the first live check (it changes nothing and prints no secret):
```
C:\ProgramData\fondos\db_owner\app\scripts\ops\windows\run_db_owner.cmd --check
```
`runas` asks for the `fondos_db_owner` password in this window and opens a console that prints, in this order: `target: host=127.0.0.1 port=5436 dbname=gestion`, the deployed commit with `manifest verified`, and `server verified: system_identifier 7688429679929688102, database gestion oid 34094, PostgreSQL 17`. Any `HARD STOP` line is a failure: do not continue. A receipt is written to `C:\ProgramData\fondos\db_owner\receipts\` (you can read it; copy it into `deployments/`).

## 5. Running the DDL (Step 4, only after the CI/CD provisioning is validated)
```
run_db_owner.cmd --phase relations              dry run
run_db_owner.cmd --phase relations --apply      DDL: you must type  APPLY relations ON gestion 127.0.0.1:5436
run_db_owner.cmd --phase iam --apply            second step, same confirmation with iam
run_db_owner.cmd --phase iam --down --apply     staged rollback, confirmation  APPLY ROLLBACK iam ON gestion 127.0.0.1:5436
```
The launcher hands the DSN to `migrate_backlog_v2.py` through the child's environment only. Its output and a receipt are written to the console and to `receipts\`; the DSN never appears in either.

## 6. Rotation, update and removal (elevated session)
- New tool version: remove the account and the tree (below) and provision again from the new reviewed commit. The tree is never patched in place.
- New DSN after a Postgres password change: same, or take the vault over with `takeown`/`icacls` (leaves events 4670 and 4674), replace the file and lock it again.
- Retire: `net user fondos_db_owner /delete`, then `takeown /F C:\ProgramData\fondos\db_owner /R /D Y`, `icacls C:\ProgramData\fondos\db_owner /grant Administrators:F /T`, and delete the folder.
