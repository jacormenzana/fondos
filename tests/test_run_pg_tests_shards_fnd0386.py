# tests/test_run_pg_tests_shards_fnd0386.py
# -*- coding: utf-8 -*-
"""FND-0386: the sharded full-suite runner of scripts/gov/delivery/run_pg_tests.py (pure parts: balancing, file collection, failed-file parsing, shard count).

R-7: no Docker, no database: the runner is loaded by path and only its pure functions run. What the concurrency does to the real suite is measured by running it (receipt), not here.
"""
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

spec = importlib.util.spec_from_file_location("run_pg_tests_shards", _ROOT / "scripts" / "gov" / "delivery" / "run_pg_tests.py")
rp = importlib.util.module_from_spec(spec)
sys.modules["run_pg_tests_shards"] = rp
spec.loader.exec_module(rp)


def test_the_balancing_is_even_deterministic_and_loses_no_item():
    w = {"a": 356.0, "b": 189.0, "c": 136.0, "d": 105.0, "e": 48.0, "f": 40.0, "g": 3.0, "h": 2.0}
    plan = rp.shard_plan(w, 3)
    assert sorted(x for s in plan for x in s) == sorted(w)                                       # every file runs, once
    sums = sorted(sum(w[x] for x in s) for s in plan)
    assert sums[-1] == 356.0 and sums[0] >= 240.0                                                # the heaviest file is the floor; no shard exceeds it and none is starved (523 s over 2 shards)
    assert plan == rp.shard_plan(dict(reversed(list(w.items()))), 3)                             # the insertion order does not matter


def test_one_shard_is_the_whole_suite_and_more_shards_than_items_leave_no_empty_shard():
    assert rp.shard_plan({"a": 1.0, "b": 2.0}, 1) == [["b", "a"]]
    assert len(rp.shard_plan({"a": 1.0, "b": 2.0}, 8)) == 2


def test_the_shard_count_leaves_a_cpu_and_stays_within_five():
    assert rp.auto_shards(6) == 5 and rp.auto_shards(4) == 3 and rp.auto_shards(2) == 1 and rp.auto_shards(1) == 1 and rp.auto_shards(32) == 5 and rp.auto_shards(None) == 1


def test_the_collected_files_are_the_four_default_runs_without_the_network_test():
    files = rp.collect_test_files(_ROOT)
    rels = {rr for _c, _r, rr in files}
    assert "tests/test_run_pg_tests_shards_fnd0386.py" in rels and any(r.startswith("proyecto1/tests/") for r in rels) and any(r.startswith("proyecto3/tests/") for r in rels)
    assert any(r.startswith("proyecto2/tests/") for r in rels) and not any(r.endswith("test_historia.py") for r in rels)
    p2 = [(c, r) for c, r, rr in files if rr.startswith("proyecto2/")]
    assert p2 and all(c.name == "proyecto2" and r.startswith("tests/") for c, r in p2)           # proyecto2 runs from its own directory (its own `src` package)


def test_every_collected_file_lands_in_exactly_one_shard():
    shards = rp.build_shards(_ROOT, 4, {})
    placed = [item for s in shards for item in s]
    assert len(placed) == len(set(placed)) == len(rp.collect_test_files(_ROOT))


def test_recorded_seconds_weigh_the_files_and_an_unknown_file_weighs_one_second():
    heavy = "tests/test_run_pg_tests_shards_fnd0386.py"
    shards = rp.build_shards(_ROOT, 2, {heavy: 500.0})
    first = shards[0]
    assert (_ROOT, heavy) in first and len(first) <= 2                                           # the recorded heavy file sits almost alone in its shard


def test_failed_files_come_from_the_failed_and_error_lines_in_order_without_duplicates():
    text = ("FAILED tests/test_a.py::test_x - assert 1\nFAILED tests/test_a.py::test_y\nERROR tests\\test_b.py::test_z\n"
            "FAILED proyecto2/tests/calc/test_c.py::TestC::test_w - boom\n12 passed, 3 failed in 4.1s\n")
    assert rp._failed_files(text) == ["tests/test_a.py", "tests/test_b.py", "proyecto2/tests/calc/test_c.py"]
    assert rp._failed_files("all good\n5 passed in 1s") == []


def test_the_recorded_per_file_seconds_are_read_when_present_and_ignored_when_missing(tmp_path):
    assert rp.load_durations(tmp_path) == {}
    (tmp_path / "out" / "audit").mkdir(parents=True)
    (tmp_path / "out" / "audit" / "test_durations.json").write_text('{"per_file_seconds": {"tests\\\\a.py": 3.5, "tests/b.py": 1}}', encoding="utf-8")
    assert rp.load_durations(tmp_path) == {"tests/a.py": 3.5, "tests/b.py": 1.0}
