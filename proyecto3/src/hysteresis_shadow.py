# proyecto3/src/hysteresis_shadow.py
# -*- coding: utf-8 -*-
"""FND-0158 -- monthly SHADOW comparison of the incumbent bonus (hysteresis).

Design of record: doc/decisiones/DESIGN_FND-0158_hysteresis_shadow.md. After a real build has persisted its DRAFT (the OFF arm: no incumbents), the same
selection is run again, in memory, with incumbents and ONE append-only row is written to `ingestion_log` (step P3_HYSTERESIS_SHADOW). It never changes what
the DRAFT holds and never changes the build's return code: any failure becomes one P3_HYSTERESIS_SHADOW_ERROR row (or a printed warning) and the build goes on.
`PORTFOLIO_HYSTERESIS_ENABLED` is never read here and stays OFF: this module only measures.

Two streams per row (band = portfolio_builder.HYSTERESIS_BAND):
  one_step  incumbents = the PRODUCTION funds (what the owner actually holds): the exact diff and turnover of the next promotion.
  chain     incumbents = the funds of the previous month's ON portfolio (its own history; the first month starts from PRODUCTION).

Cost convention = the PIT backtest's (`pit_backtest.monthly_turnover`, `hysteresis_experiment`): traded notional = sum |w_new - w_base| over funds, cost drag =
traded notional x tx_cost_bps / 1e4 (25 bp). `one_way_turnover` = traded notional / 2 is stored next to it.

The top of the module is pure (no database, no `pipeline` / `portfolio_builder` import: R-7). `run_shadow` and `shadow_after_build` take the builder and the
connection as arguments, so tests drive them with fakes.
"""
from __future__ import annotations

import contextlib
import io
import json
import types
from datetime import datetime, timezone

STEP = "P3_HYSTERESIS_SHADOW"
STEP_ERROR = "P3_HYSTERESIS_SHADOW_ERROR"
STATUS = "SHADOW"
STATUS_ERROR = "ERROR"
ROW_VERSION = 1
TX_COST_BPS = 25.0                    # PIT convention (pit_backtest.hysteresis_experiment default)
_MAX_MESSAGE = 200000                 # ingestion_log.message is text; a row is about 10 KB, this only guards against a runaway


class ShadowSkip(Exception):
    """The shadow cannot be measured this month for a stated reason (no PRODUCTION, empty candidates, ...). Written as an ERROR row."""


# --------------------------------------------------------------------------------------------------------------------------------------------------
# pure functions
# --------------------------------------------------------------------------------------------------------------------------------------------------
def fund_rows(funds: list) -> list:
    """`portfolio.all_funds` -> a compact, JSON-safe list [{isin, subportfolio, weight, score}] (master weight)."""
    out = []
    for f in funds or []:
        out.append({"isin": f["isin"], "subportfolio": f.get("subportfolio"), "weight": round(float(f.get("master_weight", 0.0) or 0.0), 6),
                    "score": (round(float(f["score"]), 4) if f.get("score") is not None else None)})
    return sorted(out, key=lambda r: (str(r["subportfolio"]), r["isin"]))


def weights_of(rows: list) -> dict:
    """[{isin, weight}] -> {isin: weight}; an ISIN held in two sub-portfolios adds up."""
    w: dict = {}
    for r in rows or []:
        w[r["isin"]] = w.get(r["isin"], 0.0) + float(r["weight"])
    return w


def traded_notional(new: dict, base: dict) -> float:
    """sum |w_new - w_base| over every fund (target weights, drift ignored). A fund missing on one side counts as weight 0."""
    return sum(abs(new.get(i, 0.0) - base.get(i, 0.0)) for i in set(new) | set(base))


def one_way_turnover(new: dict, base: dict) -> float:
    """Half the traded notional: the share of capital that moves out (and in)."""
    return traded_notional(new, base) / 2.0


def cost_drag(traded: float, tx_cost_bps: float = TX_COST_BPS) -> float:
    """PIT convention: traded notional x bps per side (a fraction of capital)."""
    return traded * tx_cost_bps / 1e4


