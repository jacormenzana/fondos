#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
prune_merged_branches.py -- deletes the branches whose work is already part of master (Backlog v3.1 housekeeping, 2026-11-01).

    python scripts/ops/prune_merged_branches.py                        # DRY RUN: lists what the rule would delete from the remote; deletes nothing
    python scripts/ops/prune_merged_branches.py --apply                # deletes them (git push origin --delete)
    python scripts/ops/prune_merged_branches.py --scope local --apply  # the same for this clone's own branches, never the checked-out one (git branch -D, only on branches this tool found in origin/master)

Why. GitHub's "Automatically delete head branches" is on, yet the branches of the pull requests that the auto-PR workflow opens and the auto-merge lands stay: the PR history shows `auto_merge_enabled`,
`merged`, `closed` and no `head_ref_deleted` event. This tool deletes them from a workflow (.github/workflows/branch-cleanup.yml, on every push to master, daily, and by hand).

Two rules, both of which require the branch tip to be CONTAINED IN master, so nothing can be lost (the commits are in master and a branch is recreated from its SHA):
  --rule pr        (the workflow's) the branch also has a MERGED pull request whose last commit is exactly the tip: this very state of the branch was merged. A branch pushed again after
                   its merge, or one that never had a pull request, is left alone. Needs the `gh` CLI and GH_TOKEN.
  --rule ancestor  (a person's one-off clean-up) the tip being in master is enough; --keep protects names, --prefix widens the prefixes (default feat/ fix/ chore/ docs/).
Never deleted, whatever the rule: master, main, a branch with an OPEN pull request, a name outside the prefixes. If `gh` fails the `pr` rule deletes nothing (exit 3).
Exit codes: 0 ok (also when there is nothing to delete), 3 git or gh failed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ("feat/", "fix/", "chore/", "docs/")
NEVER = ("master", "main", "HEAD")
CHUNK = 20
RC_OK, RC_ENV = 0, 3


# ── pure ────────────────────────────────────────────────────────────────────────

def select_branches(tips: dict, *, rule: str, merged: set, merged_pr_tips: dict, open_heads: set, prefixes: tuple = PREFIXES, keep: tuple = ()) -> list:
    """[(branch, reason)] sorted by name: the branches to delete. `tips` = {branch: tip sha}; `merged` = the branches whose tip is contained in master; `merged_pr_tips` = {branch: {last commit
    shas of its merged pull requests}}; `open_heads` = branches with an open pull request. A branch is selected only when its tip is in master AND (rule pr) one of its merged pull requests ended on
    exactly that tip. Pure."""
    if rule not in ("pr", "ancestor"):
        raise ValueError(f"rule must be pr or ancestor, got {rule!r}")
    out = []
    for name, tip in sorted(tips.items()):
        if name in NEVER or name in keep or name in open_heads or not name.startswith(tuple(prefixes)):
            continue
        if name not in merged:
            continue
        if rule == "pr":
            if tip not in merged_pr_tips.get(name, ()):
                continue
            out.append((name, "a merged pull request ended on this very commit"))
        else:
            out.append((name, "its tip is contained in master"))
    return out


def chunks(items: list, size: int = CHUNK) -> list:
    """`items` in lists of at most `size`. Pure."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def parse_tips(text: str) -> dict:
    """{branch: sha} from `git for-each-ref refs/remotes/origin --format='%(refname:strip=3) %(objectname)'`. Pure."""
    out = {}
    for line in text.splitlines():
        name, _, sha = line.strip().rpartition(" ")
        if name and sha and name != "HEAD":
            out[name] = sha
    return out


def parse_pr_lists(open_json: str, merged_json: str) -> tuple:
    """(names in use by an open PR, {head name: {last commit shas of its merged PRs}}) from two `gh pr list --json` outputs. A name is in use as the HEAD of an open pull request or as its BASE (deleting a
    base would close the pull requests stacked on it). Pure."""
    open_heads = set()
    for p in json.loads(open_json or "[]"):
        open_heads |= {p["headRefName"], p.get("baseRefName") or p["headRefName"]}
    merged: dict = {}
    for p in json.loads(merged_json or "[]"):
        merged.setdefault(p["headRefName"], set()).add(p["headRefOid"])
    return open_heads, merged


# ── I/O ────────────────────────────────────────────────────────────────────────

def _run(cmd: list, cwd: Path) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def main(argv: list | None = None, cwd: Path | None = None, run=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="delete (default: only list)")
    ap.add_argument("--rule", choices=("pr", "ancestor"), default="pr")
    ap.add_argument("--scope", choices=("remote", "local"), default="remote")
    ap.add_argument("--prefix", action="append", default=[], help="an extra branch-name prefix to prune (repeatable)")
    ap.add_argument("--keep", action="append", default=[], help="a branch name that is never deleted (repeatable)")
    args = ap.parse_args(argv)
    where = cwd or ROOT
    do = run or (lambda cmd: _run(cmd, where))
    prefixes = tuple(PREFIXES) + tuple(args.prefix)
    try:
        do(["git", "fetch", "--prune", "-q", "origin"])
        if args.scope == "local":
            current = do(["git", "branch", "--show-current"]).strip()
            names = [n.strip() for n in do(["git", "branch", "--merged", "origin/master", "--format=%(refname:short)"]).splitlines() if n.strip()]
            doomed = [n for n in names if n not in NEVER and n != current and n not in args.keep and n.startswith(prefixes)]
            reason = {n: "its tip is contained in origin/master" for n in doomed}
        else:
            tips = parse_tips(do(["git", "for-each-ref", "refs/remotes/origin", "--format=%(refname:strip=3) %(objectname)"]))
            merged = {n.strip() for n in do(["git", "for-each-ref", "--merged", "origin/master", "refs/remotes/origin", "--format=%(refname:strip=3)"]).splitlines() if n.strip()}
            open_heads, merged_prs = set(), {}
            if args.rule == "pr":
                open_heads, merged_prs = parse_pr_lists(do(["gh", "pr", "list", "--state", "open", "--json", "headRefName,baseRefName", "--limit", "1000"]),
                                                        do(["gh", "pr", "list", "--state", "merged", "--json", "headRefName,headRefOid", "--limit", "1000"]))
            picked = select_branches(tips, rule=args.rule, merged=merged, merged_pr_tips=merged_prs, open_heads=open_heads, prefixes=prefixes, keep=tuple(args.keep))
            doomed = [n for n, _why in picked]
            reason = dict(picked)
        print(f"{args.scope} branches the {args.rule} rule selects: {len(doomed)}" + ("" if args.apply else "  (DRY RUN: nothing deleted; --apply deletes them)"))
        for n in doomed:
            print(f"  {n}  -- {reason[n]}")
        if args.apply:
            for group in chunks(doomed):
                if args.scope == "local":
                    do(["git", "branch", "-D", *group])      # -D on purpose: `-d` judges "merged" against HEAD or the upstream, not against origin/master, and refused branches that ARE in master
                else:
                    do(["git", "push", "origin", "--delete", *group])
            print(f"deleted {len(doomed)}")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_ENV
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
