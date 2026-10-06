# proyecto1/tests/test_statistical_audit_catalog_retired.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/catalog_retired.py (RETIRED_RULES, C1/FND-0130,
2026-09-28). Registro de correcciones ya aplicadas en los catalogos que los dos ficheros de skill
aun describen en su forma pre-correccion.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import datetime as dt
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.catalog_retired import RETIRED_RULES, RetiredRule


def test_every_entry_is_a_retired_rule_with_a_non_empty_reason():
    assert RETIRED_RULES  # non-empty
    for rule_id, entry in RETIRED_RULES.items():
        assert isinstance(entry, RetiredRule)
        assert entry.reason and len(entry.reason) > 20


def test_every_date_parses_as_iso():
    for entry in RETIRED_RULES.values():
        dt.date.fromisoformat(entry.date)  # raises ValueError if malformed


def test_fully_removed_rule_has_no_replacement():
    assert RETIRED_RULES["VOL_ANN_EQUALS_SRRI_VOL"].replaced_by is None


def test_self_corrected_rules_replace_by_the_same_id():
    for rule_id in ("FROZEN_NAV_ZERO_VOL", "SORTINO_VS_SHARPE_UP",
                     "ANNUAL_LE_ACCUMULATED", "ANNUAL_EQUALS_TOTAL_AT_1Y"):
        assert RETIRED_RULES[rule_id].replaced_by == rule_id


def test_a_replaced_rule_points_at_an_active_rule():
    """SORTINO_VS_SHARPE_DOWN no longer exists (FND-0234); its replacement must be a live catalog rule."""
    from shared.statistical_audit.catalog_invariants import P2_INVARIANTS
    replacement = RETIRED_RULES["SORTINO_VS_SHARPE_DOWN"].replaced_by
    assert replacement == "SORTINO_DOWNSIDE_BOUND"
    assert replacement in {r.rule_id for r in P2_INVARIANTS}
    assert "SORTINO_VS_SHARPE_DOWN" not in {r.rule_id for r in P2_INVARIANTS}
