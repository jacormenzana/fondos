# Specification: a GitHub App that opens pull requests, run by a dedicated service account (FND-0353, next cycle)

Written for: the repository owner and the DevOps lead, who provision it. Nothing here is implemented. It builds on `GITHUB_PR_AUTOMATION_CREDENTIALS.md` (what is missing and the least privilege). **Decisions are locked** (owner, 2026-10-29); every control below is mandatory, none is optional.

## 1. The problem
An agent session can build a branch and push it, but it cannot open the pull request. `master` is protected (pull request, four required checks, administrators included). Each change therefore needs a person to click "Create pull request", and when that step stalls the work waits on a branch (this deployment needed a bridgehead branch for exactly that reason).

## 2. Locked decisions
| # | Decision | Consequence |
|---|---|---|
| D1 | **The provisioning path is a GitHub App.** A machine user and a personal token are rejected. | Installation tokens last one hour; every action is attributed to the app in the audit log; no personal secret exists. |
| D2 | **The App's private key is ONE file (`.pem`), owned by a dedicated local service account `fondos_ci_runner`; no other account can read it.** The agent session context has zero read permission on the key directory. | The owner's own Windows user, which agent sessions run as, is NOT in the ACL. |
| D3 | **`open_pr.py` runs only as `fondos_ci_runner`, through a scheduled task, from a read-only deployed copy.** The agent session never holds the key, the JWT or the token, and cannot change the code the runner executes. | The agent can only ask; the runner decides under fixed rules (sections 5 and 6). |
| D4 | **The request that crosses from the agent to the runner is untrusted input and is validated against a locked schema before it is used.** | No injection or path-traversal vector (section 5). |
| D5 | **The database ticket log is written only after the runner has produced a success receipt that GitHub itself confirms.** | The system of record never records an action that did not happen (section 6). |
| D6 | **Every read attempt on the key file leaves a Windows Security event, and those events are forwarded to an external immutable store.** | Local logs alone can be wiped by an attacker with SYSTEM (section 7). |
| D7 | **No credential is ever cached or stored by the Windows Credential Manager.** The installation token is generated in memory by `open_pr.py` at each run and discarded as soon as the API call completes (owner, 2026-10-30; supersedes the earlier optional token cache). | No state to manage, no lingering credential, no cache invalidation: the token exchange is one API call per run (section 8). |
| D8 | **The runner validates the permissions and repositories of the token GitHub issued before it opens the inbox** (section 5, startup check). | A mis-configured App fails at initialization with a distinct code, never mid-request. |

## 3. The App
- Name `fondos-pr-opener`, installed on the single repository `jacormenzana/fondos` only (never "all repositories", never an organisation scope).
- Repository permissions: **Pull requests: read and write**, **Checks: read**, **Metadata: read**. Nothing else. In particular never *Contents: write* (merging and direct branch writes), *Administration*, *Actions*, *Workflows* or *Secrets*.
- Webhooks off, no callback URL, no user-to-server authorization: a server-to-server App.
- The installation token is requested **down-scoped** at every use (`POST /app/installations/{id}/access_tokens` with `permissions` = pull requests write, checks read).
- Merging stays with the owner. An identity that could open and merge with green checks would remove the only human decision on `master`.

## 4. The service account, the key and the code the runner executes (mandatory)
**Account.** A local account `fondos_ci_runner`: not an administrator, no group beyond the default, password set by the owner and held only by the scheduled task, **denied interactive and Remote Desktop logon** (*Deny log on locally*, *Deny log on through Remote Desktop Services*), granted only *Log on as a batch job*. It has no database credential, no `.env`, no access to the owner's profile.

**Key file.** `C:\ProgramData\fondos\ci_runner\github_app_keys\fondos-pr-opener.pem`, exactly as GitHub generates it (one unmodified PEM, RSA 2048), outside the owner's profile. Its path is configured only in the scheduled task's environment (`FONDOS_GITHUB_APP_KEY_FILE`). The App id and installation id are not secret and live in `config`.

