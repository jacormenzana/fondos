"""
tests/test_p1p2_hedging_drift.py -- the Hedging_Policy drift line of the post-cycle report (FND-0244, step 3).

Drift = an active fund whose stored Hedging_Policy is not Hedged although the CURRENT parser derives HEDGED from its cached KIID text and
name. It comes from CODE changes (the persisted class-code signal, the compensation-boilerplate fix), not from new KIIDs, so it is checked
over the whole active universe, with the one derivation a P1 pass persists (kiid_parser.derive_hedging_policy). Pure scan + fail-soft wiring.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "proyecto1"):                    # P1 modules import each other as `core.*`
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_SPEC = importlib.util.spec_from_file_location(
    "p1p2_cycle_report_hd", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p1p2_cycle_report.py")
cr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cr)


def _row(isin, stored, text="", lang="EN", name="FUND X EUR"):
    return (isin, name, stored, lang, text)


def _derive_by_name(text, lang, name):
    return "HEDGED" if "HEDGED" in (name or "").upper() else None


class _Clock:
    """A fake clock: every call advances by `step`, so the phase times are deterministic."""
    def __init__(self, step=1.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _report(**over):
    r = {"stamp": "s", "since": "2026-10-04", "today": "2026-10-04", "universe": {"active": 1000, "inactive": 50, "total": 1050},
         "nature": {"Mixtos": 1000}, "kiid_status": {"OK": 1000}, "reliability": {"srri_null": 0, "profile_null": 0, "dqf_warn": 0, "dqf_missing": 0},
         "wrong_doc_stale": [], "low_confidence_nature": 0, "dq_cycle": [], "nav_stale": [], "nav_sources": {}, "real_orphans": {},
         "coverage": {"sharpe": 1000}, "alerts": {}}
    r.update(over)
    return r


# ---------------------------------------------------------------- the pure scan
def test_the_scan_counts_only_funds_stored_as_not_hedged_that_the_parser_now_derives_as_hedged():
    rows = [_row("A", "Unhedged", name="F HEDGED"), _row("B", None, name="F HEDGED"), _row("C", "Hedged", name="F HEDGED"),
            _row("D", "Unhedged", name="F PLAIN"), _row("E", "hedged", name="F HEDGED")]
    res = cr.scan_hedging_drift([rows], _derive_by_name)
    assert res["count"] == 2 and res["isins"] == ["A", "B"] and res["scanned"] == 5 and res["skipped"] is False


def test_the_scan_is_batch_invariant_and_handles_an_empty_stream():
    rows = [_row(f"I{i:03d}", "Unhedged", name="F HEDGED" if i % 3 == 0 else "F PLAIN") for i in range(10)]
    whole = cr.scan_hedging_drift([rows], _derive_by_name)
    pieces = cr.scan_hedging_drift([rows[:4], rows[4:5], rows[5:]], _derive_by_name)
    assert (whole["count"], whole["isins"], whole["scanned"]) == (pieces["count"], pieces["isins"], pieces["scanned"]) \
        == (4, ["I000", "I003", "I006", "I009"], 10)
    empty = cr.scan_hedging_drift([], _derive_by_name)
    assert (empty["scanned"], empty["count"], empty["isins"]) == (0, 0, [])


def test_the_json_keeps_only_the_first_isins_but_counts_all():
    rows = [_row(f"I{i:03d}", None, name="F HEDGED") for i in range(50)]
    res = cr.scan_hedging_drift([rows], _derive_by_name)
    assert res["count"] == 50 and len(res["isins"]) == cr.HEDGING_DRIFT_LIST == 20
    json.dumps(res)


def test_the_time_breakdown_separates_fetch_detect_and_compare_and_flags_a_slow_run():
    rows = [_row("A", None, name="F HEDGED")]
    res = cr.scan_hedging_drift([rows, rows], _derive_by_name, clock=_Clock(1.0), slow_s=5.0)
    s = res["seconds"]
    assert set(s) == {"fetch", "detect", "compare", "total"} and s["total"] == round(s["fetch"] + s["detect"] + s["compare"], 3)
    assert res["slow"] is True                                           # 3 fetches + 2 detects + 2 compares of 1 s each = 7 s > 5 s
    assert cr.scan_hedging_drift([rows], _derive_by_name, clock=_Clock(0.0001), slow_s=5.0)["slow"] is False


# ---------------------------------------------------------------- the derivation is the parser's own
def test_the_derivation_is_the_one_the_parser_persists_text_first_then_name():
    from core.kiid_parser import derive_hedging_policy, hedging_from_name
    assert derive_hedging_policy("", None, "ACME GLOBAL BOND HEDGED EUR") == ("HEDGED", "HEDGING_FROM_NAME")
    assert derive_hedging_policy(None, None, "ACME FUND I EUR") == (None, None)
    assert hedging_from_name(None) == (None, None) and hedging_from_name("") == (None, None)
    assert hedging_from_name("ACME GLOBAL ALLOC AH EUR")[1] in (None, "HEDGING_FROM_NAME_CLASSCODE")     # a class code ending in H, if the regex takes it
    txt = "This share class is not hedged. Currency exposure is unhedged against the euro."
    policy, tag = derive_hedging_policy(txt, "EN", "ACME FUND HEDGED EUR")
    assert policy == "UNHEDGED" and tag == "HEDGING_TEXT[EN]"           # the text wins over a name marker, exactly as in parse_kiid_generic


def test_parse_kiid_generic_and_the_drift_derivation_agree():
    """The report can never disagree with what a P1 pass would write: same function, same inputs."""
    from core.kiid_parser import derive_hedging_policy, parse_kiid_generic
    for text, name in (("", "ACME BOND HEDGED EUR"), ("Currency exposure is unhedged.", "ACME BOND EUR"), ("", "PLAIN FUND EUR")):
        parsed = parse_kiid_generic(text, fund_name=name) if text else parse_kiid_generic(" ", fund_name=name)
        derived, _ = derive_hedging_policy(text, parsed.get("Language"), name)
        assert (parsed.get("Hedging_Policy") or None) == derived, (text, name)


# ---------------------------------------------------------------- the report lines
def test_attention_item_for_a_nonzero_drift_and_none_for_zero_or_an_old_report():
    drift = {"skipped": False, "count": 18, "scanned": 2552, "isins": ["IE000Y3GPJX1", "LU0579955484"],
             "seconds": {"fetch": 0.5, "detect": 1.5, "compare": 0.01, "total": 2.01}, "slow": False}
    items = cr.attention_items(_report(hedging_drift=drift), None)
    assert len(items) == 1 and "18 fondos activos" in items[0] and "IE000Y3GPJX1" in items[0] and "Esperado tras el ciclo: 0" in items[0]
    assert cr.attention_items(_report(hedging_drift={**drift, "count": 0, "isins": []}), None) == []
    assert cr.attention_items(_report(), None) == []                    # a report written before the check existed
    assert cr.attention_items(_report(hedging_drift={"skipped": True}), None) == []


def test_a_failed_check_is_an_attention_item_but_a_slow_one_is_only_a_warning_marker():
    (item,) = cr.attention_items(_report(hedging_drift={"skipped": False, "error": "OperationalError: boom"}), None)
    assert "OperationalError: boom" in item
    slow = {"skipped": False, "count": 0, "scanned": 10, "isins": [], "slow": True,
            "seconds": {"fetch": 4.0, "detect": 3.0, "compare": 0.1, "total": 7.1}}
    assert cr.attention_items(_report(hedging_drift=slow), None) == []                 # a corpus constant would repeat every cycle
    text = cr.render_text(_report(hedging_drift=slow), None, [])
    assert "total=7.1s) [WARN lento: limite 5s]" in text and "fetch=4.0s" in text


def test_render_always_prints_the_breakdown_and_says_when_it_was_skipped():
    ok = {"skipped": False, "count": 2, "scanned": 100, "isins": ["A", "B"], "slow": False,
          "seconds": {"fetch": 0.2, "detect": 0.8, "compare": 0.0, "total": 1.0}}
    text = cr.render_text(_report(hedging_drift=ok), None, [])
    assert "Deriva de Hedging_Policy: 2 fondos de 100 revisados (fetch=0.2s detect=0.8s compare=0.0s total=1.0s)" in text
    assert "omitida (--no-hedging-drift)" in cr.render_text(_report(hedging_drift={"skipped": True}), None, [])
    assert "no disponible (X: y)" in cr.render_text(_report(hedging_drift={"skipped": False, "error": "X: y"}), None, [])
    assert "Deriva de Hedging_Policy" not in cr.render_text(_report(), None, [])        # old report shape


# ---------------------------------------------------------------- the fail-soft wiring
class _Conn:
    """A fake connection whose server-side cursor serves canned candidate rows in the batch sizes asked for."""
    def __init__(self, candidates):
        self.candidates, self.cursor_names, self.fetch_sizes, self.executed = candidates, [], [], []

    def cursor(self, name=None):
        conn = self
        conn.cursor_names.append(name)

        class Cur:
            pos = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                conn.executed.append(sql)

            def fetchmany(self, size):
                conn.fetch_sizes.append(size)
                out = conn.candidates[self.pos:self.pos + size]
                self.pos += size
                return out
        return Cur()

    def close(self):
        pass


def test_collect_hedging_drift_streams_with_a_named_cursor_in_batches_of_200():
    cands = [("I%04d" % i, "ACME FUND HEDGED EUR" if i < 3 else "ACME FUND EUR", "Unhedged", "EN", "") for i in range(450)]
    conn = _Conn(cands)
    res = cr.collect_hedging_drift(conn)
    assert conn.cursor_names == ["hedging_drift"] and cr.HEDGING_DRIFT_BATCH == 200
    assert set(conn.fetch_sizes) == {200} and len(conn.fetch_sizes) == 4                  # 200 + 200 + 50 + the empty fetch that ends it
    assert res["scanned"] == 450 and res["count"] == 3 and res["isins"] == ["I0000", "I0001", "I0002"]
    assert conn.executed == [cr.Q_HEDGING_CANDIDATES]
    json.dumps(res)


def test_collect_hedging_drift_never_raises():
    res = cr.collect_hedging_drift(object())                              # no cursor(): the report must survive with an error entry
    assert res["skipped"] is False and "AttributeError" in res["error"]


def test_a_slow_scan_logs_a_structured_warning(monkeypatch, caplog):
    import logging
    slow = {"skipped": False, "scanned": 5, "count": 0, "isins": [], "slow": True,
            "seconds": {"fetch": 4.0, "detect": 3.0, "compare": 0.1, "total": 7.1}}
    monkeypatch.setattr(cr, "scan_hedging_drift", lambda *a, **k: slow)
    with caplog.at_level(logging.WARNING, logger="p1p2_cycle_report"):
        cr.collect_hedging_drift(_Conn([]))
    assert any("hedging_drift slow: total=7.1s" in r.message and "fetch=4.0s" in r.message for r in caplog.records)


def test_the_candidate_query_is_a_static_literal_that_skips_funds_already_hedged():
    sql = cr.Q_HEDGING_CANDIDATES
    assert "{" not in sql and "?" not in sql and "IS DISTINCT FROM 'Hedged'" in sql and "in_current_universe = 1" in sql


def test_main_skips_the_check_with_the_flag_and_records_it(tmp_path, monkeypatch, capsys):
    import shared.db
    canned = _canned_for_main()
    monkeypatch.setattr(shared.db, "get_connection", lambda: canned)
    assert cr.main(["--stamp", "s1", "--since", "2026-10-01", "--out-dir", str(tmp_path), "--no-hedging-drift"]) == 0
    assert "omitida (--no-hedging-drift)" in capsys.readouterr().out
    assert json.loads((tmp_path / "P1_P2_cycle_report_s1.json").read_text(encoding="utf-8"))["hedging_drift"] == {"skipped": True}


def test_main_runs_the_check_by_default_and_its_failure_does_not_change_the_exit_code(tmp_path, monkeypatch, capsys):
    import shared.db
    monkeypatch.setattr(shared.db, "get_connection", lambda: _canned_for_main())
    assert cr.main(["--stamp", "s2", "--since", "2026-10-01", "--out-dir", str(tmp_path)]) == 0
    assert "Deriva de Hedging_Policy: no disponible" in capsys.readouterr().out


def _canned_for_main():
    rows = {
        cr.Q_UNIVERSE: [(1000, 50, 1050)], cr.Q_NATURE: [("Mixtos", 1000)], cr.Q_KIID_STATUS: [("OK", 1000)],
        cr.Q_RELIABILITY: [(0, 0, 0, 0)], cr.Q_WRONG_DOC_STALE: [], cr.Q_LOW_CONFIDENCE: [(0,)], cr.Q_DQ_CYCLE: [],
        cr.Q_NAV_STALE: [], cr.Q_NAV_SOURCES: [], cr.Q_REAL_ORPHANS: [], cr.Q_COVERAGE: [("sharpe", 1000)],
        cr.Q_ALERTS: [], cr.Q_OLS_ZERO_BACKFILL: [],
    }

    class Conn:
        def execute(self, sql, params=()):
            r = rows[sql]
            return type("Cur", (), {"fetchall": lambda s: r})()

        def close(self):
            pass
    return Conn()          # no cursor(): the drift check fails soft
