# test_fund_family_builder_import.py
# -*- coding: utf-8 -*-
"""
Regression test for FIX-FAMBUILDER-IMPORT-1.

Ensures fund_family_builder is importable as a package module
(the invocation context used by the batch launcher:
  python -m proyecto1.core.fund_family_builder)
where `core` alone is not a top-level package.

R-7 compliant: no import of pipeline.py or core.io.
"""

import importlib


def test_family_builder_importable_as_package():
    """
    Module must load cleanly via its fully-qualified package path.
    Before the fix, the bare `from core.classify_utils import ...` at module
    level raised ModuleNotFoundError in this import context, crashing the
    entire fund_family_builder step every cycle.
    """
    mod = importlib.import_module("proyecto1.core.fund_family_builder")
    assert hasattr(mod, "build_fund_families"), (
        "build_fund_families() not found — top-level API missing"
    )
    # RFC_INCOMPATIBLE_FAMILIES must be a non-empty frozenset (populated in classify_utils)
    assert mod.RFC_INCOMPATIBLE_FAMILIES, (
        "RFC_INCOMPATIBLE_FAMILIES is empty — guarded import did not resolve the symbol"
    )
    assert isinstance(mod.RFC_INCOMPATIBLE_FAMILIES, frozenset), (
        "RFC_INCOMPATIBLE_FAMILIES must be a frozenset"
    )
