# proyecto1/tests/test_kiid_common_stocks_mandate_fnd0237.py
# -*- coding: utf-8 -*-
"""FND-0237: two English equity-mandate phrasings the KIID nature detector missed (R-7: classify_utils only, no DB).

Measured on the 2,950 active KIID texts: exactly 26 detections change, all toward Renta Variable (the 10 Restantes funds: 9 Capital Group
New Perspective / Investment Company of America hedged classes and ARCUS JAPAN, plus 16 Capital Group classes already Renta Variable by
Morningstar), no other fund moves.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from proyecto1.core.classify_utils import detect_nature_from_kiid  # noqa: E402

PAD = "This document provides key information about this investment product. " * 4      # the objective window starts at char 200


def _kiid(objective: str) -> str:
    return PAD + "Investment objective. " + objective


def test_capital_group_common_stocks_mandate_is_renta_variable():
    t = _kiid("The fund's investment objective is to provide long-term growth of capital. The fund seeks to take advantage of opportunities "
              "by investing in common stocks of companies located around the world, which may include emerging markets. In pursuing its "
              "investment objective, the fund invests primarily in common stocks that the investment adviser believes have the potential "
              "for growth. The fund may invest up to 10% of its assets in nonconvertible debt securities rated Baa1 or below.")
    assert detect_nature_from_kiid(t) == "Renta Variable"


def test_will_mainly_be_shares_mandate_is_renta_variable():
    t = _kiid("The investment objectives of the Sub-Fund are to achieve long-term capital appreciation and to outperform the Tokyo Stock "
              "Exchange First Section Total Return Index (TOPIXTR). Investments will mainly be shares in large and medium sized Japanese "
              "companies. Up to 15% of the Sub-Fund's net assets may be held in corporate bonds. About 95% of the Sub-Fund's net assets "
              "will be held in long positions in shares and other equity-linked securities.")
    assert detect_nature_from_kiid(t) == "Renta Variable"


def test_a_bond_mandate_that_only_allows_common_stocks_is_not_renta_variable():
    t = _kiid("The fund invests primarily in investment grade corporate bonds and government bonds. The fund may also hold up to 5% of "
              "its assets in common stocks received through a restructuring of bond holdings.")
    assert detect_nature_from_kiid(t) != "Renta Variable"


def test_a_bond_mandate_phrased_in_the_future_tense_is_not_renta_variable():
    t = _kiid("The Sub-Fund's investments will mainly be bonds issued by governments and corporations. Up to 10% of the net assets may be "
              "invested in shares.")
    assert detect_nature_from_kiid(t) != "Renta Variable"


def test_bare_stocks_is_not_an_equity_mandate_signal():
    """'stocks' alone (stock-picking / stock-index prose) must not satisfy the new pattern."""
    t = _kiid("The fund invests primarily in a diversified portfolio of fixed income securities selected with a stocks and flows analysis.")
    assert detect_nature_from_kiid(t) != "Renta Variable"
