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


def dsn_problem(name: str, value: str):
    """Why `value` cannot be the connection string `name` stands for, or None when it parses (or `name` is not a connection string). The reason never contains the value. A wrong shape used to be
    stored without complaint and surface later, inside a migration, as `missing "=" after ...`. Pure."""
    if "DSN" not in name:
        return None
    if value[:1] in ('"', "'") or value[-1:] in ('"', "'"):
        return "it is wrapped in quotes: store the bare value, without quotes"
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "it contains a control character (a paste with Ctrl+V can insert one in the hidden prompt: paste with a right-click, or type the value)"
    if value.upper().startswith(name.upper() + "="):
        return f"it starts with '{name}=': store only the value after the equals sign"
    try:
        from psycopg.conninfo import conninfo_to_dict
        parts = conninfo_to_dict(value)
    except Exception:  # noqa: BLE001
        return ("it is not a PostgreSQL connection string: use postgresql://USER:PASSWORD@HOST:PORT/DBNAME (a password containing @ : / # ? or % must be percent-encoded) "
                "or key=value pairs")
    if not (parts.get("host") or parts.get("hostaddr")):
        return "it has no host"
    if not parts.get("user"):
        return "it has no user"
    port = str(parts.get("port") or "")
    if port and not all(p.isdigit() for p in port.split(",")):
        return "the port is not a number (a ':' , '/' or '?' inside the password probably split the string in the wrong place: percent-encode it, or use the key=value form)"
    return None


def dsn_hints(value: str) -> list:
    """Facts about the SHAPE of a rejected connection string, enough to see what is wrong without showing any of it: lengths and counts, never characters of the value (the scheme is named only
    when it is a PostgreSQL one). Pure."""
    import re
    v = value
    out = [f"length {len(v)} characters"]
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", v)
    if m and m.group(1).lower() in ("postgresql", "postgres"):
        out.append(f"starts with {m.group(1).lower()}:// (good)")
        rest = v[m.end():]
        out.append(f"'@' signs: {v.count('@')} (a URI needs exactly one separating the credentials from the host; a password containing @ must be written %40)")
        if "@" in rest and "@" not in re.split(r"[/?]", rest, maxsplit=1)[0]:
            out.append("a '/' or '?' appears before the '@': it ends the credentials early (inside the password they must be written %2F and %3F)")
        hostpart = rest.rsplit("@", 1)[-1]
        authority = re.split(r"[/?]", hostpart, maxsplit=1)[0]
        port = authority.rsplit(":", 1)[-1] if ":" in authority else ""
        if port and not port.isdigit():
            out.append("the port after the host is not a number (a ':' , '/' or '?' inside the password probably split the string in the wrong place)")
        if "/" not in hostpart:
            out.append("no '/DBNAME' after the host")
        bad_pct = len(re.findall(r"%(?![0-9A-Fa-f]{2})", v))
        if bad_pct:
            out.append(f"'%' signs not followed by two hex digits: {bad_pct} (a '%' inside the password must be written %25)")
    else:
        out.append("does not start with postgresql:// or postgres:// (and is not a list of key=value pairs)")
    ws = sum(1 for c in v if c.isspace())
    ctl = sum(1 for c in v if ord(c) < 32 or ord(c) == 127)
    non_ascii = sum(1 for c in v if ord(c) > 126)
    if ws:
        out.append(f"whitespace characters: {ws}")
    if ctl:
        out.append(f"control characters: {ctl} (a paste with Ctrl+V can insert one in this prompt: use right-click, or type the value)")
    if non_ascii:
        out.append(f"non-ASCII characters: {non_ascii}")
    return out


def dsn_summary(value: str) -> str:
    """user@host:port/dbname of a valid connection string, without the password. Pure."""
    from psycopg.conninfo import conninfo_to_dict
    d = conninfo_to_dict(value)
    return f"{d.get('user')}@{d.get('host') or d.get('hostaddr')}:{d.get('port') or '5432'}/{d.get('dbname') or d.get('user')}" + ("" if d.get("password") else "  (NO password in it)")


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
            problem = dsn_problem(args.name, value)
            if problem:
                print(f"REFUSED: {args.name} is not a usable connection string: {problem}. Nothing was stored.", file=sys.stderr)
                if "DSN" in args.name:
                    print("What the entry looks like (structure only, no characters of it are shown):", file=sys.stderr)
                    for hint in dsn_hints(value):
                        print(f"  - {hint}", file=sys.stderr)
                    print("Easiest form that needs no encoding of the password: host=127.0.0.1 port=5436 dbname=fondos user=fondos_owner password=YOURPASSWORD", file=sys.stderr)
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
        malformed = []
        for n in names:
            if n in bad:
                print(f"  {n:36} CANNOT be decrypted by this user")
                continue
            problem = dsn_problem(n, v.get(n))
            if problem:
                malformed.append(n)
                print(f"  {n:36} WRONG SHAPE: {problem}  (store it again: owner_vault.py set {n})")
                for hint in dsn_hints(v.get(n)):
                    print(f"      - {hint}")
            else:
                print(f"  {n:36} ok" + (f"  -> {dsn_summary(v.get(n))}" if "DSN" in n else ""))
        return 1 if (bad or malformed) else 0
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
