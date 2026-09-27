# -*- coding: utf-8 -*-
"""
FND-0110 (2026-09-26 full-cycle incident): LU1739342035's freshly-downloaded KIID decoded to text
containing an embedded NUL (0x00) byte, and the write failed with "PostgreSQL text fields cannot
contain NUL (0x00) bytes" — SQLite silently accepted the same bytes pre-cutover, so this had never
surfaced before. Root-cause fix: strip NUL bytes at the single point all KIID text passes through
(core.io.extract_text_from_pdf_bytes), so no downstream consumer ever sees one.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import core.io as io_mod  # noqa: E402


def test_strip_nul_bytes_removes_embedded_nul_and_is_a_noop_otherwise():
    assert io_mod._strip_nul_bytes("clean text") == "clean text"
    assert io_mod._strip_nul_bytes("before\x00after") == "beforeafter"
    assert io_mod._strip_nul_bytes("\x00\x00\x00") == ""
    assert io_mod._strip_nul_bytes("") == ""
    assert io_mod._strip_nul_bytes(None) is None


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_extract_text_from_pdf_bytes_strips_nul_from_the_pdfplumber_path(monkeypatch):
    monkeypatch.setattr(io_mod, "DLA_ENABLED", False)
    monkeypatch.setattr(
        io_mod.pdfplumber, "open",
        lambda b: _FakePdf([_FakePage("SRRI\x00 indicator: 3")]),
    )
    result = io_mod.extract_text_from_pdf_bytes(b"%PDF-fake%")
    assert "\x00" not in result
    assert result == "SRRI indicator: 3"


def test_extract_text_from_pdf_bytes_strips_nul_from_the_dla_path(monkeypatch):
    monkeypatch.setattr(io_mod, "DLA_ENABLED", True)

    def _fake_dla_aware(pdf_bytes, ocr_enabled, ocr_lang, ocr_dpi):
        return "cost table\x00row", {"fallback": False}

    import core.dla_extractor as dla_mod
    monkeypatch.setattr(dla_mod, "extract_text_dla_aware", _fake_dla_aware)
    monkeypatch.setattr(dla_mod, "emit_dla_log", lambda *a, **k: None)

    result = io_mod.extract_text_from_pdf_bytes(b"%PDF-fake%")
    assert "\x00" not in result
    assert result == "cost tablerow"
