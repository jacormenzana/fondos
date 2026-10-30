# Interface contract: the pull-request pipeline (FND-0353)

Written for: the engineers who build and review the pull-request runner. Specification-first (owner order, 2026-10-30): this contract and the tests were written before the code; `CICD_SERVICE_ACCOUNT_SPEC.md` is the source of every rule, the tests in `tests/test_open_pr_*.py` are its executable form, the modules satisfy them.

## 1. Files and who may write them
| Path (under `C:\ProgramData\fondos\ci_runner\`) | Writer | Reader | Content |
|---|---|---|---|
| `inbox\<uuid4>.json` | agent (`open_pr_request.py`, via `<uuid4>.json.part` then rename) | runner | request v1, `open_pr_request.schema.json` |
| `outbox\<uuid4>.result.json` | runner only | agent | receipt v1, `open_pr_receipt.schema.json` |
| `outbox\.processed_ids` | runner only | runner | one request id per line (repeated ids are a hard fail) |
| `quarantine\<name>` | runner only | owner | the request files that failed a hard rule; non-empty means the runner processes nothing |
| `alerts\security.log` | runner only | owner | `CRITICAL <utc> <detail>` per security event |
| `app\config.json` | deployer | runner | identifiers only: `app_id`, `installation_id`, `runner_sid`, optional `review_login` |

## 2. Modules
| Module | Side | Public contract |
|---|---|---|
| `open_pr_schema.py` | both | `load_strict_json(bytes)` (UTF-8, no BOM, no duplicate key, no NaN/Infinity; `StrictJsonError`); `validate(instance, schema) -> list[str]` for exactly the keywords the two schemas use, raising on any other keyword; `sha256_file` |
| `open_pr.py` | runner | `validate_request(raw, filename, seen_ids, schema)` raises `HardFail(rule)`; `load_app_key(path, read_acl, allowed)` raises `KeyProblem(code, message)` with the ACL checked before the content is read; `make_jwt(app_id, pem, now)`; `startup_check(token_response, installation_repos, repo)` raises `StartupMismatch(rule)`; `handle_request`; `process_inbox`; `run(cfg, api, read_acl, now, log) -> exit code`; `windows_read_acl(path)`; `load_config(root, env)`; `main` |
| `open_pr_request.py` | agent | `new_request`, `write_request(inbox, req, schema)` (refuses a request the runner would quarantine), `read_receipt`, `confirm_pull(receipt, fetch_pull)`, `log_if_confirmed(...) -> LOGGED / ALREADY_LOGGED / NOT_LOGGED: <why>`, `reconcile`, `check_isolation`; CLI `send`, `log`, `--reconcile`, `--check-isolation` |
| `api.request(method, path, *, bearer, body=None) -> (status, data)` | runner | the whole GitHub surface the runner uses; `UrllibApi` is the real one, tests use a fake |

## 3. Exit codes of the runner
`0` ok (refusals included) · `10` key (`KEY_MISSING`, `KEY_EMPTY`, `KEY_NOT_SINGLE_PEM`, `KEY_NOT_RSA`, `KEY_ACL`) · `11` manifest or pinned schema differs · `12` startup mismatch (D8) · `13` security hard fail or non-empty quarantine · `14` GitHub API failure. Every non-zero code except `14` also writes a `CRITICAL` line to `alerts\security.log`, with one exception: a run blocked only because quarantine is not empty (`13`) logs and exits without a new line, so a waiting scheduled task does not grow the alert log every cycle.

## 4. Order inside `run()`
1. manifest of the deployed tree and the SHA-256 of both schemas (acceptance 3, 13) → `11`
2. quarantine empty, else `13`
3. key: exists, ACL restricted (before reading), one RSA 2048 PEM → `10`
4. kill switch `FONDOS_OPEN_PR_ENABLED` off → every valid request is `REFUSED`, no token requested
5. JWT (10 minutes) → down-scoped installation token → `GET /installation/repositories` → `startup_check` (D8) → `12` **before the inbox is opened**
6. inbox, request by request: strict gate (`HardFail` → quarantine, `REJECTED_SECURITY`, alert, stop) → ordinary rules (`REFUSED`) → pull request (`PR_OPENED`, or `FAILED` naming the missing permission) → receipt → request file removed

## 5. Acceptance map (spec section 12)
| # | Check | Test | Environment |
|---|---|---|---|
| 1 | key absent → distinct failure, nothing opened | `test_open_pr_runner.py` | – |
| 2 | agent context cannot read the key | `test_open_pr_request.py::check_isolation` (classification), `assert_boundaries.ps1` B01–B02 | real machine |
| 3 | agent cannot change the runner's code; altered file makes the runner exit | runner test (manifest) + `assert_boundaries.ps1` B04 | real machine |
| 4 | telemetry (4656/4663/4670, alert task) | – | real machine, Security log |
| 5 | external forwarding, 1102, heartbeat | – | **not provisioned: needs the DevOps sink of spec section 7** |
| 6 | schema table | `test_open_pr_schema.py`, runner hard-fail tests | – |
| 7 | extra principal on the key → refuse and name it | runner test | – |
| 8 | App without Pull requests: write → fails at the first write, names it | runner test | – |
| 9 | each ordinary rule | runner tests | – |
| 10 | receipts / GitHub confirmation / exactly once / reconcile | `test_open_pr_request.py` | – |
| 11 | key, JWT, token appear nowhere | runner test | – |
| 12 | static tests | `test_open_pr_static.py` | – |
| 13 | pinned schema and manifest | schema + runner tests | – |
| 14 | startup check D8 | runner tests | – |
| 15 | one real docs-only pull request | – | real machine, owner supervises |
