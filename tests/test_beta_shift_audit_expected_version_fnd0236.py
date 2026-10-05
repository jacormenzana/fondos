"""FND-0236: the beta gate must expect the macro family's token, not the bare CALC_VERSION, once versioning is on.

Without this, the first macro-only version bump would make beta_shift_audit report 100% of the freshly recomputed rows
as "still on an older version" -- the 2026-10-05 false stall, reversed.
"""
import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("beta_shift_audit_t", _ROOT / "scripts" / "audit" / "beta_shift_audit.py")
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)

CV = "20261004"


@pytest.fixture
def switch(monkeypatch):
    from shared import config
    return lambda on: monkeypatch.setattr(config, "FAMILY_VERSIONING_ENABLED", on, raising=False)


def test_switch_off_expects_the_bare_calc_version(switch):
    switch(False)
    assert audit.expected_version(CV) == CV
    assert audit.expected_version(CV, {"macro": "20261101"}) == CV          # overrides are ignored while off


def test_switch_on_without_a_macro_override_expects_the_calc_version(switch):
    switch(True)
    assert audit.expected_version(CV, {}) == CV
    assert audit.expected_version(CV, {"fx": "20261101"}) == CV             # another family's override is irrelevant


def test_switch_on_with_a_macro_override_expects_its_token(switch):
    switch(True)
    assert audit.expected_version(CV, {"macro": "20261101"}) == f"{CV}.20261101"


def test_default_overrides_in_the_repo_are_empty_so_the_gate_is_unchanged_today(switch):
    switch(True)
    assert audit.expected_version(CV) == CV
