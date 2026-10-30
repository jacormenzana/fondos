# Unified provisioning runbook: CI/CD runner + Owner DSN vault (FND-0353, FND-0354)

Written for: the repository owner, at the keyboard of an elevated terminal. It is the single document to follow, in order; the two specifications behind it (`CICD_SERVICE_ACCOUNT_SPEC.md`, `OWNER_DSN_VAULT.md`) explain why and are not needed during the run. No agent session performs any step.

**What this run provisions:** the isolated identities and storage boundaries (two local accounts, the GitHub App and its key, the vault, the read-only DDL tool tree) with all ACLs, audit rules and assertions. **What it does not provision:** `open_pr.py`, `open_pr_request.py`, the request schema, the deployed `app\` copy and the scheduled tasks of the runner (the code does not exist yet; spec section 11 steps 4 to 6 and acceptance 1 to 14), and no DDL. Step 2 of the resumption sequence therefore stays OPEN until that code is built and the acceptance checks pass; no database migration happens before then.

## Step 0. Sterilize (before anything else)
The vault script copies the Python interpreter from `C:\data\envs\des`, so that directory must not be changeable by a live process while the copy happens.
1. Close every Claude Code / editor / terminal session, **including the one this runbook came from**.
2. Restart Windows. Do not start any agent session until Step 8 is finished.
3. Open only: a browser (Step 1 and 3) and *Windows PowerShell as administrator* (Steps 2 and 4).

## Step 1. Create the GitHub App (browser, signed in as `jacormenzana`, two-factor on)
*Settings* > *Developer settings* > *GitHub Apps* > *New GitHub App*, then exactly:
| Field | Value |
|---|---|
| GitHub App name | `fondos-pr-opener` (if taken: `fondos-pr-opener-jacormenzana`, and tell me, because the bot login in the receipts changes) |
| Description | `Opens pull requests for jacormenzana/fondos. Cannot merge.` |
| Homepage URL | `https://github.com/jacormenzana/fondos` |
| Callback URL, Setup URL | empty |
| Expire user authorization tokens, Request user authorization (OAuth) during installation, Enable Device Flow, Redirect on update | all unchecked |
| Webhook > Active | unchecked |
| Repository permissions | **Pull requests: Read and write**; **Checks: Read-only**; Metadata: Read-only (preselected); every other row *No access* |
| Account permissions, Organization permissions | every row *No access* |
| Subscribe to events | none |
| Where can this GitHub App be installed? | **Only on this account** |

Click *Create GitHub App*. On the *About* page write down the **App ID**. Under *Private keys* click *Generate a private key* once: the browser downloads `fondos-pr-opener.<date>.private-key.pem`. Do not open it. Note its full path.

## Step 2. CI runner account, tree, ACLs and key (elevated PowerShell #1)
First line: set the path of the downloaded key (edit it), then paste the rest as one block.
```powershell
$key  = 'C:\Users\<you>\Downloads\fondos-pr-opener.<date>.private-key.pem'
```
```powershell
$c    = 'ee7e0e5bf9a609ccaea041eeda107fac5e8f84bb'
$blob = '798eb26ffeb2a612ce74e3910af32bf4503e1ffd'
$repo = 'C:\desarrollo\fondos'
$path = 'scripts/ops/windows/provision_ci_runner.ps1'
$git  = (Get-Command git.exe).Source
if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
if (-not (Test-Path -LiteralPath $key)) { throw 'Set $key to the downloaded .pem first.' }
$g = @('-c', "safe.directory=$repo", '-C', $repo)
if ((& $git @g rev-parse "${c}:${path}") -ne $blob) { throw 'The script in that commit is not the reviewed one: stop.' }
$text = (& $git @g show "${c}:${path}") -join "`n"
& ([scriptblock]::Create($text)) -KeySource $key
```
It asks, in order: the daily user (press Enter if the name shown is yours), the password of `fondos_ci_runner` twice. It then creates the account (no interactive, Remote Desktop or network logon; batch logon only), `C:\ProgramData\fondos\ci_runner\` with `github_app_keys\`, `app\`, `venv\`, `inbox\`, `outbox\`, `quarantine\`, `alerts\`, every ACL of the spec, the audit rule on the key directory, and moves the key in (validated as one RSA PEM of 2048 bits), then overwrites and deletes the downloaded file. Success ends with `PROVISIONED OK` and the key directory's `icacls` listing: exactly `fondos_ci_runner` (R), `SYSTEM` (F), `Administrators` (F). `FAILED` means the run undid itself and left your downloaded key untouched: read the message, fix the cause, run again.
The downloaded key sat in your Downloads folder for the minutes between Step 1 and this step; if anything ran in that time that should not have, delete the key in the App's page and generate another.

## Step 3. Install the App (browser)
App page > *Install App* > *Install* next to `jacormenzana` > *Only select repositories* > `jacormenzana/fondos` > *Install*. The address afterwards is `https://github.com/settings/installations/<number>`: write down the **installation id**. Then *Settings* > *Applications* > *Installed GitHub Apps* > *Configure*: repository access lists only `jacormenzana/fondos`; permissions list exactly `Pull requests: Read and write`, `Checks: Read-only`, `Metadata: Read-only`. Anything more: delete the App and redo Step 1.

