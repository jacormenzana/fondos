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
# FND-0295/0296: a PARTIAL NAV load (752 of 3,739 funds without data on 2026-10-18) left ~20% of the funds a week behind the rest and nothing reported it:
# the NAV_STALE_DAYS line only sees > 60 days. A fund is "lagging" when its newest NAV is more than NAV_LAG_TOLERANCE_DAYS behind the reference date; the
# tolerance is the statistical audit's cross-section tolerance (snapshot.build_snapshot tolerance_days=5): a lagging fund is held out of every cross-sectional
# audit block and the promotion gate [GATE:DATA_COVERAGE] can then refuse a scenario that contains it.
NAV_LAG_TOLERANCE_DAYS = 5
NAV_LAG_ATTENTION_SHARE = 0.05   # report it when more than this share of the active funds lags
COVERAGE_DROP_PP = 2.0           # a P3-consumed metric losing more than this many points of coverage
TOP_N = 12                       # rows shown per ranked list
HEDGING_DRIFT_BATCH = 200        # rows per server-side cursor fetch (bounded memory: the texts are ~15 kB each)
# Time budget of the drift check. Measured 2026-10-08 on the live corpus: 13.8 s for 2,552 funds, ~98% of it in the KIID regexes (re-measured
# with a profiler: compiling them costs nothing), so ~14 s is the I/O + regex baseline, not a regression. Within the budget the run is a plain
# INFO line; only an overrun (a real degradation: a slower disk, a regex that backtracks, a corpus that doubled) is a WARNING.
HEDGING_DRIFT_SLOW_S = 20.0
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

Q_NAV_NEWEST = """
    SELECT n.isin, MAX(n.date) AS newest
    FROM fund_nav_monthly n
    JOIN fund_master fm ON fm.isin = n.isin
    LEFT JOIN nav_sources ns ON ns.isin = n.isin
    WHERE fm.in_current_universe = 1
      AND COALESCE(ns.data_status, 'OK') <> 'STALE_FROZEN'
    GROUP BY n.isin
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

# FND-0243: active funds with NAV whose share-class currency is unknown. With EUR_NAV_CONVERSION_ENABLED they are EXCLUDED from
# EUR scoring (never assumed EUR); while it is off they are scored on their stored NAV as if it were EUR.
Q_CLASS_CCY_UNKNOWN = """
    SELECT fm.isin
    FROM fund_master fm
    WHERE fm.in_current_universe = 1
      AND fm.fund_currency IS NULL
      AND EXISTS (SELECT 1 FROM fund_nav_monthly n WHERE n.isin = fm.isin)
    ORDER BY fm.isin
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

def _eur_view_on() -> bool:
    sys.path.insert(0, str(ROOT))
    from shared import config
    return bool(config.EUR_NAV_CONVERSION_ENABLED)


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
        "class_ccy_unknown": [r[0] for r in conn.execute(Q_CLASS_CCY_UNKNOWN).fetchall()],
        "eur_view_on": _eur_view_on(),
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
    s = res["seconds"]
    if res["slow"]:
        log.warning("hedging_drift slow: total=%.1fs (limit %.0fs) fetch=%.1fs detect=%.1fs compare=%.1fs scanned=%d",
                    s["total"], HEDGING_DRIFT_SLOW_S, s["fetch"], s["detect"], s["compare"], res["scanned"])
    else:                                  # within the budget: the facts, as a neutral diagnostic
        log.info("hedging_drift: %d funds drift of %d scanned in %.1fs (fetch=%.1fs detect=%.1fs compare=%.1fs)%s",
                 res["count"], res["scanned"], s["total"], s["fetch"], s["detect"], s["compare"],
                 (": " + ", ".join(res["isins"])) if res["isins"] else "")
    return res


# ── analysis (pure: no database, R-7) ─────────────────────────────────────────────────────────────

