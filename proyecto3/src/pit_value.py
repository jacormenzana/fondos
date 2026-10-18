# proyecto3/src/pit_value.py
# -*- coding: utf-8 -*-
"""
Model-value study of P3 on a SAVED PIT run -- FND-0193, pre-registered in doc/decisiones/PREREG_FND-0193.md (read it first).

Does scoring + the regime model beat simple, investable baselines net of costs? This module only COMPOSES existing pieces:

  * weights of the model (`pit_backtest.build_weights`), of the baselines and of the ablations (`pit_baselines`), all evaluated
    by the SAME return / cost layer (`pit_backtest.assemble_table`, 25 bp on the real turnover of each strategy's own weights);
  * E1 full-history comparison with Holm (primary baselines), sub-periods, leave-one-episode-out, regimes, cost sensitivity;
  * E2 walk-forward choice of the hysteresis band (`pit_oos.select_cell`, 1-month embargo);
  * E3 deflated Sharpe with the trial ledger, E4 CSCV probability of backtest overfitting (`backtest_inference`);
  * the drawdown circuit breaker and `verdict()`, which applies the pre-registered KEEP / SIMPLIFY / REPLACE rules mechanically.

No DB access when the run folder holds its input snapshot (`inputs/`, written by p3_pit_backtest since FND-0193). Pure pandas / numpy (R-7).
"""

import dataclasses
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.backtest_inference import (cscv_pbo, drawdown_probability, holm_stepdown, ledger_deflated_sharpe,
                                              paired_sharpe_difference, sharpe_ratio)
from proyecto3.src.pit_backtest import assemble_table, build_weights, prepare_pit_backtest
from proyecto3.src.pit_baselines import (balanced_weights, cheapest_metric, multiplier_variant, pool_weights, rank_weights,
                                         static_regime_hist, trailing_metrics)
from proyecto3.src.pit_oos import (apply_weight_no_trade_band, cadence_tables, _phase_average, load_saved_run, period_stats,
                                   select_cell)
from proyecto3.src.portfolio_engine import DEFAULT_CONSTRAINTS

PRIMARY = ("B-POOL", "B-STATIC", "B-LOWVOL", "B-6040")
SECONDARY = ("B-MOM", "B-CHEAP")
MODEL = "M_h10"
MODEL_H0 = "M_h0"
BAND = 0.10
MANDATE_MAX_DD = -0.15
SUB_PERIODS = (("2005-2010", "2005-01-01", "2010-12-31"), ("2011-2016", "2011-01-01", "2016-12-31"),
               ("2017-2021", "2017-01-01", "2021-12-31"), ("2022-2026", "2022-01-01", "2026-12-31"))
EARLY = ("early", "1900-01-01", "2004-12-31")
EPISODES = (("2005-11..2007-08", "2005-11-01", "2007-08-31"), ("2008", "2008-01-01", "2008-12-31"), ("2020", "2020-01-01", "2020-12-31"),
            ("2022", "2022-01-01", "2022-12-31"))
WF_BANDS = (0.0, 0.05, 0.10, 0.20, 0.40)
WF_FIRST_YEAR = 2010
ABLATION_LAYERS = ("noMult", "eqW", "staticMix")           # A1, A2, A3
FAMILIES = (("alpha", ("alpha_persistence",)), ("fx", ("fx_",)), ("regime", ("regime_",)), ("crisis", ("crisis_",)),
            ("slope", ("sharpe_slope", "return_slope")), ("roll", ("roll_",)))


# ============================================================
# Inputs
# ============================================================

@dataclass
class Study:
    pit_run: object
    inputs: object
    hist: pd.DataFrame
    at: pd.DatetimeIndex
    base: object                      # PreparedBacktest (forward returns, cash leg, pool benchmark), shared by every strategy
    detail: "pd.DataFrame | None"
    target: "pd.Series | None"
    max_stale_days: int
    run_dir: Path


def load_study(run_dir, conn=None) -> Study:
    run = Path(run_dir)
    pit_run, inputs, hist, at = load_saved_run(run, conn)
    max_stale = 60
    if (run / "manifest.json").exists():
        max_stale = int(json.loads((run / "manifest.json").read_text(encoding="utf-8")).get("args", {}).get("max_stale_days", 60))
    from proyecto3.src.pit_inputs import has_inputs_snapshot, load_inputs_snapshot
    target = load_inputs_snapshot(run)[2] if has_inputs_snapshot(run) else None
    base = prepare_pit_backtest(pit_run, inputs, hist, at, (1,), max_stale)
    detail = pd.read_parquet(run / "scores_detail.parquet") if (run / "scores_detail.parquet").exists() else None
    return Study(pit_run, inputs, hist, at, base, detail, target, max_stale, run)


