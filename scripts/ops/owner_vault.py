#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
owner_vault.py -- one-time setup of the owner vault (shared/owner_vault.py). Run it in YOUR terminal, once per string; after that the owner tools find their strings by themselves.

    python scripts/ops/owner_vault.py set FONDOS_PG_DSN_OWNER      # asks for the value with hidden input (a real console is required); stores it encrypted (Windows DPAPI)
    python scripts/ops/owner_vault.py list                         # the NAMES stored (never a value) and which tools receive each
    python scripts/ops/owner_vault.py check                        # every stored value decrypts for this user (never prints one)
    python scripts/ops/owner_vault.py remove FONDOS_PG_DSN_OWNER

The values are never printed, logged or passed on the command line. The file is %LOCALAPPDATA%\\fondos\\owner_vault.json, outside the repository. `set` refuses to run without a real console, so an
agent's shell cannot fill or change the vault. Honest limit: a guard, not a security boundary (NORMAS_BACKLOG 9.2).
Exit codes: 0 ok, 1 a check failed or the name is unknown, 2 usage or no console.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared import owner_vault as ov  # noqa: E402


def tools_for(name: str) -> list:
    """The scripts that receive `name`. Pure."""
    return sorted(t for t, names in ov.OWNER_TOOLS.items() if name in names)


def main(argv: list | None = None, *, vault: "ov.Vault | None" = None, console: bool | None = None, ask=getpass.getpass) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("set", "remove"):
        sub.add_parser(cmd).add_argument("name", choices=ov.VAULT_VARIABLES)
    sub.add_parser("list")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    v = vault or ov.Vault()
    if console is None:
        from shared.backlog_client import _is_console
        console = _is_console(sys.stdin)
    try:
        if args.cmd == "set":
            if not console:
                print("REFUSED: the vault is filled only by a person at a real console (hidden input). Nothing was stored.", file=sys.stderr)
                return 2
            value = ask(f"value of {args.name} (hidden): ").strip()
            again = ask("again (hidden): ").strip()
            if not value or value != again:
                print("REFUSED: the two entries are empty or differ. Nothing was stored.", file=sys.stderr)
                return 2
            v.set(args.name, value)
            print(f"{args.name} stored for this Windows user. Tools that receive it: {', '.join(tools_for(args.name)) or '(none)'}")
            return 0
        if args.cmd == "remove":
            print(f"{args.name}: {'removed' if v.remove(args.name) else 'was not stored'}")
            return 0
        names = v.names()
        if args.cmd == "list":
            for n in ov.VAULT_VARIABLES:
                print(f"  {n:36} {'stored' if n in names else 'not stored'}   -> {len(tools_for(n))} tool(s)")
            return 0
        bad = [n for n in names if _fails(v, n)]                                  # check
        for n in names:
            print(f"  {n:36} {'CANNOT be decrypted by this user' if n in bad else 'ok'}")
        return 1 if bad else 0
    except ov.VaultError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _fails(vault, name: str) -> bool:
    try:
        return not vault.get(name)
    except ov.VaultError:
        return True


if __name__ == "__main__":
    sys.exit(main())
