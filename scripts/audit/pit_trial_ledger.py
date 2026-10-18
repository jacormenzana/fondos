"""Trial ledger of the P3 model-value study (FND-0193): every configuration ever evaluated on the PIT artifacts.

The deflated Sharpe ratio and the probability of backtest overfitting need N = the number of configurations that were
tried, not only the ones that were reported. This scanner enumerates them exhaustively and conservatively (an UPPER bound):

* every config row of every grid CSV (`oos_grid`, `cadence_grid`, `hysteresis_experiment`, `cost_sensitivity`,
  `crisis_variants`) under both artifact roots,
* every run folder (manifest = one run-level variant: flags, macro variant, universe, start),
* every PIT run log (sample, dry-run and failed attempts leave a log but no folder).

Read-only, standard library only. Writes the ledger CSV and a summary JSON; prints the summary.

    python scripts/audit/pit_trial_ledger.py --out-csv doc/decisiones/LEDGER_FND-0193.csv --out-json doc/decisiones/LEDGER_FND-0193.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

DEFAULT_ROOTS = (r"out\reports\pit_backtest", r"c:\data\fondos\reports\pit_backtest")
DEFAULT_LOG_DIR = r"proyecto3\log"
RUN_RE = re.compile(r"^\d{8}_\d{6}$")
GRID_FILES = {
    "oos_grid.csv": "oos_grid",
    "cadence_grid.csv": "cadence_grid",
    "hysteresis_experiment.csv": "hysteresis",
    "cost_sensitivity.csv": "cost_sensitivity",
    "crisis_variants.csv": "crisis_variants",
}
# Config-defining columns per grid kind (the period / split / cost live in the folder name or in a column that does not
# define a distinct strategy configuration).
CONFIG_COLS = {
    "oos_grid": ("hysteresis_band", "weight_band"),
    "cadence_grid": ("cadence_months",),
    "hysteresis": ("hysteresis_band",),
    "cost_sensitivity": ("bps_per_side",),
    "crisis_variants": ("scope", "variant", "z_cut"),
}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def scan(repo: Path, roots, log_dir: Path) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    seen_files: dict[tuple[str, str], str] = {}  # (run, relative path) -> root where first seen
    legacy_only: list[str] = []
    runs_with_manifest: set[str] = set()
    for root_s in roots:
        root = Path(root_s)
        root = root if root.is_absolute() else repo / root
        if not root.is_dir():
            continue
        for run_dir in sorted(p for p in root.iterdir() if p.is_dir() and RUN_RE.match(p.name)):
            run = run_dir.name
            for path in sorted(run_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(run_dir).as_posix()
                key = (run, rel)
                if key in seen_files:
                    continue  # the same artifact present in the old and the new root: counted once
                seen_files[key] = str(root)
                if root_s != roots[0]:
                    legacy_only.append(f"{run}/{rel}")  # present only in the old root: still counted
                if rel == "manifest.json":
                    runs_with_manifest.add(run)
                    m = json.loads(path.read_text(encoding="utf-8"))
                    a = m.get("args", {})
                    rows.append({"kind": "run", "run": run, "folder": "", "config": json.dumps(
                        {"scope": m.get("scope"), "flag": a.get("flag"), "macro": a.get("macro_variant"), "cuo": a.get("current_universe_only"),
                         "start": a.get("start"), "hyst": a.get("hysteresis_band"), "git": m.get("git_commit")}, sort_keys=True),
                        "period": "", "sharpe": "", "max_drawdown": "", "source": str(path)})
                    continue
                name = path.name
                if name in GRID_FILES:
                    kind = GRID_FILES[name]
                    folder = Path(rel).parent.as_posix() if "/" in rel else ""
                    for r in _read_csv(path):
                        cfg = {c: r.get(c) for c in CONFIG_COLS[kind]}
                        if kind == "cost_sensitivity":
                            cfg["window_m"] = r.get("window_m")
                        rows.append({"kind": kind, "run": run, "folder": folder, "config": json.dumps(cfg, sort_keys=True),
                                     "period": r.get("period", ""), "sharpe": r.get("sharpe", ""),
                                     "max_drawdown": r.get("max_drawdown", ""), "source": str(path)})
    n_logs = 0
    log_only_runs: list[str] = []
    if log_dir.is_dir():
        for p in sorted(log_dir.glob("log_P3_pitBacktest_*.log")):
            run = p.stem.rsplit("_", 2)[-2] + "_" + p.stem.rsplit("_", 1)[-1]
            n_logs += 1
            if run not in runs_with_manifest:
                log_only_runs.append(run)
                rows.append({"kind": "run_log_only", "run": run, "folder": "", "config": json.dumps({"log": p.name}),
                             "period": "", "sharpe": "", "max_drawdown": "", "source": str(p)})
    info = {"legacy_root_files_not_in_new_root": sorted(set(legacy_only)), "n_run_logs": n_logs, "log_only_runs": log_only_runs}
    return rows, info


def summarise(rows: list[dict], info: dict) -> dict:
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    # N_upper: every row, except that a grid row is counted once per (folder, config) -- its design/blind pair is one configuration.
    seen = set()
    n_upper = 0
    for r in rows:
        if r["kind"] in ("run", "run_log_only"):
            n_upper += 1
            continue
        k = (r["run"], r["kind"], r["folder"], r["config"])
        if k not in seen:
            seen.add(k)
            n_upper += 1
    # N_distinct: collapse split / cost / run copies of the same strategy configuration.
    distinct = {(r["kind"], r["config"]) for r in rows if r["kind"] not in ("run", "run_log_only", "cost_sensitivity")}
    sharpes = [s for s in (_f(r["sharpe"]) for r in rows if r["kind"] in ("oos_grid", "cadence_grid", "hysteresis") and r["period"] in ("", "design", "blind")) if s is not None]
    var_sh = statistics.pvariance(sharpes) if len(sharpes) > 1 else None
    return {
        "rows_by_kind": by_kind,
        "n_total_rows": len(rows),
        "N_scan_upper": n_upper,
        "N_scan_x2": 2 * n_upper,
        "N_distinct_strategy_configs": len(distinct),
        "n_runs_with_manifest": by_kind.get("run", 0),
        "n_run_logs": info["n_run_logs"],
        "n_log_only_runs": len(info["log_only_runs"]),
        "n_sharpe_values": len(sharpes),
        "var_annualised_sharpe_across_trials": var_sh,
        "legacy_root_files_not_in_new_root": info["legacy_root_files_not_in_new_root"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    ap.add_argument("--out-csv")
    ap.add_argument("--out-json")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    log_dir = Path(a.log_dir)
    log_dir = log_dir if log_dir.is_absolute() else repo / log_dir
    rows, info = scan(repo, a.roots, log_dir)
    summ = summarise(rows, info)
    if a.out_csv:
        Path(a.out_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out_csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["kind", "run", "folder", "config", "period", "sharpe", "max_drawdown", "source"])
            w.writeheader()
            w.writerows(rows)
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(summ, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summ, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