def selection_diff(off_rows: list, on_rows: list, incumbents: set) -> dict:
    """ISINs only in the OFF portfolio (what hysteresis kept out), only in the ON portfolio, and `kept_by_hysteresis` = the ON-only funds that are
    incumbents (the ones OFF would have rotated out). Each entry carries its sub-portfolio and score. Two equal portfolios: three empty lists."""
    off_by = {r["isin"]: r for r in off_rows or []}
    on_by = {r["isin"]: r for r in on_rows or []}

    def pick(by, isins):
        return [{"isin": i, "subportfolio": by[i].get("subportfolio"), "score": by[i].get("score")} for i in sorted(isins)]

    only_off, only_on = set(off_by) - set(on_by), set(on_by) - set(off_by)
    return {"only_off": pick(off_by, only_off), "only_on": pick(on_by, only_on),
            "kept_by_hysteresis": pick(on_by, {i for i in only_on if i in incumbents})}


def pair_rotations(exits: list, entries: list) -> list:
    """Pair the funds that left with the funds that came in, inside the same sub-portfolio, by descending weight (an estimate: the builder does not
    say which entry replaced which exit). Returns [(isin_out, isin_in, weight_in)]; unmatched funds are not priced."""
    pairs = []
    subs = sorted({str(r.get("subportfolio")) for r in list(exits) + list(entries)})
    for sub in subs:
        outs = sorted((r for r in exits if str(r.get("subportfolio")) == sub), key=lambda r: (-float(r["weight"]), r["isin"]))
        ins = sorted((r for r in entries if str(r.get("subportfolio")) == sub), key=lambda r: (-float(r["weight"]), r["isin"]))
        for o, i in zip(outs, ins):
            pairs.append((o["isin"], i["isin"], float(i["weight"])))
    return pairs


def stream_metrics(on_rows: list, base_rows: list, off_rows: list, incumbents: set, cost_fn=None, tx_cost_bps: float = TX_COST_BPS) -> dict:
    """Everything one stream stores. `base_rows` = the baseline of that stream (PRODUCTION for one_step and for the live arm, the previous ON portfolio
    for the chain). `cost_fn(isin_out, isin_in)` -> fraction of capital (portfolio_builder.estimate_rotation_cost) or None."""
    w_on, w_base = weights_of(on_rows), weights_of(base_rows)
    traded = traded_notional(w_on, w_base)
    ins = [r for r in on_rows if r["isin"] not in w_base]
    outs = [r for r in base_rows if r["isin"] not in w_on]
    est = None
    if cost_fn is not None:
        try:
            est = round(sum(float(cost_fn(o, i)) * w for o, i, w in pair_rotations(outs, ins)), 6)
        except Exception:                                                     # noqa: BLE001 -- a missing fee never loses the row
            est = None
    return {"weights": on_rows,
            "n_funds": len(w_on), "traded_notional": round(traded, 6), "one_way_turnover": round(traded / 2.0, 6),
            "cost_drag": round(cost_drag(traded, tx_cost_bps), 6),
            "funds_in": sorted(i["isin"] for i in ins), "funds_out": sorted(o["isin"] for o in outs),
            "rotation_cost_estimate": est,
            "diff_vs_off": selection_diff(off_rows, on_rows, incumbents)}


def build_row(*, scenario_id: str, band: float, off_rows: list, production_rows: list, one_step_rows: list, chain_rows: list, chain_base_rows: list,
              chain_base_scenario: str | None, calc_versions: dict | None, identity: dict | None,
              rotation_gate_enabled: bool, cost_fn=None, tx_cost_bps: float = TX_COST_BPS, now: datetime | None = None) -> dict:
    """The JSON stored in `ingestion_log.message`. `off_rows` is the live (OFF) arm; the baselines are PRODUCTION (live and one_step) and the previous ON
    portfolio (chain; PRODUCTION on the first month)."""
    prod_isins = {r["isin"] for r in production_rows}
    chain_isins = {r["isin"] for r in chain_base_rows}
    live = {"n_funds": len(weights_of(off_rows)), "traded_notional": round(traded_notional(weights_of(off_rows), weights_of(production_rows)), 6)}
    live["one_way_turnover"] = round(live["traded_notional"] / 2.0, 6)
    live["cost_drag"] = round(cost_drag(live["traded_notional"], tx_cost_bps), 6)
    return {"v": ROW_VERSION, "scenario_id": scenario_id, "built_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "band": band, "tx_cost_bps": tx_cost_bps, "calc_versions": calc_versions or {}, "build_identity": identity or {},
            "rotation_gate_enabled": bool(rotation_gate_enabled),
            "baseline_scenario": None, "chain_base_scenario": chain_base_scenario,
            "off": {**live, "weights": off_rows},
            "one_step": stream_metrics(one_step_rows, production_rows, off_rows, prod_isins, cost_fn, tx_cost_bps),
            "chain": stream_metrics(chain_rows, chain_base_rows, off_rows, chain_isins, cost_fn, tx_cost_bps)}


