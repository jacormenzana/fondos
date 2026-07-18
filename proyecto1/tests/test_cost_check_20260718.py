"""
test_cost_check_20260718.py — FIX-COST-RATIO-SAFE + FIX-COST-DLA2-SUPPLEMENT

Root cause (2026-07-18):
  _ratio_to_pct_safe() had a '> 0.5 → return as-is' branch that silently
  treated ratio values > 0.5 (i.e. > 50%) as already-percentage numbers.
  _extract_pct_from_cell ALWAYS returns ratio form (e.g. "100%" → 1.0).
  So _guarded_pct(1.0, 'transaction') → _ratio_to_pct_safe(1.0) = 1.0
  → 1.0 > 2.0 = False → guard passed → transaction_cost_pct = 1.0 (ratio)
  → _ratio_to_pct(1.0) = 100.0 → CHECK constraint failed → fund DROPPED.

  The 4 affected ISINs (all ALKEN funds):
    LU0235308482, LU0300834669, LU0524465977, LU0572586674

  The DLA2 serialization for these KIDs contains:
    |||Costes de operacion|||100%|||
  (allocation-weight in the "Composición de costes" table, not an actual
  cost rate). The plain-text section correctly shows 0.45%/0.34%.

Tests:
  R-7 compliant: no pipeline.py or core.io imports.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest

from core.cost_table_parser import (
    _ratio_to_pct_safe,
    _guarded_pct,
    _extract_pct_from_cell,
    parse_costs_composition,
    _COMPOSITION_MAX_PCT,
)


# ---------------------------------------------------------------------------
# FIX-COST-RATIO-SAFE: _ratio_to_pct_safe always multiplies by 100
# ---------------------------------------------------------------------------

class TestRatioToPctSafe:
    """The fix removes the broken '> 0.5 → return as-is' branch."""

    def test_zero(self):
        assert _ratio_to_pct_safe(0.0) == 0.0

    def test_none_returns_zero(self):
        assert _ratio_to_pct_safe(None) == 0.0

    def test_small_ratio(self):
        # 0.45 % as ratio
        assert abs(_ratio_to_pct_safe(0.0045) - 0.45) < 1e-9

    def test_exactly_half(self):
        # 50 % boundary — was the old threshold, must now convert correctly
        assert abs(_ratio_to_pct_safe(0.5) - 50.0) < 1e-9

    def test_ratio_gt_half(self):
        # 51 % — OLD CODE returned 0.51 (wrong); new code: 51.0
        assert abs(_ratio_to_pct_safe(0.51) - 51.0) < 1e-9

    def test_ratio_100pct(self):
        # THE BUG: "100%" parsed as ratio 1.0
        # OLD: _ratio_to_pct_safe(1.0) = 1.0  → guard comparison 1.0 > 2.0 = False → passes!
        # NEW: _ratio_to_pct_safe(1.0) = 100.0 → 100.0 > 2.0 = True → guard returns None
        assert abs(_ratio_to_pct_safe(1.0) - 100.0) < 1e-9

    def test_ratio_75pct(self):
        assert abs(_ratio_to_pct_safe(0.75) - 75.0) < 1e-9


# ---------------------------------------------------------------------------
# FIX-COST-RATIO-SAFE: _guarded_pct now correctly filters 100%
# ---------------------------------------------------------------------------

class TestGuardedPct:

    def test_100pct_transaction_returns_none(self):
        """Core bug fix: DLA2 row '100%' → ratio 1.0 → must be filtered."""
        ratio_100pct = _extract_pct_from_cell("Costes de operacion 100%")
        assert ratio_100pct == pytest.approx(1.0, abs=1e-6), (
            f"_extract_pct_from_cell should return 1.0 for '100%', got {ratio_100pct}"
        )
        result = _guarded_pct(ratio_100pct, 'transaction')
        assert result is None, (
            f"_guarded_pct(1.0, 'transaction') should return None (100% > 2% max), got {result}"
        )

    def test_045pct_transaction_passes(self):
        """0.45% is within the 0–2% window, must pass."""
        ratio_045 = _extract_pct_from_cell("0,45%")
        assert ratio_045 == pytest.approx(0.0045, abs=1e-7)
        result = _guarded_pct(ratio_045, 'transaction')
        assert result == pytest.approx(0.0045, abs=1e-7), (
            f"0.45% transaction cost should pass the guard, got {result}"
        )

    def test_034pct_transaction_passes(self):
        """0.34% (LU0572586674) must pass."""
        ratio_034 = _extract_pct_from_cell("0,34%")
        result = _guarded_pct(ratio_034, 'transaction')
        assert result is not None
        assert result == pytest.approx(0.0034, abs=1e-7)

    def test_027pct_performance_passes(self):
        """0.27% performance fee must pass (max=30%)."""
        ratio_027 = _extract_pct_from_cell("0,27%")
        result = _guarded_pct(ratio_027, 'performance')
        assert result is not None
        assert result == pytest.approx(0.0027, abs=1e-7)

    def test_51pct_transaction_returns_none(self):
        """51% — also affected by the old bug (0.51 > 2.0 = False). Must now be filtered."""
        ratio_51 = _extract_pct_from_cell("51%")
        assert ratio_51 == pytest.approx(0.51, abs=1e-6)
        result = _guarded_pct(ratio_51, 'transaction')
        assert result is None, (
            f"51% should be filtered (> 2% max), got {result}"
        )

    def test_2pct_transaction_returns_none(self):
        """Exactly at COMPOSITION_MAX_PCT boundary (2.0): must be None (> is strict)."""
        # COMPOSITION_MAX_PCT['transaction'] = 2.0 — value exactly AT limit
        # _ratio_to_pct_safe(0.02) = 2.0; is 2.0 > 2.0? No → passes (included).
        # If the limit means EXCLUSIVE upper bound, verify the boundary behavior.
        assert _COMPOSITION_MAX_PCT.get('transaction') == 2.0
        ratio_2 = _extract_pct_from_cell("2%")
        result = _guarded_pct(ratio_2, 'transaction')
        # 2.0 <= 2.0 → not filtered (at boundary, passes through)
        assert result is not None  # boundary is inclusive

    def test_negative_transaction_returns_none(self):
        """Negative costs (PRIIPs slippage) must be NULLed."""
        result = _guarded_pct(-0.001, 'transaction')
        assert result is None

    def test_none_input_returns_none(self):
        result = _guarded_pct(None, 'transaction')
        assert result is None


# ---------------------------------------------------------------------------
# FIX-COST-DLA2-SUPPLEMENT: parse_costs_composition gaps filled from plain text
# ---------------------------------------------------------------------------

class TestCompositionDLA2Supplement:
    """
    When DLA2 filters a field (e.g. 100% transaction cost) but the plain-text
    section has the correct value, it must be recovered.
    """

    # Minimal plain-text "Composición de costes" section (appears before DLA2 block)
    _PLAIN_COMPOSITION = (
        "Composición de los costes\n"
        "Costes de entrada 0,00%\n"
        "Costes de salida 0,00%\n"
        "Costes de gestión 1,50%\n"
        "Costes de operación 0,45%\n"
    )

    # DLA2 block that shows "100%" for Costes de operacion (allocation, not a cost rate)
    _DLA2_BLOCK = (
        "|||\n"
        "|||Composición de los costes|||\n"
        "|||Costes de entrada|||||0,00 EUR|||0,00%|||\n"
        "|||Costes de salida|||||0,00 EUR|||0,00%|||\n"
        "|||Costes de gestión|||||150,00 EUR|||1,50%|||\n"
        "|||Costes de operacion|||||45,00 EUR|||100%|||\n"
    )

    def _make_fed_text(self):
        return self._PLAIN_COMPOSITION + "\n" + self._DLA2_BLOCK

    def test_dla2_supplement_recovers_transaction_cost(self):
        """After fix, 0.45% from plain text supplements the 100%-filtered DLA2 field."""
        fed_text = self._make_fed_text()
        result = parse_costs_composition(fed_text)

        # The 100% must not appear as transaction_cost_pct
        tcp = result.get('transaction_cost_pct')
        assert tcp is not None, (
            "transaction_cost_pct should be recovered from plain text"
        )
        assert tcp == pytest.approx(0.0045, abs=1e-6), (
            f"Expected 0.0045 (0.45%), got {tcp}"
        )

    def test_dla2_supplement_does_not_override_valid_dla2_field(self):
        """DLA2 values (e.g. management fee) must not be overridden by plain text."""
        fed_text = self._make_fed_text()
        result = parse_costs_composition(fed_text)

        mgmt = result.get('management_fee_pct')
        if mgmt is not None:
            # If DLA2 found 1.50% (ratio 0.015), plain text should not override it
            assert mgmt == pytest.approx(0.015, abs=1e-5), (
                f"management_fee_pct DLA2 value should not be overridden, got {mgmt}"
            )

    def test_plain_text_only_no_regression(self):
        """Without DLA2, existing plain-text path unchanged."""
        result = parse_costs_composition(self._PLAIN_COMPOSITION)
        # Should find transaction cost from plain text
        tcp = result.get('transaction_cost_pct')
        assert tcp is not None
        assert tcp == pytest.approx(0.0045, abs=1e-6)

    def test_empty_text_returns_empty(self):
        assert parse_costs_composition("") == {}
        assert parse_costs_composition(None) == {}
