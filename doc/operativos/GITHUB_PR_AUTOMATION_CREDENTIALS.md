# GitHub credentials for autonomous pull-request creation

**Purpose.** An agent session can build a branch and push it, but it cannot open the pull request, so a person has to click "Create pull request" for every change. This note states what is missing and the least privilege that removes that step. The provisioning design is fixed in `CICD_SERVICE_ACCOUNT_SPEC.md` (a GitHub App run by an isolated service account); this note only records the facts and the permission set it implements. It changes nothing by itself.

**Written for:** DevOps / the repository owner. Facts verified 2026-10-28; provisioning path fixed 2026-10-29 and 2026-10-30.

## 1. What works today and what does not

| Step | Today | Why |
|---|---|---|
| Build a branch from `origin/master` and push it | works | the git credential already configured on the machine can push branches |
| Read pull requests and check results | works, limited | the repository is public: unauthenticated API reads work, capped at **60 requests per hour** per IP |
| **Open a pull request** | **does not work** | `POST /repos/jacormenzana/fondos/pulls` needs an authenticated identity; `gh` is not installed and there is no API token in the agent environment (the tokens of 2026-10-26 were deleted, one had been exposed in a pasted terminal output) |
| Merge a pull request | the owner | `master` is protected (pull request required, four required checks, administrators included); the owner decides each merge |
| Apply database changes | the owner | DDL is applied only by the owner, through `OWNER_DSN_VAULT.md` |

## 2. The permission set (single repository `jacormenzana/fondos`)

| Permission (repository) | Level | Why |
|---|---|---|
| **Pull requests** | **Read and write** | `POST /repos/{owner}/{repo}/pulls` (create) and `PATCH .../pulls/{n}` (edit title / body) |
| **Checks** | Read-only | read the four required check results with the authenticated rate limit (5,000 requests per hour) instead of 60 |
| Metadata | Read-only | granted automatically to every GitHub App |

Every other permission is *No access*. In particular the identity never holds:

- **Contents: write.** It is what merging a pull request needs (`PUT .../pulls/{n}/merge`), and it lets the identity write to branches directly. Merging stays with the owner.
- **Administration.** `scripts/ops/github_protect_master.py` uses it to read or change branch protection; that is a separate, owner-held credential and is never reused here.
- **Actions**, **Workflows**, **Secrets**, or any organisation-wide scope.

## 3. Credential kinds that are rejected
A machine user, a classic token and the owner's personal fine-grained token are rejected: they either hold more than the set above, make every pull request look like the owner's, or expire silently. The only credential is the GitHub App of `CICD_SERVICE_ACCOUNT_SPEC.md` (decision D1); its private key, handling and rotation are fixed in that document (sections 4, 7 and 9), and no secret is ever in `.env`, in the repository, in a log or in a chat.

## 4. How a provisioned App is checked without exposing a secret
The check is the App's own settings page (*Settings* > *Applications* > *Installed GitHub Apps* > *Configure*): repository access lists only `jacormenzana/fondos`, and the permissions are exactly the three rows of section 2 (`CICD_SERVICE_ACCOUNT_SPEC.md` section 13, step 16). A merge permission is never tested against a real pull request.

## 5. What it gives, and what stays manual
The agent asks, through the inbox of the specification, and the runner opens the pull request with the commit message body as description. **Manual by design:** reviewing, waiting for the four required checks, merging, deleting the branch, and every database change.
