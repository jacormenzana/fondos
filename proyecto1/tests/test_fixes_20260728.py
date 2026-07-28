# test_fixes_20260728.py
# R-7: no pipeline.py / core.io imports.
# Covers:
#   FIX-TOTALRET-ABBREV-1 — "total ret" (common name abbreviation) added to
#       NAME_SIGNALS_RF_FLEXIBLE so detect_nature_from_name catches it.
#       Root cause: MFS M GBL TOTAL RET (active, Family=Flexible Fixed Income)
#       stayed in Restantes because the name prefilter had "total return" but not
#       the abbreviation "total ret".  Ruffer Total Ret (Mixtos, Multi-Asset)
#       verified unaffected: detect_nature_from_kiid returns Mixtos for it via
#       Capa 1, so Capa 2 name detection is never reached.

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from proyecto1.core.classify_utils import detect_nature_from_name


class TestTotalRetAbbrev:
    """FIX-TOTALRET-ABBREV-1: 'total ret' abbreviation treated as RF_Flexible."""

    def test_mfs_gbl_total_ret(self):
        """Active Restantes fund must now be detected as RF_Flexible by name."""
        assert detect_nature_from_name("mfs m gbl total ret n1 usd acc") == "RF_Flexible"

    def test_total_ret_generic(self):
        """Generic 'total ret' abbreviation maps to RF_Flexible."""
        assert detect_nature_from_name("acme total ret bond eur acc") == "RF_Flexible"

    def test_total_ret_mgr_prefix(self):
        """Manager-prefixed 'total ret' still works."""
        assert detect_nature_from_name("some mgr global total ret eur acc") == "RF_Flexible"

    def test_total_return_full_still_works(self):
        """'total return' (full word, already in list) continues to work."""
        assert detect_nature_from_name("some fund total return bond eur") == "RF_Flexible"

    def test_ruffer_total_ret_not_affected_by_name(self):
        """Ruffer Total Ret: name alone now returns RF_Flexible, but Capa 1 (KIID)
        returns Mixtos for this fund before Capa 2 is reached — restantes ordering
        means the name signal is only a tiebreaker when KIID gives nothing."""
        # The name-only function returns RF_Flexible (as expected after the fix).
        # Correctness for RUFFER depends on KIID text (Capa 1) returning Mixtos first,
        # which is verified in the live-DB check. This test documents the expected
        # name-level output.
        assert detect_nature_from_name("ruffer total ret cr eurhdg acc") == "RF_Flexible"

    def test_no_match_for_unrelated(self):
        """Unrelated names do not accidentally match."""
        assert detect_nature_from_name("jupiter dynamic equity eur acc") != "RF_Flexible"
