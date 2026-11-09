# proyecto3/tests/test_data_suspect_phase2_fnd0394.py
# -*- coding: utf-8 -*-
"""FND-0394 phase 2: the operational barriers of the shared suspicion predicate -- the live scorer's hard filter, the exemption_grace indicator, the D-DATA-SUSPECT promotion gate and the point-in-time evaluation.

R-7: no DB, synthetic frames only. Every barrier ships OFF with `DATA_SUSPECT_ENABLED = False`: the first tests pin that OFF changes nothing, the rest turn the switch ON explicitly (monkeypatch) and pin what each barrier does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402
from proyecto3.src import scenario_lifecycle as lc  # noqa: E402
from shared import data_suspect as ds  # noqa: E402


def _row(**kw):
    base = {"max_dd": -0.05, "return_ann_real": 0.03, "Fund_Nature": "Mixtos", "Credit_Quality": "Investment Grade"}
    base.update(kw)
    return pd.Series(base)


REASON = "data_suspect: DEVIATION, 1 quantity/ies at |z| >= 50; strongest sharpe|since_inception z 80.0; verify it, then record a verified-genuine exemption (shared.config.DATA_SUSPECT_EXEMPTIONS)"


# ---- the switch ------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_selection_effect_ships_off():
    assert cfg.DATA_SUSPECT_ENABLED is False


def test_off_a_suspect_column_changes_nothing(monkeypatch):
    monkeypatch.setattr(cfg, "DATA_SUSPECT_ENABLED", False)
    assert fs.check_hard_filters(_row(data_suspect_reason=REASON), "Dinamica") is None


# ---- the live scorer -------------------------------------------------------------------------------------------------------------------------------------------------

def test_on_the_scorer_excludes_a_suspect_with_its_reason_and_lets_every_other_fund_through(monkeypatch):
    monkeypatch.setattr(cfg, "DATA_SUSPECT_ENABLED", True)
    assert fs.check_hard_filters(_row(data_suspect_reason=REASON), "Dinamica") == REASON
    assert fs.check_hard_filters(_row(data_suspect_reason=None), "Dinamica") is None
    assert fs.check_hard_filters(_row(), "Dinamica") is None                              # a frame without the column (PIT without it) fails open


def test_a_data_hold_is_checked_before_a_suspicion(monkeypatch):
    monkeypatch.setattr(cfg, "DATA_SUSPECT_ENABLED", True)
    monkeypatch.setattr(cfg, "P3_DATA_HOLD_ENABLED", True)
    assert fs.check_hard_filters(_row(data_hold_reason="data_hold: FND-0404 until 2027-02-07 (x)", data_suspect_reason=REASON), "Dinamica").startswith("data_hold:")


def test_the_grace_note_reaches_the_notes_and_the_detail_only_when_a_fund_is_in_grace():
    assert fs._build_score_notes("Expansion", None) == "regime=Expansion"                          # unchanged for every other fund
    assert fs._build_score_notes("Expansion", None, "exemption_grace: FND-0403 expired 2027-05-07; exempt for this build only").endswith("exempt for this build only")
    assert fs._build_score_notes("Expansion", None, float("nan")) == "regime=Expansion"            # a NaN from a frame where only some rows are in grace is not a note
    assert fs._build_score_notes("Expansion", "x", None) == "regime=Expansion | excluido: x"


def _scoring_frame(grace=None):
    rng = np.random.default_rng(4)
    n = 12
    df = pd.DataFrame({"Fund_Name": [f"F{i}" for i in range(n)], "Fund_Nature": ["Mixtos"] * n, "srri_kiid": 4.0, "Credit_Quality": "Investment Grade",
                       "Investment_Focus": "Global", "Ongoing_Charge": 0.01, "SRRI_Quality_Flag": "OK", "fund_family_id": [None] * n,
                       "max_dd": -rng.uniform(0.02, 0.1, n), "return_ann_real": rng.uniform(0.01, 0.06, n), "sharpe": rng.uniform(0.2, 1.0, n),
                       "srri_nav": 4.0}, index=pd.Index([f"F{i:02d}" for i in range(n)], name="isin"))
    if grace:
        df["exemption_grace"] = pd.Series({"F03": grace}, dtype=object).reindex(df.index)
    return df


def test_the_grace_flag_is_a_result_column_and_a_detail_key_only_for_the_fund_in_grace():
    note = "exemption_grace: FND-0403 expired 2027-05-07; exempt for this build only"
    base = fs.score_funds_from_df(_scoring_frame(), "Expansion", verbose=False)
    got = fs.score_funds_from_df(_scoring_frame(note), "Expansion", verbose=False)
    assert "exemption_grace" not in base.columns and all("exemption_grace" not in d for d in base["detail"])      # no grace anywhere: the frame is what it was
    flagged = got[got["exemption_grace"].notna()]
    assert set(flagged["isin"]) == {"F03"} and flagged["eligible"].all()                              # in grace the fund is still eligible: the exemption holds for this build
    assert all(d["exemption_grace"] == note for d in flagged["detail"])
    assert all("exemption_grace" not in d for d in got[got["exemption_grace"].isna()]["detail"])
    pd.testing.assert_series_equal(base.sort_values(["isin", "subportfolio"]).reset_index(drop=True)["score_final"],
                                   got.sort_values(["isin", "subportfolio"]).reset_index(drop=True)["score_final"])      # the flag changes no score


# ---- the promotion gate ----------------------------------------------------------------------------------------------------------------------------------------------

def _gate(**over):
    base = dict(status="DRAFT", n_funds=30, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.2, budget=0.3, audit=_scope(), built_at=None)
    base.update(over)
    return lc.evaluate_promotion(**base)


def _scope():
    from shared.statistical_audit.gating import AuditScope
    from datetime import datetime
    return AuditScope(run_id="audit_x", run_ts=datetime(2026, 11, 9, 10, 0))


def test_the_gate_code_is_overridable_and_known_to_the_catalogue():
    assert lc.GATE_DATA_SUSPECT == "DATA_SUSPECT" and lc.GATE_DATA_SUSPECT in lc.OVERRIDABLE_GATES


def test_without_suspects_the_gate_says_nothing():
    assert _gate().blockers == [] and _gate(data_suspects=[]).blockers == []


def test_a_suspect_in_the_scenario_is_a_soft_blocker_with_its_code_and_reason():
    out = _gate(data_suspects=[("LU0001", REASON)]).blockers
    mine = [b for b in out if b.startswith("[GATE:DATA_SUSPECT]")]                       # the gate appends its own generic "how to override" hint after the soft blockers
    assert len(mine) == 1 and "LU0001" in mine[0] and "verified-genuine exemption" in mine[0]


def test_the_reasoned_override_covers_the_suspicion_and_is_logged():
    v = _gate(data_suspects=[("LU0001", REASON)], accept_alarms="owner decision after review", accept_ap="FND-0394", ap_exists=True)
    assert v.blockers == [] and [c for c, _ in v.overridden] == [lc.GATE_DATA_SUSPECT]


def test_an_unreadable_panel_refuses_under_the_same_code_fail_closed():
    out = _gate(data_suspects=None, data_suspect_problem="UndefinedTable: fund_metrics").blockers
    assert out[0].startswith("[GATE:DATA_SUSPECT]") and "UndefinedTable" in out[0]


# ---- the helpers every consumer shares -------------------------------------------------------------------------------------------------------------------------------

def test_the_reason_covers_every_share_class_of_a_suspect_family_and_names_the_way_out():
    s = ds.Suspect(family="F1", isins=("A", "B"), representative="A", kinds=("DEVIATION", "INTEGRITY"), n_metrics=2, max_abs_z=80.0,
                   detail={"nav_flag": {}, "top_metrics": [("sharpe|since_inception|0|v1", 80.0, 100)]})
    out = ds.reasons_by_isin([s])
    assert set(out) == {"A", "B"} and out["A"] == out["B"]
    assert out["A"].startswith("data_suspect: DEVIATION+INTEGRITY, 2 quantity/ies") and "strongest sharpe|since_inception|0|v1 z 80.0" in out["A"] and "DATA_SUSPECT_EXEMPTIONS" in out["A"]


def test_load_suspect_reasons_applies_the_exemptions_in_force(monkeypatch):
    rng = np.random.default_rng(2)
    rows = []
    for k in ("m|h|0|v1", "m|h|1|v1"):
        v = rng.normal(0, 1, 60)
        med, mad = float(np.median(v)), float(np.median(np.abs(v - np.median(v))))
        rows += [(f"H{i:02d}", "N1", k, float(x), 100) for i, x in enumerate(v)] + [("X", "N1", k, med + 80 / 0.6745 * mad, 100)]
    panel = pd.DataFrame(rows, columns=list(ds.PANEL_COLUMNS))
    monkeypatch.setattr(ds, "load_panel", lambda conn: panel)
    import shared.family_scope as fsc
    monkeypatch.setattr(fsc, "load_family_map", lambda conn: {})
    monkeypatch.setattr(ds, "load_effective_exemptions", lambda conn, as_of=None: {})
    assert set(ds.load_suspect_reasons(object())) == {"X"}
    monkeypatch.setattr(ds, "load_effective_exemptions", lambda conn, as_of=None: {"X": {"m|h"}})
    assert ds.load_suspect_reasons(object()) == {}


# ---- the point-in-time evaluation ------------------------------------------------------------------------------------------------------------------------------------

def _pit_frame(extreme="X"):
    rng = np.random.default_rng(6)
    isins = [f"P{i:02d}" for i in range(40)] + [extreme]
    f = pd.DataFrame({"Fund_Nature": "Mixtos"}, index=pd.Index(isins, name="isin"))
    for m in ds.PIT_METRICS:
        v = rng.normal(0, 1, len(isins))
        med, mad = float(np.median(v[:-1])), float(np.median(np.abs(v[:-1] - np.median(v[:-1]))))
        v[-1] = med + 90 / 0.6745 * mad
        f[m] = v
    return f, pd.Series(120.0, index=f.index)


def test_pit_panel_has_one_row_per_fund_and_metric_in_the_stored_key_form():
    f, n = _pit_frame()
    p = ds.pit_panel(f, n)
    assert set(p["metric"]) == {f"{m}|pit|0|v1" for m in ds.PIT_METRICS} and len(p) == len(f) * len(ds.PIT_METRICS)
    assert {ds.quantity_of(k) for k in p["metric"]} == {f"{m}|pit" for m in ds.PIT_METRICS}


def test_pit_suspects_are_judged_from_the_frame_of_the_date_and_exemptions_name_the_metric():
    f, n = _pit_frame()
    got = ds.pit_suspect_reasons(f, n)
    assert set(got) == {"X"} and got["X"].startswith("data_suspect:")
    assert ds.pit_exemptions({"X": {"sharpe|since_inception", "alpha_persistence|crisis"}}) == {"X": {"sharpe|pit", "alpha_persistence|pit"}}
    every = {"X": {f"{m}|since_inception" for m in ds.PIT_METRICS}}
    assert ds.pit_suspect_reasons(f, n, None, every) == {}                              # the verified-genuine metrics stop counting in the past too (today's registry)


def test_a_young_fund_is_not_a_pit_suspect_and_a_tiny_universe_judges_nobody():
    f, n = _pit_frame()
    young = n.copy()
    young.loc["X"] = 3.0                                                                 # 3 months of NAV against 120 for its peers: a small sample, not a defect
    assert ds.pit_suspect_reasons(f, young) == {}
    assert ds.pit_suspect_reasons(f.iloc[:4], n.iloc[:4]) == {}


def test_a_share_class_of_a_suspect_family_is_held_back_with_it():
    f, n = _pit_frame()
    f.loc["P00"] = f.loc["X"]                                                            # the same series under another class
    got = ds.pit_suspect_reasons(f, n, {"X": "FAM", "P00": "FAM"})
    assert set(got) == {"X", "P00"}


# ---- the promotion script's helper ---------------------------------------------------------------------------------------------------------------------------------------

def _promote_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("p3_promote_scenario_dsp2", _ROOT / "scripts" / "launch" / "p3_promote_scenario.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_promotion_helper_keeps_only_the_scenarios_own_funds_and_fails_closed_on_an_unreadable_panel(monkeypatch):
    mod = _promote_module()
    monkeypatch.setattr(ds, "load_suspect_reasons", lambda conn: {"IN1": "r1", "OUT": "r2", "IN2": "r3"})

    class _Conn:
        rolled = False

        def rollback(self):
            self.rolled = True
    c = _Conn()
    assert mod._data_suspects(c, ["IN2", "IN1", "NONE"]) == ([("IN1", "r1"), ("IN2", "r3")], None) and not c.rolled

    def boom(conn):
        raise RuntimeError("panel gone\nsecond line")
    monkeypatch.setattr(ds, "load_suspect_reasons", boom)
    out, problem = mod._data_suspects(c, ["IN1"])
    assert out is None and problem == "RuntimeError: panel gone" and c.rolled              # a failed read rolls the transaction back, then the gate refuses
