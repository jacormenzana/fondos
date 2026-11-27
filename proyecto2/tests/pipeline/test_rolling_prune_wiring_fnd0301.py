# proyecto2/tests/pipeline/test_rolling_prune_wiring_fnd0301.py
# -*- coding: utf-8 -*-
"""
FND-0301: the rolling block of run_pipeline replaces the fund's history -- prune_timeseries runs on the fund's computed rows BEFORE the upsert,
in the same per-fund transaction. The behaviour of the writer is tested on Postgres (tests/writers/test_metrics_writer_pg.py); this pins the
WIRING without importing run_pipeline (R-7): the call exists, receives (conn, isin, roll_rows, dry_run), comes first, and its total is reported.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "pipeline" / "run_pipeline.py"


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


def test_prune_precedes_the_rolling_upsert_with_the_funds_rows():
    text = SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    prune, write = _calls(tree, "_prune_timeseries"), _calls(tree, "_write_timeseries")
    assert len(prune) == 1 and len(write) == 1
    p, w = prune[0], write[0]
    assert p.lineno < w.lineno                                                              # prune first
    assert [ast.unparse(a) for a in p.args] == ["conn", "isin", "roll_rows", "dry_run"]    # this fund's computed rows, honours --dry-run
    assert [ast.unparse(a) for a in w.args][:3] == ["conn", "roll_rows", "dry_run"]         # the upsert writes the same rows


def test_prune_total_is_reported_in_the_run_summary_lines():
    text = SRC.read_text(encoding="utf-8")
    assert text.count("ts_pruned={ts_pruned_total}") == 2          # [RUN END] log line and the RUN_SUMMARY row (extra keys are tolerated)
    assert "ts_pruned_total += ts_pruned" in text


def test_the_rejected_windows_flow_from_the_computation_to_the_pruner():
    """FND-0443: the set compute_rolling_rows fills is the one handed to the pruner."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    (compute,), (prune,) = _calls(tree, "compute_rolling_rows"), _calls(tree, "_prune_timeseries")
    given = {k.arg: ast.unparse(k.value) for k in compute.keywords}
    taken = {k.arg: ast.unparse(k.value) for k in prune.keywords}
    assert given.get("uncovered_windows") and taken.get("uncovered_windows") == given["uncovered_windows"]


def test_with_the_switch_off_nothing_extra_is_pruned():
    """FND-0443: ROLLING_UNCOVERED_PRUNE_ENABLED ships OFF; the rejected windows are emptied before the prune unless the owner switched it on."""
    import re
    text = SRC.read_text(encoding="utf-8")
    assert re.search(r"if not shared_config\.ROLLING_UNCOVERED_PRUNE_ENABLED:[^\n]*\n\s+_uncovered\.clear\(\)\n\s+ts_pruned = _prune_timeseries\(", text)
    config = (SRC.parents[3] / "shared" / "config.py").read_text(encoding="utf-8")
    assert "\nROLLING_UNCOVERED_PRUNE_ENABLED: bool = False" in config and '"ROLLING_UNCOVERED_PRUNE_ENABLED")' in config
