# proyecto1/tests/test_fix_if_r4_20260913.py
# -*- coding: utf-8 -*-
"""
Regression test for FIX-IF-R4 (pipeline.py, 2026-09-13), found during
pipelineP1P2Audit.

Root cause: the Investment_Focus Nature-based default
(pipeline.py ~line 2175, "if not fund_master_record.get('Investment_Focus')")
checked only the raw in-cycle record, not the effective value (record or BD),
violating R-4 ("_X_eff = record.get('X') or _X_bd — never record.get('X') alone").
For a CACHED fund whose in-record Investment_Focus is None mid-cycle even
though fund_master already holds a corrected 'Sector' value (set by BL-30 in
a prior cycle), the default clobbered it back to 'Broad', forcing BL-30 to
fix it again in the SAME cycle — self-inflicted churn (BL30_INVESTMENT_FOCUS_SECTOR
fire-count in ingestion_log oscillated 15<->326 across consecutive full
nature-first runs instead of converging).

Fix: check the effective value (`fund_master_record.get("Investment_Focus")
or _if_bd`), matching the pattern BL-30 itself already used a few lines later.
_bd_prev (the BD-effective-value fetch) was also moved earlier in pipeline.py
so _if_bd is available at the default-block's location.

R-7: no imports of pipeline.py or core.io — mirrors the exact conditional
(pipeline.py ~line 2175-2191), same convention as test_audit_fixes_20260715.py.
"""
from __future__ import annotations


def _investment_focus_default(record_if: str | None, if_bd: str | None,
                               fund_nature: str | None) -> str | None:
    """Mirrors pipeline.py's post-fix Investment_Focus default block exactly:
    only applies the Nature-based default when NEITHER the in-cycle record NOR
    the BD-effective value already has Investment_Focus populated."""
    if not (record_if or if_bd):
        if fund_nature in ("Renta Fija Corto Plazo", "Monetario"):
            return "Broad"
        elif fund_nature == "Renta Variable":
            return "Broad"
        elif fund_nature in ("Renta Fija Flexible", "Mixtos"):
            return "Broad"
        return record_if  # Alternativo/Estructurado/Restantes: no default
    return record_if  # already populated (record or BD) — do not touch


class TestInvestmentFocusR4Default:
    def test_bd_sector_value_not_clobbered_when_record_empty(self):
        """The exact regression scenario: CACHED fund, in-record Investment_Focus
        is None this cycle, but BD already has 'Sector' from a prior BL-30 fix."""
        result = _investment_focus_default(
            record_if=None, if_bd="Sector", fund_nature="Renta Fija Flexible"
        )
        assert result is None, (
            "Must NOT default to 'Broad' when BD already holds a value — "
            "COALESCE should preserve the BD 'Sector' value untouched"
        )

    def test_default_still_applies_when_truly_never_classified(self):
        """No record value AND no BD value → the Nature-based default must
        still fire (this is the behavior BL-63 introduced and must survive)."""
        for nature in ("Renta Fija Corto Plazo", "Monetario", "Renta Variable",
                       "Renta Fija Flexible", "Mixtos"):
            assert _investment_focus_default(None, None, nature) == "Broad"

    def test_record_value_takes_precedence(self):
        """A freshly-classified record value is never overridden."""
        result = _investment_focus_default(
            record_if="Sector", if_bd=None, fund_nature="Renta Variable"
        )
        assert result == "Sector"

    def test_no_default_for_ambiguous_natures(self):
        """Alternativo/Estructurado/Restantes: no default assigned (ambiguous)."""
        for nature in ("Alternativo", "Estructurado", "Restantes"):
            assert _investment_focus_default(None, None, nature) is None
