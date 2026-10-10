# tests/test_backlog_links_signals.py
# -*- coding: utf-8 -*-
"""scripts/audit/backlog_links.py: the pure core (signals S1-S4, candidate assembly, review file rules S-1 / D-2 / D-3).

Fixtures only: no database, no git, no pipeline import (R-7). Live-data behaviour is reported by `--evaluate`, never gated here.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backlog_links as L  # noqa: E402

KNOWN = {f"FND-000{i}" for i in range(1, 8)}


# ─── S1 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_norm_id_pads_and_accepts_short_forms():
    assert L.norm_id("FND-246") == "FND-0246" and L.norm_id("FND-0246") == "FND-0246"


def test_explicit_mentions_cue_types_and_ignore_self_and_unknown_ids():
    docs = {
        "FND-0001": "Depends on FND-0002 to land first. Linked: FND-0003 (x). See FND-9999 and FND-0001 itself.",
        "FND-0004": "Duplicate of FND-0005.",
        "FND-0006": "Nothing here.",
    }
    s1 = L.signal_explicit(docs, KNOWN)
    assert s1[("FND-0001", "FND-0002")]["type"] == "DEPENDS_ON"
    assert s1[("FND-0001", "FND-0003")]["type"] == "RELATED"      # the cue of the previous sentence must not bleed over
    assert s1[("FND-0004", "FND-0005")]["type"] == "DUPLICATES"
    assert all("FND-9999" not in k and k[0] != k[1] for k in s1)


def test_reverse_cue_swaps_the_direction():
    # "FND-0004: ... split out to FND-0005" means FND-0005 was carved out of FND-0004
    s1 = L.signal_explicit({"FND-0004": "The abbreviation work was split out to FND-0005."}, KNOWN)
    assert s1 == {("FND-0005", "FND-0004"): {"type": "SPLIT_FROM", "n": 1, "evidence": s1[("FND-0005", "FND-0004")]["evidence"]}}


def test_a_stray_word_before_the_sentence_does_not_reverse_the_link():
    s1 = L.signal_explicit({"FND-0001": "This blocks the whole run. Related: FND-0002."}, KNOWN)
    assert s1[("FND-0001", "FND-0002")]["type"] == "RELATED"


# ─── S2 / S3 shared overlap ──────────────────────────────────────────────────────────────────────────────────────────────

def test_paths_are_extracted_and_resolved_to_repo_files():
    text = r"touches proyecto1\core\kiid_parser.py, metrics_writer.py and doc/reglas/NORMAS_BATCH.md plus nope.py"
    cited = L.extract_paths(text)
    assert {"proyecto1/core/kiid_parser.py", "metrics_writer.py", "doc/reglas/NORMAS_BATCH.md", "nope.py"} <= cited
    known = ["proyecto1/core/kiid_parser.py", "proyecto2/src/writers/metrics_writer.py", "doc/reglas/NORMAS_BATCH.md"]
    assert L.resolve_paths(cited, known) == set(known)          # unique basename resolves; unknown nope.py is dropped


def test_footprint_uses_commits_that_name_the_ticket_and_cited_paths():
    commits = [{"message": "fix(p1): thing (FND-0001, FND-0002)", "files": ["a.py", "b.py"]},
               {"message": "unrelated", "files": ["z.py"]}]
    fp = L.footprints({"FND-0001": "", "FND-0002": "", "FND-0003": "see a.py"}, commits)
    assert fp["FND-0001"] == {"a.py", "b.py"} and fp["FND-0002"] == {"a.py", "b.py"} and fp["FND-0003"] == {"a.py"}


def test_idf_overlap_drops_hub_terms_and_single_owner_terms_and_is_smoothed():
    sets = {f"T{i:02d}": {"hub_term"} for i in range(30)}
    sets["T00"].add("rare_shared")
    sets["T01"].add("rare_shared")
    sets["T02"].add("only_me")
    out = L.idf_overlap(sets)
    assert out[("T00", "T01")][1] == ["rare_shared"]
    assert 0.0 < out[("T00", "T01")][0] < 1.0                   # smoothing: one shared term is never a perfect match
    assert all("hub_term" not in v[1] for v in out.values())
    assert not any("T02" in k for k in out)


# ─── S4 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_tfidf_ranks_similar_text_above_dissimilar():
    docs = {
        "A": "share class currency header parser kiid class label currency resolution",
        "B": "kiid header parser resolves the share class currency from the class label",
        "C": "portfolio rotation cost hysteresis backtest turnover band",
        "D": "portfolio backtest turnover hysteresis band rotation experiment",
    }
    cos = L.tfidf_cosine(docs, min_cos=0.0)
    assert cos[("A", "B")] > cos.get(("A", "C"), 0.0)
    assert cos[("C", "D")] > cos.get(("B", "C"), 0.0)


# ─── candidates ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _s1(a, b, typ="RELATED"):
    return {(a, b): {"type": typ, "n": 1, "evidence": "e"}}


def test_regression_the_fnd0243_fnd0246_pair_is_proposed():
    docs = {"FND-0246": "KIID header class label. Linked: FND-0243 (investor currency).",
            "FND-0243": "Non-EUR share classes are scored on an assumed EUR currency."}
    s1 = L.signal_explicit(docs, {"FND-0243", "FND-0246"})
    cands = L.build_candidates(s1=s1, s2={}, s3={}, s4={}, existing=set(), scope={"FND-0243", "FND-0246"})
    assert [(c["from_id"], c["to_id"]) for c in cands] == [("FND-0246", "FND-0243")]
    assert cands[0]["signals"] == ["S1"]


def test_pairs_with_a_link_on_record_are_not_reproposed_in_either_direction():
    s1 = _s1("FND-0001", "FND-0002")
    assert L.build_candidates(s1=s1, s2={}, s3={}, s4={}, existing={("FND-0002", "FND-0001")}, scope=None) == []


def test_s2_alone_needs_corroboration_and_closed_closed_pairs_are_out_of_scope():
    s2 = {("FND-0001", "FND-0002"): (0.9, ["a.py"]), ("FND-0003", "FND-0004"): (0.9, ["b.py"])}
    s3 = {("FND-0003", "FND-0004"): (0.2, ["x_y_z"])}
    cands = L.build_candidates(s1={}, s2=s2, s3=s3, s4={}, existing=set(), scope={"FND-0001", "FND-0003"})
    assert [(c["from_id"], c["to_id"]) for c in cands] == [("FND-0003", "FND-0004")]
    assert L.build_candidates(s1={}, s2=s2, s3=s3, s4={}, existing=set(), scope={"FND-0006"}) == []


def test_non_explicit_candidates_are_capped_per_pending_ticket_but_stated_links_are_not():
    n = L.MAX_PER_TICKET + 3
    s4 = {("FND-0001", f"FND-10{i:02d}"): 0.9 - i * 0.01 for i in range(n)}
    s1 = {("FND-0001", f"FND-20{i:02d}"): {"type": "RELATED", "n": 1, "evidence": "e"} for i in range(n)}
    cands = L.build_candidates(s1=s1, s2={}, s3={}, s4=s4, existing=set(), scope={"FND-0001"})
    assert sum("S1" not in c["signals"] for c in cands) == L.MAX_PER_TICKET
    assert sum("S1" in c["signals"] for c in cands) == n


def test_clusters_are_connected_components():
    assert L.clusters([("A", "B"), ("B", "C"), ("X", "Y")]) == [["A", "B", "C"], ["X", "Y"]]


# ─── review file ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_decision_parsing():
    assert L.parse_decision("") is None
    assert L.parse_decision("Confirm") == ("CONFIRMED", None)
    assert L.parse_decision("reject") == ("REJECTED", None)
    assert L.parse_decision("retype:depends_on") == ("CONFIRMED", "DEPENDS_ON")
    for bad in ("maybe", "retype:BLOCKS"):
        with pytest.raises(ValueError):
            L.parse_decision(bad)


def _row(i, f="FND-0001", t="FND-0002", typ="OVERLAPS", decision="confirm", fs="OPEN", ts="OPEN"):
    return {"row": str(i), "from_id": f, "to_id": t, "type": typ, "evidence": "ev", "decision": decision,
            "from_status": fs, "to_status": ts, "absorbs": ""}


def test_staleness_flags_a_vanished_or_status_changed_ticket_but_not_a_ticket_closed_at_generation():
    rows = [_row(1), _row(2, t="FND-0003", ts="CLOSED"), _row(3, t="FND-0004"), _row(4, f="FND-0005")]
    live = {"FND-0001": {"status": "OPEN"}, "FND-0002": {"status": "OPEN"}, "FND-0003": {"status": "CLOSED"},
            "FND-0004": {"status": "CLOSED"}}                   # FND-0004 closed since; FND-0005 deleted
    flagged = L.stale_rows(rows, live)
    assert set(flagged) == {2, 3}
    assert "closed" in flagged[2].lower() or "CLOSED" in flagged[2]
    assert "no longer exists" in flagged[3]


def test_events_skip_flagged_rows_unless_forced_and_record_the_override():
    rows = [_row(1), _row(2, t="FND-0004", decision="retype:SPLIT_FROM"), _row(3, f="FND-0005", t="FND-0006", decision="reject"),
            _row(4, f="FND-0006", t="FND-0007", decision="")]
    flagged = {1: "FND-0004 changed status OPEN -> CLOSED"}
    ev, notes = L.build_events(rows, flagged, forced=set(), username="u", source="r.csv")
    assert [(e["from_id"], e["to_id"], e["status"]) for e in ev] == [("FND-0001", "FND-0002", "CONFIRMED"),
                                                                    ("FND-0005", "FND-0006", "REJECTED")]
    assert notes and notes[0].startswith("SKIPPED")
    ev, notes = L.build_events(rows, flagged, forced={("FND-0001", "FND-0004", "SPLIT_FROM")}, username="u", source="r.csv")
    forced = [e for e in ev if e["to_id"] == "FND-0004"][0]
    assert forced["link_type"] == "SPLIT_FROM" and "FORCED despite" in forced["evidence"]
    assert any(n.startswith("FORCED") for n in notes)


def test_apply_review_dry_run_confirm_and_archive(tmp_path, monkeypatch):
    path = tmp_path / "review_20261011_000000.csv"
    rows = [dict(_row(1), conf="0.9", signals="S1", from_update_ts="", to_update_ts="", note="")]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=L.REVIEW_COLS)
        w.writeheader()
        w.writerows(rows)
    path.with_suffix(".md").write_text("md", encoding="utf-8")
    recorded = []
    monkeypatch.setattr(L.bc, "list_tickets", lambda **kw: [{"action_point_id": i, "status": "OPEN"} for i in kw["ids"]])
    monkeypatch.setattr(L.bc, "record_link_events", lambda evs, **kw: recorded.extend(evs) or list(range(len(evs))))

    assert L.mode_apply_review(path, confirm=False, forced=set(), username="u") == 0
    assert recorded == [] and path.exists()                       # dry run writes nothing, moves nothing

    assert L.mode_apply_review(path, confirm=True, forced=set(), username="u") == 0
    assert len(recorded) == 1 and recorded[0]["status"] == "CONFIRMED"
    assert not path.exists() and (tmp_path / "archived" / path.name).exists()          # D-2: archived after a real apply

    again = tmp_path / path.name                                  # same file name re-created: refused (already archived)
    again.write_text("x", encoding="utf-8")
    assert L.mode_apply_review(again, confirm=True, forced=set(), username="u") == 2
