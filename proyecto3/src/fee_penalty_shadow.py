# proyecto3/src/fee_penalty_shadow.py
# -*- coding: utf-8 -*-
"""P3-7 -- monthly forward SHADOW of the AC arm: hysteresis band + one-off fee penalty (lever L4 at H = 7 months).

Decision of record: the owner, 2026-11-22 (doc/audit/P3_7_TRIALS_REPORT.md, doc/decisiones/PREREG_P3_7.md). The stream mirrors the FND-0158 hysteresis shadow
(`hysteresis_shadow`, design in doc/decisiones/DESIGN_FND-0158_hysteresis_shadow.md) and reuses its pure functions and its row shape. After a real build has persisted its DRAFT
(the live arm: no incumbents, no penalty), the scores are recomputed IN MEMORY with L4 switched on at H (`rescore_candidates`: the live loader, the live scorer, nothing persisted),
the same selection runs again with incumbents on those scores, and ONE append-only row is written to `ingestion_log` (step P3_FEE_PENALTY_SHADOW). The row is dated by `created_at`,
so the stream is blind by construction: a row exists before its outcome is known. It never changes what the DRAFT holds and never changes the build's return code: any failure becomes one
P3_FEE_PENALTY_SHADOW_ERROR row (or a printed warning) and the build goes on. The live flags (`P3_L4_FEE_PENALTY_ENABLED`, `PORTFOLIO_HYSTERESIS_ENABLED`) are never read here and stay OFF:
this module only measures.

Two streams per row, as in the hysteresis shadow (band = the live band): `one_step` (incumbents = the PRODUCTION funds: the exact diff and turnover of the next promotion) and `chain`
(incumbents = the funds of the previous AC row's portfolio, its own history; the first month starts from PRODUCTION). The band-only comparator is the existing hysteresis stream; the live arm
(`off`) is the DRAFT just built. Cost convention = the PIT backtest's (traded notional x 25 bp), exactly `hysteresis_shadow`'s.

The arm is "the live ranking levers + L4 at H": when another lever (L1-L3) is ON the stream refuses to mix arms (a ShadowSkip, written as an ERROR row). The class choice (arm B) is retired and
never part of AC; `score_from_df` is called with the live switches, so the shadow differs from the live build by the penalty and the band only.

The top of the module is pure (pandas only: R-7, no database, no `pipeline` / `portfolio_builder` / `fund_scorer` import): the scorer functions and the builder are arguments.
"""
from __future__ import annotations

import contextlib
import dataclasses
import io
import json

import pandas as pd

from proyecto3.src import hysteresis_shadow as hs

STEP = "P3_FEE_PENALTY_SHADOW"
STEP_ERROR = "P3_FEE_PENALTY_SHADOW_ERROR"
ARM = "AC"
CANDIDATE_COLUMNS = ["block", "isin", "score_total", "fund_name", "fund_nature", "management_company", "fund_family_id"]   # = score_candidates.CANDIDATE_COLUMNS (a test pins it)
ShadowSkip = hs.ShadowSkip


# --------------------------------------------------------------------------------------------------------------------------------------------------
# pure functions
# --------------------------------------------------------------------------------------------------------------------------------------------------
def shadow_levers(base, h_years: float):
    """The levers of the AC arm: the live configuration `base` (a `ranking_levers.LeverConfig`) with L4 at `h_years`."""
    return dataclasses.replace(base, l4_h_years=float(h_years))


def other_levers_on(base) -> bool:
    """True when a lever other than L4 is ON in `base`: the AC arm is defined over the ranking with L1-L3 OFF, so the stream does not run in that case."""
    return any(v is not None for v in (base.l1_q, base.l2_z0, base.l3_k, base.l5_nature_k, base.l6_chase_f))


def candidates_from_scores(scores: pd.DataFrame, management_company: dict) -> pd.DataFrame:
    """The scorer's result (one row per fund and sub-portfolio) -> the candidate frame the builder reads: eligible rows with a positive final score, ordered like the live query
    (block, score DESC; the ISIN breaks a tie so a run is reproducible). `management_company` = {isin: company} (a fund missing from it gets None)."""
    if scores is None or scores.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    s = scores[scores["eligible"].astype(bool) & (scores["score_final"].astype(float) > 0)]
    out = pd.DataFrame({
        "block": s["subportfolio"].to_numpy(), "isin": s["isin"].to_numpy(), "score_total": s["score_final"].astype(float).to_numpy(),
        "fund_name": s["fund_name"].to_numpy(), "fund_nature": s["fund_nature"].to_numpy(),
        "management_company": [management_company.get(i) for i in s["isin"]],
        "fund_family_id": s["fund_family_id"].to_numpy() if "fund_family_id" in s.columns else [None] * len(s)})
    out = out.sort_values(["block", "score_total", "isin"], ascending=[True, False, True], kind="mergesort").reset_index(drop=True)
    return out[CANDIDATE_COLUMNS]


