# proyecto3/src/baseline_shadow.py
# -*- coding: utf-8 -*-
"""FND-0445 -- the BASELINE shadow: arm L (the live scorer with L4 off, the builder with no incumbents, i.e. band 0) recorded at every real build, so that R24, R36 and the revert trigger keep their
baseline once AC (or B) is live (doc/backlog/PKG_M_ac_adoption.md section 2; PREREG_P3_7 section 14, "After adoption").

After a real build has persisted its DRAFT, the scores are recomputed IN MEMORY with the levers of L, the same builder selects with no incumbents, and ONE append-only row is written to `ingestion_log`
(step P3_BASELINE_SHADOW), in the shape of the hysteresis shadow. Streams: `one_step` (turnover from PRODUCTION) and `chain` (from the previous baseline row; the first row starts from the DRAFT of the
latest AC row of an earlier build, which is L, else from PRODUCTION). The two hold the same funds: a selection without incumbents does not depend on its history. Every row records the live flags of its
build, so the reader finds the adoption month from the rows alone.

Identity check: while the live arm IS L (hysteresis OFF and L4 OFF) the baseline must equal the live DRAFT (funds, weights, scores). A difference writes the row, marked MISMATCH, plus one
P3_BASELINE_SHADOW_ERROR row (an L-against-L comparison, outside the reading ban of PREREG_P3_7 section 13). Funds with the same score are ordered as the live query ordered them (`align_ties`): that
order is arbitrary and is not part of arm L. After adoption nothing is compared.

It never changes what the DRAFT holds and never changes the build's return code: any failure becomes one P3_BASELINE_SHADOW_ERROR row (or a printed warning) and the build goes on.

The module is pure at its top (pandas only: R-7, no database, no `pipeline` / `portfolio_builder` / `fund_scorer` / `shared` import): the builder, the connection, the scorer functions and the live flags
are arguments.
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json

import pandas as pd

from proyecto3.src import fee_penalty_shadow as fp
from proyecto3.src import hysteresis_shadow as hs

STEP = "P3_BASELINE_SHADOW"
STEP_ERROR = "P3_BASELINE_SHADOW_ERROR"
ARM = "L"
BAND = 0.0
MATCH, MISMATCH, NOT_APPLICABLE = "MATCH", "MISMATCH", "NOT_APPLICABLE"
WEIGHT_TOL = 1e-6
SCORE_TOL = 5e-5                                   # scores are stored with 4 decimals
ShadowSkip = hs.ShadowSkip


# --------------------------------------------------------------------------------------------------------------------------------------------------
# pure functions
# --------------------------------------------------------------------------------------------------------------------------------------------------
def baseline_levers(base):
    """The levers of arm L: the live configuration `base` (a `ranking_levers.LeverConfig`) with the one-off fee penalty (L4) OFF."""
    return dataclasses.replace(base, l4_h_years=None)


def live_is_baseline(flags: dict) -> bool:
    """True while the live build IS arm L (no incumbent bonus and no fee penalty): only then can the baseline be compared with the DRAFT."""
    return not flags["PORTFOLIO_HYSTERESIS_ENABLED"] and not flags["P3_L4_FEE_PENALTY_ENABLED"]


def no_incumbents():
    """A previous portfolio with no fund. Passed instead of None so `PortfolioBuilder.build` never loads PRODUCTION when PORTFOLIO_HYSTERESIS_ENABLED is ON."""
    return hs._as_incumbent_portfolio([], "baseline-no-incumbents")


def align_ties(candidates: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """`candidates` ordered by (block, score DESC), funds with the SAME score in the order of `live` (the live query's rows, per block); a fund the live pool does not hold goes after them, by ISIN."""
    rank = {}
    for pos, (block, isin) in enumerate(zip(live["block"], live["isin"])):
        rank.setdefault((block, isin), pos)
    keyed = candidates.assign(_rank=[rank.get(k, len(live)) for k in zip(candidates["block"], candidates["isin"])])
    keyed = keyed.sort_values(["block", "score_total", "_rank", "isin"], ascending=[True, False, True, True], kind="mergesort")
    return keyed.drop(columns="_rank").reset_index(drop=True)


def identity_diff(live_rows: list, base_rows: list) -> "dict | None":
    """None when the two portfolios (`hysteresis_shadow.fund_rows`) hold the same funds with the same weights and scores; else what differs, as 'subportfolio:ISIN' lists."""
    live = {(str(r.get("subportfolio")), r["isin"]): r for r in live_rows}
    base = {(str(r.get("subportfolio")), r["isin"]): r for r in base_rows}

    def differs(a, b):
        if abs(float(a["weight"]) - float(b["weight"])) > WEIGHT_TOL:
            return True
        sa, sb = a.get("score"), b.get("score")
        return (sa is None) != (sb is None) or (sa is not None and abs(float(sa) - float(sb)) > SCORE_TOL)

    out = {"only_live": sorted(f"{s}:{i}" for s, i in set(live) - set(base)), "only_baseline": sorted(f"{s}:{i}" for s, i in set(base) - set(live)),
           "changed": sorted(f"{s}:{i}" for s, i in set(live) & set(base) if differs(live[(s, i)], base[(s, i)]))}
    return out if any(out.values()) else None


def summary_line(rows: list) -> str:
    """ONE line for the build output: month and row counts and the identity verdict of the latest row. No turnover, cost or selection value."""
    if not rows:
        return "baseline shadow: 0 months accumulated"
    state = rows[-1].get("identity_check")
    text = "not applicable (a candidate is adopted)" if state == NOT_APPLICABLE else str(state)
    return f"baseline shadow ({ARM}: band {BAND:.2f}, no fee penalty): {hs.counts_text(rows)}; identity check {text}"


# --------------------------------------------------------------------------------------------------------------------------------------------------
# I/O glue (the connection, the builder and the scorer functions are arguments)
# --------------------------------------------------------------------------------------------------------------------------------------------------
def read_rows(conn, limit: int = 120) -> list:
    """The stored rows of THIS stream, oldest first (a row that does not parse is skipped)."""
    return hs.read_rows(conn, limit, step=STEP)


def rescore_candidates(conn, regime: str, *, load_metrics, score_from_df, base_levers) -> "tuple[pd.DataFrame, dict]":
    """The candidate pool of arm L: `fee_penalty_shadow.rescore_with_levers` with L4 off. Raises ShadowSkip when another ranking lever is ON (the arm is the ranking without any of them)."""
    levers = baseline_levers(base_levers)
    if fp.other_levers_on(levers):
        raise ShadowSkip("another ranking lever (L1-L3, L5, L6) is ON: the baseline arm is defined over the ranking without levers, so the stream does not mix arms")
    candidates, scores, _df = fp.rescore_with_levers(conn, regime, levers, load_metrics=load_metrics, score_from_df=score_from_df)
    return candidates, {"n_scored": int(len(scores)) if scores is not None else 0, "n_candidates": int(len(candidates))}


def _last_draft_before(conn, scenario_id: str) -> "tuple[list, str] | tuple[None, None]":
    """(funds of the DRAFT, its scenario id) of the latest AC row of ANOTHER build: its `off` is the live arm of that build, which is L (the AC stream is switched off at adoption)."""
    for row in reversed(hs.read_rows(conn, step=fp.STEP)):
        if row.get("scenario_id") != scenario_id and (row.get("off") or {}).get("weights"):
            return row["off"]["weights"], row.get("scenario_id")
    return None, None


def run_shadow(conn, builder, regime_result, scenario_id: str, live_portfolio, *, live_flags: dict, rescore, live_order=None, cost_fn=None, identity=None,
               rotation_gate_enabled: bool = False, calc_versions: dict | None = None) -> "tuple[dict, dict | None]":
    """Build arm L in memory (dry_run=True: nothing is persisted) and return (row, identity difference or None). `rescore()` -> (candidates, info); `live_order()` -> the live candidate rows, read only
    while the live arm is L. Raises ShadowSkip when there is nothing to compare."""
    production = builder.load_previous(exclude_scenario_id=scenario_id)
    if production is None:
        raise ShadowSkip("no PRODUCTION scenario: the live arm has no incumbents to compare with")
    off_rows = hs._portfolio_rows(live_portfolio)
    if not off_rows:
        raise ShadowSkip("the live build has no funds (empty candidates)")
    production_rows = hs._portfolio_rows(production)
    candidates, info = rescore()
    if candidates is None or candidates.empty:
        raise ShadowSkip("no eligible candidates for the baseline arm")
    comparable = live_is_baseline(live_flags)
    if comparable and live_order is not None:
        candidates = align_ties(candidates, live_order())
    if calc_versions is None:
        calc_versions = hs._calc_versions(conn, [r["isin"] for r in off_rows])

    last = hs._last_row(conn, step=STEP)
    if last:
        chain_base_rows, chain_base_scenario = last["chain"]["weights"], last["scenario_id"]
    else:
        chain_base_rows, chain_base_scenario = _last_draft_before(conn, scenario_id)
    if not chain_base_rows:
        chain_base_rows, chain_base_scenario = production_rows, production.scenario_id

    with contextlib.redirect_stdout(io.StringIO()):                          # the builder prints its sub-portfolio lines: the log shows only the live arm
        built = builder.build(regime_result, scenario_id=scenario_id, dry_run=True, previous_portfolio=no_incumbents(), candidates=candidates)
    base_rows = hs._portfolio_rows(built)
    row = hs.build_row(scenario_id=scenario_id, band=BAND, off_rows=off_rows, production_rows=production_rows, one_step_rows=base_rows, chain_rows=base_rows,
                       chain_base_rows=chain_base_rows, chain_base_scenario=chain_base_scenario, calc_versions=calc_versions, identity=identity,
                       rotation_gate_enabled=rotation_gate_enabled, cost_fn=cost_fn)
    diff = identity_diff(off_rows, base_rows) if comparable else None
    row.update({"baseline_scenario": production.scenario_id, "arm": ARM, "live_flags": dict(live_flags), "n_scored": int(info.get("n_scored", 0)),
                "n_candidates": int(info.get("n_candidates", 0)), "identity_check": (MISMATCH if diff else MATCH) if comparable else NOT_APPLICABLE})
    return row, diff


def shadow_after_build(conn, builder, regime_result, scenario_id: str, live_portfolio, *, enabled: bool, live_flags: dict, rescore, live_order=None, cost_fn=None, identity=None,
                       rotation_gate_enabled: bool = False, calc_versions: dict | None = None) -> str | None:
    """The fail-soft hook: NEVER raises. Returns the line to print (or None when the switch is OFF). A failure is one P3_BASELINE_SHADOW_ERROR row (and, if even that cannot be written, a printed
    warning). An identity MISMATCH keeps the SHADOW row (so the month counts as captured and the scheduled build does not rebuild every day) and adds one ERROR row."""
    if not enabled:
        return None
    try:
        row, diff = run_shadow(conn, builder, regime_result, scenario_id, live_portfolio, live_flags=live_flags, rescore=rescore, live_order=live_order, cost_fn=cost_fn, identity=identity,
                               rotation_gate_enabled=rotation_gate_enabled, calc_versions=calc_versions)
        hs._write(conn, STEP, hs.STATUS, json.dumps(row, separators=(",", ":"), default=str))
        if diff:
            hs._write(conn, STEP_ERROR, hs.STATUS_ERROR, json.dumps({"v": hs.ROW_VERSION, "scenario_id": scenario_id, "arm": ARM,
                                                                     "reason": "identity check: the baseline arm differs from the live DRAFT while the live arm is L", **diff}))
        return summary_line(read_rows(conn))
    except Exception as exc:                                                 # noqa: BLE001 -- the shadow measures, it never refuses
        reason = f"{type(exc).__name__}: {str(exc)[:300]}"
        try:
            conn.rollback()
        except Exception:                                                    # noqa: BLE001
            pass
        try:
            hs._write(conn, STEP_ERROR, hs.STATUS_ERROR, json.dumps({"v": hs.ROW_VERSION, "scenario_id": scenario_id, "arm": ARM, "reason": reason}))
        except Exception:                                                    # noqa: BLE001
            try:
                conn.rollback()
            except Exception:                                                # noqa: BLE001
                pass
            return f"AVISO: baseline shadow failed and its error row could not be written ({reason})"
        return f"AVISO: baseline shadow skipped ({reason}); one {STEP_ERROR} row written"