def table_for(study: Study, W: pd.DataFrame, cash: pd.Series, bps: float = 25.0) -> pd.DataFrame:
    prep = dataclasses.replace(study.base, W=W, cash_w=cash)
    return assemble_table(prep, bps, 1, study.target)


# ============================================================
# Strategies (weights), evaluated lazily at any cost level
# ============================================================

def model_weights(study: Study, scores: "pd.DataFrame | None" = None, band: float = BAND, hist: "pd.DataFrame | None" = None,
                  weight_method: str = "score_proportional") -> "tuple[pd.DataFrame, pd.Series]":
    cons = dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=band, weight_method=weight_method) if (band or weight_method != "score_proportional") \
        else DEFAULT_CONSTRAINTS
    return build_weights(study.pit_run.scores if scores is None else scores, study.inputs.attrs, study.hist if hist is None else hist,
                         study.at, cons, use_incumbents=band > 0, fx_exposed=getattr(study.inputs, "fx_exposed", None))


def ablation_name(layers) -> str:
    return "ABL_" + "+".join(layers)


def family_keys(detail: pd.DataFrame) -> dict:
    """{family: [multiplier keys]} over the m_* columns of a scores_detail frame (keys without the m_ prefix)."""
    out = {name: [] for name, _ in FAMILIES}
    out["other"] = []
    for col in [c for c in detail.columns if c.startswith("m_")]:
        key = col[2:]
        for name, prefixes in FAMILIES:
            if key.startswith(prefixes):
                out[name].append(key)
                break
        else:
            out["other"].append(key)
    return {k: v for k, v in out.items() if v}


def build_catalogue(study: Study, ablations: bool = True, a5: bool = True) -> dict:
    """{name: (W, cash)} for the model, every baseline and the ablations. Costs are applied later by `tables`."""
    sc, hist, at = study.pit_run.scores, study.hist, study.at
    cat = {MODEL: model_weights(study, band=BAND), MODEL_H0: model_weights(study, band=0.0)}
    cat["B-POOL"] = pool_weights(sc, hist, at)
    cat["B-STATIC"] = pool_weights(sc, static_regime_hist(hist), at)
    cat["B-6040"] = balanced_weights(sc, at)
    tm = trailing_metrics(study.inputs.nav, at, study.max_stale_days)
    for tag, band in (("", BAND), ("_h0", 0.0)):
        cat[f"B-LOWVOL{tag}"] = rank_weights(sc, hist, at, tm["vol_36m"], higher_is_better=False, hysteresis_band=band)
        cat[f"B-MOM{tag}"] = rank_weights(sc, hist, at, tm["ret_12m"], higher_is_better=True, hysteresis_band=band)
    cat["B-CHEAP"] = rank_weights(sc, hist, at, cheapest_metric(study.inputs.attrs, at, study.inputs.nav.columns), higher_is_better=False,
                                  hysteresis_band=BAND)
    if ablations:
        no_mult = multiplier_variant(sc.drop(columns=[c for c in ("detail",) if c in sc.columns]))
        static = static_regime_hist(hist)
        for r in range(1, len(ABLATION_LAYERS) + 1):
            for layers in _combos(ABLATION_LAYERS, r):
                cat[ablation_name(layers)] = model_weights(
                    study, scores=no_mult if "noMult" in layers else None, band=BAND, hist=static if "staticMix" in layers else None,
                    weight_method="equal" if "eqW" in layers else "score_proportional")
    if a5 and study.detail is not None:
        fams = family_keys(study.detail)
        plain = sc.drop(columns=[c for c in ("detail",) if c in sc.columns])
        for fam, keys in fams.items():
            cat[f"A5_drop_{fam}"] = model_weights(study, scores=multiplier_variant(plain, study.detail, drop=tuple(keys)), band=BAND)
            cat[f"A5_only_{fam}"] = model_weights(study, scores=multiplier_variant(plain, study.detail, keep=tuple(keys)), band=BAND)
    return cat


def _combos(items, r):
    from itertools import combinations
    return list(combinations(items, r))


