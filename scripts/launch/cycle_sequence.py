# scripts/launch/cycle_sequence.py
# -*- coding: utf-8 -*-
"""The master cycle sequence, defined ONCE (FND-0309): every *_runCycle orchestrator is a SUBSET of it.

The master is what P1P2P3_runCycle.bat does end to end. Each phase says which projects it belongs to; an orchestrator that covers a set of
projects must run every phase whose projects intersect that set, in the master order. Nothing here runs anything: tests/test_cycle_sequence_e2e.py
runs each orchestrator in the simulated tool tree, reads the tool trace, maps it to these phases with `observed()` and compares it with `expected()`.
A phase added to the master but missing from a partial orchestrator (the 2026-10-24 case: P2P3_runCycle never refreshed macro + NAV) fails that test.

Pure module: no I/O, no project imports (R-7).
"""
from __future__ import annotations

from typing import Iterable, Mapping

P1, P2, P3 = "P1", "P2", "P3"

# (phase id, projects it belongs to) in MASTER ORDER. POST_P2 is placed by expected(): right after P2_CALC when P3 is not in the flow, after the
# DRAFT when it is (the same run is then the drift and the promotion evidence).
PHASES: tuple = (
    ("PRE_P2", frozenset({P2})),             # frozen baseline audit of the metrics, before anything writes (RC 107 gate)
    ("PRE_P1", frozenset({P1})),             # frozen baseline audit of costs + benchmarks B1-B7
    ("BETA_SNAPSHOT", frozenset({P2})),      # macro betas before the recompute
    ("HARVEST", frozenset({P1})),
    ("BENCH", frozenset({P1})),              # benchmark refresh
    ("P1_PIPELINE", frozenset({P1})),        # mark_stale + classification + families + export
    ("POST_P1", frozenset({P1})),            # P1 audit with drift, right after the last P1 writer
    ("DISCOVER", frozenset({P2})),           # macro (BCE, FRED, Eurostat, INE) + NAV discover/load
    ("P2_CALC", frozenset({P2})),            # P2 indicators + export
    ("POST_P2", frozenset({P2, P3})),        # position depends on P3, see expected()
    ("BETA_COMPARE", frozenset({P2})),
    ("P3_FRESHNESS", frozenset({P2})),       # would P3 accept the data (informational)
    ("CYCLE_REPORT", frozenset({P1, P2})),   # the P1+P2 cycle report ([ATENCION] lines; informational)
    ("P3_BUILD", frozenset({P3})),
    ("P3_REPORT", frozenset({P3})),
)
ORDER = tuple(p for p, _ in PHASES)
SCOPES_OF = dict(PHASES)

# Which projects each orchestrator covers.
ORCHESTRATORS: Mapping[str, frozenset] = {
    "P1_runCycle": frozenset({P1}),
    "P2_runCycle": frozenset({P2}),
    "P3_runCycle": frozenset({P3}),
    "P1P2_runCycle": frozenset({P1, P2}),
    "P2P3_runCycle": frozenset({P2, P3}),
    "P1P2P3_runCycle": frozenset({P1, P2, P3}),
}

# The options of each project's phases. An orchestrator that covers a project must ACCEPT every option of that project (the master forwards them):
# a partial cycle that cannot, say, --force a P2 recompute or build a P3 --scenario is an incomplete subset even when its phases are right.
PROJECT_OPTIONS: Mapping[str, tuple] = {
    P1: ("--no-harvest", "--harvest-sync", "--harvest-retire", "--harvest-limit", "--harvest-max-drop-pct", "--benchmarks-load", "--no-export"),
    P2: ("--skip-macro", "--workers", "--force", "--no-export"),
    P3: ("--scenario", "--report-dir", "--p3-dry-run", "--allow-stale"),
}
COMMON_OPTIONS = ("--skip-preflight",)


def required_options(projects: Iterable[str]) -> list:
    """Every option an orchestrator covering `projects` must accept (sorted, no repeats)."""
    out = set(COMMON_OPTIONS)
    for p in projects:
        out.update(PROJECT_OPTIONS[p])
    return sorted(out)


_HARVEST = {"p1_db_harvest", "harvest_gate", "p1_kiid_sync"}
_P1_PIPELINE = {"mark_stale", "run_block", "fund_family_builder", "export_p1"}
_DISCOVER = {"macro_discovery", "nav_discovery"}
_P2_CALC = {"run_pipeline", "export_metrics"}


def expected(projects: Iterable[str]) -> list:
    """The phases an orchestrator covering `projects` must run, in order."""
    scope = set(projects)
    has_p3 = P3 in scope
    out = []
    for pid in ORDER:
        if not (SCOPES_OF[pid] & scope):
            continue
        if pid == "POST_P2":
            continue                                     # placed below
        out.append(pid)
        if pid == "P2_CALC" and not has_p3 and (SCOPES_OF["POST_P2"] & scope):
            out.append("POST_P2")                        # no P3 in the flow: the POST audit follows the last P2 writer
        if pid == "P3_BUILD" and has_p3:
            out.append("POST_P2")                        # P3 in the flow: the POST audit follows the DRAFT
    return out


def phase_of(tool: str, args: list) -> str | None:
    """The master phase a traced tool call belongs to; None = a helper that belongs to the previous phase (the benchmark audit);
    'UNKNOWN:<tool>' for a tool this module does not know (a new tool must be classified here, never silently ignored)."""
    if tool == "run_statistical_audit":
        domain = args[args.index("--domain") + 1] if "--domain" in args else ""
        run_id = args[args.index("--run-id") + 1] if "--run-id" in args else ""
        kind = "PRE" if run_id.startswith("pre_") else "POST" if run_id.startswith("post_") else ""
        suffix = {"p2": "P2", "costs": "P1"}.get(domain, "")
        return f"{kind}_{suffix}" if kind and suffix else f"UNKNOWN:{tool}"
    if tool == "audit_benchmark_consistency":
        return None
    if tool == "beta_shift_audit":
        return "BETA_SNAPSHOT" if "--snapshot" in args else "BETA_COMPARE" if "--compare" in args else f"UNKNOWN:{tool}"
    if tool in _HARVEST:
        return "HARVEST"
    if tool == "benchmark_loader":
        return "BENCH"
    if tool in _P1_PIPELINE:
        return "P1_PIPELINE"
    if tool in _DISCOVER:
        return "DISCOVER"
    if tool in _P2_CALC:
        return "P2_CALC"
    return {"p3_freshness_check": "P3_FRESHNESS", "p1p2_cycle_report": "CYCLE_REPORT", "p3_build_portfolio": "P3_BUILD",
            "generate_report": "P3_REPORT"}.get(tool, f"UNKNOWN:{tool}")


def observed(trace_lines: Iterable[str]) -> list:
    """Phase ids in the order a trace shows them: consecutive repeats collapse and a phase that comes back later (the blocking beta gate repeats
    BETA_COMPARE before P3) is kept at its FIRST position."""
    seen, out = set(), []
    for line in trace_lines:
        parts = line.split()
        if not parts:
            continue
        pid = phase_of(parts[0], parts[1:])
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
    return out