**ACL**, set once in an elevated terminal (cmd.exe) and verified:
```
mkdir "C:\ProgramData\fondos\ci_runner\github_app_keys"
icacls "C:\ProgramData\fondos\ci_runner\github_app_keys" /inheritance:r /grant:r "%COMPUTERNAME%\fondos_ci_runner:(OI)(CI)(R)" "NT AUTHORITY\SYSTEM:(OI)(CI)(F)" "BUILTIN\Administrators:(OI)(CI)(F)"
icacls "C:\ProgramData\fondos\ci_runner\github_app_keys\fondos-pr-opener.pem"
```
Exactly those three principals; **the owner's user is not listed**. `BUILTIN\Administrators` stays for rotation and recovery: UAC strips that group from a non-elevated token, so a non-elevated agent session has zero access even if the owner's user is an administrator. **Elevation is technically gated**: UAC is fixed at *Prompt for credentials on the secure desktop* (`EnableLUA` = 1, `ConsentPromptBehaviorAdmin` = 1, `PromptOnSecureDesktop` = 1). An agent process cannot obtain an elevated token, because that needs an administrator password typed on the secure desktop, which no process can reach or answer; acceptance 2 asserts the three values. Every elevated read is an audited event (section 7). `open_pr.py` checks the ACL before it reads the key and exits with a distinct code if anything else can read it, or if the file is missing, empty, not a single PEM or not an RSA private key. `open_pr.py --check-key` runs only that check and prints no key material. The key is never in `.env`, never in the repository (`*.pem` and `github_app_keys/` in `.gitignore`; a static test fails the build if any tracked file contains a private-key header), never in an environment variable, never printed or logged.