def collect_backlog_links() -> dict:
    """Relationship state of the action-point backlog (scripts/audit/backlog_links.py; NORMAS_BACKLOG.md). Never raises: the backlog
    is a different database, and its being down must not cost the cycle report."""
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "audit"))
        import backlog_links
        return backlog_links.cycle_summary()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def collect_governance(conn, today: date | None = None) -> dict:
    """Data-governance facts of the cycle (FND-0251): accepted audit residuals about to lapse (or whose AP is already CLOSED) and the
    reasoned overrides the PRODUCTION scenario was promoted with. Read-only; never raises (an unreadable source becomes an `error` key,
    not a failed report)."""
    out: dict = {"residual_attention": [], "production_overrides": {}}
    try:
        sys.path.insert(0, str(ROOT))
        from shared.config import AUDIT_RESIDUAL_EXPIRY_WARN_DAYS
        from shared.backlog_client import get_action_point_status
        from shared.statistical_audit.accepted_residuals import attention_lines
        for domain in ("p2_metrics", "cost_attributes"):
            out["residual_attention"] += [f"[{domain}] {line}" for line in
                                          attention_lines(conn, domain, AUDIT_RESIDUAL_EXPIRY_WARN_DAYS, today,
                                                          ap_status=get_action_point_status)]
    except Exception as exc:                                      # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    try:
        from proyecto3.src import scenario_lifecycle as lc
        prod = lc.production_scenario_id(conn)
        if prod:
            row = conn.execute("SELECT message FROM ingestion_log WHERE step = %s AND message LIKE %s "
                               "ORDER BY created_at DESC LIMIT 1", (lc.STEP_PROMOTED, f"scenario {prod} promoted%")).fetchone()
            message = row[0] if row else ""
            out["production_overrides"] = {"scenario": prod,
                                           "overrides": [p.strip() for p in message.split("; ") if p.strip().startswith("[OVERRIDE:")]}
    except Exception as exc:                                      # noqa: BLE001
        try:
            conn.rollback()
        except Exception:                                         # noqa: BLE001
            pass
        out.setdefault("error", f"{type(exc).__name__}: {exc}")
    return out


def nav_lag_summary(newest: dict, tolerance_days: int = NAV_LAG_TOLERANCE_DAYS) -> dict:
    """How many funds are behind the rest. `newest` = {isin: newest monthly NAV date}. The REFERENCE is the 90th-percentile date, not the maximum:
    a handful of future- or weekend-stamped points must not make every other fund look late. A fund lags when reference - newest > tolerance_days."""
    if not newest:
        return {"funds": 0, "reference": None, "lagging": 0, "share": 0.0, "examples": []}
    dates = sorted(newest.values())
    reference = dates[int(0.9 * (len(dates) - 1))]
    lagging = sorted((i for i, d in newest.items() if (reference - d).days > tolerance_days), key=lambda i: (newest[i], i))
    return {"funds": len(newest), "reference": str(reference), "lagging": len(lagging), "share": len(lagging) / len(newest),
            "oldest": str(newest[lagging[0]]) if lagging else None, "examples": lagging[:5]}


def collect_nav_lag(conn) -> dict:
    """NAV lag of the active universe (nav_lag_summary). Read-only; never raises (an unreadable source becomes an `error` key)."""
    try:
        return nav_lag_summary({r[0]: r[1] for r in conn.execute(Q_NAV_NEWEST).fetchall()})
    except Exception as exc:                                  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:                                     # noqa: BLE001
            pass
        return {"error": f"{type(exc).__name__}: {exc}"}


