# GitHub credentials for autonomous pull-request creation

**Purpose.** An agent session can build a branch and push it, but it cannot open the pull request, so a person has to click "Create pull request" for every change. This note
states exactly what is missing and the least privilege that would remove that step, so that DevOps can provision it. It changes nothing by itself.

**Written for:** DevOps / the repository owner. Facts verified 2026-10-28.

## 1. What works today and what does not

| Step | Today | Why |
|---|---|---|
| Build a branch from `origin/master` and push it | works | the git credential already configured on the machine can push branches |
| Read pull requests and check results | works, limited | the repository is public: unauthenticated API reads work, capped at **60 requests per hour** per IP |
| **Open a pull request** | **does not work** | `POST /repos/jacormenzana/fondos/pulls` needs an authenticated identity; `gh` is not installed and there is no API token in the agent environment (deliberately: the tokens of 2026-10-26 were deleted, one had been exposed in a pasted terminal output) |
| Merge a pull request | by design, the owner | `master` is protected (pull request required, four required checks, administrators included); the owner decides each merge |
| Apply database changes | by design, the owner | DDL is applied only by the owner |

## 2. The missing credential, with the least privilege

For **creating** (and updating) pull requests only, a **fine-grained** credential scoped to **the single repository `jacormenzana/fondos`** with:

| Permission (repository) | Level | Why |
|---|---|---|
| **Pull requests** | **Read and write** | `POST /repos/{owner}/{repo}/pulls` (create) and `PATCH .../pulls/{n}` (edit title / body) need *Pull requests: write* (GitHub, "Permissions required for fine-grained personal access tokens") |
| Metadata | Read | granted automatically to every fine-grained token |

Nothing else. In particular **do not grant**:

- **Contents: write.** It is what *merging* a pull request needs (`PUT .../pulls/{n}/merge`), and it also lets the identity write to branches directly. Merging stays with the owner.
- **Administration.** That is what `scripts/ops/github_protect_master.py` uses to read or change branch protection. It is a separate, owner-held, short-lived credential and must not be reused for this purpose.
- **Actions**, **Workflows**, **Secrets**, or any organisation-wide scope. A classic token with the `repo` scope is far too broad and should not be used.

Optional, only if higher read limits are wanted: *Checks: read* (or *Commit statuses: read*) lets the identity read the four required check results with the authenticated rate limit (5,000 requests per hour) instead of 60.

## 3. Ways to provision it (in order of preference)

1. **GitHub App** installed on `jacormenzana/fondos` only, repository permissions *Pull requests: Read and write* (+ *Metadata: Read*). Installation tokens last one hour, are auditable as the app, and need no personal seat. Best for a service.
2. **A machine user**: a dedicated account with *Write* access to the repository (needed to push the branch; `master` stays protected, so it cannot push there) holding a fine-grained token with the permissions above.
3. **The owner's own fine-grained token**, expiry 30 days or less. Simplest, but it makes every pull request look like the owner's and it dies silently.

## 4. Handling rules (same as the rest of the repository)

- The secret lives in a user-level secret store or an environment variable of a shell the owner starts for that purpose. **Never** in the repository, in `.env`, in a log, or in chat; never exported in a window that also launches unrelated agents.
- An agent uses a credential only if it was provisioned **for this purpose** and says so; it never reuses a credential found for another purpose, and it never prints one.
- Rotation: tokens expire (30 days at most for a personal one); the calling script must fail with a clear message, not fall back to anything.

## 5. How to check a provisioned credential without exposing it

Run this in a shell where the secret is in `GH_PR_TOKEN` (the value is never echoed):

```
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $GH_PR_TOKEN" "https://api.github.com/repos/jacormenzana/fondos/pulls?state=open"      # expect 200
```

A 200 proves the identity is authenticated and can read the repository's pull requests; a 401 means the token is invalid or expired, a 404 that it is not scoped to this repository. That it can **create but not merge** is checked on the token's own settings page (repository permissions: only *Pull requests: Read and write* and *Metadata: Read*), **not** by trying to merge: never test a merge permission against a real pull request.

## 6. What it would give, and what stays manual

With the credential: the agent opens the pull request itself, with the commit message body as description, and reports the link. **Still manual by design:** reviewing, waiting for the four required checks, merging, deleting the branch, and every database change.
