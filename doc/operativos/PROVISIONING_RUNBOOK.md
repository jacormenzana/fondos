# Unified provisioning runbook: CI/CD runner + Owner DSN vault (FND-0353, FND-0354)

Written for: the repository owner, at the keyboard. It is the single document to follow, in order. Every block is self-contained (it carries its own pinned-commit check) and ends with the one call that prompts, so paste it whole. No agent session performs any step.

**What this run provisions:** two isolated local accounts, the GitHub App and its key, the vault, the read-only DDL tool tree, with all ACLs, audit rules, assertions and a structured boundary receipt. **What it does not provision:** `open_pr.py`, `open_pr_request.py`, the request schema, the runner's deployed `app\` copy and its scheduled tasks (the code does not exist yet; spec section 11 steps 4 to 6 and acceptance 1 to 15), and no DDL. Step 2 of the resumption sequence stays OPEN until that code is built and the acceptance checks pass; no database migration happens before then.

**Pinned commits** (every block refuses a script whose git blob differs from the one printed here):
| Script | Commit | Blob |
|---|---|---|
| `provision_ci_runner.ps1` | `124f0a2a9a63c2936699eda2a46ceaa84a6ee44b` | `afe45ac1ce1a01b358868a126b624483deea3034` |
| `provision_db_owner.ps1` and `assert_boundaries.ps1` | `f0b7c4c96301f6d29a17424cf3c31e8436dfa49a` | `786e436380903482953b85fa31e89997b78ccb3d`, `cd7ff36a6cc6378321b1347aa07afc8b420477cc` |

## Step 0. Sterilize
1. Close every Claude Code / editor / terminal session, including the one this runbook came from. Restart Windows. Start no agent session until Step 8 is finished.
2. Open only a browser and *Windows PowerShell as administrator*.
3. About the Python interpreter that `provision_db_owner.ps1` copies from `C:\data\envs\des`: the script does not trust that directory. It copies an allow-list (the standard library, DLLs, `psycopg` and its libraries, `tzdata`, `typing_extensions`; no other package, no `.pth` file, no `__pycache__`) and then checks **every copied file against the hash recorded when it was installed** (`conda-meta` for conda files, the packages' `RECORD` for pip files). A copy that contains a bytecode file, a `.pth` pointer, a `sitecustomize`/`usercustomize` hook, a file that has no install-time record, or a file whose hash differs from its record, **refuses the run and undoes it**; only a copy where all of them pass is hashed into `MANIFEST.sha256`. The source environment is not modified (deleting files from a shared environment is a side effect this run does not need). Stated limit: the records live in the same directory, so this proves the copy equals what was installed and catches additions and edits of single files; an attacker who rewrote both a file and its record is stopped only by the restart and the absence of agent sessions in this step.

## Step 1. Create the audited key staging folder (elevated PowerShell)
```powershell
function Invoke-Pinned { param([string]$Commit, [string]$Path, [string]$Blob, [hashtable]$Params)
  $repo = 'C:\desarrollo\fondos'; $git = (Get-Command git.exe).Source
  if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
  $g = @('-c', "safe.directory=$repo", '-C', $repo)
  if ((& $git @g rev-parse "${Commit}:${Path}") -ne $Blob) { throw "The script in $Commit is not the reviewed one: stop." }
  $text = (& $git @g show "${Commit}:${Path}") -join "`n"
  & ([scriptblock]::Create($text)) @Params }