def parse_row(message: str) -> dict:
    """Read a stored row back; raises ValueError for anything that is not one of ours (bad JSON, wrong shape)."""
    data = json.loads(message)
    if not isinstance(data, dict) or data.get("v") != ROW_VERSION or "chain" not in data or "weights" not in data["chain"]:
        raise ValueError("not a P3_HYSTERESIS_SHADOW row")
    return data


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def summary_line(rows: list) -> str:
    """ONE line for the build output / cycle report: months accumulated, mean one-way turnover OFF (live) / ON (one-step), mean ON cost drag, mean
    number of funds the hysteresis kept. `rows` = parsed rows, oldest first."""
    if not rows:
        return "hysteresis shadow: 0 months accumulated"
    off_t = _mean([r["off"]["one_way_turnover"] for r in rows])
    on_t = _mean([r["one_step"]["one_way_turnover"] for r in rows])
    drag = _mean([r["one_step"]["cost_drag"] for r in rows])
    kept = _mean([float(len(r["one_step"]["diff_vs_off"]["kept_by_hysteresis"])) for r in rows])
    return (f"hysteresis shadow (band {rows[-1]['band']:.2f}, switch OFF): {len(rows)} month(s); mean one-way turnover OFF {off_t:.1%} / ON {on_t:.1%}; "
            f"mean ON cost drag {drag:.2%}; mean funds kept by hysteresis {kept:.1f}")


# --------------------------------------------------------------------------------------------------------------------------------------------------
# I/O glue (the builder and the connection are arguments)
# --------------------------------------------------------------------------------------------------------------------------------------------------
def _portfolio_rows(portfolio) -> list:
    return fund_rows(portfolio.all_funds)


def _as_incumbent_portfolio(rows: list, scenario_id: str):
    """A minimal object with what `PortfolioBuilder.build(previous_portfolio=...)` reads: `.sub_portfolios[].name` and `.funds[]["isin"]`."""
    subs: dict = {}
    for r in rows:
        subs.setdefault(r.get("subportfolio"), []).append({"isin": r["isin"], "weight": r["weight"], "role": "", "fund_nature": ""})
    return types.SimpleNamespace(scenario_id=scenario_id,
                                 sub_portfolios=[types.SimpleNamespace(name=n, funds=f, regime_weight=1.0) for n, f in sorted(subs.items(), key=lambda kv: str(kv[0]))])


def read_rows(conn, limit: int = 120) -> list:
    """The stored rows, oldest first; a row that does not parse is skipped (it is only history)."""
    cur = conn.execute("SELECT id, message FROM ingestion_log WHERE step = %s ORDER BY id DESC LIMIT %s", (STEP, int(limit)))
    out = []
    for _id, message in reversed(cur.fetchall()):
        try:
            out.append(parse_row(message))
        except (ValueError, TypeError):
            continue
    return out


def _last_row(conn):
    cur = conn.execute("SELECT message FROM ingestion_log WHERE step = %s ORDER BY id DESC LIMIT 1", (STEP,))
    row = cur.fetchone()
    if not row:
        return None
    try:
        return parse_row(row[0])
    except (ValueError, TypeError):
        return None                                                          # bad JSON in the previous row: the chain restarts from PRODUCTION


def _write(conn, step: str, status: str, message: str) -> None:
    conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (NULL, %s, %s, %s, now())", (step, status, message[:_MAX_MESSAGE]))
    conn.commit()


