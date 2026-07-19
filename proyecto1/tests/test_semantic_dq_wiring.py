# proyecto1/tests/test_semantic_dq_wiring.py
# -*- coding: utf-8 -*-
"""
Tests unitarios para:
  1. semantic_validation_to_dq_tuples() (classify_utils.py, A3 2026-07-11)
  2. INTER-14: Market_Cap_Focus solo aplica a Renta Variable
     (validate_all_semantic_consistency, classify_utils.py, A4 2026-07-11)

Reglas de diseño (R-7): ningún import de pipeline.py ni core.io.
Solo classify_utils, sin dependencias externas.
"""

from __future__ import annotations
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import pytest
from classify_utils import (
    validate_all_semantic_consistency,
    semantic_validation_to_dq_tuples,
)


# ─────────────────────────────────────────────────────────────────────────────
# semantic_validation_to_dq_tuples (A3)
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticValidationToDqTuples:
    """Tests unitarios del helper de conversión A3."""

    def test_empty_result_returns_empty_list(self):
        val_result = {"is_valid": True, "critical_errors": [], "warnings": [], "corrected_record": {}}
        assert semantic_validation_to_dq_tuples(val_result) == []

    def test_critical_error_becomes_warn_tuple(self):
        val_result = {
            "is_valid": False,
            "critical_errors": [{"rule": "Nature-Family", "message": "Family incoherente con Nature"}],
            "warnings": [],
            "corrected_record": {},
        }
        tuples = semantic_validation_to_dq_tuples(val_result)
        assert len(tuples) == 1
        check_code, dq_level, log_status, message = tuples[0]
        assert check_code.startswith("SEM_")
        assert "NATURE_FAMILY" in check_code or "NATURE" in check_code
        assert dq_level == "WARN"
        assert log_status == "WARN"
        assert "incoherente" in message

    def test_warning_becomes_info_tuple(self):
        val_result = {
            "is_valid": True,
            "critical_errors": [],
            "warnings": [{"rule": "ESG-SFDR", "message": "Is_ESG=False pero SFDR=9"}],
            "corrected_record": {},
        }
        tuples = semantic_validation_to_dq_tuples(val_result)
        assert len(tuples) == 1
        check_code, dq_level, log_status, message = tuples[0]
        assert dq_level == "INFO"
        assert log_status == "INFO"
        assert "SFDR" in message

    def test_mixed_produces_correct_order_and_counts(self):
        """2 critical_errors + 1 warning → 3 tuplas; critical primero."""
        val_result = {
            "is_valid": False,
            "critical_errors": [
                {"rule": "A", "message": "msg A"},
                {"rule": "B", "message": "msg B"},
            ],
            "warnings": [{"rule": "C", "message": "msg C"}],
            "corrected_record": {},
        }
        tuples = semantic_validation_to_dq_tuples(val_result)
        assert len(tuples) == 3
        # Primero los dos críticos (WARN)
        assert tuples[0][1] == "WARN"
        assert tuples[1][1] == "WARN"
        # Luego el warning (INFO)
        assert tuples[2][1] == "INFO"

    def test_check_code_format(self):
        """El check_code tiene prefijo SEM_ y max 50 chars."""
        val_result = {
            "is_valid": False,
            "critical_errors": [{"rule": "Very-Long-Rule-Name-That-Could-Overflow", "message": "x"}],
            "warnings": [],
            "corrected_record": {},
        }
        tuples = semantic_validation_to_dq_tuples(val_result)
        code = tuples[0][0]
        assert code.startswith("SEM_")
        assert len(code) <= 50

    def test_all_tuples_have_four_elements(self):
        val_result = {
            "is_valid": False,
            "critical_errors": [{"rule": "X", "message": "m"}],
            "warnings": [{"rule": "Y", "message": "n"}],
            "corrected_record": {},
        }
        for tup in semantic_validation_to_dq_tuples(val_result):
            assert len(tup) == 4


# ─────────────────────────────────────────────────────────────────────────────
# INTER-14: Market_Cap_Focus ↔ Fund_Nature coherence (A4)
# ─────────────────────────────────────────────────────────────────────────────