Invoke-Pinned '124f0a2a9a63c2936699eda2a46ceaa84a6ee44b' 'scripts/ops/windows/provision_ci_runner.ps1' 'afe45ac1ce1a01b358868a126b624483deea3034' @{ Stage = $true }
```
It prompts for the daily user (Enter if the name shown is yours) and creates `C:\ProgramData\fondos\key_staging`: only your user can write there (no other account, no Downloads, no OneDrive, no search index), every access to it is written to the Security log. It ends with `STAGING READY`.

## Step 2. Create the GitHub App and save the key into the staging folder (browser)
Browser: *Settings* > *Downloads* > turn ON **Ask where to save each file before downloading**. Then, signed in as `jacormenzana` (two-factor on): *Settings* > *Developer settings* > *GitHub Apps* > *New GitHub App*, exactly:
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

Click *Create GitHub App* and write down the **App ID**. Under *Private keys* click *Generate a private key* once; in the save dialog choose **`C:\ProgramData\fondos\key_staging`**. Do not open the file. Write its full path down.

## Step 3. CI runner account, tree, ACLs and key (elevated PowerShell)
Edit the first line to the path from Step 2, then paste the whole block:
```powershell
$key = 'C:\ProgramData\fondos\key_staging\<the file name>.pem'
function Invoke-Pinned { param([string]$Commit, [string]$Path, [string]$Blob, [hashtable]$Params)
  $repo = 'C:\desarrollo\fondos'; $git = (Get-Command git.exe).Source
  if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
  $g = @('-c', "safe.directory=$repo", '-C', $repo)
  if ((& $git @g rev-parse "${Commit}:${Path}") -ne $Blob) { throw "The script in $Commit is not the reviewed one: stop." }
  $text = (& $git @g show "${Commit}:${Path}") -join "`n"
  & ([scriptblock]::Create($text)) @Params }
Invoke-Pinned '124f0a2a9a63c2936699eda2a46ceaa84a6ee44b' 'scripts/ops/windows/provision_ci_runner.ps1' 'afe45ac1ce1a01b358868a126b624483deea3034' @{ KeySource = $key }
```
Prompts, in order: daily user (Enter), the `fondos_ci_runner` password twice. The run:
1. refuses a key that is not directly inside `key_staging`, or is not exactly one unmodified RSA PEM of about 2048 bits;
2. creates the account (no interactive, Remote Desktop or network logon; batch logon only), `C:\ProgramData\fondos\ci_runner\` with `github_app_keys\`, `app\`, `venv\`, `inbox\`, `outbox\`, `quarantine\`, `alerts\`, every ACL of the spec, and the audit rule on the key directory;
3. copies the key in, then **reads the Security audit trail of the staged file and refuses the key** if any process other than the browser, Windows Defender or this script read, wrote or deleted its content, or if the trail is empty (auditing not working). On refusal: delete the key in GitHub, generate another, repeat from Step 1;
4. asserts every ACL, and only then overwrites the staged file with zeros, deletes it and removes `key_staging`.
Success ends with `PROVISIONED OK` and the key directory's `icacls` listing: exactly `fondos_ci_runner` (R), `SYSTEM` (F), `Administrators` (F). `FAILED` means the run undid itself and left the staged key untouched.

## Step 4. Install the App (browser)
App page > *Install App* > *Install* next to `jacormenzana` > *Only select repositories* > `jacormenzana/fondos` > *Install*. The address afterwards is `https://github.com/settings/installations/<number>`: write down the **installation id**. Then *Settings* > *Applications* > *Installed GitHub Apps* > *Configure*: repository access lists only `jacormenzana/fondos`; permissions list exactly `Pull requests: Read and write`, `Checks: Read-only`, `Metadata: Read-only`. Anything more: delete the App and redo Step 2. (The runner repeats this check by itself at every start, spec D8, once it exists.)

## Step 5. Owner DSN vault and DDL tool (elevated PowerShell)
Paste as one block. Prompts, in order: daily user (Enter), the `fondos_db_owner` password twice, the owner DSN (`postgresql://<role>:<password>@127.0.0.1:5436/gestion`, no echo).
```powershell
function Invoke-Pinned { param([string]$Commit, [string]$Path, [string]$Blob, [hashtable]$Params)
  $repo = 'C:\desarrollo\fondos'; $git = (Get-Command git.exe).Source
  if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
  $g = @('-c', "safe.directory=$repo", '-C', $repo)
  if ((& $git @g rev-parse "${Commit}:${Path}") -ne $Blob) { throw "The script in $Commit is not the reviewed one: stop." }
  $text = (& $git @g show "${Commit}:${Path}") -join "`n"
  & ([scriptblock]::Create($text)) @Params }
