# proyecto3/src/pit_class_choice.py
# -*- coding: utf-8 -*-
"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md sections 4 and 8): the fee-aware class choice in the PIT, per trial.

The live scorer (`fund_scorer.deduplicate_by_family`, switch `P3_FEE_AWARE_CLASS_ENABLED`, ships OFF) keeps ONE class per (family, sub-portfolio) before the portfolio is built. The PIT never calls that function:
its engine drops the second class of a family itself (`select_candidates`, `exclude_families`), so the best-score class always wins. This module applies the SAME rule to the candidate frames of one date
before the engine sees them, with the fees of the TRIAL (a `ClassChoice`) instead of a global switch, so every arm of a study states its own configuration and the shipped default (`None`) changes nothing.

ONE rule for both paths (R3): the decision is `fund_scorer._fee_aware_pick` itself; a test pins that the live deduplication and this pass keep the same class on the same data.
Known interaction, measured and not corrected here (PREREG_P3_7 declares the rule as built): the choice does not know what the portfolio already holds, so with a hysteresis band a family's held class
can be replaced by a cheaper one and that switch trades two sets of one-off fees. The runner reports it.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from proyecto3.src import fund_scorer as fs


@dataclass(frozen=True)
class ClassChoice:
    """The configuration of the class choice of one trial. `fees`: ISIN-indexed frame with Entry_Fee_Pct and Exit_Fee_Pct (the frozen snapshot); `ongoing`: ISIN-indexed Ongoing_Charge; `tolerance`: the
    score tolerance below the best class (shared.config.P3_FEE_AWARE_CLASS_SCORE_TOLERANCE). A class absent from `fees` or `ongoing` has unknown fees, and an unknown fee keeps the best class."""
    fees: pd.DataFrame
    ongoing: pd.Series
    tolerance: float


def _choose_one(frame: pd.DataFrame, choice: ClassChoice) -> pd.DataFrame:
    if frame.empty or "fund_family_id" not in frame.columns:
        return frame
    fam = frame["fund_family_id"]
    has_family = fam.notna() & (fam.astype(str).str.strip() != "")
    keyed = frame.loc[has_family, ["isin", "score_total", "fund_family_id"]]
    sizes = keyed["fund_family_id"].map(keyed["fund_family_id"].value_counts())
    multi = keyed[sizes > 1]
    if multi.empty:
        return frame
    isin = multi["isin"]
    helper = pd.DataFrame({"fund_family_id": multi["fund_family_id"], "score_final": multi["score_total"].astype(float),
                           fs.FEE_AWARE_COLUMNS[0]: isin.map(choice.ongoing.to_dict()).to_numpy(), fs.FEE_AWARE_COLUMNS[1]: isin.map(choice.fees["Entry_Fee_Pct"].to_dict()).to_numpy(),
                           fs.FEE_AWARE_COLUMNS[2]: isin.map(choice.fees["Exit_Fee_Pct"].to_dict()).to_numpy()}, index=multi.index)
    drop = []
    for _fam, group in helper.groupby("fund_family_id", sort=False):
        best = group.sort_values("score_final", ascending=False, kind="stable").index[0]            # the frame arrives sorted by score: the first of equal scores stays
        pick = fs._fee_aware_pick(group, best, choice.tolerance)
        drop += [i for i in group.index if i != pick]
    return frame.drop(index=drop)


def apply_class_choice(candidates: dict, choice: "ClassChoice | None") -> dict:
    """`candidates` ({sub-portfolio: frame}, as `pit_backtest.candidates_by_sub` builds it) with one class per family: the chosen one. `None` returns the input unchanged (the default of every arm
    that does not use the class choice). A fund with no family id is its own family; a family with one candidate is untouched; the order of the frame is kept."""
    if choice is None:
        return candidates
    return {sub: _choose_one(frame, choice) for sub, frame in candidates.items()}