def _calc_versions(conn, isins: list) -> dict:
    """{algorithm_version: number of the live portfolio's funds with a stored metric row on it}: the CALC_VERSION mix the row was built on. Never raises."""
    try:
        cur = conn.execute("SELECT algorithm_version, COUNT(DISTINCT isin) FROM fund_metrics WHERE isin = ANY(%s) GROUP BY algorithm_version", (list(isins),))
        return {str(v): int(n) for v, n in cur.fetchall()}
    except Exception:                                                        # noqa: BLE001 -- information only
        try:
            conn.rollback()
        except Exception:                                                    # noqa: BLE001
            pass
        return {}


def run_shadow(conn, builder, regime_result, scenario_id: str, live_portfolio, *, band: float, cost_fn=None, identity=None,
               rotation_gate_enabled: bool = False, calc_versions: dict | None = None) -> dict:
    """Build the two ON arms in memory (dry_run=True: nothing is persisted) and return the row. Raises ShadowSkip when there is nothing to compare with."""
    production = builder.load_previous(exclude_scenario_id=scenario_id)
    if production is None:
        raise ShadowSkip("no PRODUCTION scenario: the live arm has no incumbents to compare with")
    off_rows = _portfolio_rows(live_portfolio)
    if not off_rows:
        raise ShadowSkip("the live build has no funds (empty candidates)")
    production_rows = _portfolio_rows(production)
    if calc_versions is None:
        calc_versions = _calc_versions(conn, [r["isin"] for r in off_rows])

    last = _last_row(conn)
    chain_base_rows = (last["chain"]["weights"] if last else None) or production_rows
    chain_base_scenario = (last["scenario_id"] if last else None) or production.scenario_id

    with contextlib.redirect_stdout(io.StringIO()):                          # the builder prints its sub-portfolio lines: the log shows only the live arm
        one_step = builder.build(regime_result, scenario_id=scenario_id, dry_run=True, previous_portfolio=production)
        chain = builder.build(regime_result, scenario_id=scenario_id, dry_run=True,
                              previous_portfolio=_as_incumbent_portfolio(chain_base_rows, chain_base_scenario))
    row = build_row(scenario_id=scenario_id, band=band, off_rows=off_rows, production_rows=production_rows, one_step_rows=_portfolio_rows(one_step),
                    chain_rows=_portfolio_rows(chain), chain_base_rows=chain_base_rows, chain_base_scenario=chain_base_scenario, calc_versions=calc_versions,
                    identity=identity, rotation_gate_enabled=rotation_gate_enabled, cost_fn=cost_fn)
    row["baseline_scenario"] = production.scenario_id
    return row


def shadow_after_build(conn, builder, regime_result, scenario_id: str, live_portfolio, *, enabled: bool, band: float, cost_fn=None, identity=None,
                       rotation_gate_enabled: bool = False, calc_versions: dict | None = None) -> str | None:
    """The fail-soft hook: NEVER raises. Returns the line to print (or None when the switch is OFF). Every failure is one P3_HYSTERESIS_SHADOW_ERROR row
    (and, if even that cannot be written, a printed warning): the build's return code and its DRAFT are never touched."""
    if not enabled:
        return None
    try:
        row = run_shadow(conn, builder, regime_result, scenario_id, live_portfolio, band=band, cost_fn=cost_fn, identity=identity,
                         rotation_gate_enabled=rotation_gate_enabled, calc_versions=calc_versions)
        _write(conn, STEP, STATUS, json.dumps(row, separators=(",", ":"), default=str))
        return summary_line(read_rows(conn))
    except Exception as exc:                                                 # noqa: BLE001 -- the shadow measures, it never refuses
        reason = f"{type(exc).__name__}: {str(exc)[:300]}"
        try:
            conn.rollback()
        except Exception:                                                    # noqa: BLE001
            pass
        try:
            _write(conn, STEP_ERROR, STATUS_ERROR, json.dumps({"v": ROW_VERSION, "scenario_id": scenario_id, "reason": reason}))
        except Exception:                                                    # noqa: BLE001
            try:
                conn.rollback()
            except Exception:                                                # noqa: BLE001
                pass
            return f"AVISO: hysteresis shadow failed and its error row could not be written ({reason})"
        return f"AVISO: hysteresis shadow skipped ({reason}); one {STEP_ERROR} row written"