**The code the runner executes is not in the owner's checkout.** If the task ran `scripts\ops\open_pr.py` from the working tree, an agent session could edit the script and run arbitrary code as the runner. So:
- The task runs a **deployed, read-only copy** in `C:\ProgramData\fondos\ci_runner\app\` (ACL: `fondos_ci_runner` read and execute, `Administrators` full, the owner's user read only), produced from a reviewed commit by the owner or DevOps with a documented deploy step that also writes a `MANIFEST.sha256` (hash of every file plus the commit SHA).
- The task uses a **dedicated virtual environment** in `C:\ProgramData\fondos\ci_runner\venv\` under the same ACL, installed from pinned requirements with hashes. The only third-party package is `cryptography` (RS256 signing and key loading); strict JSON and the locked-schema validation are the standard-library module `open_pr_schema.py`, and HTTPS is `urllib`, so `PyJWT`, `jsonschema` and `requests` (and their dependency trees) are not installed at all: the agent cannot alter site-packages, and the runner has fewer files to trust.
- At start the runner verifies the manifest (every file hash and the schema hash) and exits with a distinct code if anything differs.
- `open_pr.py` makes **no process calls at all** (no `subprocess`, `os.system`, `eval`, `exec`, `pickle`, `shell=True`, `yaml.load`); a static test fails the build if any appears. It reads Git facts from the GitHub API (branch exists, commits ahead), never by running `git`.

## 5. The inbox request is untrusted input (D4)
The agent never runs `open_pr.py`. `scripts/ops/open_pr_request.py` (agent side, no secret) writes a request file and starts the task with `schtasks /Run /TN fondos_open_pr`; the task runs `open_pr.py --process-inbox`.

**Startup check (D8), before the inbox is opened.** Every run begins with: the manifest check (section 4), the key-file ACL check, a JWT, and the installation token (requested down-scoped, section 3). The runner then validates what GitHub actually issued, and exits with a distinct code and a line in `alerts\security.log` BEFORE it opens a single request file if anything differs:
- the `permissions` object of the token response (`POST /app/installations/{id}/access_tokens`) is exactly `{"pull_requests": "write", "checks": "read", "metadata": "read"}`: no extra key, no other level (the permissions of an installation are reported there and by `GET /app/installations/{id}`; `GET /installation/repositories` does not carry them);
- `repository_selection` is `selected`, and the `repositories` of the response are exactly `jacormenzana/fondos`;
- `GET /installation/repositories` with the issued token returns `total_count` 1 and `full_name` `jacormenzana/fondos`.
A mistake in the GitHub UI (a permission added, the App installed on all repositories) therefore stops the runner at initialization, not in the middle of a request.

**Directories.** Inbox `C:\ProgramData\fondos\ci_runner\inbox\`: the owner's user *Modify*, the runner *Modify*. Outbox `...\outbox\`: the runner *Modify*, the owner's user *Read* ONLY (the agent cannot forge a receipt). Quarantine `...\quarantine\` and alerts `...\alerts\`: the runner *Modify*, the owner's user *Read*.

**The runner never trusts the file.** In this order, any failure being a HARD FAIL (below):
1. Open it as bytes, refuse a size over 64 KB or a name that is not `<uuid4>.json`; read nothing else from the file system on behalf of the request (the body is text inside the JSON; no path from the request is ever opened).
2. Decode as strict UTF-8, no BOM; `json.loads` with a hook that rejects duplicate keys, `NaN` and `Infinity`.
3. Validate against the **locked JSON Schema** `scripts/ops/open_pr_request.schema.json` (Draft 2020-12) with `additionalProperties: false` at every level, all fields required and typed:
   - `v`: the integer `1`;
   - `id`: a UUID v4, equal to the file name, and not seen before (the runner keeps the processed ids);
   - `branch`: `^(?!-)(?!.*\.\.)(?!.*//)(?!.*\.lock$)(?!.*@\{)[A-Za-z0-9._/-]{1,100}$` (a git-safe name; no shell metacharacter can match);
   - `ticket`: `^FND-[0-9]{4}$`;
   - `title`: `^[A-Za-z0-9 .,:()/_#+'-]{1,200}$` (no backtick, `$`, `;`, `&`, `|`, `<`, `>`, `\`, `"`, `!`, `*`, `?`, braces, brackets, `~`, newline);
   - `body`: a string of at most 20,000 characters without control characters other than newline and tab. The body is Markdown and necessarily contains backticks and similar characters, so a metacharacter filter on it would be both useless and harmful; the control is structural instead: the body is only ever the value of a JSON field in an HTTPS request to GitHub, never reaches a shell, a file path, an `eval` or a log line, and the runner has no process-call code at all (section 4).
4. Check the facts through the GitHub API: the branch exists on origin and is not `master`, it has commits ahead of `master`, the ticket id is well formed, `FONDOS_OPEN_PR_ENABLED` is on in the task's environment (kill-switch registry of `NORMAS_IMPLEMENTACION.md`; default off).
5. Only then read the key, build a 10-minute RS256 JWT, exchange it for a down-scoped installation token, open the pull request requesting a review from the owner, and write the receipt (section 6).

**HARD FAIL.** An unexpected key, a wrong type, a malformed branch, a forbidden character in `branch`, `ticket` or `title`, a duplicate key, an oversized or non-UTF-8 file, a repeated id, or a file name that does not match its id: the runner (a) moves the file to quarantine without reading it further, (b) writes a `REJECTED_SECURITY` result with the rule that failed, (c) appends a **critical** line to `alerts\security.log`, (d) exits with a distinct code and **processes nothing more from the inbox** until the owner clears quarantine. The pre-flight prints the number of lines of `alerts\security.log` and of the files in quarantine; non-zero is a CAUTION. An ordinary refusal of a well-formed request (for example, no commits ahead) is not a security event: it is a `REFUSED` result with the reason.

## 6. The ticket log is transactional with the pull request (D5)
The runner holds no database credential, so the agent side writes the ticket log, but **only after a success receipt**, and a receipt is not trusted either.
- **Receipt** `outbox\<id>.result.json` (the owner's user cannot write there), strictly formatted, schema-validated by the agent side with `additionalProperties: false`: `{"v":1, "request_id":<uuid>, "status":"PR_OPENED", "pr_number":<int>, "pr_url":"https://github.com/jacormenzana/fondos/pull/<number>", "head_sha":"<40 hex>", "base":"master", "opened_at":"<iso utc>"}`. Any other status (`REFUSED`, `REJECTED_SECURITY`, `FAILED`) carries no URL and **never produces a database log**.
- **GitHub confirms it.** Before writing the log the agent side calls the public GitHub API: `GET /repos/jacormenzana/fondos/pulls/<number>` must return a pull request that is open, whose `head.sha` equals the receipt's, whose base is `master`, whose author is the bot of the App (`fondos-pr-opener[bot]`) and whose URL equals the receipt's. The source of truth is GitHub, not a file. If the API is unreachable the log waits (retry); it is never written on trust.
- **Exactly once.** The log text contains the request id; before appending, the agent side checks that no log of that ticket already contains it; after appending it writes `<id>.logged` in its own state directory. `open_pr_request.py --reconcile` finds receipts with no `.logged` marker (a crash between the pull request and the log) and appends them idempotently. The unsafe direction (a log with no pull request) cannot happen; the safe one (a pull request with a late log) is repaired by `--reconcile`.
- **Failure is not logged as an action.** A timeout or a refusal leaves the result file and a message to the operator; the ticket log is untouched.

## 7. Key access telemetry, forwarded off the machine (D6, mandatory)
**Local part.**
- `auditpol /set /subcategory:"File System" /success:enable /failure:enable` (elevated, once) and a SACL on the key file and its directory: an audit rule for `Everyone`, rights `ReadData, WriteData, Delete, ChangePermissions, TakeOwnership`, flags `Success, Failure` (`Get-Acl -Audit` / `Set-Acl` with `System.Security.AccessControl.FileSystemAuditRule`). Events in the Security log: **4656** (a handle was requested; the failure flag marks denied attempts), **4663** (an access was made), **4660** (deleted), **4670** (permissions changed).
- The Security log is sized to at least 256 MB. An event-triggered scheduled task (XPath filter on 4656 and 4663 whose `ObjectName` is the key file and whose `SubjectUserName` is not `fondos_ci_runner`) appends one line to `alerts\key_access.log`; the pre-flight shows its line count (non-zero is a CAUTION until the owner acknowledges it by moving the file into `deployments/`).

**External part (a local log alone is insufficient: an attacker with SYSTEM clears it with `wevtutil cl Security`).**
- The events **4656, 4663, 4660 and 4670 for the key file, plus 1102 (the audit log was cleared) and 4719 (the audit policy was changed)**, are forwarded to a store on **another machine** that the machine's own administrators cannot rewrite: a syslog or SIEM sink with append-only / object-lock (WORM) storage and a retention of at least one year.
- Transport: TLS with mutual authentication (RFC 5425 syslog-over-TLS or the SIEM agent's equivalent), by a forwarder service running as its own account (not the runner, not the owner), reading the Security log only through a filter limited to the event ids above.
- Latency: an event is at the sink within 60 seconds. A heartbeat from the forwarder every minute; a silent forwarder for more than 5 minutes raises an alert at the sink (an attacker stopping the forwarder is itself detected).
- The product is DevOps' choice (NXLog Community Edition, Winlogbeat or Windows Event Forwarding to a collector that relays by syslog), under the constraints above; the sink must not be a service on the same machine or on the same WSL2 distribution.

## 8. The installation token is ephemeral (D7)
**Token caching is banned.** The Windows Credential Manager is not used at all, for the key, the JWT or the token.
- `open_pr.py` builds the 10-minute JWT, exchanges it for the down-scoped installation token (section 3) and keeps both **in process memory only**, in local variables.
- The token is used for the API calls of the one request being processed and dropped (the reference is deleted and the HTTP session closed) as soon as the last call returns, on success and on every error path (`try`/`finally`). It is never written to disk, an environment variable, a result file, a log line or any store, and it is not reused by the next request or the next run.
- Cost: one extra `POST /app/installations/{id}/access_tokens` per run. A run opens a pull request, so the volume is a handful a day, far below any GitHub limit; there is no case for a cache.
- Static test (acceptance 12): `open_pr.py` and its deployed copy contain no reference to the Credential Manager (`cmdkey`, `CredWrite`, `CredRead`, `keyring`, `win32cred`) and no write of a token or JWT to any file.

## 9. Controls
| Control | Rule |
|---|---|
| Rotation | the installation token lasts one hour; the private key is rotated by DevOps every 180 days (generate a second key in GitHub, copy it in under the same ACL, delete the first key in GitHub, delete the old file), recorded in `deployments/` |
| Revocation | one action: uninstall the App or delete its key in GitHub; or disable the scheduled task; the runner then opens nothing |
| Audit | every pull request carries the footer line used in this repository and the ticket id; the App appears in the GitHub audit log; the key file's events are in the Security log AND at the external sink; every success appends one ticket log |
| Blast radius | one repository, pull requests and check reads only: a leaked key can open spam pull requests and cannot merge, push to `master` or touch settings. This limits damage; sections 4 to 7 are what protect the key |
| Separation | the branch-protection credential used by `github_protect_master.py` is a different, owner-held, short-lived credential and is never reused |

## 10. Defaults
1. The runner requests a review from the owner on every pull request it opens.
2. The pull-request-only regime starts when acceptance criterion 15 passes, not on a calendar date; from then on the bridgehead pattern is retired and any new bridgehead needs a written owner exception in `deployments/`.
3. The first real pull request is a docs-only change.

## 11. Provisioning steps (owner and DevOps, in order)
1. Create the App `fondos-pr-opener` (section 3) with the exact form of section 13; install it on `jacormenzana/fondos` only; generate one private key (RSA 2048); note the App id and the installation id.
2. Create the account `fondos_ci_runner` with the logon restrictions of section 4.
3. Create the key directory with its ACL, copy the PEM in, delete the downloaded copy, run `open_pr.py --check-key` as the runner.
4. Deploy the read-only app copy and the virtual environment (section 4) from a reviewed commit; check the manifest.
5. Create the inbox, outbox, quarantine and alerts directories, the scheduled tasks `fondos_open_pr` and the alert task, with the ACLs of section 5; turn on the audit policy and the SACL; install the log forwarder and prove the external path.
6. Turn on `FONDOS_OPEN_PR_ENABLED` in the task's environment only.
7. Acceptance (below).

## 12. Acceptance
1. With the key file absent the runner writes a distinct failure and opens nothing.
2. **The agent context cannot read the key**: from a NON-ELEVATED process of the owner's user, reading the key file and listing its directory fail with "Access is denied" and `icacls` does not list the owner's user. `open_pr_request.py --check-isolation` makes that attempt, reads the three UAC values above, and reports PASS only on "denied" with all three values set.
3. **The agent context cannot change the runner's code**: writing to `app\` or `venv\` from the owner's user fails; altering a deployed file makes the runner exit at the manifest check.
4. Telemetry: the denied attempt produces a 4656 failure event; a read by the runner produces a 4663 success event; a permission change produces 4670; the alert task writes a line for the denied attempt and none for the runner.
5. **External forwarding**: the same events reach the external sink within 60 seconds; on a test machine, `wevtutil cl Security` produces event 1102 at the sink; stopping the forwarder raises the heartbeat alert. **WAIVED by the owner on 2026-10-30, conditionally: this check is NOT passed and is never reported as passed.** Interim telemetry is the local Security log of the machine; the collector, its certificates and the immutable store are deferred to a post-deployment DevOps sprint (no date set); `provision_log_sink.ps1` and `LOG_SINK_RUNBOOK.md` are ready and unexecuted. Consequences stated: a holder of SYSTEM can clear or stop the local log with no remote copy; the 256 MB Security log size of section 7 is set by the owner's own command in Step 4a of `PROVISIONING_RUNBOOK.md` (`wevtutil sl Security /ms:268435456`), not by a script, and is reported back with `wevtutil gl Security`; the local alert task of section 7 is now critical-path work (owner, 2026-10-30) and is not built yet. Recorded in FND-0353 (outbox note 01).
6. **Schema (D4)**: a table-driven test sends each of these as a request file and expects a HARD FAIL (quarantine, `REJECTED_SECURITY`, a critical line, exit code, inbox processing stopped): an extra key at the top level and in a nested position, a missing key, a wrong type, a duplicate key, a branch with `..`, `//`, a leading `-`, `@{`, `.lock`, a space, `;`, `$(`, a backtick, a newline or a Unicode look-alike, a title with `;`, `|`, `&`, a backtick or `$`, an oversized file, a file name that is not its id, a repeated id, invalid UTF-8, a BOM. A well-formed request passes every step.
7. With a key file readable by any extra principal the runner refuses with a distinct code and names the principal; with the restricted ACL it proceeds.
8. With a key whose App lacks *Pull requests: write* the runner fails at the first write and names the missing permission.
9. Each ordinary rule of section 5 step 4 has a test: `master` as head, no commits ahead, a branch not on origin, the kill switch off.
10. **Receipts (D5)**: with a fake runner, the agent side writes NO database log for `REFUSED`, `REJECTED_SECURITY`, `FAILED`, a timeout, a malformed receipt, or a receipt whose pull request GitHub does not confirm (wrong `head.sha`, closed, other author, other URL); it writes exactly one for a confirmed `PR_OPENED`; a second run does not duplicate it; `--reconcile` writes the missing log after a simulated crash.
11. A fake-API test shows that the key, the JWT and the token appear in no request body, no log, no result file and no output.
12. The static tests pass: only `open_pr.py` reads the key file (`open_pr_request.py` names the path in exactly one place, the `--check-isolation` probe that must be denied, and holds no key-loading code); no tracked file contains a private-key header; `.gitignore` covers `*.pem`; `open_pr.py` contains no process-call or dynamic-execution construct and no Credential Manager or token-cache construct (section 8).
13. The schema file and the manifest hashes are pinned: changing the schema without redeploying makes the runner exit.
14. **Startup check (D8)**: against a fake API, each of these makes the runner exit with its distinct code and an alert line before the inbox is opened (a request file placed in the inbox stays unread): an extra permission (`contents: write`), a missing one, `pull_requests` at `read`, an extra key, `repository_selection` = `all`, a second repository, another repository, `total_count` 2 from `/installation/repositories`. The exact permission set and one repository pass.
15. One real, docs-only pull request is opened through the inbox by an agent session under the owner's supervision and merged by the owner: it shows the App's identity, the agent session never held the key, the ticket log appears only after GitHub confirms the pull request, and the owner's merge is the only manual step.

## 13. Creating the App in the GitHub UI, field by field (provisioning step 1)
Prerequisites. Signed in to GitHub as `jacormenzana` (the owner of the repository) with two-factor authentication on; the repository `jacormenzana/fondos` exists and `master` carries the protection of `AGENTS.md`. Nothing else is needed: no organisation, no webhook endpoint, no server.

1. Open *Settings* (profile menu) > *Developer settings* > *GitHub Apps* > *New GitHub App*.
2. **GitHub App name:** `fondos-pr-opener`. The name is global; if GitHub reports it taken, stop and choose `fondos-pr-opener-jacormenzana`, then replace the bot login in section 6 (`fondos-pr-opener[bot]`) with the login GitHub shows for the new name before anything is deployed.
3. **Description:** `Opens pull requests for jacormenzana/fondos. Cannot merge.`
4. **Homepage URL:** `https://github.com/jacormenzana/fondos` (required by the form, not used).
5. *Identifying and authorizing users*: **Callback URL** empty; **Expire user authorization tokens** unchecked; **Request user authorization (OAuth) during installation** unchecked; **Enable Device Flow** unchecked.
6. *Post installation*: **Setup URL** empty; **Redirect on update** unchecked.
7. *Webhook*: **Active** unchecked (no URL, no secret).
8. *Permissions*, **Repository permissions**: set exactly these three and leave every other row at *No access*:
   | Row | Level |
   |---|---|
   | Pull requests | Read and write |
   | Checks | Read-only |
   | Metadata | Read-only (preselected, cannot be removed) |
9. *Permissions*, **Account permissions** and **Organization permissions**: every row *No access*.
10. *Subscribe to events*: nothing is available without a webhook; leave all unchecked.
11. **Where can this GitHub App be installed?** *Only on this account*.
12. Click *Create GitHub App* (GitHub asks for the password or a passkey). On the *About* page note the **App ID** (a number), which is not a secret.
13. *Private keys* > *Generate a private key*: the browser downloads one `.pem`. Generate it exactly once; two keys are never active together except during a rotation. Save it **into `C:\ProgramData\fondos\key_staging`** (browser: *Settings* > *Downloads* > *Ask where to save each file before downloading* ON), never into Downloads (which can be synced or indexed). `provision_ci_runner.ps1 -Stage` creates that folder with an audit rule first; the main run reads the Security audit trail of the file and refuses the key if any process other than the browser, Windows Defender or the script itself touched it, then overwrites and deletes it. Do not open it, paste it or upload it anywhere.
14. Left sidebar > *Install App* > *Install* next to `jacormenzana` > *Only select repositories* > `jacormenzana/fondos` > *Install*. Never choose *All repositories*.
15. The address after installing is `https://github.com/settings/installations/<number>`: that number is the **installation id** (not a secret). Record App id, installation id and the bot login in `config`, never the key.
16. Verification. *Settings* > *Applications* > *Installed GitHub Apps* > *Configure*: *Repository access* lists only `jacormenzana/fondos`, and *Permissions* lists exactly `Pull requests: Read and write`, `Checks: Read-only`, `Metadata: Read-only`. Any additional line means the form was filled wrongly: delete the App and start again from step 1. Record the check in `deployments/` with the date.
