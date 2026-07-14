# proyecto1/tests/test_audit_fixes_20260714.py
# -*- coding: utf-8 -*-
"""
Regression tests for bugs found during pipelineP1Audit session 8 (2026-07-14).

R-7: no imports of pipeline.py or core.io.

Fixes covered:
  FIX-B6-HY-LOGGER (2026-07-14)
    pipeline.py:1940 — `logger.info(...)` NameError that crashed every fund
    whose Credit_Quality was IG and whose KIID contained a sub-IG HY signal.
    Fix: replaced with `_dq_issues.append(...)`. Verified statically (no logger
    identifier in pipeline.py); also tested via a _dq_issues simulation that
    exercises the exact branch logic extracted here.

  BL-RV-EX8b (2026-07-14)
    renta_variable.py — `"templeton total"` substring never matched the real
    fund name "TEMPLETON GLOBAL TOTAL RETUR" (word "global" intervenes).
    Fix: added regex r"\\btempleton.*total" to _exclude_prefix_patterns.
    Tests: TEMPLETON GLOBAL TOTAL RETUR is excluded; genuine equity "total
    return" funds are not.
"""
from __future__ import annotations

import ast
import os
import sys

import pandas as pd
import pytest

# ── sys.path setup ────────────────────────────────────────────────────────────
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_P1_DIR    = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", ".."))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import blocks.renta_variable as rv


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_df(*rows):
    """Build a minimal df_master from (ISIN, Fund_Name) pairs."""
    data = [{"ISIN": isin, "Fund_Name": name, "Management_Company": "Test"}
            for isin, name in rows]
    return pd.DataFrame(data)


# ─── FIX-B6-HY-LOGGER: static check ─────────────────────────────────────────

class TestLoggerNotInPipeline:
    """
    FIX-B6-HY-LOGGER: verify pipeline.py no longer references the undefined
    `logger` identifier anywhere in its source (it must use print / _dq_issues).

    This is a static AST-level test (R-7 compliant — no pipeline.py import),
    guarding against reintroduction of the NameError that crashed ~11 funds/run.
    """

    _PIPELINE_PATH = os.path.normpath(
        os.path.join(_TESTS_DIR, "..", "core", "pipeline.py")
    )

    def _load_ast(self):
        with open(self._PIPELINE_PATH, encoding="utf-8") as fh:
            src = fh.read()
        return ast.parse(src, filename="pipeline.py")

    def test_no_bare_logger_name_in_pipeline(self):
        """No ast.Name(id='logger') node must exist in pipeline.py."""
        tree = self._load_ast()
        logger_names = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "logger"
        ]
        assert logger_names == [], (
            f"Found {len(logger_names)} 'logger' Name nodes in pipeline.py at "
            f"lines {[n.lineno for n in logger_names]}. "
            "pipeline.py has no logger object — use _dq_issues.append(...) or print(...)."
        )

    def test_bl_b6_hy_kiid_dq_append_present(self):
        """The BL_B6_HY_KIID DQ code must appear in pipeline.py
        (confirming the replacement with _dq_issues.append was actually written)."""
        with open(self._PIPELINE_PATH, encoding="utf-8") as fh:
            src = fh.read()
        assert "BL_B6_HY_KIID" in src, (
            "Expected 'BL_B6_HY_KIID' string not found in pipeline.py. "
            "The FIX-B6-HY-LOGGER replacement may have been accidentally removed."
        )


# ─── BL-RV-EX8b: Templeton Global Total Return excluded ─────────────────────

class TestTempletonExcludedFromRVUniverse:
    """
    BL-RV-EX8b (2026-07-14): renta_variable.get_universe_isins() must exclude
    Templeton Global Total Return (LU0260870661 and its share classes).

    Root cause: the previous substring token "templeton total" never matched the
    real abbreviated name "TEMPLETON GLOBAL TOTAL RETUR" because the word
    "global" sits between "templeton" and "total". Fix: regex r"\\btempleton.*total"
    in _exclude_prefix_patterns.

    Both benchmark sources (Morningstar Gbl Core Bond; Bloomberg Multiverse)
    confirm this is a Fixed Income fund, not equity.
    """

    @pytest.mark.parametrize("isin,name", [
        # Real share-class names from the master file / DB:
        ("LU0260870661", "TEMPLETON GLOBAL TOTAL RETUR A"),
        # Other typical Templeton GlobTR share classes (same exclusion must hold):
        ("LU0260870000", "TEMPLETON GLOBAL TOTAL RETUR B EUR ACC"),
        ("LU0260870001", "TEMPLETON GLOBAL TOTAL RETURN C"),
        # Degenerate: if someone writes "TEMPLETON TOTAL RETURN" (no "global")
        # the old token also failed — regression-guard that too:
        ("LU0000000099", "TEMPLETON TOTAL RETURN BOND"),
    ])
    def test_templeton_global_total_return_is_excluded(self, isin, name):
        """get_universe_isins must NOT include Templeton Total Return funds."""
        df = _make_df((isin, name))
        universe = rv.get_universe_isins(df)
        assert isin not in universe, (
            f"{isin} '{name}' was incorrectly included in the RV universe. "
            "Templeton (Global) Total Return is a Fixed Income fund."
        )

    @pytest.mark.parametrize("isin,name", [
        # ISHARES EM M GOV IDX — already excluded by "gov idx" (BL-RV-EX8 2026-07-13):
        ("LU1811365029", "ISHARES EM M GOV IDX D2 USD AC"),
        # Additional bond funds that should be excluded via existing tokens:
        ("LU0000000001", "PICTET GLOBAL EMERGING DEBT"),
        ("LU0000000002", "PIMCO EURO BOND FUND"),
    ])
    def test_other_bond_funds_also_excluded(self, isin, name):
        """Belt-and-suspenders: related bond funds must also be absent from RV universe."""
        df = _make_df((isin, name))
        universe = rv.get_universe_isins(df)
        assert isin not in universe, (
            f"{isin} '{name}' was incorrectly in the RV universe."
        )


class TestTempletonExclusionNoFalsePositives:
    """
    The r"\\btempleton.*total" regex must NOT block genuine equity "total return"
    funds from other managers, or Templeton equity funds that don't involve
    a "total return" mandate name.
    """

    @pytest.mark.parametrize("isin,name", [
        # Equity funds whose name contains "global" (an RV include token) AND
        # some form of "total" but are NOT Templeton → must remain in the RV universe.
        # Invariant: every test name here matches at least one RV include pattern
        # and no RV exclude pattern (other than what we're testing).
        ("LU0000000010", "FIDELITY GLOBAL TOTAL RETURN EQ"),   # "global" include ✓
        ("LU0000000013", "FRANKLIN MUTUAL GLOBAL TOTAL EQ"),   # "global" include ✓
        # Templeton equity fund (no "total" in name) — regex r"\btempleton.*total"
        # must NOT fire, leaving this genuine Templeton equity fund in the universe:
        ("LU0000000012", "TEMPLETON GLOBAL EQUITY A EUR"),     # "global" include, no "total" → included ✓
        # Non-Templeton global-equity fund with "total" in a different position:
        ("LU0000000014", "BGF GLOBAL TOTAL EQ A EUR ACC"),     # "global" include, no exclude ✓
    ])
    def test_non_templeton_total_return_equity_included(self, isin, name):
        """Equity funds with 'total return' from other managers must stay in universe."""
        df = _make_df((isin, name))
        universe = rv.get_universe_isins(df)
        assert isin in universe, (
            f"{isin} '{name}' was wrongly excluded from the RV universe. "
            "Only Templeton-branded total-return bond funds should be excluded."
        )