def build_row(*, base_row: dict, h_years: float, info: dict) -> dict:
    """The hysteresis row (`hysteresis_shadow.build_row`) plus what is specific to this stream."""
    row = dict(base_row)
    row.update({"arm": ARM, "l4_h_years": float(h_years), "n_scored": int(info.get("n_scored", 0)), "n_candidates": int(info.get("n_candidates", 0)),
                "n_fee_unknown": info.get("n_fee_unknown")})
    return row


def summary_line(rows: list, show_comparisons: bool = False) -> str:
    """ONE line for the build output. By default ONLY the month and row counts: PREREG_P3_7 section 13 says turnover, cost and selection-difference fields are not
    displayed before the first review (owner order 2026-11-27). `show_comparisons` (shared.config.P3_SHADOW_COMPARISON_DISPLAY_ENABLED, switched on at the review)
    adds mean one-way turnover of the live arm and of AC (one-step), mean AC cost drag and mean funds whose fee was unknown. `rows` = parsed rows, oldest first."""
    if not rows:
        return "fee-penalty shadow: 0 months accumulated"
    head = f"fee-penalty shadow ({ARM}: band {rows[-1]['band']:.2f} + penalty H {rows[-1]['l4_h_years']:.2f} y, live arm OFF): {hs.counts_text(rows)}"
    if not show_comparisons:
        return f"{head}; {hs.WITHHELD}"
    off_t = hs._mean([r["off"]["one_way_turnover"] for r in rows])
    ac_t = hs._mean([r["one_step"]["one_way_turnover"] for r in rows])
    drag = hs._mean([r["one_step"]["cost_drag"] for r in rows])
    unk = hs._mean([float(r["n_fee_unknown"]) if r.get("n_fee_unknown") is not None else None for r in rows])
    return (f"{head}; mean one-way turnover OFF {off_t:.1%} / {ARM} {ac_t:.1%}; "
            f"mean {ARM} cost drag {drag:.2%}" + (f"; mean funds with an unknown fee {unk:.1f}" if unk is not None else ""))


# --------------------------------------------------------------------------------------------------------------------------------------------------
# I/O glue (the connection, the builder and the scorer functions are arguments)
# --------------------------------------------------------------------------------------------------------------------------------------------------
def read_rows(conn, limit: int = 120) -> list:
    """The stored rows of THIS stream, oldest first (a row that does not parse is skipped)."""
    return hs.read_rows(conn, limit, step=STEP)


def rescore_candidates(conn, regime: str, h_years: float, *, load_metrics, score_from_df, base_levers, count_unknown_fees=None) -> "tuple[pd.DataFrame, dict]":
    """The AC arm's candidate pool: the live metrics frame (`load_metrics(conn, regime=...)`), scored by the live scorer (`score_from_df(df, regime, verbose=False, levers=...)`) with the live
    levers plus L4 at `h_years`, NOTHING persisted. `count_unknown_fees(df, h_years)` -> (_, n) is `ranking_levers.l4_penalty` (information only: the funds whose one-off fee is unknown, which take
    the median penalty of their cell). Returns (candidates, info). Raises ShadowSkip when another lever is ON or there are no metrics."""
    if other_levers_on(base_levers):
        raise ShadowSkip("another ranking lever (L1-L3) is ON: the AC arm is defined over the ranking with only L4 added, so the stream does not mix arms")
    with contextlib.redirect_stdout(io.StringIO()):                          # the loader and the scorer print: the build log shows only the live arm
        df = load_metrics(conn, regime=regime)
        if df is None or df.empty:
            raise ShadowSkip("no metrics to score: the penalty arm has no candidates")
        scores = score_from_df(df, regime, verbose=False, levers=shadow_levers(base_levers, h_years))
    unknown = None
    if count_unknown_fees is not None:
        try:
            unknown = int(count_unknown_fees(df, float(h_years))[1])
        except Exception:                                                     # noqa: BLE001 -- information only; a missing count never loses the row
            unknown = None
    isins = sorted({str(i) for i in scores["isin"]}) if scores is not None and not scores.empty else []
    mgr: dict = {}
    if isins:
        mgr = {r[0]: r[1] for r in conn.execute("SELECT ISIN, Management_Company FROM fund_master WHERE ISIN = ANY(%s)", (isins,)).fetchall()}
    cands = candidates_from_scores(scores, mgr)
    return cands, {"n_scored": int(len(scores)) if scores is not None else 0, "n_candidates": int(len(cands)), "n_fee_unknown": unknown}


