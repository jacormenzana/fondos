#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p1p2_cycle_report.py -- post-cycle diagnostic report of P1_P2_Complete.bat (read-only).

Why: the operational-reliability checks of a P1->P2 cycle (the SQL in the pipelineP1P2Audit skill) were
only ever run by hand, after the fact, so a cycle could end "OK" while leaving funds silently stale or a
P3-consumed metric with fewer funds than the cycle before. This runs them at the end of every cycle,
compares with the previous report and lists what needs a look, so the next analysis starts from
facts instead of from a log grep.

What it reports (active universe = fund_master.in_current_universe = 1):
  * universe size and its change, Fund_Nature mix, KIID status mix;
  * reliability: SRRI / Profile NULL, Data_Quality_Flag WARN / MISSING, WRONG_DOC funds whose
    flag is not WARN (their old values stay valid-looking for P2/P3), NATURE_LOW_CONFIDENCE this cycle;
  * data-quality issues detected this cycle, by check_code and level;
  * NAV: funds whose newest monthly NAV is older than NAV_STALE_DAYS (frozen funds counted apart),
    nav_sources status mix;
  * P2: real/nominal pairing gaps, coverage of every metric P3 reads (since_inception) and its change
    vs the previous report, rolling alerts by level;
  * hedging drift (FND-0244): active funds whose stored Hedging_Policy is not Hedged although the CURRENT parser
    derives HEDGED from their cached KIID text and name. It is caused by CODE changes, not by new KIIDs, so it looks
    at the whole active universe, with the same derivation a P1 pass persists (kiid_parser.derive_hedging_policy);
    expected to be 0 after a full P1 cycle. Streamed in server-side batches, timed (a breakdown is always printed; over
    HEDGING_DRIFT_SLOW_S a WARNING with it), skippable with --no-hedging-drift.

Output: text on stdout and P1_P2_cycle_report_<stamp>.json in --out-dir. The previous report is the
newest other P1_P2_cycle_report_*.json there. Read-only; exit 0 unless the database cannot be read.
A finding is only a line starting with `[ATENCION]` -- the report never decides to stop a cycle.

Usage:
    python scripts/launch/p1p2_cycle_report.py --stamp 20261004_153000 --since 2026-10-04
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("p1p2_cycle_report")
DEFAULT_OUT_DIR = ROOT / "proyecto1" / "log"
REPORT_GLOB = "P1_P2_cycle_report_*.json"

NAV_STALE_DAYS = 60              # prices older than this are "stale" (pipelineP1P2Audit, NAV-staleness gate)
COVERAGE_DROP_PP = 2.0           # a P3-consumed metric losing more than this many points of coverage
TOP_N = 12                       # rows shown per ranked list
HEDGING_DRIFT_BATCH = 200        # rows per server-side cursor fetch (bounded memory: the texts are ~15 kB each)
HEDGING_DRIFT_SLOW_S = 5.0       # over this the check logs a WARNING with its time breakdown (it still runs; --no-hedging-drift skips it)
HEDGING_DRIFT_LIST = 20          # ISINs kept in the JSON

RC_OK, RC_DB_UNREADABLE = 0, 1
_LEVEL_RANK = {"WARN": 0, "MISSING": 1, "INFERRED": 2}      # DQ levels, most actionable first; INFO last

# ── SQL (static literals: tests/test_sql_explain_sweep_pg.py EXPLAINs each against the real DDL) ──

Q_UNIVERSE = """
    SELECT COUNT(*) FILTER (WHERE in_current_universe = 1) AS active,
           COUNT(*) FILTER (WHERE in_current_universe = 0) AS inactive,
           COUNT(*) AS total
    FROM fund_master
"""

Q_NATURE = """
    SELECT fund_nature, COUNT(*) AS n
    FROM fund_master
    WHERE in_current_universe = 1
    GROUP BY fund_nature
    ORDER BY n DESC, fund_nature
"""

Q_KIID_STATUS = """
    SELECT COALESCE(km.kiid_status, '-') AS kiid_status, COUNT(*) AS n
    FROM fund_kiid_metadata km
    JOIN fund_master fm ON fm.isin = km.isin
    WHERE km.kiid_class = 1 AND fm.in_current_universe = 1
    GROUP BY COALESCE(km.kiid_status, '-')
    ORDER BY n DESC, kiid_status
"""