$c = 'f0b7c4c96301f6d29a17424cf3c31e8436dfa49a'
Invoke-Pinned $c 'scripts/ops/windows/provision_db_owner.ps1' '786e436380903482953b85fa31e89997b78ccb3d' @{ Commit = $c }
```
The run creates `fondos_db_owner`, the vault, the read-only tree (`app\` from the commit, `runtime\` with the verified interpreter), `MANIFEST.sha256`, all ACLs and audit rules, asserts them and undoes everything on any failure. It ends with `PROVISIONED OK`, `Runtime: N files match their install-time hashes`, the SHA-256 of `MANIFEST.sha256` and the vault's `icacls` listing (exactly `fondos_db_owner` (R) and `SYSTEM` (DENY)).

## Step 6. Automated boundary assertions (NORMAL, non-elevated PowerShell)
This replaces any manual checking. It runs ten checks with the token an agent session has, counts a check as passed only on the specific "access denied" error (a missing path or any other error is a failure), refuses to run if the window is elevated, and writes `deployments\<UTC time>_boundary-assertions_<computer>.json` with a `.sha256` next to it. Optional: add `SshKey = 'C:\Users\<you>\.ssh\<your private key>'` to the hashtable to have `ssh-keygen -Y sign` write a `.sig` (namespace `fondos-boundaries`).
```powershell
function Invoke-Pinned { param([string]$Commit, [string]$Path, [string]$Blob, [hashtable]$Params)
  $repo = 'C:\desarrollo\fondos'; $git = (Get-Command git.exe).Source
  if (-not $git.StartsWith($env:ProgramFiles, [StringComparison]::OrdinalIgnoreCase)) { throw "git is $git, outside Program Files" }
  $g = @('-c', "safe.directory=$repo", '-C', $repo)
  if ((& $git @g rev-parse "${Commit}:${Path}") -ne $Blob) { throw "The script in $Commit is not the reviewed one: stop." }
  $text = (& $git @g show "${Commit}:${Path}") -join "`n"
  & ([scriptblock]::Create($text)) @Params }
$ab = 'cd7ff36a6cc6378321b1347aa07afc8b420477cc'
Invoke-Pinned 'f0b7c4c96301f6d29a17424cf3c31e8436dfa49a' 'scripts/ops/windows/assert_boundaries.ps1' $ab @{ ScriptBlob = $ab }
```
Expected: ten `PASS` lines (B01 to B10) and `OVERALL: PASS`. Any `FAIL` line is a failure: stop and report it. What the receipt is: a repeatable, machine-readable record of what a non-elevated process of your user could and could not do, with the token facts and the script's blob id. It is evidence, not a proof: anything running as your user could write a file of the same shape. That is why it is committed to the repository and reviewed, and why the signature is optional rather than a boundary.

## Step 7. First live check of the DDL launcher (changes nothing)
```
C:\ProgramData\fondos\db_owner\app\scripts\ops\windows\run_db_owner.cmd --check
```
`runas` asks for the `fondos_db_owner` password and opens a console that prints, in this order: `target: host=127.0.0.1 port=5436 dbname=gestion`; the deployed commit with `manifest verified`; `server verified: system_identifier 7688429679929688102, database gestion oid 34094, PostgreSQL 17`; the receipt path. A `HARD STOP` line is a failure.

## Step 8. Record and report
Results only, never a key, DSN or password: both `PROVISIONED OK` blocks and `icacls` listings (Steps 3 and 5), the `Runtime: N files...` line, the SHA-256 of `MANIFEST.sha256`, the App ID and installation id, the path and `OVERALL` line of the boundary receipt, the lines of Step 7. Copy the DDL launcher receipt from `C:\ProgramData\fondos\db_owner\receipts\` to `deployments/`. Then restart the agent session: its first action is the read-only pre-flight and a check of the results above.

## Step 9. What happens next (no action now)
1. Build `open_pr.py` (including the D8 startup check of the issued token's permissions), `open_pr_request.py`, the locked schema, the deploy script and the scheduled tasks, and run acceptance 1 to 15 of `CICD_SERVICE_ACCOUNT_SPEC.md`. Step 2 is validated only when they pass.
2. Only then Step 4 of the resumption sequence: merge `deploy/master-override` through a pull request, then `run_db_owner.cmd --phase relations --apply` and `--phase iam --apply`, the verifier, the ingestion of the 13 pending notes.
