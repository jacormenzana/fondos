"""Content fingerprint of the declarative rule set (catalogs + tolerances).

Stored on every audit_statistic / audit_finding row so a finding can be tied
to the exact rule set that produced it. A content hash, not a hand-bumped
string like P2's CALC_VERSION: it cannot be forgotten, and it changes only
when a catalog or tolerance file's bytes change.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent


def _rule_files() -> list[Path]:
    return sorted(_PKG_DIR.glob("catalog_*.py")) + [_PKG_DIR / "tolerances.py"]


def compute_catalog_version() -> str:
    digest = hashlib.sha1()
    for path in _rule_files():
        digest.update(path.name.encode("utf-8"))
        # CRLF -> LF so an autocrlf checkout does not change the fingerprint.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]