def tables(study: Study, cat: dict, bps: float = 25.0, names=None) -> dict:
    return {n: table_for(study, W, c, bps) for n, (W, c) in cat.items() if names is None or n in names}


# ============================================================
# E2: walk-forward choice of the hysteresis band
# ============================================================

def walk_forward_band(study: Study, bands=WF_BANDS, first_year: int = WF_FIRST_YEAR, tolerance: float = 0.02, bps: float = 25.0) -> dict:
    """Each January from `first_year` choose the band on the expanding window that ends one month before the application window's
    decision dates (1-month embargo: a date's return lands the month after), apply it for that calendar year. Dates before `first_year`
    use band 0 (nothing to choose from yet) and are not reported. The stitched WEIGHTS go through the cost layer once, so a switch of
    band pays its real turnover. Returns {'table', 'choices', 'weights', 'tables'}."""
    raw = {b: model_weights(study, band=b) for b in bands}
    cols = sorted(set().union(*(set(w.columns) for w, _ in raw.values())))
    wb = {b: (w.reindex(columns=cols), c) for b, (w, c) in raw.items()}          # one column set, so rows can be swapped between bands
    tab = {b: table_for(study, *wb[b], bps) for b in bands}
    at = study.at
    years = sorted({t.year for t in at if t.year >= first_year})
    start_band = 0.0 if 0.0 in wb else bands[0]
    W, cash = wb[start_band][0].copy(), wb[start_band][1].copy()
    choices = {}
    for y in years:
        design_end = pd.Timestamp(year=y - 1, month=12, day=31) - pd.offsets.MonthEnd(1)
        rows = {b: period_stats(tab[b], None, design_end) for b in bands}
        design = pd.DataFrame({b: {"sharpe": r["sharpe"], "mean_turnover": r["mean_turnover"]} for b, r in rows.items()}).T
        pick = select_cell(design, tolerance)
        choices[y] = float(pick)
        idx = at[at.year == y]
        W.loc[idx, :] = wb[pick][0].loc[idx].to_numpy()
        cash.loc[idx] = wb[pick][1].loc[idx].to_numpy()
    return {"table": table_for(study, W, cash, bps), "choices": pd.Series(choices, name="band"), "weights": (W, cash), "tables": tab}


# ============================================================
# Statistics on tables
# ============================================================

def series_of(table: pd.DataFrame) -> pd.Series:
    return table["net_turnover_1m"]


def stats_row(table: pd.DataFrame, start=None, end=None) -> dict:
    t = table.loc[start:end] if (start is not None or end is not None) else table
    from proyecto3.src.pit_backtest import series_stats
    st = series_stats(t["net_turnover_1m"], t["cash_ret_1m"])
    return {**st, "mean_turnover": float(t["turnover"].mean())}


def mask_months(index: pd.DatetimeIndex, start: str, end: str) -> pd.Series:
    return pd.Series((index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end)), index=index)


def sharpe_diff_point(a: pd.Series, b: pd.Series, cash: pd.Series, keep: "pd.Series | None" = None) -> float:
    d = pd.concat({"a": a, "b": b}, axis=1).dropna()
    if keep is not None:
        d = d[keep.reindex(d.index).fillna(False).to_numpy(dtype=bool)]
    if len(d) < 12:
        return float("nan")
    c = cash.reindex(d.index).fillna(0.0)
    return sharpe_ratio(d["a"] - c) - sharpe_ratio(d["b"] - c)


def paired(a: pd.Series, b: pd.Series, cash: pd.Series, n_boot: int = 2000, mean_block: float = 6.0) -> dict:
    out = paired_sharpe_difference(a, b, cash, n_boot=n_boot, mean_block=mean_block)
    out["p_a_le_b"] = 1.0 - out["prob_a_better"] if np.isfinite(out["prob_a_better"]) else float("nan")      # H0: a not better than b
    out["p_a_ge_b"] = out["prob_a_better"]                                                                  # H0: a not worse than b
    return out


