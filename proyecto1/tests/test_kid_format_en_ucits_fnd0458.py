# proyecto1/tests/test_kid_format_en_ucits_fnd0458.py
# -*- coding: utf-8 -*-
"""FND-0458: an English UCITS KIID matched only 'key investor information' (1 UCITS signal, 2 needed), so detect_kid_format returned UNKNOWN and
the cost router sent it nowhere. 'ongoing charges' and 'one-off charges' (the English counterparts of 'gastos corrientes') are counted only
while KID_FORMAT_EN_UCITS_COST_SIGNALS_ENABLED is True (OFF until the R6 verdict, owner order 2026-11-28). Synthetic texts, no fund named (P#5).

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

import cost_format_router as R  # noqa: E402

# The shape of the 70 stored documents: 'key investor information' plus the two cost headings, WITHOUT the entry / exit / ongoing charge
# triple within 80 characters (that triple is already a UCITS signal, so a text with it is recognised with the switch OFF).
EN_UCITS = ("Key Investor Information. This document provides you with key investor information about this fund. "
            "Objectives and investment policy ... Risk and reward profile ... Charges. One-off charges taken before or after you invest: "
            "the maximum that might be taken out of your money before it is invested. Charges taken from the fund over a year: "
            "Ongoing charges 1.62%. Past performance ... Practical information ...")
EN_PRIIPS = ("Key Information Document. Purpose: this document provides you with key information about this investment product. "
             "What is this product? Recommended holding period: 5 years. Performance scenarios: stress scenario, unfavourable scenario, "
             "moderate scenario, favourable scenario. Costs over time. Composition of costs: one-off costs upon entry or exit; ongoing costs taken each year.")


def test_english_ucits_kiid_is_recognised_only_with_the_switch_on():
    assert R.detect_kid_format(EN_UCITS, en_cost_signals=False) == "UNKNOWN"
    assert R.detect_kid_format(EN_UCITS, en_cost_signals=True) == "UCITS_KIID"


def test_a_priips_kid_stays_priips_with_the_switch_on():
    # PRIIPs KIDs say 'ongoing costs' / 'one-off costs', never 'charges': the new signals do not fire
    assert R.detect_kid_format(EN_PRIIPS, en_cost_signals=False) == "PRIIPS_KID"
    assert R.detect_kid_format(EN_PRIIPS, en_cost_signals=True) == "PRIIPS_KID"
    assert R._count_ucits_signals(EN_PRIIPS, en_cost_signals=True) == 0


@pytest.mark.parametrize("phrase", ["Ongoing charges 1.62%", "ongoing charge 0.80 %", "One-off charges taken", "oneoff charges"])
def test_each_new_signal_counts_once(phrase):
    assert R._count_ucits_signals(phrase, en_cost_signals=True) - R._count_ucits_signals(phrase, en_cost_signals=False) == 1


def test_default_follows_the_config_switch_and_ships_off(monkeypatch):
    import shared.config as cfg
    assert cfg.KID_FORMAT_EN_UCITS_COST_SIGNALS_ENABLED is False, "the switch ships OFF (selection freeze until the R6 verdict)"
    assert R.detect_kid_format(EN_UCITS) == "UNKNOWN"
    monkeypatch.setattr(cfg, "KID_FORMAT_EN_UCITS_COST_SIGNALS_ENABLED", True, raising=False)
    assert R.detect_kid_format(EN_UCITS) == "UCITS_KIID"
