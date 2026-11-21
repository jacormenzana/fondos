# scripts/audit/p3/p3_crisis_diagnostic.py
# -*- coding: utf-8 -*-
r"""
Turning-point diagnostic of roadmap v4 (FND-0435, doc/backlog/PKG_L_p3_alignment.md): why the score lost at the 2008 top and bottom. READ-ONLY on the SEALED saved PIT run (its scores and its inputs/
snapshot; no database, nothing written to the repository except the report folder).

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_crisis_diagnostic.py --run-dir out\reports\pit_backtest\20261119_202509

Slice 1 (this file): the seal guard, the R9 date lock and step D0, the reproduction of the numbers an earlier scratch session produced (hindsight ceiling of a static mix under the 15 % drawdown
limit and its two crash-window variants; eligibility by nature in the Defensiva sub-portfolio), each labelled REPRODUCED or NOT REPRODUCED. Nothing new is built on a number until it is
reproduced. D1 (the top), D2 (the bottom, all three sub-portfolios), the frozen counterfactuals, D3 / D5 and the integrity block follow in later slices.
Artifacts: out/reports/p3_crisis_diagnostic/<STAMP>/ : d0_reproduction.csv, d0_eligibility_by_nature.csv, REPORT.md. Exit codes: 2 seal mismatch, 3 a D0 ceiling did not reproduce (the report is still written).
"""
import argparse
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402
from proyecto3.src.pit_backtest import prepare_pit_backtest  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, SealMismatch, verify_seal  # noqa: E402

# Numbers taken from the scratch session of 2026-11-19/20 (annual return of the best static mix with maximum drawdown >= -15 %), to the two decimals they were quoted at.
SCRATCH_CEILING = {"all months": 0.0414, "crash window removed (hindsight)": 0.0775, "crash losses halved (hindsight)": 0.0634}
CEILING_TOL = 6e-5                              # half a unit of the second quoted decimal, in return units
D0_YEARS = (2006, 2007, 2008, 2009, 2010, 2012, 2014, 2016, 2018, 2020, 2024)      # June of each year: calm years and the design years; locked episodes are skipped, not read
D0_NATURES = ("Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible")


def build_parser():
    p = argparse.ArgumentParser(description="Turning-point diagnostic on the sealed PIT run (read-only), slice 1: seal, date lock, D0.")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_crisis_diagnostic"))
    p.add_argument("--skip-seal", action="store_true", help="only for a run that is not the sealed baseline (the report says so)")
    return p