Q_RELIABILITY = """
    SELECT COUNT(*) FILTER (WHERE srri IS NULL) AS srri_null,
           COUNT(*) FILTER (WHERE profile IS NULL) AS profile_null,
           COUNT(*) FILTER (WHERE data_quality_flag = 'WARN') AS dqf_warn,
           COUNT(*) FILTER (WHERE data_quality_flag = 'MISSING') AS dqf_missing
    FROM fund_master
    WHERE in_current_universe = 1
"""

# WRONG_DOC skips publish_fund: the previous classification and costs persist and look valid.
Q_WRONG_DOC_STALE = """
    SELECT fm.isin, fm.fund_nature, COALESCE(fm.data_quality_flag, '-') AS flag
    FROM fund_master fm
    JOIN fund_kiid_metadata km ON km.isin = fm.isin AND km.kiid_class = 1
    WHERE km.kiid_status = 'WRONG_DOC'
      AND fm.in_current_universe = 1
      AND COALESCE(fm.data_quality_flag, '') <> 'WARN'
    ORDER BY fm.isin
"""

Q_LOW_CONFIDENCE = """
    SELECT COUNT(DISTINCT isin) AS n
    FROM ingestion_log
    WHERE step = 'NATURE_LOW_CONFIDENCE' AND created_at >= %s
"""

Q_DQ_CYCLE = """
    SELECT check_code, level, COUNT(*) AS n
    FROM fund_data_quality_issues
    WHERE detected_at >= %s
    GROUP BY check_code, level
    ORDER BY n DESC, check_code, level
"""

Q_NAV_STALE = """
    SELECT n.isin, MAX(n.date) AS newest, COALESCE(MIN(ns.data_status), '-') AS data_status
    FROM fund_nav_monthly n
    JOIN fund_master fm ON fm.isin = n.isin
    LEFT JOIN nav_sources ns ON ns.isin = n.isin
    WHERE fm.in_current_universe = 1
    GROUP BY n.isin
    HAVING MAX(n.date) < %s
    ORDER BY newest, n.isin
"""

Q_NAV_SOURCES = """
    SELECT COALESCE(status, '-') AS status, COALESCE(data_status, '-') AS data_status, COUNT(*) AS n
    FROM nav_sources
    GROUP BY COALESCE(status, '-'), COALESCE(data_status, '-')
    ORDER BY n DESC, status, data_status
"""

# Every deflatable nominal metric must have its real pair; a single is a silent deflation gap.
Q_REAL_ORPHANS = """
    SELECT a.metric, COUNT(*) AS n
    FROM fund_metrics a
    JOIN fund_master fm ON fm.isin = a.isin
    WHERE fm.in_current_universe = 1
      AND a.real_flag = 0
      AND a.metric IN ('return_ann', 'sharpe', 'max_dd', 'vol_ann')
      AND NOT EXISTS (SELECT 1 FROM fund_metrics b
                      WHERE b.isin = a.isin AND b.metric = a.metric AND b.horizon = a.horizon
                        AND b.metric_version = a.metric_version AND b.real_flag = 1)
    GROUP BY a.metric
    ORDER BY a.metric
"""

# The metrics proyecto3/src/fund_scorer.py reads at since_inception (return_ann is read deflated).
Q_COVERAGE = """
    SELECT m.metric, COUNT(DISTINCT m.isin) AS n
    FROM fund_metrics m
    JOIN fund_master fm ON fm.isin = m.isin
    WHERE fm.in_current_universe = 1
      AND m.horizon = 'since_inception'
      AND m.value IS NOT NULL
      AND m.value <> 'NaN'::float8
      AND ((m.metric = 'return_ann' AND m.real_flag = 1)
        OR (m.metric IN ('sharpe', 'max_dd', 'alpha_persistence', 'capture_ratio', 'momentum_rank',
                         'fx_contribution_pct', 'srri_nav',
                         'crisis_stress_score_mdd', 'crisis_stress_score_ttr') AND m.real_flag = 0))
    GROUP BY m.metric
    ORDER BY m.metric
"""

