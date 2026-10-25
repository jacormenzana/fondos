# scripts/launch/p3_pit_backtest.py
# -*- coding: utf-8 -*-
"""
Point-in-time backtest of the P3 portfolio (FND-0159): scores the universe at every month-end with the
information available then, builds the portfolio with the live engine, evaluates 1/3/12-month forward returns
(costs, cash leg, IPC+M3 target, Sharpe) and writes an auditable set of artifacts. READ-ONLY on the database.

    C:\\data\\envs\\des\\python.exe scripts\\launch\\p3_pit_backtest.py --dry-run
    C:\\data\\envs\\des\\python.exe scripts\\launch\\p3_pit_backtest.py --sample-per-nature 4          (40-ISIN style sample)
    C:\\data\\envs\\des\\python.exe scripts\\launch\\p3_pit_backtest.py                                (FULL universe, owner-run)

Artifacts (one directory per run, default C:\\data\\fondos\\reports\\pit_backtest\\<STAMP>):
    manifest.json            arguments, git commit, versions, lags, universe size, durations
    timings.json             seconds per stage (risk/peers/momentum/short/scoring), cache hits, cache size
    summary.txt              Backtester.summary(): per-regime and global results, simulated series, target
    pit_backtest_table.csv/.parquet   one row per month-end (returns net of costs, benchmark, target, turnover)
    cost_sensitivity.csv     0/25/50 bps per side x 1/3/12 months (mean return, cost, excess, hit ratios)
    hysteresis_experiment.csv  one row per hysteresis band (0 = no incumbents): turnover, cost drag, chained-series
                             return/vol/Sharpe/drawdown, 12m excess (FND-0205); skipped with --hysteresis-grid ""
    series_stats.json        annual return / vol / Sharpe / max drawdown of the chained monthly series
    universe_by_date.csv     funds entering / stale / too young / scored / eligible at every date
    short_gate_coverage.csv  where the short-horizon gates were evaluable (fail-open view)
    scores_long.parquet      every PIT score (as_of, regime, isin, subportfolio, score_final, eligible, ...)
    scores_detail.parquet    with --keep-detail: every Layer-3 multiplier component and base metric per fund and date (FND-0193 ablation)
    inputs/*.parquet         snapshot of the DB-side inputs the run saw (NAV panel, attributes, deposit rate, FX flags, regime history,
                             IPC+M3 target): an analysis of the saved run reads these, not the database, so it reproduces the run exactly
Log: proyecto3\\log\\log_P3_pitBacktest_<STAMP>.log. Exit codes: 0 ok, 2 nothing to evaluate.
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from proyecto3.src.backtesting import Backtester
from proyecto3.src.pit_backtest import COST_GRID_BPS, table_stats
from proyecto3.src.pit_cache import CODE_VERSION
from proyecto3.src.pit_inputs import save_inputs_snapshot
from shared.config import (FUND_DATA_ELIGIBILITY_ENABLED, P3_FUND_NAV_MAX_AGE_DAYS, P3_FUND_NAV_MAX_GAP_RUN_MONTHS,
                           P3_FUND_NAV_MIN_COVERAGE, P3_WRONG_DOC_EXCLUSION_ENABLED, REGIME_PUBLICATION_LAG_MONTHS)
from shared.db import get_connection
from shared.eur_nav import assert_release_coupling   # FND-0243 D4

DEFAULT_OUT_ROOT = _ROOT / "out" / "reports" / "pit_backtest"      # inside the project (git-ignored out/), no longer c:\data
DEFAULT_CACHE_DIR = _ROOT / "proyecto3" / "cache" / "pit"
LOG_DIR = _ROOT / "proyecto3" / "log"
EXIT_NOTHING_TO_EVALUATE = 2
logger = logging.getLogger("p3_pit_backtest")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Point-in-time backtest of the P3 portfolio (read-only on the DB).")
    p.add_argument("--start", default="2005-01-31", help="first month-end to evaluate (default 2005-01-31)")
    p.add_argument("--end", default=None, help="last month-end (default: last month with a regime label)")
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--isin-file", help="text file, one ISIN per line ('#' comments allowed)")
    scope.add_argument("--isins", help="comma-separated ISIN list")
    scope.add_argument("--sample-per-nature", type=int, metavar="N",
                       help="N funds per Fund_Nature by md5(isin), In_Current_Universe=1 (4 -> the ~32-40 fund sample)")
    p.add_argument("--bps-main", type=float, default=25.0, help="cost per side (bp) for the main table (default 25)")
    p.add_argument("--grid-bps", default=",".join(str(int(b)) for b in COST_GRID_BPS),
                   help="cost sensitivity grid in bp per side (default 0,25,50)")
    p.add_argument("--entry-sides", type=int, choices=(1, 2), default=1,
                   help="1 = cost once per window at entry (approved convention); 2 = round trip")
    p.add_argument("--hysteresis-band", type=float, default=0.0,
                   help="score bonus for last month's holdings in the MAIN table (0 = every month a fresh selection; 0.05 = +5%%)")
    p.add_argument("--hysteresis-grid", default="0,0.05,0.10,0.20,0.40",
                   help='bands compared in hysteresis_experiment.csv (default "0,0.05,0.10,0.20,0.40"; "" = skip)')
    p.add_argument("--max-stale-days", type=int, default=P3_FUND_NAV_MAX_AGE_DAYS,
                   help=f"max age of a NAV observation (default {P3_FUND_NAV_MAX_AGE_DAYS} = the live build-time stale-NAV bound, "
                        f"P3_FUND_NAV_MAX_AGE_DAYS; FND-0254: it was a PIT-only 45)")
    p.add_argument("--macro-variant", choices=("none", "control", "iterative_hy"), default="none",
                   help="FND-0224 scorer group B in the backtest: none = PIT v1 (macro multipliers neutral); control = PIT macro "
                        "betas with the production regression; iterative_hy = iterative VIF candidate. Not none: also writes "
                        "macro_coverage.csv and crisis_variants.csv (sign/scale variants of the crisis multiplier)")
    p.add_argument("--macro-workers", type=int, default=1,
                   help="processes for the macro regressions (about 1 h single-process for the full universe; results identical)")
    p.add_argument("--flag", action="append", default=[], metavar="NAME=0|1",
                   help="override one P2 bundle flag for THIS run only (e.g. CAPTURE_MONTH_END_ENABLED=0): lets you attribute an effect "
                        "to one fix without editing shared/config.py. Repeatable; allowed names: shared.config.P2_BUNDLE_FLAGS. The "
                        "effective flag state is recorded in manifest.json")
    p.add_argument("--current-universe-only", action="store_true",
                   help="exclude In_Current_Universe=0 funds (default: retired funds ARE in the PIT universe)")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="parquet cache directory")
    p.add_argument("--no-cache", action="store_true", help="neither read nor write the parquet cache")
    p.add_argument("--out-dir", default=None, help="artifact directory (default <reports>/pit_backtest/<STAMP>)")
    p.add_argument("--no-scores", action="store_true", help="do not write scores_long.parquet")
    p.add_argument("--snap-nav-month-end", action="store_true",
                   help="FND-0193 alignment gate (run R4): move every monthly NAV date to the calendar month-end before scoring, so funds that "
                        "report on the last business day and funds that report on the month-end share one row per month")
    p.add_argument("--keep-detail", action="store_true",
                   help="also write scores_detail.parquet (every Layer-3 multiplier component per fund and date; FND-0193 ablation). "
                        "Bypasses the scores cache (the scoring stage runs cold, about 6 min more)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit; computes and writes nothing")
    return p


def read_isin_file(path: str) -> list:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def resolve_isins(conn, args) -> "list | None":
    """None = whole universe."""
    if args.isin_file:
        return read_isin_file(args.isin_file)
    if args.isins:
        return [s.strip() for s in args.isins.split(",") if s.strip()]
    if args.sample_per_nature:
        rows = conn.execute("""
            SELECT isin FROM (
                SELECT fm.isin, ROW_NUMBER() OVER (PARTITION BY fm.fund_nature ORDER BY md5(fm.isin)) AS rn
                FROM fund_master fm
                JOIN (SELECT DISTINCT isin FROM fund_nav_monthly) n ON n.isin = fm.isin
                WHERE fm.in_current_universe = 1
            ) t WHERE rn <= %s ORDER BY isin
        """, (args.sample_per_nature,)).fetchall()
        return [r[0] for r in rows]
    return None


def _git_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=_ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        return r.stdout.strip() + (" (+uncommitted changes)" if dirty else "")
    except Exception:
        return "unknown"


# FND-0251 / FND-0254: what a PIT run does NOT model, said in the artifacts themselves so a PIT-vs-live divergence is never misread.
# Stale NAV (the universe rule, max_stale_days = the live bound) and NAV continuity (shared.nav_continuity, evaluated from the rows observable
# at each date) ARE modelled since FND-0254; accepted audit residuals are not, on purpose: they are today's governance decisions.
NOT_MODELLED_NOTICE = ("accepted audit residuals of the live build-time data eligibility (FUND_DATA_ELIGIBILITY_ENABLED) are NOT modelled in "
                       "this point-in-time run: they are today's governance decisions, applying them in the past would be look-ahead "
                       "(FND-0254); stale NAV and NAV continuity ARE modelled when the switch is ON")
# FND-0323 D1: KIID_Status has no history, so with P3_WRONG_DOC_EXCLUSION_ENABLED ON today's WRONG_DOC status is applied at EVERY date.
WRONG_DOC_NOTICE = ("P3_WRONG_DOC_EXCLUSION_ENABLED is ON: funds whose KIID_Status is WRONG_DOC today are ineligible at every date of this run "
                    "(the status has no history; same documented look-ahead as every static attribute of the PIT universe, FND-0323)")


def write_artifacts(out_dir: Path, table: pd.DataFrame, grid: pd.DataFrame, bt, summary: str, manifest: dict,
                    save_scores: bool = True, hysteresis: "pd.DataFrame | None" = None,
                    crisis_variants: "pd.DataFrame | None" = None) -> dict:
    """Write every artifact; returns {name: path}. Missing optional pieces (short coverage) are skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    run = bt.last_pit_run
    written = {}

    def _put(name, writer):
        path = out_dir / name
        writer(path)
        written[name] = str(path)

    _put("summary.txt", lambda p: p.write_text(f"NOTE: {NOT_MODELLED_NOTICE}\n\n{summary}", encoding="utf-8"))
    _put("pit_backtest_table.csv", lambda p: table.to_csv(p, float_format="%.8g"))
    _put("pit_backtest_table.parquet", lambda p: table.to_parquet(p))
    _put("cost_sensitivity.csv", lambda p: grid.to_csv(p, index=False, float_format="%.8g"))
    if hysteresis is not None:
        _put("hysteresis_experiment.csv", lambda p: hysteresis.to_csv(p, index=False, float_format="%.8g"))
    if crisis_variants is not None:
        _put("crisis_variants.csv", lambda p: crisis_variants.to_csv(p, index=False, float_format="%.6g"))
    if run.macro is not None:
        _put("macro_coverage.csv", lambda p: pd.DataFrame(
            {m: f.notna().mean(axis=1) for m, f in run.macro.items()}).to_csv(p, float_format="%.4f"))
    _put("series_stats.json", lambda p: p.write_text(json.dumps(table_stats(table), indent=2, default=str), encoding="utf-8"))
    _put("universe_by_date.csv", lambda p: run.universe.to_csv(p))
    if run.short_coverage is not None:
        _put("short_gate_coverage.csv", lambda p: run.short_coverage.to_csv(p))
    if save_scores and len(run.scores):
        if run.detail is not None and len(run.detail):     # --keep-detail: every Layer-3 component, already flattened and cached by block (FND-0193)
            _put("scores_detail.parquet", lambda p: run.detail.to_parquet(p))
        _put("scores_long.parquet", lambda p: run.scores.to_parquet(p))
    ctx = getattr(bt, "last_pit_context", None)
    if ctx is not None:                                   # FND-0193: the run's DB-side inputs, frozen next to its scores
        _put("inputs", lambda p: save_inputs_snapshot(out_dir, ctx["inputs"], ctx["hist"], ctx.get("target")))
    _put("timings.json", lambda p: p.write_text(json.dumps(
        {"seconds": run.timings, "cache_hits": run.cache_hits}, indent=2, default=str), encoding="utf-8"))
    manifest = {**manifest, "artifacts": sorted(written) + ["manifest.json"]}
    _put("manifest.json", lambda p: p.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8"))
    return written


def _setup_logging(stamp: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"log_P3_pitBacktest_{stamp}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(fmt)
        root.addHandler(h)
    return path


def apply_flag_overrides(specs, cfg=None) -> dict:
    """Apply "NAME=0|1" overrides to the P2 bundle flags of shared.config for this process; returns the EFFECTIVE state of
    every bundle flag. Unknown names or values are rejected (a typo must not silently run the wrong experiment)."""
    if cfg is None:
        import shared.config as cfg
    for spec in specs or []:
        name, sep, raw = spec.partition("=")
        name = name.strip()
        if name not in cfg.P2_BUNDLE_FLAGS:
            raise SystemExit(f"--flag {spec!r}: {name!r} is not a P2 bundle flag; choose one of {list(cfg.P2_BUNDLE_FLAGS)}")
        if raw.strip().lower() not in ("0", "1", "true", "false"):
            raise SystemExit(f"--flag {spec!r}: the value must be 0/1 (or true/false)")
        setattr(cfg, name, raw.strip().lower() in ("1", "true"))
    return {n: bool(getattr(cfg, n)) for n in cfg.P2_BUNDLE_FLAGS}


def main(argv=None, conn=None) -> int:
    args = build_parser().parse_args(argv)
    flag_state = apply_flag_overrides(args.flag)
    assert_release_coupling()   # FND-0243 D4: checked AFTER --flag overrides, before any connection
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _setup_logging(stamp)
    logger.info("P2 bundle flags in effect: %s", flag_state)
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / stamp
    grid_bps = tuple(float(x) for x in args.grid_bps.split(",") if x.strip())
    hyst_bands = tuple(float(x) for x in args.hysteresis_grid.split(",") if x.strip())
    conn = conn or get_connection()

    isins = resolve_isins(conn, args)
    n_universe = len(isins) if isins is not None else conn.execute(
        "SELECT COUNT(DISTINCT isin) FROM fund_nav_monthly").fetchone()[0]
    scope = "FULL UNIVERSE" if isins is None else f"{len(isins)} ISINs"
    logger.info("P3 PIT backtest | %s | %s .. %s | %.0f bp main, grid %s, entry_sides=%d | cache %s | out %s",
                scope, args.start, args.end or "last regime month", args.bps_main, grid_bps, args.entry_sides,
                "OFF" if args.no_cache else args.cache_dir, out_dir)
    if isins is None:
        logger.info("FULL universe: ~%d funds with monthly NAV. Expected ~20-25 min cold, <1 min with a warm cache "
                    "(estimate from a 32-fund sample; owner-run).", n_universe)
    if args.dry_run:
        logger.info("--dry-run: nothing computed, nothing written (log: %s)", log_path)
        return 0

    t0 = time.perf_counter()
    bt = Backtester.for_pit(conn)
    table = bt.run_pit(start_date=args.start, end_date=args.end, isins=isins, cache_dir=args.cache_dir,
                       use_cache=not args.no_cache, max_stale_days=args.max_stale_days,
                       current_universe_only=args.current_universe_only, tx_cost_bps=args.bps_main,
                       entry_sides=args.entry_sides, hysteresis_band=args.hysteresis_band,
                       macro_variant=None if args.macro_variant == "none" else args.macro_variant,
                       macro_workers=args.macro_workers, keep_detail=args.keep_detail,
                       snap_nav_month_end=args.snap_nav_month_end)
    if table is None or table.empty:
        logger.error("nothing to evaluate (no regime history or empty date range)")
        return EXIT_NOTHING_TO_EVALUATE
    grid = bt.pit_cost_sensitivity(bps=grid_bps, entry_sides=args.entry_sides)
    hysteresis = bt.pit_hysteresis_experiment(bands=hyst_bands) if hyst_bands else None
    crisis_variants = bt.pit_crisis_variants() if args.macro_variant != "none" else None
    summary = bt.summary(table)
    elapsed = time.perf_counter() - t0

    manifest = {
        "started": stamp, "elapsed_seconds": round(elapsed, 1), "git_commit": _git_commit(), "scope": scope,
        "n_isins_requested": None if isins is None else len(isins), "n_funds_with_nav": int(n_universe),
        "args": vars(args), "p2_bundle_flags": flag_state, "cache_code_version": CODE_VERSION,
        "regime_publication_lags_months": REGIME_PUBLICATION_LAG_MONTHS,
        "dates": [str(table.index.min().date()), str(table.index.max().date()), int(len(table))],
        "python": sys.version.split()[0], "pandas": pd.__version__, "log": str(log_path),
        "not_modelled": [NOT_MODELLED_NOTICE] + ([WRONG_DOC_NOTICE] if P3_WRONG_DOC_EXCLUSION_ENABLED else []),
        "wrong_doc_exclusion": {"enabled": bool(P3_WRONG_DOC_EXCLUSION_ENABLED)},      # FND-0323 D1: the rule this run applied
        # FND-0254: the build-time eligibility settings this run applied (the live rule when the switch is ON), auditable from the artifact
        "data_eligibility": {"enabled": bool(FUND_DATA_ELIGIBILITY_ENABLED), "stale_nav_days": int(args.max_stale_days),
                             "live_stale_nav_days": int(P3_FUND_NAV_MAX_AGE_DAYS),
                             "nav_continuity": ({"min_coverage": P3_FUND_NAV_MIN_COVERAGE, "max_gap_run_months": P3_FUND_NAV_MAX_GAP_RUN_MONTHS}
                                                if FUND_DATA_ELIGIBILITY_ENABLED else None)},
    }
    written = write_artifacts(out_dir, table, grid, bt, summary, manifest, save_scores=not args.no_scores,
                              hysteresis=hysteresis, crisis_variants=crisis_variants)
    if crisis_variants is not None:
        logger.info("crisis multiplier variants (FND-0225):\n%s", crisis_variants.round(4).to_string(index=False))
    if hysteresis is not None:
        logger.info("hysteresis experiment (turnover / Sharpe by band):\n%s",
                    hysteresis[["hysteresis_band", "mean_turnover", "annual_cost_drag", "ann_return", "sharpe",
                                "max_drawdown", "mean_excess_12m"]].round(4).to_string(index=False))
    print(summary)
    logger.info("done in %.1f s | stages %s | artifacts in %s", elapsed,
                {k: round(v, 1) for k, v in bt.last_pit_run.timings.items()}, out_dir)
    for name in sorted(written):
        logger.info("  %s", written[name])
    return 0


if __name__ == "__main__":
    from shared.backlog_client import capture_exceptions
    _rc = 0
    with capture_exceptions(object_name="p3_pit_backtest.py", object_type="JOB"):
        _rc = main()
    sys.exit(_rc)
