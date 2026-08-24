# -*- coding: utf-8 -*-
"""
Tests for the global normalize_kiid_text() canonical helper.

FIX-UNICODE-NFC-GLOBAL (2026-08-24) — promotes NFC + NBSP normalization from
scattered patches to a single governed function applied once at the read
boundary (io.get_kiid_for_isin).

Canonical source: proyecto1/core/classify_utils.normalize_kiid_text()
Application site: proyecto1/core/io.get_kiid_for_isin()

Covers:
  T-NFC-01  Decomposed to precomposed (the root cause)
  T-NFC-02  Already-NFC text is returned unchanged (idempotence)
  T-NFC-03  None / empty safety
  T-NFC-04  NBSP (U+00A0) is collapsed to a regular space
  T-NFC-05  Multi-character Spanish cost keywords recover correctly
  T-NFC-06  Double application is idempotent
  T-NFC-07  Structural newlines are NOT collapsed (deferred DEFER-WSPC-COLLAPSE)
"""

import unicodedata
import sys
import os

# Allow import from proyecto1/core/ when run from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from classify_utils import normalize_kiid_text


NBSP = " "   # U+00A0 NON-BREAKING SPACE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decompose(s: str) -> str:
    """Force-decompose a string to NFD (simulate pdfplumber decomposed output)."""
    return unicodedata.normalize("NFD", s)


def _is_nfc(s: str) -> bool:
    return unicodedata.is_normalized("NFC", s)


# ---------------------------------------------------------------------------
# T-NFC-01: Decomposed to precomposed
# ---------------------------------------------------------------------------

def test_decomposed_o_accent_recomposes():
    """'o' + U+0301 must become the precomposed form."""
    decomposed = _decompose("gestión")
    assert not _is_nfc(decomposed), "test fixture must start as non-NFC"
    result = normalize_kiid_text(decomposed)
    assert result == "gestión"


def test_decomposed_spanish_cost_keywords():
    """All common cost-regex keywords recover after decomposition."""
    keywords = [
        "gestión",
        "operación",
        "comisión",
        "rendimiento",
        "período",
        "acción",
        "participación",
    ]
    for kw in keywords:
        decomposed = _decompose(kw)
        result = normalize_kiid_text(decomposed)
        assert result == kw, f"Failed for keyword '{kw}': got '{result}'"


def test_decomposed_sentence_recomposes():
    """A realistic KIID sentence with multiple accented chars round-trips."""
    original = "Comisión de gestión: 1,50% del valor de la participación"
    decomposed = _decompose(original)
    assert not _is_nfc(decomposed), "test fixture must start as non-NFC"
    result = normalize_kiid_text(decomposed)
    assert result == original
    assert _is_nfc(result)


# ---------------------------------------------------------------------------
# T-NFC-02: Already-NFC text is returned unchanged
# ---------------------------------------------------------------------------

def test_already_nfc_unchanged():
    """Text already in NFC passes through without modification."""
    s = "Ongoing charges: 1.23%"
    assert _is_nfc(s)
    assert normalize_kiid_text(s) == s


def test_ascii_only_unchanged():
    """Pure-ASCII text is unmodified (NFC is a no-op for ASCII)."""
    s = "This is a test KIID string with only ASCII."
    assert normalize_kiid_text(s) == s


# ---------------------------------------------------------------------------
# T-NFC-03: None / empty safety
# ---------------------------------------------------------------------------

def test_none_returns_none():
    assert normalize_kiid_text(None) is None


def test_empty_string_returns_empty():
    assert normalize_kiid_text("") == ""


def test_whitespace_only_passes_through():
    """Whitespace-only string (regular spaces) passes through unchanged."""
    s = "   "
    assert normalize_kiid_text(s) == s


# ---------------------------------------------------------------------------
# T-NFC-04: NBSP (U+00A0) is collapsed to a regular space
# ---------------------------------------------------------------------------

def test_nbsp_collapsed_to_space():
    """Single NBSP must become a regular ASCII space.

    pdfplumber frequently emits U+00A0 between words in cost tables.
    Patterns like r'comision\\s+de' match NBSP but fixed-literal word
    sequences ('comision de') do not.  Collapsing at the read boundary
    eliminates the ambiguity uniformly.
    """
    s = "coste" + NBSP + "de" + NBSP + "gestión"
    result = normalize_kiid_text(s)
    assert NBSP not in result, "NBSP must be collapsed"
    assert result == "coste de gestión"


def test_nbsp_run_each_becomes_space():
    """Each NBSP in a run becomes one space (not further collapsed; that is deferred)."""
    s = "a" + NBSP + NBSP + "b"
    result = normalize_kiid_text(s)
    assert result == "a  b"
    assert NBSP not in result


def test_nbsp_combined_with_decomposed():
    """NFC recomposition and NBSP collapse both apply in one pass."""
    s = _decompose("comisión") + NBSP + _decompose("gestión")
    result = normalize_kiid_text(s)
    assert result == "comisión gestión"
    assert NBSP not in result
    assert _is_nfc(result)


# ---------------------------------------------------------------------------
# T-NFC-05: Regex matching works after normalization (integration smoke test)
# ---------------------------------------------------------------------------

def test_regex_matches_after_normalization():
    """After normalize_kiid_text, accent-tolerant regexes must match."""
    import re
    pat = re.compile(r"comisi[oó]n\s+de\s+gesti[oó]n", re.IGNORECASE)

    decomposed_with_nbsp = _decompose("Comisión") + NBSP + "de" + NBSP + _decompose("gestión")
    normalized = normalize_kiid_text(decomposed_with_nbsp)
    assert pat.search(normalized), (
        f"Pattern must match after normalize_kiid_text(); "
        f"normalized to {repr(normalized)!r}"
    )


# ---------------------------------------------------------------------------
# T-NFC-06: Double application is idempotent
# ---------------------------------------------------------------------------

def test_idempotent():
    """normalize_kiid_text(normalize_kiid_text(s)) == normalize_kiid_text(s)."""
    samples = [
        "gestión",
        _decompose("operación"),
        "coste" + NBSP + "de",
        "ASCII only",
        "",
        None,
    ]
    for s in samples:
        once = normalize_kiid_text(s)
        twice = normalize_kiid_text(once)
        assert twice == once, f"Not idempotent for input {s!r}"


# ---------------------------------------------------------------------------
# T-NFC-07: Structural newlines are NOT collapsed (DEFER-WSPC-COLLAPSE)
# ---------------------------------------------------------------------------

def test_newlines_preserved():
    """Newlines must survive -- they are structural markers in KIID section finders.

    Full whitespace collapse (\\s+ to single space) is deferred to backlog
    item DEFER-WSPC-COLLAPSE.  This test is the regression guard.
    """
    s = "Sección 1\nContenido\n\nSección 2"
    result = normalize_kiid_text(s)
    assert "\n" in result, "Newlines must be preserved"
    assert result.count("\n") == s.count("\n")