# A CALC_VERSION-drift backfill that recomputed funds but ran the macro OLS for none: the quarterly OLS gate
# skipped everything and the macro betas stay on the old version (2026-10-05 RCA). RUN_SUMMARY rows from before
# FND-0208 carry no ols_funds= field and never match.
Q_OLS_ZERO_BACKFILL = """
    SELECT s.batch_id
    FROM p2_pipeline_log s
    WHERE s.step = 'RUN_SUMMARY'
      AND s.created_at >= %s
      AND s.message ~ 'ols_funds=0( |$)'
      AND EXISTS (SELECT 1 FROM p2_pipeline_log b
                  WHERE b.batch_id = s.batch_id AND b.step = 'BACKFILL_START'
                    AND starts_with(b.message, 'CALC_VERSION drift'))
    ORDER BY s.created_at
"""

Q_ALERTS = """
    SELECT a.level, COUNT(*) AS n
    FROM fund_metric_alerts a
    JOIN fund_master fm ON fm.isin = a.isin
    WHERE fm.in_current_universe = 1
    GROUP BY a.level
    ORDER BY a.level
"""

# FND-0244 hedging drift. The fixes only ever move a fund TOWARD Hedged, so a fund already stored as Hedged cannot drift and is not read
# (the texts are the heavy part: ~40 MB over the ~2,550 remaining active funds). Streamed through a server-side cursor.
Q_HEDGING_CANDIDATES = """
    SELECT fm.isin, fm.fund_name, fm.hedging_policy, k.language, k.raw_kiid_text
    FROM fund_master fm
    JOIN fund_kiid_metadata k ON k.isin = fm.isin AND k.kiid_class = 1
    WHERE fm.in_current_universe = 1
      AND fm.hedging_policy IS DISTINCT FROM 'Hedged'
      AND k.raw_kiid_text IS NOT NULL
    ORDER BY fm.isin
"""


# ── collection ────────────────────────────────────────────────────────────────────────────────────
# Every statement is passed to execute() as its Q_* constant, never through a wrapper: the SQL sweep
# (tests/test_sql_explain_sweep_pg.py) can only EXPLAIN a statement it can see as a literal at the call.

def collect(conn, since: date, today: date) -> dict:
    """The raw facts of one cycle as a JSON-serialisable dict. Read-only."""
    active, inactive, total = conn.execute(Q_UNIVERSE).fetchall()[0]
    rel = conn.execute(Q_RELIABILITY).fetchall()[0]
    wrong_doc = [{"isin": r[0], "fund_nature": r[1], "flag": r[2]}
                 for r in conn.execute(Q_WRONG_DOC_STALE).fetchall()]
    nav_stale = [{"isin": r[0], "newest": str(r[1]), "data_status": r[2]}
                 for r in conn.execute(Q_NAV_STALE, (today - timedelta(days=NAV_STALE_DAYS),)).fetchall()]
    dq = [{"check_code": r[0], "level": r[1], "n": int(r[2])}
          for r in conn.execute(Q_DQ_CYCLE, (since,)).fetchall()]
    return {
        "since": str(since),
        "today": str(today),
        "universe": {"active": int(active), "inactive": int(inactive), "total": int(total)},
        "nature": {r[0]: int(r[1]) for r in conn.execute(Q_NATURE).fetchall()},
        "kiid_status": {r[0]: int(r[1]) for r in conn.execute(Q_KIID_STATUS).fetchall()},
        "reliability": {"srri_null": int(rel[0]), "profile_null": int(rel[1]),
                        "dqf_warn": int(rel[2]), "dqf_missing": int(rel[3])},
        "wrong_doc_stale": wrong_doc,
        "low_confidence_nature": int(conn.execute(Q_LOW_CONFIDENCE, (since,)).fetchall()[0][0]),
        "dq_cycle": dq,
        "nav_stale": nav_stale,
        "nav_sources": {f"{r[0]}/{r[1]}": int(r[2]) for r in conn.execute(Q_NAV_SOURCES).fetchall()},
        "real_orphans": {r[0]: int(r[1]) for r in conn.execute(Q_REAL_ORPHANS).fetchall()},
        "coverage": {r[0]: int(r[1]) for r in conn.execute(Q_COVERAGE).fetchall()},
        "alerts": {r[0]: int(r[1]) for r in conn.execute(Q_ALERTS).fetchall()},
        "ols_zero_backfills": [r[0] for r in conn.execute(Q_OLS_ZERO_BACKFILL, (since,)).fetchall()],
    }


# ── hedging drift (FND-0244) ──────────────────────────────────────────────────────────────────────