## Step 4. Owner DSN vault and DDL tool (elevated PowerShell #2)
Paste as one block. The last line prompts; answer in this order: daily user (Enter), password of `fondos_db_owner` twice, the owner DSN (`postgresql://<role>:<password>@127.0.0.1:5436/gestion`, no echo).
```powershell
$c    = '7d7061461ea6cb22d9a06a13806fe87b21f4d909'
$blob = '96f964ff17b7698ffd6e9d379fa8962b023b400f'
$repo = 'C:\desarrollo\fondos'
$path = 'scripts/ops/windows/provision_db_owner.ps1'
$git  = (Get-Command git.exe).Source
if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
$g = @('-c', "safe.directory=$repo", '-C', $repo)
if ((& $git @g rev-parse "${c}:${path}") -ne $blob) { throw 'The script in that commit is not the reviewed one: stop.' }
$text = (& $git @g show "${c}:${path}") -join "`n"
& ([scriptblock]::Create($text)) -Commit $c
```
Success ends with `PROVISIONED OK`, the SHA-256 of `MANIFEST.sha256` and the vault's `icacls` listing (exactly `fondos_db_owner` (R) and `SYSTEM` (DENY)). `FAILED` undoes the run.

## Step 5. Verify the boundaries (normal, non-elevated cmd.exe)
Each line must answer as written; any other answer is a failure: stop and report it.
```
dir  C:\ProgramData\fondos\ci_runner\github_app_keys                      Access is denied
type C:\ProgramData\fondos\ci_runner\github_app_keys\fondos-pr-opener.pem  Access is denied
echo x> C:\ProgramData\fondos\ci_runner\outbox\probe.txt                  Access is denied
echo x> C:\ProgramData\fondos\ci_runner\app\probe.txt                     Access is denied
echo x> C:\ProgramData\fondos\ci_runner\inbox\probe.txt                   succeeds   (then: del C:\ProgramData\fondos\ci_runner\inbox\probe.txt)
type C:\ProgramData\fondos\db_owner\vault\owner_dsn.conf                  Access is denied
dir  C:\ProgramData\fondos\db_owner\vault                                 Access is denied
echo x> C:\ProgramData\fondos\db_owner\app\probe.txt                      Access is denied
net localgroup Administrators                                             lists neither fondos_ci_runner nor fondos_db_owner
findstr /b /i "FONDOS_PG_DSN_OWNER" "C:\desarrollo\fondos\.env"           no output
```

## Step 6. First live check of the DDL launcher (changes nothing)
```
C:\ProgramData\fondos\db_owner\app\scripts\ops\windows\run_db_owner.cmd --check
```
`runas` asks for the `fondos_db_owner` password and opens a console that prints, in this order: `target: host=127.0.0.1 port=5436 dbname=gestion`; the deployed commit with `manifest verified`; `server verified: system_identifier 7688429679929688102, database gestion oid 34094, PostgreSQL 17`; the receipt path. A `HARD STOP` line is a failure.

## Step 7. Record and report
Paste into the next session (results only, never a key, DSN or password): the `PROVISIONED OK` lines and both `icacls` listings from Steps 2 and 4; the SHA-256 of `MANIFEST.sha256`; the App ID and the installation id; the ten answers of Step 5; the four lines of Step 6. Copy the receipt from `C:\ProgramData\fondos\db_owner\receipts\` to `deployments/` in the repository.

## Step 8. What happens next (no action now)
1. Restart the agent session; its first action is the read-only pre-flight and a check of the results above.
2. Build `open_pr.py`, `open_pr_request.py`, the locked schema, the deploy script and the scheduled tasks, and run acceptance 1 to 14 of `CICD_SERVICE_ACCOUNT_SPEC.md`. Step 2 is validated only when they pass.
3. Only then Step 4 of the resumption sequence: merge `deploy/master-override` through a pull request, then `run_db_owner.cmd --phase relations --apply` and `--phase iam --apply`, the verifier, the ingestion of the 13 pending notes.
