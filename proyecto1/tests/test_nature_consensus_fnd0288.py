# proyecto1/tests/test_nature_consensus_fnd0288.py
# -*- coding: utf-8 -*-
"""FND-0288 -- Fund_Nature votes that one phrase decided against every other signal (R-7: classify_utils only, no DB).

Found 2026-10-17 from the fund_metric_alerts audit: 3 of the 4 PRODUCTION funds on ALARM were compared against the wrong peer group.
  * Fundsmith Equity -> Mixtos: the rule `"fund of funds" in w` matched the umbrella sentence "sub-fund of FUNDSmith SICAV" (no word boundary).
  * Bellevue Medtech -> Mixtos: "at least two thirds ... in equity securities, up to one third in debt securities" reached the generic
    "equity words and bond words both appear -> Mixtos" sink; name and Morningstar both said equity, nothing backed the KIID.
  * ACATIS Value Event -> Renta Fija Flexible: "diferentes clases de inversiones" (synonym of "clases de activos") was not an explicit
    multi-asset phrase, so the only asset words left were a government-bond allowance.
Measured over the 2,950 active funds with the full resolver: exactly 32 resolved natures change (18 Mixtos->Renta Variable, 9 Renta Variable->
Mixtos [Capital Group / CIF Global Allocation, Invesco Sustainable Allocation, R-co Valor Balanced], 4 Mixtos->Renta Fija Flexible [GS High
Yield / EM Corporate Bond], 1 Renta Fija Flexible->Mixtos [ACATIS]); the JPM / Invesco "Global Income" funds, whose Morningstar class backs
their KIID Mixtos, do not move.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from proyecto1.core import classify_utils as cu  # noqa: E402
from proyecto1.core.classify_utils import detect_nature_from_kiid, resolve_nature_evidence  # noqa: E402

PAD = "This document provides key information about this investment product. " * 4


def _kiid(objective: str) -> str:
    return PAD + "Objectives and investment policy. " + objective


BELLEVUE = _kiid("The Fund invests at least two thirds of the net assets in a portfolio of carefully chosen shares and other equity "
                 "securities of companies in the medical technology industry. Up to one third of the net assets can be invested in debt "
                 "securities of private and government issuers with various terms and credit ratings.")
FUNDSMITH = _kiid("Fundsmith Equity Fund is a sub-fund of Fundsmith SICAV. The Fund's objective is to achieve long-term growth in value, "
                  "investing in shares of companies on a global basis. The Fund will not invest in other funds, property or derivatives.")


# ---------------- FIX-NAT-FOF-WORDBOUND-1 ----------------

def test_an_umbrella_named_funds_is_not_a_fund_of_funds():
    assert detect_nature_from_kiid(FUNDSMITH) == "Renta Variable"


def test_a_sub_fund_of_an_umbrella_literally_named_funds_is_not_a_fund_of_funds_either():
    t = _kiid("The Fund is a sub-fund of Funds Selection SICAV. The Fund invests in shares of companies on a global basis.")
    assert detect_nature_from_kiid(t) == "Renta Variable"


def test_a_real_fund_of_funds_is_still_mixtos():
    assert detect_nature_from_kiid(_kiid("The Fund is a fund of funds. It invests in units of other funds across equities and bonds.")) == "Mixtos"
    assert detect_nature_from_kiid(_kiid("Le compartiment est un fonds de fonds investi en parts d'autres OPC actions et obligations.")) == "Mixtos"


# ---------------- FIX-NAT-MULTIASSET-INVERSIONES-1 ----------------

def test_diferentes_clases_de_inversiones_is_an_explicit_multi_asset_mandate():
    t = _kiid("El fondo se gestiona activamente. El fondo invierte en diferentes clases de inversiones. Se podrán incorporar títulos de deuda "
              "y bonos, pudiendo invertirse por encima del 35% del patrimonio del Fondo en deuda pública de la República Federal Alemana.")
    assert detect_nature_from_kiid(t) == "Mixtos"


# ---------------- ARB-1 ----------------

def test_the_bellevue_text_is_a_lone_mixtos_vote_on_its_own():
    assert detect_nature_from_kiid(BELLEVUE) == "Mixtos"
    assert resolve_nature_evidence("xyz fund", BELLEVUE, None, 5, None)[0] == "Mixtos"                  # no other signal: the KIID stands


def test_two_independent_signals_outvote_a_kiid_nothing_backs():
    nature, conf, trace = resolve_nature_evidence("bellevue medtech b usd acc", BELLEVUE, None, 5, "Equity")
    assert nature == "Renta Variable" and trace["primary_source"] == "consensus" and trace["arb1_from"] == "Mixtos"
    assert trace["reason"].startswith("consensus+2corrob")


def test_one_other_signal_is_not_a_consensus():
    assert resolve_nature_evidence("bellevue medtech b usd acc", BELLEVUE, None, 5, None)[0] == "Mixtos"
    assert resolve_nature_evidence("xyz fund", BELLEVUE, None, 5, "Equity")[0] == "Mixtos"


def test_a_signal_that_backs_the_kiid_blocks_the_arbitration():
    # JPM / Invesco "Global Income" pattern: name and benchmark say equity, but Morningstar (Mixed) backs the KIID's Mixtos.
    nature, _conf, trace = resolve_nature_evidence("bellevue medtech b usd acc", BELLEVUE, "msci world index", 5, "Mixed")
    assert nature == "Mixtos" and "arb1_from" not in trace


def test_the_consensus_nature_must_fit_the_realized_volatility_at_least_as_well():
    band = 3                                                           # Mixtos is in band, Renta Variable is two bands away
    assert cu._vol_consistency_factor("Renta Variable", band) < cu._vol_consistency_factor("Mixtos", band)
    nature, _conf, trace = resolve_nature_evidence("bellevue medtech b usd acc", BELLEVUE, None, band, "Equity")
    assert nature == "Mixtos" and "arb1_from" not in trace


def test_explicit_rule_natures_are_never_arbitrated():
    commodity = _kiid("El Compartimento tiene como objetivo participar en el crecimiento de los mercados de materias primas e invierte en "
                      "instrumentos vinculados a los índices Bloomberg Commodity Indexes.")
    assert detect_nature_from_kiid(commodity) == "Alternativo"
    nature, _c, trace = resolve_nature_evidence("vontobel commodity", commodity, "msci world index", 5, "Equity")
    assert nature == "Alternativo" and "arb1_from" not in trace