def scan_hedging_drift(batches, derive, clock=time.perf_counter, slow_s: float = HEDGING_DRIFT_SLOW_S) -> dict:
    """Pure over its inputs. `batches` yields lists of rows (isin, fund_name, stored policy, language, kiid text); `derive(text, language,
    name)` returns the policy a P1 pass would persist. Counts the funds whose stored policy is not Hedged while `derive` says HEDGED, and
    times the three phases separately: waiting for rows (fetch), the detectors (detect) and the comparison (compare)."""
    secs = {"fetch": 0.0, "detect": 0.0, "compare": 0.0}
    drift, scanned = [], 0
    it = iter(batches)
    while True:
        t0 = clock()
        batch = next(it, None)
        secs["fetch"] += clock() - t0
        if batch is None:
            break
        t0 = clock()
        derived = [derive(text, lang, name) for _isin, name, _stored, lang, text in batch]
        secs["detect"] += clock() - t0
        t0 = clock()
        for (isin, _name, stored, _lang, _text), policy in zip(batch, derived):
            if policy == "HEDGED" and (stored or "").upper() != "HEDGED":
                drift.append(isin)
        secs["compare"] += clock() - t0
        scanned += len(batch)
    total = sum(secs.values())
    return {"skipped": False, "scanned": scanned, "count": len(drift), "isins": drift[:HEDGING_DRIFT_LIST],
            "seconds": {**{k: round(v, 3) for k, v in secs.items()}, "total": round(total, 3)}, "slow": total > slow_s}


def _hedging_batches(conn, size: int = HEDGING_DRIFT_BATCH):
    """Server-side cursor: only `size` texts are ever in memory, whatever the size of the universe."""
    with conn.cursor(name="hedging_drift") as cur:
        cur.execute(Q_HEDGING_CANDIDATES)
        while True:
            rows = cur.fetchmany(size)
            if not rows:
                return
            yield rows