def run_shadow(conn, builder, regime_result, scenario_id: str, live_portfolio, *, band: float, h_years: float, rescore, cost_fn=None, identity=None,
               rotation_gate_enabled: bool = False, calc_versions: dict | None = None) -> dict:
    """Build the two AC arms in memory (dry_run=True: nothing is persisted) and return the row. `rescore()` -> (candidates, info) (see `rescore_candidates`). Raises ShadowSkip when there is nothing to compare."""
    production = builder.load_previous(exclude_scenario_id=scenario_id)
    if production is None:
        raise ShadowSkip("no PRODUCTION scenario: the live arm has no incumbents to compare with")
    off_rows = hs._portfolio_rows(live_portfolio)
    if not off_rows:
        raise ShadowSkip("the live build has no funds (empty candidates)")
    production_rows = hs._portfolio_rows(production)
    candidates, info = rescore()
    if candidates is None or candidates.empty:
        raise ShadowSkip("no eligible candidates under the penalty")
    if calc_versions is None:
        calc_versions = hs._calc_versions(conn, [r["isin"] for r in off_rows])

    last = hs._last_row(conn, step=STEP)
    chain_base_rows = (last["chain"]["weights"] if last else None) or production_rows
    chain_base_scenario = (last["scenario_id"] if last else None) or production.scenario_id

    with contextlib.redirect_stdout(io.StringIO()):                          # the builder prints its sub-portfolio lines: the log shows only the live arm
        one_step = builder.build(regime_result, scenario_id=scenario_id, dry_run=True, previous_portfolio=production, candidates=candidates)
        chain = builder.build(regime_result, scenario_id=scenario_id, dry_run=True,
                              previous_portfolio=hs._as_incumbent_portfolio(chain_base_rows, chain_base_scenario), candidates=candidates)
    base = hs.build_row(scenario_id=scenario_id, band=band, off_rows=off_rows, production_rows=production_rows, one_step_rows=hs._portfolio_rows(one_step),
                        chain_rows=hs._portfolio_rows(chain), chain_base_rows=chain_base_rows, chain_base_scenario=chain_base_scenario, calc_versions=calc_versions,
                        identity=identity, rotation_gate_enabled=rotation_gate_enabled, cost_fn=cost_fn)
    base["baseline_scenario"] = production.scenario_id
    return build_row(base_row=base, h_years=h_years, info=info)


def shadow_after_build(conn, builder, regime_result, scenario_id: str, live_portfolio, *, enabled: bool, band: float, h_years: float, rescore, cost_fn=None, identity=None,
                       rotation_gate_enabled: bool = False, calc_versions: dict | None = None, show_comparisons: bool = False) -> str | None:
    """The fail-soft hook: NEVER raises. Returns the line to print (or None when the switch is OFF). Every failure is one P3_FEE_PENALTY_SHADOW_ERROR row (and, if even that cannot be
    written, a printed warning): the build's return code and its DRAFT are never touched."""
    if not enabled:
        return None
    try:
        row = run_shadow(conn, builder, regime_result, scenario_id, live_portfolio, band=band, h_years=h_years, rescore=rescore, cost_fn=cost_fn, identity=identity,
                         rotation_gate_enabled=rotation_gate_enabled, calc_versions=calc_versions)
        hs._write(conn, STEP, hs.STATUS, json.dumps(row, separators=(",", ":"), default=str))
        return summary_line(read_rows(conn), show_comparisons=show_comparisons)
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
            return f"AVISO: fee-penalty shadow failed and its error row could not be written ({reason})"
        return f"AVISO: fee-penalty shadow skipped ({reason}); one {STEP_ERROR} row written"
