# -*- coding: utf-8 -*-
"""
Regression tests for ACT-07 / ACT-08 fixes (audit 2026-08-21).

ACT-07: "ab rtrn" added to NAME_SIGNALS_ALTERNATIVO + _PREFILTER_ALT_INCLUDE
  → GS Absolute Return Tracker (LU1103308125) must be detected as Alternativo,
    not fall through to Mixtos / restantes.

R-7: no pipeline.py or core.io imports.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_P1 = _ROOT / "proyecto1"
for _p in [str(_P1), str(_P1 / "core"), str(_P1 / "blocks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.classify_utils import (
    _prefilter_match_alternativo,
    _prefilter_match_mixtos,
    detect_nature_from_prefilter,
    detect_nature_from_name,
    NAME_SIGNALS_ALTERNATIVO,
)


# ─── ACT-07: "ab rtrn" Absolute Return abbreviation ────────────────────────

class TestAbRtrnAlternativo:
    """LU1103308125 GS AB RTRN TRCK PRF E HDG AC — Absolute Return Tracker.

    Previously fell through to restantes (no matching prefilter) and inherited
    stale Mixtos classification via COALESCE. The fund has a cash benchmark
    (ICE BofA 3-Month German Treasury Bill) and an explicit hedge-fund-
    replication mandate — unambiguously Alternativo.
    """

    def test_prefilter_ab_rtrn_is_alternativo(self):
        # Standard abbreviation "ab rtrn" ≡ "absolute return"
        assert _prefilter_match_alternativo("gs ab rtrn trck prf e hdg ac") is True

    def test_prefilter_ab_rtrn_not_mixtos(self):
        # Must not be claimed by the mixtos block
        assert _prefilter_match_mixtos("gs ab rtrn trck prf e hdg ac") is False

    def test_detect_nature_ab_rtrn(self):
        # detect_nature_from_prefilter must return Alternativo (checked before Mixtos)
        assert detect_nature_from_prefilter("gs ab rtrn trck prf e hdg ac") == "Alternativo"

    def test_name_signals_contains_ab_rtrn(self):
        # Validate the signal is present in the canonical list (R-1 / P#11)
        assert "ab rtrn" in NAME_SIGNALS_ALTERNATIVO

    def test_abs_ret_still_in_name_signals(self):
        # "absret" lives in NAME_SIGNALS_ALTERNATIVO (used by detect_nature_from_name /
        # restantes classifier), not in _PREFILTER_ALT_INCLUDE (block universe selector).
        # Both paths must continue to resolve to Alternativo.
        assert "absret" in NAME_SIGNALS_ALTERNATIVO
        assert detect_nature_from_name("jupiter st absret r eur") == "Alternativo"

    def test_ocs_variant_alternativo(self):
        # LU1103307408 GS AB RTRN TRCK PRF OCS HDG AC (sister share class, already Alternativo)
        assert _prefilter_match_alternativo("gs ab rtrn trck prf ocs hdg ac") is True
        assert detect_nature_from_prefilter("gs ab rtrn trck prf ocs hdg ac") == "Alternativo"
