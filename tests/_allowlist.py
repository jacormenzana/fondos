"""
tests/_allowlist.py — the one rule every static-guard allowlist in tests/ follows.

Each allowlist entry is `{key: reason}` and the reason must either cite a backlog ticket
(`FND-0123 ...`) or start with `DESIGN:` followed by a real explanation (>= 20 characters). This
raises the cost of a lazy bypass and puts the justification in the commit diff for review. It does
not stop a determined bypass; the protocol also asks that the evidence for an exemption goes in the
commit message and is flagged for the operator's review (see doc/reglas/NORMAS_IMPLEMENTACION.md).
A stale key (its target no longer exists) must fail its suite, so allowlists cannot rot.
"""
from __future__ import annotations

import re

_REASON = re.compile(r"^(FND-\d{4}\b|DESIGN: \S.{18,})", re.S)


def check_reason(text: str | None) -> bool:
    return bool(text and _REASON.match(text.strip()))


def bad_reasons(allowlist: dict) -> list:
    """Keys of `allowlist` whose reason is missing or does not satisfy check_reason()."""
    return [k for k, v in allowlist.items() if not check_reason(v)]