def collect_macro_freshness(conn, today: date) -> dict:
    """Per-series staleness of series_macro against each series' own limit (shared/macro_freshness.py, FND-0256) and the leg-by-leg state of the
    composite regime inputs (ipc_yoy_avg). Read-only, informational; never raises (an unreadable source becomes an `error` key)."""
    try:
        sys.path.insert(0, str(ROOT))
        from shared import macro_freshness as mf
        rows = conn.execute("SELECT indicator, geography, MAX(date) FROM series_macro GROUP BY indicator, geography").fetchall()
        statuses = mf.assess({(r[0], r[1]): r[2] for r in rows}, today)
        composites = mf.composite_legs(statuses)
        return {"series": len(statuses), "stale": [f"{s.indicator}/{s.geography}" for s in statuses if s.stale],
                "composites": {k: v["state"] for k, v in composites.items()}, "lines": mf.attention_lines(statuses, composites)}
    except Exception as exc:                                  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:                                     # noqa: BLE001
            pass
        return {"error": f"{type(exc).__name__}: {exc}"}


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
    unk = report.get("class_ccy_unknown", [])               # absent in reports written before this check (FND-0243)
    if unk:
        fate = ("EXCLUIDOS del scoring EUR (EUR_NAV_CONVERSION_ENABLED)" if report.get("eur_view_on") else
                "puntuados hoy como si su NAV fuera EUR; quedaran EXCLUIDOS al activar EUR_NAV_CONVERSION_ENABLED")
        items.append(f"{len(unk)} fondos activos con NAV y sin divisa de clase (Fund_Currency NULL): {fate} "
                     f"(p. ej. {', '.join(unk[:5])}); resolver en P1 (FND-0243)")
    hd = report.get("hedging_drift") or {}                  # absent in reports written before this check
    if hd.get("count"):
        items.append(f"{hd['count']} fondos activos con Hedging_Policy distinta de Hedged que el parser actual deriva como HEDGED "
                     f"(p. ej. {', '.join(hd.get('isins', [])[:5])}): deriva por cambio de codigo, no por KIID nuevo; "
                     "un ciclo P1 completo los corrige (COALESCE). Esperado tras el ciclo: 0")
    if hd.get("error"):
        items.append(f"no se pudo calcular la deriva de Hedging_Policy: {hd['error']}")
    bl = report.get("backlog_links") or {}                  # absent in reports written before this check
    if bl.get("blocked_in_progress"):
        items.append(f"{len(bl['blocked_in_progress'])} tickets en curso con un prerequisito DEPENDS_ON sin cerrar "
                     f"({', '.join(bl['blocked_in_progress'][:6])}): backlog_links.py --for <id>")
    gov = report.get("governance") or {}                    # absent in reports written before this check (FND-0251)
    for line in gov.get("residual_attention", []):
        items.append(line)
    po = gov.get("production_overrides") or {}
    if po.get("overrides"):
        items.append(f"el escenario PRODUCCION {po['scenario']} se promovio con {len(po['overrides'])} override(s) de datos que siguen "
                     f"vigentes hasta que otro lo sustituya: {' | '.join(po['overrides'])[:300]}")
    if gov.get("error"):
        items.append(f"no se pudo evaluar la gobernanza de datos (residuales / overrides): {gov['error']}")
    nl = report.get("nav_lag") or {}                        # absent in reports written before this check (FND-0295/0296)
    if nl.get("error"):
        items.append(f"no se pudo evaluar el desfase de NAV: {nl['error']}")
    elif nl.get("funds") and nl["share"] > NAV_LAG_ATTENTION_SHARE:
        items.append(f"{nl['lagging']} de {nl['funds']} fondos activos ({100 * nl['share']:.0f}%) tienen su ultimo NAV mas de {NAV_LAG_TOLERANCE_DAYS} dias por "
                     f"detras de la fecha de referencia {nl['reference']} (el mas antiguo {nl['oldest']}; p. ej. {', '.join(nl['examples'][:3])}): carga NAV parcial. "
                     "La auditoria los excluye de los bloques transversales y la puerta [GATE:DATA_COVERAGE] puede rechazar una cartera que los contenga. "
                     "Revisar 'Fondos sin datos' en el log de la carga y relanzar solo la carga (nav_discovery --mode load)")
    mfr = report.get("macro_freshness") or {}               # absent in reports written before this check (FND-0256)
    items += mfr.get("lines", [])
    if mfr.get("error"):
        items.append(f"no se pudo evaluar la frescura de las series macro: {mfr['error']}")
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
    unk = report.get("class_ccy_unknown")
    if unk is not None:
        L += [f"Divisa de clase desconocida (activos con NAV): {len(unk)}"] + ([f"    {', '.join(unk[:TOP_N])}"] if unk else [])
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
            if hd.get("isins"):                  # neutral diagnostic: which funds (the first HEDGING_DRIFT_LIST)
                L += ["    " + ", ".join(hd["isins"])]
    nl = report.get("nav_lag")
    if nl and not nl.get("error") and nl.get("funds"):
        L += [f"Desfase de NAV: {nl['lagging']} de {nl['funds']} fondos activos ({100 * nl['share']:.1f}%) mas de {NAV_LAG_TOLERANCE_DAYS} dias por detras de "
              f"la referencia {nl['reference']}"]
    mfr = report.get("macro_freshness")
    if mfr and not mfr.get("error"):
        L += [f"Frescura macro: {mfr['series']} series, {len(mfr['stale'])} caducadas"
              + (f" ({', '.join(mfr['stale'][:6])})" if mfr["stale"] else "")
              + "; entradas de regimen: " + ", ".join(f"{k}={v}" for k, v in mfr["composites"].items())]
    bl = report.get("backlog_links")
    if bl:
        if bl.get("error"):
            L += [f"Relaciones del backlog: no disponible ({bl['error']})"]
        elif not bl.get("store_applied"):
            L += [f"Relaciones del backlog: almacen sin aplicar; {bl['candidates_unreviewed']} candidatas sin revisar "
                  f"({bl['candidates_stated']} citadas en texto), {bl['clusters']} grupos entre {bl['open']} tickets abiertos"]
        else:
            L += [f"Relaciones del backlog: {bl['links_confirmed']} confirmadas, {bl['open_depends_on']} DEPENDS_ON entre abiertos, "
                  f"{bl['candidates_unreviewed']} candidatas sin revisar ({bl['candidates_stated']} citadas en texto), "
                  f"{bl['clusters']} grupos (mayor {bl['largest_cluster']})"]
    L += ["-" * 60]
    L += [f"[ATENCION] {i}" for i in items] if items else ["Sin puntos de atencion."]
    return "\n".join(L)


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    if not logging.getLogger().handlers:             # make the INFO diagnostics and the overrun WARNING visible in the launcher's log
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
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
            report["backlog_links"] = collect_backlog_links()
            report["governance"] = collect_governance(conn, today)
            report["macro_freshness"] = collect_macro_freshness(conn, today)
            report["nav_lag"] = collect_nav_lag(conn)
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
