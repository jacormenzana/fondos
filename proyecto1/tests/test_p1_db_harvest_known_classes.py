# proyecto1/tests/test_p1_db_harvest_known_classes.py
# -*- coding: utf-8 -*-
"""
FND-0328 (S1, 2026-11-15): AIMA / CARA / FMDB are known non-KIID document classes of the Deutsche Bank catalogue, so they stop raising NORM-001/002/003 on every
harvest, while a genuinely new codSus still does and a known class that disappears or collapses raises NORM-004 against the immediately previous harvest.

Two layers (R-7: the rule is testable without importing pipeline.py):
  * pure functions (`is_unknown_codsus`, `known_class_findings`, `known_class_summary`): no database;
  * Postgres (isolated per-test schema, `cmd_harvest` commits internally): the previous-harvest lookup by `harvest_ts` (never by insertion order), the counts being
    deduplicated DB rows, and the harvest's own log lines on a mocked catalogue.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from unittest.mock import MagicMock, patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_HARVEST_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "harvest"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location("p1_db_harvest_known_classes_test", os.path.join(_HARVEST_DIR, "p1_db_harvest.py"))
_pdh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pdh)

_CATALOGUE_DDL = """
    CREATE TABLE db_document_catalogue (
        harvest_ts text NOT NULL, gestora_label text NOT NULL, gestora_value text, cod_db text NOT NULL, fund_name text,
        isin varchar(12), link_label text NOT NULL, href text NOT NULL, cod_doc text, cod_sus text, cod_cont text, idioma text,
        PRIMARY KEY (harvest_ts, cod_db, href)
    )
"""


# ---------------------------------------------------------------------------------------- pure layer
def test_the_three_known_classes_and_the_workbook_classes_are_not_unknown_but_anything_else_is():
    for code in ("AIMA", "CARA", "FMDB", "KIID", "LIIC", "MECO", "FGES", "LFII", "CCA", "ISSF"):
        assert _pdh.is_unknown_codsus(code) is False, code
    assert _pdh.is_unknown_codsus("ZZZZ") is True
    assert _pdh.is_unknown_codsus(None) is False and _pdh.is_unknown_codsus("") is False, "an empty codSus is not a class"


def test_known_classes_present_and_stable_raise_nothing():
    cur = {"AIMA": 2957, "CARA": 74, "FMDB": 5904}
    assert _pdh.known_class_findings(cur, dict(cur)) == []
    assert _pdh.known_class_findings(cur, None) == [], "no previous harvest: a present class is fine"
    assert _pdh.known_class_findings(cur, {"AIMA": 3000, "CARA": 80, "FMDB": 6000}) == [], "a small drop is noise"


def test_a_known_class_with_zero_rows_warns_with_or_without_a_previous_harvest():
    (level, msg), = _pdh.known_class_findings({"AIMA": 10, "CARA": 0, "FMDB": 10}, None)
    assert level == "WARNING" and "NORM-004" in msg and "CARA" in msg and "previous" not in msg, "no baseline: only the zero check, no percentage"
    (level, msg), = _pdh.known_class_findings({"AIMA": 10, "FMDB": 10}, {"AIMA": 10, "CARA": 74, "FMDB": 10})
    assert level == "WARNING" and "CARA" in msg and "previous harvest: 74" in msg


def test_a_known_class_that_collapses_below_the_ratio_warns_and_the_exact_ratio_does_not():
    findings = _pdh.known_class_findings({"AIMA": 4, "CARA": 40, "FMDB": 5904}, {"AIMA": 10, "CARA": 80, "FMDB": 5904})
    assert [m.split()[3] for _, m in findings] == ["AIMA"], "AIMA 10 -> 4 is below 50 %; CARA 80 -> 40 is exactly 50 % and is not below it"
    assert "10 to 4" in findings[0][1]


def test_a_missing_baseline_never_warns_by_itself_and_a_class_absent_before_is_not_a_drop():
    assert _pdh.known_class_findings({"AIMA": 1, "CARA": 1, "FMDB": 1}, {}) == [], "previous harvest exists but had none of them: nothing to compare against"


def test_the_summary_line_lists_the_three_classes_in_order():
    assert _pdh.known_class_summary({"AIMA": 2957, "FMDB": 5904}) == "known non-KIID codSus (not anomalies): AIMA=2957 CARA=0 FMDB=5904"


# ---------------------------------------------------------------------------------------- Postgres layer
def _put(conn, harvest_ts, cod_sus, n, tag):
    for i in range(n):
        conn.execute(
            "INSERT INTO db_document_catalogue (harvest_ts, gestora_label, cod_db, link_label, href, cod_sus, isin) VALUES (%s, 'g', %s, 'l', %s, %s, 'LU0000000001')",
            (harvest_ts, f"F{tag}{i}", f"https://x/{tag}{i}", cod_sus))


def test_previous_harvest_is_chosen_by_harvest_ts_not_by_insertion_order(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute(_CATALOGUE_DDL)
    _put(conn, "20261115_000000", "AIMA", 4, "n")        # the NEWER harvest is inserted first
    _put(conn, "20261101_000000", "AIMA", 10, "o")
    _put(conn, "20261101_000000", "FMDB", 20, "p")
    _put(conn, "20261115_000000", "KIID", 3, "k")

    assert _pdh._known_class_counts(conn, "20261115_000000") == {"AIMA": 4}, "only the known classes, as stored"
    assert _pdh._previous_known_class_counts(conn, "20261115_000000") == {"AIMA": 10, "FMDB": 20}
    assert _pdh._previous_known_class_counts(conn, "20261101_000000") is None, "the oldest harvest has no previous one"
    findings = _pdh.known_class_findings(_pdh._known_class_counts(conn, "20261115_000000"), _pdh._previous_known_class_counts(conn, "20261115_000000"))
    assert sorted(m.split()[3] for _, m in findings) == ["AIMA", "CARA", "FMDB"], "AIMA collapsed, FMDB and CARA have no rows in the new harvest"


def test_an_empty_table_has_no_previous_harvest(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute(_CATALOGUE_DDL)
    assert _pdh._previous_known_class_counts(conn, "20261115_000000") is None


_XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<catalogo fechaPublicacion="2026-11-15">
  <gestora codigo="G1" nombreGestora="Test Gestora">
    <fondo codDB="F001" nombreFondo="Test Fund One">
      <documento contenido="KIID"><codigoIsin>LU0000000001</codigoIsin><link>https://example.com/doc?codDoc=D1&amp;idioma=ES&amp;codSus=KIID&amp;codCont=C1</link></documento>
      <documento contenido="AIMA"><codigoIsin>LU0000000001</codigoIsin><link>https://example.com/doc?codDoc=D2&amp;idioma=ES&amp;codSus=AIMA&amp;codCont=C2</link></documento>
      <documento contenido="AIMA"><codigoIsin>LU0000000001</codigoIsin><link>https://example.com/doc?codDoc=D3&amp;idioma=ES&amp;codSus=AIMA&amp;codCont=C3</link></documento>
      <documento contenido="FMDB"><codigoIsin>LU0000000001</codigoIsin><link>https://example.com/doc?codDoc=D4&amp;idioma=ES&amp;codSus=FMDB&amp;codCont=C4</link></documento>
      <documento contenido="CARA"><codigoIsin>LU0000000001</codigoIsin><link>https://example.com/doc?codDoc=D5&amp;idioma=ES&amp;codSus=CARA&amp;codCont=C5</link></documento>
    </fondo>
  </gestora>
</catalogo>
""".encode("iso-8859-1")


