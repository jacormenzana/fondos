# proyecto1/tests/test_cost_arbitration_primitives_resolve.py
# -*- coding: utf-8 -*-
"""
The three DLA2 extraction primitives that cost_arbitration imports (xband / ruled / OCR fallback) are
production code and live in proyecto1/core (moved out of scripts/diag on 2026-09-26, where a
"diagnostics" folder was a hidden production dependency).

R-7: imports only cost_arbitration (no pipeline.py / core.io).
"""
import os
import sys

_CORE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import cost_arbitration as ca  # noqa: E402


def _module_dir(fn) -> str:
    return os.path.normpath(os.path.dirname(sys.modules[fn.__module__].__file__))


def test_primitives_resolve_from_core_and_not_from_scripts_diag():
    ca._PRIMS.clear()
    prims = ca._load_primitives()
    for name in ("extract_from_open_pdf", "extract_aci_xband", "extract_ruled_from_pdf",
                 "_recover_oc_text", "cost_values_agree"):
        assert callable(prims[name]), name
    for name in ("extract_from_open_pdf", "extract_ruled_from_pdf"):
        assert _module_dir(prims[name]) == _CORE, f"{name} must come from proyecto1/core"
    # the diagnostics folder must not be needed (or even importable) for production extraction
    assert not any(m.startswith("scripts.diag") for m in sys.modules)


def test_no_scripts_diag_candidate_is_left_in_the_import_list():
    src = open(ca.__file__, encoding="utf-8").read()
    assert "scripts.diag" not in src.replace("scripts/diag", "")