def collect_hedging_drift(conn) -> dict:
    """Fail-soft: the diagnostics never change the cycle, so any problem becomes an `error` entry in the report, not an exception."""
    try:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "proyecto1"))          # P1 modules import each other as `core.*`
        from core.kiid_parser import derive_hedging_policy
        res = scan_hedging_drift(_hedging_batches(conn), lambda text, lang, name: derive_hedging_policy(text, lang, name)[0])
    except Exception as exc:
        return {"skipped": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if res["slow"]:
        s = res["seconds"]
        log.warning("hedging_drift slow: total=%.1fs (limit %.0fs) fetch=%.1fs detect=%.1fs compare=%.1fs scanned=%d",
                    s["total"], HEDGING_DRIFT_SLOW_S, s["fetch"], s["detect"], s["compare"], res["scanned"])
    return res


# ── analysis (pure: no database, R-7) ─────────────────────────────────────────────────────────────

def coverage_pct(report: dict) -> dict:
    """Share (0-100) of the active universe that has each P3-consumed metric."""
    active = report.get("universe", {}).get("active", 0)
    if not active:
        return {}
    return {m: 100.0 * n / active for m, n in report.get("coverage", {}).items()}


def _delta(cur: dict, prev: dict | None) -> dict:
    """{key: cur - prev} over the keys present in either; empty without a previous report."""
    if not prev:
        return {}
    return {k: cur.get(k, 0) - prev.get(k, 0) for k in set(cur) | set(prev) if cur.get(k, 0) != prev.get(k, 0)}


def attention_items(report: dict, previous: dict | None) -> list:
    """What deserves a look, as sentences. Informational: nothing here stops a cycle."""
    items = []
    u = report["universe"]
    if previous:
        d = u["active"] - previous.get("universe", {}).get("active", u["active"])
        if d:
            items.append(f"universo activo {d:+d} fondos desde el informe anterior ({previous.get('stamp', '?')})")
    if report["wrong_doc_stale"]:
        items.append(f"{len(report['wrong_doc_stale'])} fondos WRONG_DOC con Data_Quality_Flag distinto de WARN "
                     "(clasificacion y costes antiguos con apariencia de vigentes para P2/P3)")
    stale = [r for r in report["nav_stale"] if r["data_status"] != "STALE_FROZEN"]
    if stale:
        items.append(f"{len(stale)} fondos activos con NAV mensual de mas de {NAV_STALE_DAYS} dias sin estar "
                     "marcados STALE_FROZEN (revisar nav_discovery --mode update)")
    orphans = sum(report["real_orphans"].values())
    if orphans:
        items.append(f"{orphans} metricas nominales sin su par real (hueco silencioso de deflacion): "
                     + ", ".join(f"{m}={n}" for m, n in report["real_orphans"].items()))
    zero_ols = report.get("ols_zero_backfills", [])          # absent in reports written before this check
    if zero_ols:
        items.append(f"{len(zero_ols)} ejecucion(es) P2 con backfill por CALC_VERSION terminaron con ols_funds=0 "
                     f"({', '.join(zero_ols)}): el OLS macro no se recalculo y las betas macro siguen en la version "
                     "anterior; la puerta de betas (beta_shift_audit) fallara")
    hd = report.get("hedging_drift") or {}                  # absent in reports written before this check
    if hd.get("count"):
        items.append(f"{hd['count']} fondos activos con Hedging_Policy distinta de Hedged que el parser actual deriva como HEDGED "
                     f"(p. ej. {', '.join(hd.get('isins', [])[:5])}): deriva por cambio de codigo, no por KIID nuevo; "
                     "un ciclo P1 completo los corrige (COALESCE). Esperado tras el ciclo: 0")
    if hd.get("error"):
        items.append(f"no se pudo calcular la deriva de Hedging_Policy: {hd['error']}")
    # A slow run is NOT an attention item: it is a constant of the corpus (measured 2026-10-08: ~14 s, all in the KIID regexes), so an item would
    # repeat every cycle. It is a WARNING in the log and a marker on the report line; the check stays active (--no-hedging-drift skips it).
    if previous:
        cur_pct, prev_pct = coverage_pct(report), coverage_pct(previous)
        for m in sorted(cur_pct):
            if m in prev_pct and prev_pct[m] - cur_pct[m] > COVERAGE_DROP_PP:
                items.append(f"cobertura de {m} cae {prev_pct[m] - cur_pct[m]:.1f} pp "
                             f"({prev_pct[m]:.1f}% -> {cur_pct[m]:.1f}%): metrica que lee P3")
        for m in sorted(set(prev_pct) - set(cur_pct)):
            items.append(f"{m} ya no tiene ningun fondo con valor (antes {prev_pct[m]:.1f}%): metrica que lee P3")
    return items


def find_previous(out_dir: Path, current_name: str) -> Path | None:
    """Newest report in out_dir other than the one being written (names sort chronologically)."""
    others = sorted(p for p in out_dir.glob(REPORT_GLOB) if p.name != current_name)
    return others[-1] if others else None


def load_report(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _kv(title: str, cur: dict, prev: dict | None, top: int = TOP_N) -> list:
    delta = _delta(cur, prev.get(title) if prev else None) if prev else {}
    out = []
    for k, n in sorted(cur.items(), key=lambda kv: (-kv[1], kv[0]))[:top]:
        d = f"  ({delta[k]:+d})" if k in delta else ""
        out.append(f"    {k:<34} {n:>7}{d}")
    return out


def render_text(report: dict, previous: dict | None, items: list) -> str:
    u, rel = report["universe"], report["reliability"]
    L = ["=" * 60,
         f" P1+P2 -- informe de ciclo  {report.get('stamp', '')}",
         f" ciclo desde {report['since']}; comparado con: {previous.get('stamp', '?') if previous else 'nada (primer informe)'}",
         "=" * 60,
         f"Universo: activos={u['active']} inactivos={u['inactive']} total={u['total']}",
         "Fund_Nature (activos):"] + _kv("nature", report["nature"], previous)
    L += ["KIID_Status (activos, KIID_Class=1):"] + _kv("kiid_status", report["kiid_status"], previous)
    L += [f"Fiabilidad: SRRI nulo={rel['srri_null']}  Profile nulo={rel['profile_null']}  "
          f"DQ flag WARN={rel['dqf_warn']} MISSING={rel['dqf_missing']}  "
          f"Fund_Nature con confianza baja este ciclo={report['low_confidence_nature']}"]
    wd = report["wrong_doc_stale"]
    L += [f"WRONG_DOC con flag != WARN: {len(wd)}"] + [f"    {r['isin']}  {r['fund_nature']}  flag={r['flag']}" for r in wd[:TOP_N]]
    dq = sorted(report["dq_cycle"], key=lambda r: (_LEVEL_RANK.get(r["level"], 9), -r["n"], r["check_code"]))
    L += [f"Incidencias DQ detectadas desde {report['since']}: {sum(r['n'] for r in dq)} en {len(dq)} grupos"]
    L += [f"    {r['level']:<9} {r['check_code']:<40} {r['n']:>6}" for r in dq[:TOP_N]]
    ns = report["nav_stale"]
    frozen = sum(1 for r in ns if r["data_status"] == "STALE_FROZEN")
    L += [f"NAV mensual > {NAV_STALE_DAYS} dias: {len(ns)} fondos activos ({frozen} STALE_FROZEN)"]
    L += [f"    {r['isin']}  ultimo NAV {r['newest']}  data_status={r['data_status']}"
          for r in [r for r in ns if r["data_status"] != "STALE_FROZEN"][:TOP_N]]
    L += ["nav_sources (status/data_status):"] + _kv("nav_sources", report["nav_sources"], previous)
    L += [f"Pares real/nominal incompletos: {sum(report['real_orphans'].values())}"
          + (" (" + ", ".join(f"{m}={n}" for m, n in report["real_orphans"].items()) + ")" if report["real_orphans"] else "")]
    pct, ppct = coverage_pct(report), coverage_pct(previous) if previous else {}
    L += ["Cobertura de las metricas que lee P3 (since_inception, % del universo activo):"]
    for m in sorted(pct):
        d = f"  ({pct[m] - ppct[m]:+.1f} pp)" if m in ppct and abs(pct[m] - ppct[m]) >= 0.05 else ""
        L += [f"    {m:<28} {report['coverage'][m]:>6}  {pct[m]:6.1f}%{d}"]
    L += ["Alertas rolling (activos): " + (", ".join(f"{k}={v}" for k, v in report["alerts"].items()) or "ninguna")]
    hd = report.get("hedging_drift")
    if hd:
        if hd.get("skipped"):
            L += ["Deriva de Hedging_Policy: omitida (--no-hedging-drift)"]
        elif hd.get("error"):
            L += [f"Deriva de Hedging_Policy: no disponible ({hd['error']})"]
        else:
            s = hd["seconds"]
            L += [f"Deriva de Hedging_Policy: {hd['count']} fondos de {hd['scanned']} revisados "
                  f"(fetch={s['fetch']:.1f}s detect={s['detect']:.1f}s compare={s['compare']:.1f}s total={s['total']:.1f}s)"
                  + (f" [WARN lento: limite {HEDGING_DRIFT_SLOW_S:.0f}s]" if hd.get("slow") else "")]
    L += ["-" * 60]
    L += [f"[ATENCION] {i}" for i in items] if items else ["Sin puntos de atencion."]
    return "\n".join(L)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"),
                    help="id of this report (names the JSON file); default: now")
    ap.add_argument("--since", default=None,
                    help="YYYY-MM-DD, start of the cycle (DQ issues and low-confidence rows are counted from "
                         "it); default: today")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--no-hedging-drift", dest="hedging_drift", action="store_false",
                    help="skip the Hedging_Policy drift check (it streams ~40 MB of cached KIID text; on by default)")
    args = ap.parse_args(argv)

    today = date.today()
    try:
        since = date.fromisoformat(args.since) if args.since else today
    except ValueError:
        print(f"[ERROR] --since expects YYYY-MM-DD, got {args.since!r}")
        return 4
    out_dir = Path(args.out_dir)
    json_name = f"P1_P2_cycle_report_{args.stamp}.json"

    try:
        sys.path.insert(0, str(ROOT))
        from shared.db import get_connection
        conn = get_connection()
        try:
            report = collect(conn, since, today)
            report["hedging_drift"] = collect_hedging_drift(conn) if args.hedging_drift else {"skipped": True}
        finally:
            conn.close()
    except Exception as exc:
        print(f"[ERROR] cycle report skipped: {type(exc).__name__}: {exc}")
        return RC_DB_UNREADABLE

    report["stamp"] = args.stamp
    previous = load_report(find_previous(out_dir, json_name))
    print(render_text(report, previous, attention_items(report, previous)))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / json_name).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nInforme: {out_dir / json_name}")
    except OSError as exc:
        print(f"[WARN] no se pudo guardar el JSON del informe ({exc}); la comparacion del proximo ciclo no tendra base")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
