# proyecto1/tests/test_credit_quality_highyield_token_fnd0459.py
# -*- coding: utf-8 -*-
"""FND-0459: derive_credit_quality missed the high-yield token written without a space ('highyield'), so such a Renta Fija Flexible fund was
stored Mixed and escaped the Defensiva high-yield exclusion of P3. The token is read only while CREDIT_QUALITY_HIGHYIELD_TOKEN_ENABLED is True
(OFF until the R6 verdict, owner order 2026-11-28). Generic name shapes only (P#5).

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import classify_utils as cu  # noqa: E402

COMPACT = "manager us highyield a hdg acc"


@pytest.mark.parametrize("nature, off, on", [
    ("Renta Fija Flexible", "Mixed", "High Yield"),
    ("Renta Fija Corto Plazo", "Investment Grade", "High Yield"),
])
def test_compact_token_moves_only_when_the_switch_is_on(nature, off, on):
    assert cu.derive_credit_quality(nature, COMPACT, hy_compact_token=False) == off
    assert cu.derive_credit_quality(nature, COMPACT, hy_compact_token=True) == on


def test_default_follows_the_config_switch_and_ships_off(monkeypatch):
    import shared.config as cfg
    assert cfg.CREDIT_QUALITY_HIGHYIELD_TOKEN_ENABLED is False, "the switch ships OFF (selection freeze until the R6 verdict)"
    assert cu.derive_credit_quality("Renta Fija Flexible", COMPACT) == "Mixed"
    monkeypatch.setattr(cfg, "CREDIT_QUALITY_HIGHYIELD_TOKEN_ENABLED", True, raising=False)
    monkeypatch.setattr(cu, "_cfg_catalog", cfg, raising=False)
    assert cu.derive_credit_quality("Renta Fija Flexible", COMPACT) == "High Yield"


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_tokens_and_other_natures_are_unchanged(enabled):
    for name in ("manager global high yield bond", "manager euro high-yield", "fund glob.h.y.bond", "manager hy bond fund"):
        assert cu.derive_credit_quality("Renta Fija Flexible", name, hy_compact_token=enabled) == "High Yield"
    assert cu.derive_credit_quality("Renta Fija Flexible", "manager global aggregate", hy_compact_token=enabled) == "Investment Grade"
    assert cu.derive_credit_quality("Renta Variable", COMPACT, hy_compact_token=enabled) == "Not Applicable"
    assert cu.derive_credit_quality("Monetario", COMPACT, hy_compact_token=enabled) == "Investment Grade"
