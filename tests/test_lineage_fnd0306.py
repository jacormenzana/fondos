# tests/test_lineage_fnd0306.py
# -*- coding: utf-8 -*-
"""
FND-0306 slice 1: the metrics lineage fingerprint (shared/governance/lineage.py). Pure: no DB, no pipeline imports (R-7).

    python -m pytest tests/test_lineage_fnd0306.py -v
"""
import random

import pytest

from shared.governance import lineage as ln

RUN_A, RUN_B = "P2-20261108_095000-aaaaaa", "P2-20261112_194930-2984d0"


def _rows():
    return [("IE0001", "20261112", RUN_B, 40), ("IE0001", "20261112.20261019", RUN_B, 400),
            ("LU0002", "20261112", RUN_B, 38), ("LU0002", "20261112.20261019", RUN_B, 390),
            ("ES0003", "20261112", RUN_A, 30)]


def test_the_digest_is_identical_for_the_same_data_whatever_the_row_order():
    base = ln.fingerprint(_rows())
    for seed in range(5):
        shuffled = _rows()
        random.Random(seed).shuffle(shuffled)
        assert ln.fingerprint(shuffled) == base
    assert base.token() == f"v1:{base.sha256[:16]}" and len(base.sha256) == 64


def test_counts():
    fp = ln.fingerprint(_rows())
    assert (fp.funds, fp.pairs, fp.rows) == (3, 5, 898)


def test_a_rewrite_by_a_new_run_changes_the_digest():
    changed = _rows()
    changed[0] = ("IE0001", "20261112", "P2-20261113_090000-bbbbbb", 40)
    assert ln.fingerprint(changed).sha256 != ln.fingerprint(_rows()).sha256


def test_a_deletion_that_leaves_the_other_stamps_alone_changes_the_digest():
    """replace_beta_set / delete_unproduced remove rows without re-stamping the rest: only the row count sees it."""
    fewer = _rows()
    fewer[1] = ("IE0001", "20261112.20261019", RUN_B, 399)
    assert ln.fingerprint(fewer).sha256 != ln.fingerprint(_rows()).sha256


def test_a_fund_outside_the_scope_does_not_move_the_scoped_digest():
    scope = {"IE0001", "LU0002"}
    before = ln.fingerprint([r for r in _rows() if r[0] in scope])
    other_changed = _rows()
    other_changed[4] = ("ES0003", "20261113", "P2-20261113_090000-bbbbbb", 31)
    after = ln.fingerprint([r for r in other_changed if r[0] in scope])
    assert before == after


def test_unstamped_rows_are_counted_and_listed_not_hidden():
    rows = _rows() + [("FR0004", ln.UNSTAMPED_SENTINEL, ln.UNSTAMPED_SENTINEL, 3), ("DE0005", None, None, 2)]
    fp = ln.fingerprint(rows, max_unstamped_share=0.05)
    assert (fp.unstamped_rows, fp.unstamped_funds) == (5, 2) and fp.unstamped_isins == ("DE0005", "FR0004")
    assert not fp.ambiguous                                          # 5 of 903 rows


def test_too_many_unstamped_rows_is_ambiguous_not_hashed_over():
    rows = _rows() + [("FR0004", ln.UNSTAMPED_SENTINEL, ln.UNSTAMPED_SENTINEL, 100)]
    fp = ln.fingerprint(rows)                                        # 100 of 998 = 10 % > 2 %
    assert fp.ambiguous
    state, detail = ln.verdict(fp, fp)
    assert state == ln.AMBIGUOUS and "100 of 998 rows" in detail


def test_the_listing_is_capped_but_the_count_is_exact():
    rows = [(f"XS{i:04d}", None, None, 1) for i in range(30)] + [("IE0001", "20261112", RUN_B, 5000)]
    fp = ln.fingerprint(rows)
    assert fp.unstamped_funds == 30 and len(fp.unstamped_isins) == ln.LISTED_UNSTAMPED_FUNDS


def test_no_rows_at_all_is_ambiguous():
    fp = ln.fingerprint([])
    assert fp.ambiguous and (fp.funds, fp.rows) == (0, 0)
    assert ln.verdict(fp, fp)[0] == ln.AMBIGUOUS


def test_verdict_match_and_mismatch():
    a, b = ln.fingerprint(_rows()), ln.fingerprint(_rows())
    assert ln.verdict(a, b)[0] == ln.MATCH
    moved = _rows()
    moved[0] = ("IE0001", "20261112", "P2-20261113_090000-bbbbbb", 41)
    state, detail = ln.verdict(a, ln.fingerprint(moved))
    assert state == ln.MISMATCH and "rows 898 -> 899" in detail


def test_a_missing_stamp_is_unknown_never_a_pass():
    cur = ln.fingerprint(_rows())
    assert ln.verdict(None, cur)[0] == ln.UNKNOWN and ln.verdict(cur, None)[0] == ln.UNKNOWN


def test_an_older_definition_is_unknown():
    cur = ln.fingerprint(_rows())
    old = ln.LineageFingerprint("v0", cur.sha256, cur.funds, cur.pairs, cur.rows, 0, 0, (), False)
    state, detail = ln.verdict(old, cur)
    assert state == ln.UNKNOWN and "v0 -> v1" in detail


def test_the_stamp_survives_a_round_trip_through_json_types():
    import json
    fp = ln.fingerprint(_rows() + [("FR0004", None, None, 1)], max_unstamped_share=0.5)
    again = ln.LineageFingerprint.from_dict(json.loads(json.dumps(fp.as_dict())))
    assert again == fp and ln.verdict(fp, again)[0] == ln.MATCH


@pytest.mark.parametrize("bad", [None, {}, {"version": "v1"}, {"version": "v1", "sha256": "x", "funds": "n", "pairs": 1, "rows": 1,
                                                                "unstamped_rows": 0, "unstamped_funds": 0, "unstamped_isins": [], "ambiguous": False}])
def test_anything_that_is_not_a_complete_stamp_is_refused(bad):
    with pytest.raises(ValueError):
        ln.LineageFingerprint.from_dict(bad)


def test_the_loader_errors_propagate_as_incomplete_evidence():
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("lineage query failed")
    with pytest.raises(RuntimeError):
        ln.compute(_Boom())


def test_the_scope_loader_sends_a_sorted_distinct_list():
    seen = {}

    class _Conn:
        def execute(self, sql, params=()):
            seen["sql"], seen["params"] = sql, params

            class _R:
                def fetchall(self_inner):
                    return []
            return _R()
    ln.load_lineage_rows(_Conn(), ["LU0002", "IE0001", "IE0001"])
    assert seen["params"] == (["IE0001", "LU0002"],) and "ANY(%s)" in seen["sql"]