def _mock_get_with(xml):
    def _get(*_a, **_k):
        resp = MagicMock()
        resp.content = xml
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        return resp
    return _get


class _FixedNow:
    """Stands in for the module's `datetime` so each harvest gets a chosen harvest_ts (never the real clock: two runs in one second would share the key)."""
    def __init__(self, stamp):
        from datetime import datetime as _dt
        self._now = _dt.strptime(stamp, "%Y%m%d_%H%M%S")

    def now(self):
        return self._now


def _harvest_messages(conn, xml, caplog, monkeypatch, tmp_path, stamp):
    monkeypatch.setattr(_pdh, "_HARVEST_DIR", tmp_path)          # the raw JSONL goes to a temp dir, never into proyecto1/harvest
    monkeypatch.setattr(_pdh, "datetime", _FixedNow(stamp))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="p1_db_harvest"), patch.object(_pdh, "_get", _mock_get_with(xml)):
        _pdh.cmd_harvest(None, conn=conn)
    return [(r.levelno, r.getMessage()) for r in caplog.records]


def test_harvest_known_classes_are_one_info_line_and_a_new_codsus_still_warns(pg_session_conn, pg_conn_module_schema, caplog, monkeypatch, tmp_path):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute(_CATALOGUE_DDL)

    msgs = _harvest_messages(conn, _XML, caplog, monkeypatch, tmp_path, "20010101_000000")
    text = "\n".join(m for _, m in msgs)
    assert "known non-KIID codSus (not anomalies): AIMA=2 CARA=1 FMDB=1" in text
    assert not any("NORM-001" in m or "NORM-002" in m or "NORM-003" in m for _, m in msgs), "AIMA / CARA / FMDB are known: no per-run NORM warning"
    assert not any("NORM-004" in m for _, m in msgs), "all three present and there is no previous harvest"

    new_xml = _XML.replace(b"codSus=CARA", b"codSus=ZZZZ")          # CARA vanishes and an unclassified class appears
    msgs = _harvest_messages(conn, new_xml, caplog, monkeypatch, tmp_path, "20010102_000000")
    levels = {(lv, m.split("]")[0] + "]") for lv, m in msgs if "NORM-" in m}
    assert (logging.WARNING, "[NORM-001]") in levels and (logging.WARNING, "[NORM-002]") in levels, "a codSus nobody classified is still a WARNING"
    assert any("NORM-004" in m and "CARA" in m and "previous harvest: 1" in m for _, m in msgs), "CARA has 0 rows now and had 1 in the previous harvest (2001-01-01)"
    assert not any("NORM-004" in m and ("AIMA" in m or "FMDB" in m) for _, m in msgs), "AIMA and FMDB are stable"