def load_ledger(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ============================================================
# E1 .. E4
# ============================================================

def e1_full(tabs: dict, n_boot: int = 2000, mean_block: float = 6.0) -> pd.DataFrame:
    """M vs every baseline over the whole window; Holm across the primary baselines, in both directions (M better / baseline better)."""
    m = tabs[MODEL]
    cash = m["cash_ret_1m"]
    rows = {}
    for name in [*PRIMARY, *SECONDARY, "B-LOWVOL_h0", "B-MOM_h0", MODEL_H0]:
        if name not in tabs:
            continue
        r = paired(series_of(m), series_of(tabs[name]), cash, n_boot, mean_block)
        b = stats_row(tabs[name])
        rows[name] = {"sharpe_M": r["sharpe_a"], "sharpe_B": r["sharpe_b"], "diff": r["diff"], "ci_low": r["ci_low"], "ci_high": r["ci_high"],
                      "p_M_not_better": r["p_a_le_b"], "p_B_not_better": r["p_a_ge_b"], "n": r["n"],
                      "ann_return_B": b["ann_return"], "ann_vol_B": b["ann_vol"], "max_dd_B": b["max_drawdown"], "turnover_B": b["mean_turnover"]}
    df = pd.DataFrame(rows).T
    prim = [n for n in PRIMARY if n in df.index]
    fwd = holm_stepdown({n: df.loc[n, "p_M_not_better"] for n in prim})
    rev = holm_stepdown({n: df.loc[n, "p_B_not_better"] for n in prim})
    df["holm_M_better"] = pd.Series({n: fwd[n]["reject"] for n in prim})
    df["holm_B_better"] = pd.Series({n: rev[n]["reject"] for n in prim})
    df["holm_p_adj_M"] = pd.Series({n: fwd[n]["p_adj"] for n in prim})
    return df


def e1_subperiods(tabs: dict, with_early: bool = False) -> pd.DataFrame:
    m = tabs[MODEL]
    cash = m["cash_ret_1m"]
    periods = list(SUB_PERIODS) + ([EARLY] if with_early else [])
    rows = {}
    for name in [*PRIMARY, *SECONDARY]:
        if name not in tabs:
            continue
        rows[name] = {label: sharpe_diff_point(series_of(m), series_of(tabs[name]), cash, mask_months(m.index, a, b)) for label, a, b in periods}
    return pd.DataFrame(rows).T


def e1_loeo(tabs: dict) -> pd.DataFrame:
    m = tabs[MODEL]
    cash = m["cash_ret_1m"]
    rows = {}
    for name in [*PRIMARY, *SECONDARY]:
        if name not in tabs:
            continue
        rows[name] = {}
        for label, a, b in EPISODES:
            keep = ~mask_months(m.index, a, b)
            rows[name][f"without {label}"] = sharpe_diff_point(series_of(m), series_of(tabs[name]), cash, keep)
    return pd.DataFrame(rows).T


def e1_regimes(tabs: dict) -> pd.DataFrame:
    """Mean monthly net return by regime: model, B-POOL and the difference (reported, never decisive)."""
    m, p = tabs[MODEL], tabs["B-POOL"]
    d = pd.DataFrame({"regime": m["regime"], "M": series_of(m), "B-POOL": series_of(p)}).dropna()
    g = d.groupby("regime")
    return pd.DataFrame({"months": g.size(), "M_mean_m": g["M"].mean(), "B-POOL_mean_m": g["B-POOL"].mean(),
                         "diff_mean_m": g["M"].mean() - g["B-POOL"].mean()})


def strategy_overview(tabs: dict, n_boot: int = 5000) -> pd.DataFrame:
    rows = {}
    for n, t in tabs.items():
        st = stats_row(t)
        rows[n] = {**st, "dd_prob_36m_15pct": drawdown_probability(series_of(t), n_boot=n_boot)}
    return pd.DataFrame(rows).T


def layer_verdicts(tabs: dict, n_boot: int = 2000, mean_block: float = 6.0) -> pd.DataFrame:
    """For every ablation: Sharpe(model) - Sharpe(variant) with its 90% interval and the drawdown change in pp. A layer is KEPT only if its
    removal lowers Sharpe with the interval excluding 0, or worsens max drawdown by more than 2 pp (pre-registered)."""
    m = tabs[MODEL]
    mm = stats_row(m)["max_drawdown"]
    rows = {}
    for n, t in tabs.items():
        if not (n.startswith("ABL_") or n.startswith("A5_")):
            continue
        r = paired(series_of(m), series_of(t), m["cash_ret_1m"], n_boot, mean_block)
        dd = stats_row(t)["max_drawdown"]
        worsen = mm - dd                                   # > 0 when the variant draws down deeper than the model
        rows[n] = {"sharpe_model_minus_variant": r["diff"], "ci_low": r["ci_low"], "ci_high": r["ci_high"], "variant_max_dd": dd,
                   "dd_worsens_pp": 100.0 * worsen, "layer_earns_its_place": bool((r["ci_low"] > 0) or (worsen > 0.02))}
    return pd.DataFrame(rows).T


def e3_dsr(model_table: pd.DataFrame, ledger: dict) -> dict:
    ex = (series_of(model_table) - model_table["cash_ret_1m"]).dropna()
    var = ledger["var_annualised_sharpe_across_trials"]
    return {"n_months": int(len(ex)), "N_scan": int(ledger["N_scan_upper"]), "dsr_N": ledger_deflated_sharpe(ex, int(ledger["N_scan_upper"]), var),
            "dsr_2N": ledger_deflated_sharpe(ex, int(ledger["N_scan_x2"]), var)}


def pbo_matrix(study: Study, cat: dict, bps: float = 25.0, cadences=(1, 2, 3, 4, 6, 12)) -> pd.DataFrame:
    """Monthly net returns (months x configurations) of every regenerable configuration: the hysteresis x weight-band grid of the model,
    the cadence variants (phase-averaged) with and without hysteresis, the baselines and the ablations."""
    cols = {}
    for b, (W, c) in {b: model_weights(study, band=b) for b in (0.0, 0.05, 0.10, 0.20)}.items():
        for d in (0.0, 0.01, 0.02, 0.03):
            cols[f"M_h{b:g}_wb{d:g}"] = series_of(table_for(study, apply_weight_no_trade_band(W, d), c, bps))
    for h in (0.0, BAND):
        ct = cadence_tables(study.pit_run, study.inputs, study.hist, study.at, cadences=cadences, hysteresis_band=h, tx_cost_bps=bps,
                            max_stale_days=study.max_stale_days)
        for k in cadences:
            cols[f"M_h{h:g}_cad{k}"] = _phase_average(ct, k)[0]
    for n, t in tables(study, cat, bps).items():
        cols[n] = series_of(t)
    return pd.DataFrame(cols).dropna(how="any")


# ============================================================
# Verdict (pre-registered, mechanical)
# ============================================================

def verdict(e1: pd.DataFrame, sub: pd.DataFrame, loeo: pd.DataFrame, wf_vs: pd.DataFrame, dsr: dict, pbo: dict, dd: dict,
            early_ok: "bool | None" = None) -> dict:
    """KEEP / SIMPLIFY / REPLACE per PREREG section 5. Every criterion is returned with its value so the report can show why."""
    prim = [n for n in PRIMARY if n in e1.index]
    c = {}
    c["1_beats_all_primary_holm"] = {"pass": bool(len(prim) == len(PRIMARY) and e1.loc[prim, "holm_M_better"].all()),
                                     "detail": e1.loc[prim, ["diff", "holm_p_adj_M"]].round(4).to_dict("index")}
    pos_sub = {n: int((sub.loc[n, [lbl for lbl, _, _ in SUB_PERIODS]] > 0).sum()) for n in prim}
    loeo_ok = {n: bool((loeo.loc[n] > 0).all()) for n in prim}
    c["2_sign_robust"] = {"pass": bool(all(v >= 3 for v in pos_sub.values()) and all(loeo_ok.values()) and (early_ok is not False)),
                          "detail": {"positive_subperiods_of_4": pos_sub, "all_loeo_positive": loeo_ok, "early_positive": early_ok}}
    c["3_walk_forward_above_primary"] = {"pass": bool(len(wf_vs) and (wf_vs.loc[[n for n in prim if n in wf_vs.index], "diff"] > 0).all()),
                                         "detail": wf_vs["diff"].round(4).to_dict() if len(wf_vs) else {}}
    c["4_dsr_2N_ge_0.90"] = {"pass": bool(dsr["dsr_2N"] >= 0.90), "detail": dsr}
    c["5_pbo_le_0.30"] = {"pass": bool(np.isfinite(pbo["pbo"]) and pbo["pbo"] <= 0.30), "detail": pbo}
    c["6_drawdown_breaker"] = {"pass": bool(dd["worst_max_dd"] >= MANDATE_MAX_DD and dd["model_prob_36m"] <= dd["pool_prob_36m"]), "detail": dd}
    keep = all(v["pass"] for v in c.values())
    replace = bool(len(prim) and e1.loc[prim, "holm_B_better"].any())
    outcome = "KEEP" if keep else ("REPLACE_PROPOSAL" if replace else "SIMPLIFY")
    return {"outcome": outcome, "criteria": c}
