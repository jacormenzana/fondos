# tests/test_fund_currency_gate_fnd0243.py
# -*- coding: utf-8 -*-
"""FND-0243 release gate: the stored Fund_Currency must equal what a P1 pass persists now before the EUR view is enabled.
Pure rule (kiid_parser.resolve_fund_currency, the pipeline's precedence) + diff semantics + CLI exit codes. No DB (R-7)."""
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "proyecto1"), str(_REPO / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.kiid_parser import resolve_fund_currency, fund_currency_from_name_word  # noqa: E402
from shared import fund_currency_gate as g  # noqa: E402
import verify_fund_currency as cli  # noqa: E402


# ---------------- the rule = pipeline precedence ----------------

def test_trailing_suffix_beats_the_kiid_text():
    assert resolve_fund_currency("Total costs 252 USD", "EN", "JPM GLOBAL SELECT EQ A EUR ACC") == "EUR"


def test_hedged_token_beats_a_representative_kiid_of_a_sister_class():
    assert resolve_fund_currency("Example inversion: EUR 10 000", "EN", "ALGEBRIS FINANCIA R USDHDG ACC") == "USD"


def test_kiid_text_when_the_name_says_nothing():
    assert resolve_fund_currency("Total costs 174 EUR 868 EUR", "EN", "CAPITAL GROUP GLBL HI OPPS BH") == "EUR"


def test_name_word_is_the_last_fallback_and_unknown_is_none():
    assert fund_currency_from_name_word("TEMPLETON USD BOND A") == "USD"
    assert resolve_fund_currency("", "EN", "TEMPLETON USD BOND A") == "USD"
    assert resolve_fund_currency("no currency here", "EN", "SEILERN WORLD GROWTH UR") is None


# ---------------- diff semantics (COALESCE) ----------------

def test_diff_rows_pending_unknown_and_coalesce():
    rows = [
        ("A", "n", None, "EN", "t"),     # NULL -> EUR: pending
        ("B", "n", "USD", "EN", "t"),    # USD -> EUR: pending (EUR-hedged class stored as base currency)
        ("C", "n", "eur", "EN", "t"),    # same value, other case: not pending
        ("D", "n", None, "EN", ""),      # rule yields None, stored None: unknown
        ("E", "n", "GBP", "EN", ""),     # rule yields None, stored GBP: COALESCE keeps it -> neither
    ]
    resolve = lambda text, lang, name: "EUR" if text else None
    pending, unknown = g.diff_rows(rows, resolve)
    assert [p[0] for p in pending] == ["A", "B"] and unknown == ["D"]


def test_summary_counts_moves():
    s = g.summarize([("A", "n", None, "EUR"), ("B", "n", "USD", "EUR"), ("C", "n", None, "EUR")], ["D"])
    assert s.startswith("3 active funds") and "NULL->EUR:2" in s and "USD->EUR:1" in s and "1 stay unknown" in s


# ---------------- CLI ----------------

class _Conn:
    def rollback(self): pass
    def close(self): pass


@pytest.mark.parametrize("pending, rc", [([], 0), ([("A", "FUND A", None, "EUR")], 1)])
def test_cli_exit_codes(monkeypatch, capsys, pending, rc):
    import shared.db
    monkeypatch.setattr(shared.db, "get_connection", lambda: _Conn())
    monkeypatch.setattr(g, "pending_fund_currency", lambda conn: (pending, []))
    assert cli.main([]) == rc
    out = capsys.readouterr().out
    assert ("GATE: PASS" in out) if rc == 0 else ("GATE: FAIL" in out and "A  NULL -> EUR" in out)


def test_cli_unreadable_db_is_not_a_pass(monkeypatch):
    import shared.db
    monkeypatch.setattr(shared.db, "get_connection", lambda: (_ for _ in ()).throw(OSError("down")))
    assert cli.main([]) == cli.RC_DB