def _june_dates(at, years):
    at = pd.DatetimeIndex(at)
    out = []
    for y in years:
        m = at[(at.year == y) & (at.month == 6)]
        if len(m):
            out.append(m[0])
    return out


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run = Path(args.run_dir)
    sealed = None
    if run.name == RUN_STAMP and not args.skip_seal:
        try:
            sealed = verify_seal(run)
        except SealMismatch as exc:
            print(f"[SEAL] REFUSED: {exc}")
            return 2
        print(f"[SEAL] {len(sealed)} artifacts verified against the baseline seal of {RUN_STAMP}")
    else:
        print("[SEAL] NOT sealed (another run or --skip-seal): hashes are not verified")

    prereg = D.prereg_in_git_head(_ROOT)
    print(f"[LOCK] {D.PREREG_NAME} in git HEAD: {prereg} -> locked stress episodes are {'OPEN' if prereg else 'REFUSED'}")

    pit_run, inputs, hist, at = load_saved_run(run, None)                      # the run's own inputs/ snapshot: no database
    scores = pit_run.scores
    prep = prepare_pit_backtest(pit_run, inputs, hist, at, windows=(1,))
    S = D.pool_returns(scores, prep.R[1], at)

    # ---- D0a: the hindsight ceiling of a static mix ----
    rows = []
    for r in D.ceiling_variants(S):
        want = SCRATCH_CEILING[r["variant"]]
        ok = abs(r["ann"] - want) <= CEILING_TOL
        rows.append({**r, "scratch_value": want, "reproduced": ok})
        print(f"[D0] {r['variant']:36s} ann {r['ann']:+.4%} (scratch {want:+.2%}) maxDD {r['max_dd']:+.1%} mix {r['Defensiva']:.1f}/{r['Equilibrada']:.1f}/{r['Dinamica']:.1f} -> {'REPRODUCED' if ok else 'NOT REPRODUCED'}")
    d0 = pd.DataFrame(rows)

    # ---- D0b: eligibility by nature in the Defensiva sub-portfolio (June of calm / design years; a locked episode is skipped, never read) ----
    dates, skipped = [], []
    for t in _june_dates(at, D0_YEARS) + [pd.DatetimeIndex(at).max()]:
        (skipped if D.locked_episode(t) and not prereg else dates).append(t)
    D.assert_dates_allowed(dates, prereg)
    elig = D.eligibility_by_nature(scores, dates, "Defensiva", D0_NATURES)
    wide = elig.pivot(index="date", columns="nature", values=["n", "eligible", "max_drawdown"])
    print("\n[D0] Defensiva, share eligible / excluded by since-inception max_drawdown, by nature:\n" + (wide.round(2).to_string()))
    if skipped:
        print(f"[LOCK] skipped (locked episode): {[str(pd.Timestamp(t).date()) for t in skipped]}")

    # ---- D0c: the trough of eligibility over EVERY unlocked month (the scratch claims 'Renta Fija Flexible 75 % -> 35 %' and 'Corto Plazo 75 % -> 47 %' name no dates) ----
    all_dates = [t for t in sorted(scores["as_of"].unique()) if prereg or not D.locked_episode(t)]
    d0c = D.eligibility_by_nature(scores, all_dates, "Defensiva", D0_NATURES[1:]).pivot(index="date", columns="nature", values="eligible")
    trough = pd.DataFrame({"june_2007": d0c.loc[pd.Timestamp("2007-06-30")],"trough": d0c.min(), "trough_date": d0c.idxmin()})
    print("\n[D0] trough of eligibility among the unlocked months (scratch: Flexible 75 % -> 35 %, Corto Plazo 75 % -> 47 %):\n" + trough.round(2).to_string())

    out = Path(args.out_root) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    d0.to_csv(out / "d0_reproduction.csv", index=False)
    elig.to_csv(out / "d0_eligibility_by_nature.csv", index=False)
    lines = [f"# Turning-point diagnostic, slice 1 (D0), run {run.name}", "",
             f"Seal: {'verified ' + str(len(sealed)) + ' artifacts' if sealed else 'NOT verified'}. {D.PREREG_NAME} in git HEAD: {prereg}.", "",
             "Hashes verified: " + (", ".join(f"{k} {v[:12]}" for k, v in sealed.items()) if sealed else "none"), "",
             "## D0a. Hindsight ceiling of a static mix of the three pools (max drawdown >= -15 %, frictionless, equal-weight eligible pools)", "", "```", d0.round(4).to_string(index=False), "```", "",
             "A number that is not REPRODUCED stays labelled scratch-derived in every document.", "",
             "## D0b. Defensiva: eligibility by nature (June of calm or design years)", "", "```", wide.round(2).to_string(), "```", "",
             f"Skipped because locked until the preregistration is committed: {[str(pd.Timestamp(t).date()) for t in skipped] or 'none'}.", "",
             "## D0c. Trough of Defensiva eligibility over every unlocked month (Corto Plazo and Flexible)", "", "```", trough.round(2).to_string(), "```", "",
             "The scratch claims name no dates: Flexible 75 % -> 35 % and Corto Plazo 75 % -> 47 %. The trough is the same ORDER (about a third / under a half) but not the same numbers, so these two stay scratch-derived.", "",
             "Not recovered from the earlier session (stay scratch-derived): the 2008 top figures (selected funds' trailing 3-year +107 % against the pool's +34 %, fall -30 % against -18 %) and the "
             "Defensiva share in Monetario 35 % against 11 %; D1 / D2 recompute them from definitions written here first.", ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OUT] {out}")
    return 0 if d0["reproduced"].all() else 3


if __name__ == "__main__":
    sys.exit(main())
