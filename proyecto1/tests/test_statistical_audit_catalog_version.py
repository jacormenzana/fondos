# proyecto1/tests/test_statistical_audit_catalog_version.py
# -*- coding: utf-8 -*-
"""Tests de shared/statistical_audit/catalog_version.py. Cumple R-7."""

import os
import re
import sys

_ROOT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit import catalog_version as cv


def _seed_pkg(tmp_path, content=b"X = 1\n"):
    (tmp_path / "catalog_a.py").write_bytes(content)
    (tmp_path / "tolerances.py").write_bytes(b"T = 0.1\n")
    return tmp_path


def test_format_is_12_hex_and_deterministic():
    v = cv.compute_catalog_version()
    assert re.fullmatch(r"[0-9a-f]{12}", v)
    assert v == cv.compute_catalog_version()


def test_changes_when_a_catalog_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_PKG_DIR", _seed_pkg(tmp_path))
    before = cv.compute_catalog_version()
    (tmp_path / "catalog_a.py").write_bytes(b"X = 2\n")
    assert cv.compute_catalog_version() != before


def test_changes_when_tolerances_change(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_PKG_DIR", _seed_pkg(tmp_path))
    before = cv.compute_catalog_version()
    (tmp_path / "tolerances.py").write_bytes(b"T = 0.2\n")
    assert cv.compute_catalog_version() != before


def test_line_endings_do_not_change_the_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_PKG_DIR", _seed_pkg(tmp_path, b"X = 1\nY = 2\n"))
    lf = cv.compute_catalog_version()
    (tmp_path / "catalog_a.py").write_bytes(b"X = 1\r\nY = 2\r\n")
    assert cv.compute_catalog_version() == lf


def test_unrelated_module_changes_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "_PKG_DIR", _seed_pkg(tmp_path))
    before = cv.compute_catalog_version()
    (tmp_path / "persistence.py").write_bytes(b"anything\n")
    assert cv.compute_catalog_version() == before
