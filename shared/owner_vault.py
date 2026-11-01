# -*- coding: utf-8 -*-
"""
owner_vault.py -- the owner-only connection strings and tokens, injected into an OWNER TOOL when (and only when) a person runs it at a real console.

Why. The strings that give DDL or owner-role writes (FONDOS_PG_DSN_OWNER, FONDOS_BACKLOG_PG_DSN_HUMAN, FONDOS_BACKLOG_PG_DSN_ADJUDICATOR, the GitHub tokens) must not sit in `.env`: every agent session
loads that file (owner order 2026-10-29). Typing and clearing them in each terminal is the other extreme (owner, 2026-11-01: "it must be transparent and automated"). This module is the middle
way: a one-time setup (`python scripts/ops/owner_vault.py set NAME`) stores each value encrypted with Windows DPAPI in %LOCALAPPDATA%\\fondos\\owner_vault.json (outside the repository). From then
on an owner tool started at a real console finds ITS variables in its own process environment, with no step for the operator, and they vanish when the process ends (nothing is persisted, nothing
to clear). A process without a console (an agent's shell tool, a script, a pipeline, a scheduled task) gets nothing and the tool prints its usual "not set" message.

Scope (least privilege). `OWNER_TOOLS` maps a script's file name to the variables it may receive: a tool gets only its own, never the whole vault. Injection happens when `shared.config` is imported
(every owner tool does), keyed on `sys.argv[0]`; under pytest, or from a module imported by another program, nothing is injected.

Honest limit (stated in NORMAS_BACKLOG 9.2): this is a GUARD, not a security boundary. Agent sessions run as the same Windows user, and DPAPI decrypts for that user; code deliberately written to read
the vault file and call CryptUnprotectData would succeed. What it prevents is the accidental or automatic use (an agent's normal command can no longer reach an owner string), and it removes every
secret from the files and environments the agent sessions load. The boundary would be a separate Windows account (decided against on 2026-10-30 as out of proportion).

The values are never printed, logged or returned by the CLI; the tools print only the NAMES that were injected.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

VAULT_VARIABLES = ("FONDOS_PG_DSN_OWNER", "FONDOS_BACKLOG_PG_DSN_HUMAN", "FONDOS_BACKLOG_PG_DSN_ADJUDICATOR", "GITHUB_ADMIN_TOKEN", "BACKLOG_GATE_GITHUB_TOKEN",
                  "FONDOS_BACKLOG_PG_DSN_FACTS")      # the last one is read by the deploy watcher itself (vault.get), not injected into a tool
_OWNER = "FONDOS_PG_DSN_OWNER"
_HUMAN = "FONDOS_BACKLOG_PG_DSN_HUMAN"
_ADJ = "FONDOS_BACKLOG_PG_DSN_ADJUDICATOR"
_GH_ADMIN = "GITHUB_ADMIN_TOKEN"
_GH_READ = "BACKLOG_GATE_GITHUB_TOKEN"

# file name of the script -> the variables it may receive. tests/test_owner_vault.py fails when a script under scripts/ reads a vault variable and is not listed here.
OWNER_TOOLS = {
    # DDL as the owner role (the database is chosen by each tool: the backlog tools point the string at `gestion` themselves)
    "apply_benchmark_ms_checks.py": (_OWNER,), "apply_fmts_pivot.py": (_OWNER,), "migrate_audit_finding_lineage.py": (_OWNER,), "migrate_audit_finding_p3_gate.py": (_OWNER,),
    "migrate_audit_governance.py": (_OWNER,), "migrate_audit_incident.py": (_OWNER,), "migrate_backlog_links.py": (_OWNER,), "migrate_backlog_v2.py": (_OWNER,),
    "migrate_cycle_flag_read_ts.py": (_OWNER,), "migrate_cycle_telemetry.py": (_OWNER,), "migrate_fund_metric_family_state.py": (_OWNER,),
    "migrate_fund_metric_state_ols_calc_version.py": (_OWNER,), "migrate_portfolio_scenario_status.py": (_OWNER,),
    "p1p2_state.py": (_OWNER,),                              # the cycle launcher's restore point (pg_create_restore_point needs the owner role); without it the cycle runs with a WARN
    # the owner-only backlog role: a human:* identity writes only through it
    "backlog_v3_bootstrap.py": (_HUMAN,), "backlog_step0.py": (_HUMAN,), "close_release.py": (_HUMAN,), "gate_mode.py": (_HUMAN,), "backlog_links.py": (_HUMAN,),
    "rfd_matrix_probe.py": (_HUMAN,),
    "gate_protection.py": (_HUMAN, _GH_READ),
    # the adjudicator login and the GitHub tokens
    "gate_adjudicate.py": (_ADJ,),
    "github_protect_master.py": (_GH_ADMIN,),
    "backlog_sync.py": (_GH_READ,),
}
_ENTROPY = b"fondos-owner-vault-v1"
_BLOB_MAX = 20000


class VaultError(RuntimeError):
    """The vault cannot be read or written (not Windows, a damaged file, a value this user cannot decrypt). Never carries a value."""


# ---------------------------------------------------------------- the cipher (Windows DPAPI, current user)

class DpapiCipher:
    """CryptProtectData / CryptUnprotectData for the current Windows user, UI forbidden. A blob made by another user (or another machine) does not decrypt."""

    def protect(self, data: bytes) -> bytes:
        return self._call("CryptProtectData", data)

    def unprotect(self, blob: bytes) -> bytes:
        return self._call("CryptUnprotectData", blob)

    @staticmethod
    def _call(fn_name: str, data: bytes) -> bytes:
        if os.name != "nt":
            raise VaultError("the owner vault uses Windows DPAPI and is available on Windows only")
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        def make(raw: bytes):
            buf = ctypes.create_string_buffer(raw, len(raw))
            return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

        src, keep1 = make(data)
        ent, keep2 = make(_ENTROPY)
        out = Blob()
        fn = getattr(ctypes.windll.crypt32, fn_name)
        ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out))          # 0x1 = CRYPTPROTECT_UI_FORBIDDEN
        if not ok:
            raise VaultError(f"{fn_name} failed (error {ctypes.GetLastError()}): not this user's vault, or a damaged value")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
            del keep1, keep2


# ---------------------------------------------------------------- the store

def vault_path(environ: Optional[dict] = None) -> Path:
    """%LOCALAPPDATA%\\fondos\\owner_vault.json (FONDOS_OWNER_VAULT overrides it, for tests). Outside the repository on purpose."""
    env = os.environ if environ is None else environ
    if env.get("FONDOS_OWNER_VAULT"):
        return Path(env["FONDOS_OWNER_VAULT"])
    base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "fondos" / "owner_vault.json"


class Vault:
    """A JSON file of {NAME: base64(cipher(value))}. The cipher is injectable (the tests use a reversible fake; production uses DPAPI)."""

    def __init__(self, path: Optional[Path] = None, cipher=None):
        self.path = Path(path) if path else vault_path()
        self.cipher = cipher or DpapiCipher()

    def _read(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data["entries"]
            if not isinstance(entries, dict):
                raise TypeError
            return entries
        except Exception as exc:                                          # noqa: BLE001
            raise VaultError(f"the vault file {self.path} is damaged ({type(exc).__name__})") from None

    def names(self) -> list:
        return sorted(self._read())

    def has(self, name: str) -> bool:
        return name in self._read()

    def set(self, name: str, value: str) -> None:
        _check_name(name)
        if not value or len(value) > _BLOB_MAX or "\n" in value or "\r" in value:
            raise VaultError("the value must be one non-empty line")
        entries = self._read()
        entries[name] = base64.b64encode(self.cipher.protect(value.encode("utf-8"))).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "entries": entries}, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, name: str) -> Optional[str]:
        _check_name(name)
        blob = self._read().get(name)
        if blob is None:
            return None
        try:
            return self.cipher.unprotect(base64.b64decode(blob)).decode("utf-8")
        except VaultError:
            raise
        except Exception as exc:                                          # noqa: BLE001
            raise VaultError(f"{name} could not be decrypted ({type(exc).__name__})") from None

    def remove(self, name: str) -> bool:
        _check_name(name)
        entries = self._read()
        if name not in entries:
            return False
        del entries[name]
        self.path.write_text(json.dumps({"version": 1, "entries": entries}, indent=1, sort_keys=True), encoding="utf-8")
        return True


def _check_name(name: str) -> None:
    if name not in VAULT_VARIABLES:
        raise VaultError(f"{name!r} is not a vault variable ({', '.join(VAULT_VARIABLES)})")


# ---------------------------------------------------------------- injection

def wanted_by(tool: str) -> tuple:
    """The variables the script `tool` (a file name) may receive. Pure."""
    return OWNER_TOOLS.get(Path(str(tool)).name, ())


def inject(tool: str, environ: dict, *, vault: Vault, console: bool) -> list:
    """Put the vault values that `tool` may receive into `environ` (this process only), for the variables not already set. Returns the NAMES injected. Nothing happens without a console, for
    an unknown tool, or for a name the vault does not hold; a value that cannot be decrypted is skipped (the tool then says the variable is not set). Never returns or prints a value."""
    names = [n for n in wanted_by(tool) if not environ.get(n)]
    if not names or not console:
        return []
    done = []
    for name in names:
        try:
            value = vault.get(name)
        except VaultError:
            continue
        if value:
            environ[name] = value
            done.append(name)
    return done


def inject_for_current_tool() -> list:
    """Called when shared.config is imported: injects for the running script (sys.argv[0]) when a person is at a real console. Silent and harmless in every other case."""
    try:
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST") or not sys.argv or not sys.argv[0]:
            return []
        tool = Path(sys.argv[0]).name
        if not wanted_by(tool):
            return []
        from shared.backlog_client import _is_console                     # one definition of "a person at a real console" (the live-write guard's)
        names = inject(tool, os.environ, vault=Vault(), console=_is_console(sys.stdin))
        if names:
            print(f"[owner vault] {tool}: {', '.join(names)} available to this process only", file=sys.stderr)
        return names
    except Exception:                                                     # noqa: BLE001 -- the vault must never break a tool that does not need it
        return []


def env_file_secret_names(path: Path) -> list:
    """The vault variables that a .env-style file defines (names only: the file is never echoed). Used by the preflight to say that a secret has leaked into a file the agent sessions load. Pure."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip().removeprefix("export ").strip()
        if "=" in line and key in VAULT_VARIABLES and not line.lstrip().startswith("#"):
            found.append(key)
    return sorted(set(found))