class TestInter14MarketCapFocusNature:
    """Tests de la regla INTER-14 (Fase 4): MCF no aplica a naturalezas no-equity."""

    @pytest.mark.parametrize("nature", [
        "Monetario",
        "Renta Fija Corto Plazo",
        "Renta Fija Flexible",
        "Alternativo",
        "Restantes",
        "Estructurado",
    ])
    def test_mcf_nulled_for_non_equity_nature(self, nature):
        """Market_Cap_Focus='All Cap' en fondo no-equity → corrected a None + WARNING."""
        record = {
            "Fund_Nature": nature,
            "Market_Cap_Focus": "All Cap",
            "Fund_Name": "Test Fund EUR ACC",
        }
        result = validate_all_semantic_consistency(record)
        corrected = result["corrected_record"]
        assert corrected.get("Market_Cap_Focus") is None, (
            f"INTER-14: MCF debe ser None para Fund_Nature='{nature}'"
        )
        # Debe emitirse al menos un warning (no critical) para esta regla
        all_issues = result["critical_errors"] + result["warnings"]
        mcf_issue = next(
            (i for i in all_issues if "MarketCap" in i.get("rule", "") or "Market_Cap" in i.get("message", "")),
            None
        )
        assert mcf_issue is not None, (
            f"INTER-14 debe emitir un issue para Fund_Nature='{nature}' con MCF='All Cap'"
        )

    @pytest.mark.parametrize("mcf", ["All Cap", "Large Cap", "Mid Cap", "Small Cap"])
    def test_all_mcf_values_nulled_for_fi(self, mcf):
        """Cualquier valor de MCF es incoherente en RF Flexible → None."""
        record = {
            "Fund_Nature": "Renta Fija Flexible",
            "Market_Cap_Focus": mcf,
        }
        result = validate_all_semantic_consistency(record)
        assert result["corrected_record"].get("Market_Cap_Focus") is None

    def test_equity_fund_with_all_cap_not_affected(self):
        """Renta Variable + 'All Cap' → sin corrección (es válido para equity)."""
        record = {
            "Fund_Nature": "Renta Variable",
            "Market_Cap_Focus": "All Cap",
        }
        result = validate_all_semantic_consistency(record)
        assert result["corrected_record"].get("Market_Cap_Focus") == "All Cap", (
            "INTER-14 NO debe anular MCF para Renta Variable"
        )
        # No debe haber un issue de MarketCap
        all_issues = result["critical_errors"] + result["warnings"]
        mcf_issue = next(
            (i for i in all_issues if "MarketCap" in i.get("rule", "")),
            None
        )
        assert mcf_issue is None, "No debe emitirse issue de MCF para Renta Variable"

    def test_equity_fund_with_large_cap_not_affected(self):
        """Renta Variable + 'Large Cap' → sin corrección."""
        record = {
            "Fund_Nature": "Renta Variable",
            "Market_Cap_Focus": "Large Cap",
        }
        result = validate_all_semantic_consistency(record)
        assert result["corrected_record"].get("Market_Cap_Focus") == "Large Cap"

    def test_none_mcf_no_issue_emitted(self):
        """Market_Cap_Focus=None para cualquier nature → sin issue INTER-14."""
        for nature in ("Renta Fija Flexible", "Monetario", "Renta Variable"):
            record = {"Fund_Nature": nature, "Market_Cap_Focus": None}
            result = validate_all_semantic_consistency(record)
            all_issues = result["critical_errors"] + result["warnings"]
            mcf_issue = next(
                (i for i in all_issues if "MarketCap" in i.get("rule", "")),
                None
            )
            assert mcf_issue is None, f"MCF=None no debe emitir INTER-14 para nature='{nature}'"

    def test_mixtos_with_mcf_not_affected(self):
        """Mixtos puede tener MCF (componente de equity) → sin corrección por INTER-14."""
        record = {
            "Fund_Nature": "Mixtos",
            "Market_Cap_Focus": "Large Cap",
        }
        result = validate_all_semantic_consistency(record)
        # Mixtos NO está en la lista _MCF_NON_EQUITY_NATURES de INTER-14
        assert result["corrected_record"].get("Market_Cap_Focus") == "Large Cap"


# ─────────────────────────────────────────────────────────────────────────────
# Integration: semantic_validation_to_dq_tuples applied to INTER-14 result
# ─────────────────────────────────────────────────────────────────────────────

class TestInter14ToDqTuples:
    """Verifica que el output de INTER-14 pasa correctamente por el helper A3."""

    def test_inter14_warning_becomes_info_dq_tuple(self):
        """INTER-14 es un warning → tuple con dq_level='INFO'."""
        record = {
            "Fund_Nature": "Renta Fija Flexible",
            "Market_Cap_Focus": "All Cap",
        }
        val_result = validate_all_semantic_consistency(record)
        tuples = semantic_validation_to_dq_tuples(val_result)
        mcf_tuples = [t for t in tuples if "MARKETCAP" in t[0] or "MARKET_CAP" in t[0]]
        assert len(mcf_tuples) >= 1, "El issue INTER-14 debe producir al menos una 4-tupla"
        assert mcf_tuples[0][1] == "INFO", (
            "INTER-14 es warning → dq_level='INFO' (no WARN)"
        )
