"""
audit_benchmark_consistency.py
================================
Read-only diagnostic that audits `fund_benchmarks` vs `fund_master` for
characterization consistency.  Opens the DB in read-only mode — no writes.

Two Exercises
-------------
A. Multi-benchmark ISINs (KIID source vs Morningstar source):
   A1 – asset-class agreement between the two sources
   A2 – geography / sector / cap dimension divergence between the two names
   A3 – benchmark_role coherence

B. Cross-consistency (ALL ISINs, single + multi):
   B1 – asset_class ↔ Fund_Nature
   B2 – Geography
   B3 – Sector / Theme
   B4 – Market_Cap_Focus
   B5 – Currency / Hedging_Policy
   B6 – Credit_Quality / Duration_Profile  (Fixed Income only)
   B7 – Benchmark_Declared vs KIID benchmark_name (extraction drift)

Severity tiers: CRITICAL · WARN · INFO/INFERRED · BENIGN
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ── repo root on sys.path so we can import P1 modules ─────────────────────────
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from proyecto1.core.classify_utils import (
    SECTOR_FOCUS_TRANSLATION_MAP,
    THEME_TO_SECTOR_FOCUS_MAP,
    THEMATIC_MAP,
    detect_geography,
    detect_theme,
    map_theme_to_sector_focus,
    normalize_geography_en,       # canonical ES→EN (R-1 / P#11)
    # SC-H vocabulary — single source of truth (R-1 / 2026-07-15):
    BMK_CONSISTENT,
    BMK_TOLERATED,
    BMK_BENIGN_SOURCE_PAIRS,
    BMK_GEO_BENIGN_PAIRS,
    BMK_SECTOR_BENIGN_PAIRS,
    bmk_tok_credit,
    bmk_tok_duration,
    bmk_tok_cap,
    bmk_geography,
    bmk_sector,
    bmk_severity_nature,
)

# ── DB path ───────────────────────────────────────────────────────────────────
DB_PATH = _REPO / "db" / "fondos.sqlite"

# ── Local aliases → SC-H canonical vocabulary (R-1 / 2026-07-15) ─────────────
# All benchmark-comparison constants and helpers have been promoted to
# classify_utils.py (BMK_CONSISTENT, bmk_tok_credit, etc.) as the single source
# of truth.  The names below are local aliases so the rest of this file can
# keep its original identifiers without a rename sweep.
_CONSISTENT         = BMK_CONSISTENT
_TOLERATED          = BMK_TOLERATED
_BENIGN_SOURCE_PAIRS = BMK_BENIGN_SOURCE_PAIRS
_GEO_BENIGN_PAIRS   = BMK_GEO_BENIGN_PAIRS
_SECTOR_BENIGN_PAIRS = BMK_SECTOR_BENIGN_PAIRS
_tok_credit         = bmk_tok_credit
_tok_duration       = bmk_tok_duration
_tok_cap            = bmk_tok_cap
_bmk_geography      = bmk_geography
_bmk_sector         = bmk_sector


def _tok_hedged(name_l: str) -> Optional[str]:
    """Returns currency code if explicitly hedged in benchmark name, else None."""
    import re
    m = re.search(r'(\w+)\s*hedged', name_l)
    if m:
        return m.group(1).upper()
    if "hedged" in name_l:
        return "HEDGED"
    return None


def _tok_currency_in_name(name_l: str) -> Optional[str]:
    """Detect base-currency token from benchmark name (not the hedged suffix)."""
    import re
    cleaned = re.sub(r'\b(gr|nr|tr|net|hedged)\b', ' ', name_l)
    cleaned = re.sub(r'\b(eur|usd|gbp|chf|jpy|sek|nok|dkk|pln|czk|huf|aud|cad|sgd)\b',
                     ' CURR ', cleaned)
    for cur in ["eur", "usd", "gbp", "chf", "jpy"]:
        if f" {cur} " in f" {name_l} ":
            return cur.upper()
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _severity_b1(fund_nature: str, ac: Optional[str], confidence: str) -> str:
    """Return CRITICAL / WARN / INFO / OK for B1 check.
    Thin wrapper over bmk_severity_nature (classify_utils, single source of truth).
    """
    return bmk_severity_nature(fund_nature, ac, confidence)


def _root_cause_b1(fund_nature: str, ac: str, source: str) -> str:
    """Industry-level root-cause hypothesis for B1 mismatches."""
    if fund_nature == "Renta Variable" and ac in ("Rate", "Money Market"):
        return ("Benchmark is likely a Morningstar category proxy, not the fund mandate. "
                "Verify: some equity funds use a cash rate as hurdle. If asset_proxy role, "
                "KIID benchmark mis-extracted or fund is misclassified (possible liquid-alt).")
    if fund_nature == "Renta Variable" and ac == "Fixed Income":
        return ("Strong misclassification signal: equity fund benchmarked to a bond index. "
                "Check if fund is a convertible/balanced that was classified as pure equity, "
                "or if the Morningstar category assignment is wrong.")
    if fund_nature in ("Renta Fija Flexible", "Renta Fija Corto Plazo") and ac == "Equity":
        return ("Bond fund with equity benchmark: likely a classification block error "
                "(fund with equity-like KIID language classified as FI) or benchmark mis-tag. "
                "Could also be a total-return fund that uses equity as absolute-return proxy.")
    if fund_nature == "Monetario" and ac in ("Fixed Income", "Mixed"):
        return ("Money market fund benchmarked to a bond or mixed index. Common if the fund "
                "holds short-duration credit (VNAV MMF). If duration > 3M, consider "
                "reclassifying to Renta Fija Corto Plazo.")
    if fund_nature == "Mixtos" and ac in ("Equity", "Fixed Income"):
        return ("Mixed fund with a single-asset-class proxy is partially expected; both "
                "sources together should cover the two poles. If both sources point to the "
                "same single class, the fund may be misclassified.")
    return (f"Fund_Nature '{fund_nature}' vs benchmark asset_class '{ac}' (source: {source}). "
            "Verify fund mandate in KIID vs Morningstar category. Consider FORCE_REFRESH.")


def _root_cause_geo(fm_geo: str, bmk_geo: str, bmk_name: str) -> str:
    return (
        f"fund_master.Geography='{fm_geo}' but benchmark '{bmk_name}' signals '{bmk_geo}'. "
        "Hypothesis: (a) classifier used fund-name geography that was wrong, "
        "(b) benchmark is a global/regional proxy even though fund has a specific mandate, "
        "(c) fund reclassified its universe after the last KIID parse."
    )


def _root_cause_credit(fm_cq: str, bmk_cq: str, bmk_name: str) -> str:
    return (
        f"fund_master.Credit_Quality='{fm_cq}' but benchmark '{bmk_name}' signals '{bmk_cq}'. "
        "Most common cause: KIID text described credit quality in a way that matched the "
        "wrong pattern (e.g. 'investment-grade universe' in a HY fund's risk section). "
        "Also check if fund has a dual IG/HY mandate and was assigned to the wrong pole."
    )


# ── Main audit ────────────────────────────────────────────────────────────────

def run_audit(db_path: Path = DB_PATH) -> dict:
    """Run the full audit. Returns a structured findings dict."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    # ── Load data ──────────────────────────────────────────────────────────────
    bmk_rows = con.execute(
        "SELECT * FROM fund_benchmarks ORDER BY ISIN, source"
    ).fetchall()
    fm_rows = con.execute(
        "SELECT ISIN, Fund_Name, Fund_Nature, Geography, Sector_Focus, Theme, "
        "       Market_Cap_Focus, Fund_Currency, Asset_Currency, Hedging_Policy, "
        "       Credit_Quality, Duration_Profile, Benchmark_Declared, Benchmark_Type, "
        "       Alt_Strategy, SRRI, Management_Company "
        "FROM fund_master"
    ).fetchall()

    fm: dict[str, dict] = {r["ISIN"]: dict(r) for r in fm_rows}

    # Group benchmarks by ISIN → list[row]
    bmk_by_isin: dict[str, list] = defaultdict(list)
    for r in bmk_rows:
        bmk_by_isin[r["ISIN"]].append(dict(r))

    # ─────────────────────────────────────────────────────────────────────────
    # EXERCISE A — Multi-benchmark ISINs
    # ─────────────────────────────────────────────────────────────────────────
    a1_disagree_material:  list[dict] = []
    a1_disagree_benign:    list[dict] = []
    a1_null_ac:            list[dict] = []
    a2_geo_diverge:        list[dict] = []
    a2_sector_diverge:     list[dict] = []
    a2_cap_diverge:        list[dict] = []
    a3_role_conflict:      list[dict] = []

    multi_isins = {isin for isin, rows in bmk_by_isin.items() if len(rows) >= 2}

    for isin in sorted(multi_isins):
        rows   = bmk_by_isin[isin]
        master = fm.get(isin, {})
        nature = master.get("Fund_Nature", "")

        kiid_row = next((r for r in rows if r["source"] == "KIID"), None)
        ms_row   = next((r for r in rows if r["source"] == "MORNINGSTAR"), None)
        if not kiid_row or not ms_row:
            # only two of the same source — skip A1
            continue

        k_ac = kiid_row.get("asset_class")
        m_ac = ms_row.get("asset_class")
        k_name = kiid_row.get("benchmark_name") or ""
        m_name = ms_row.get("benchmark_name") or ""

        # ─ A1 asset-class agreement ──────────────────────────────────────
        if k_ac is None or m_ac is None:
            a1_null_ac.append({"ISIN": isin, "Fund_Name": master.get("Fund_Name",""),
                                "Fund_Nature": nature,
                                "kiid_ac": k_ac, "ms_ac": m_ac,
                                "kiid_bmk": k_name, "ms_bmk": m_name})
        elif k_ac != m_ac:
            pair = frozenset({k_ac, m_ac})
            record = {
                "ISIN": isin, "Fund_Name": master.get("Fund_Name",""),
                "Fund_Nature": nature,
                "kiid_ac": k_ac, "ms_ac": m_ac,
                "kiid_bmk": k_name, "ms_bmk": m_name,
                "kiid_conf": kiid_row.get("confidence"),
                "ms_conf":   ms_row.get("confidence"),
                "nature_agrees_with": (
                    "KIID" if k_ac in _CONSISTENT.get(nature, frozenset())
                    else ("MS" if m_ac in _CONSISTENT.get(nature, frozenset())
                          else "NEITHER")
                ),
            }
            if pair in _BENIGN_SOURCE_PAIRS:
                a1_disagree_benign.append(record)
            else:
                a1_disagree_material.append(record)

        # ─ A2 dimension divergence ───────────────────────────────────────
        k_geo = _bmk_geography(k_name)
        m_geo = _bmk_geography(m_name)
        if k_geo and m_geo and k_geo != m_geo:
            pair_geo = frozenset({k_geo, m_geo})
            if pair_geo not in _GEO_BENIGN_PAIRS:
                a2_geo_diverge.append({"ISIN": isin, "Fund_Name": master.get("Fund_Name",""),
                                        "Fund_Nature": nature,
                                        "kiid_geo": k_geo, "ms_geo": m_geo,
                                        "kiid_bmk": k_name, "ms_bmk": m_name})

        k_sec = _bmk_sector(k_name)
        m_sec = _bmk_sector(m_name)
        if k_sec and m_sec and k_sec != m_sec:
            a2_sector_diverge.append({"ISIN": isin, "Fund_Name": master.get("Fund_Name",""),
                                       "Fund_Nature": nature,
                                       "kiid_sector": k_sec, "ms_sector": m_sec,
                                       "kiid_bmk": k_name, "ms_bmk": m_name})

        k_cap = _tok_cap(k_name.lower())
        m_cap = _tok_cap(m_name.lower())
        if k_cap and m_cap and k_cap != m_cap:
            a2_cap_diverge.append({"ISIN": isin, "Fund_Name": master.get("Fund_Name",""),
                                    "Fund_Nature": nature,
                                    "kiid_cap": k_cap, "ms_cap": m_cap,
                                    "kiid_bmk": k_name, "ms_bmk": m_name})

        # ─ A3 role coherence ─────────────────────────────────────────────
        k_role = kiid_row.get("benchmark_role")
        m_role = ms_row.get("benchmark_role")
        # hurdle_rate for a non-Alternative fund with another asset_proxy = conflict
        roles = {k_role, m_role}
        if "hurdle_rate" in roles and "asset_proxy" in roles:
            if nature not in ("Alternativo", "Restantes", "Estructurado"):
                a3_role_conflict.append({
                    "ISIN": isin, "Fund_Name": master.get("Fund_Name",""),
                    "Fund_Nature": nature,
                    "kiid_role": k_role, "ms_role": m_role,
                    "kiid_bmk": k_name, "ms_bmk": m_name,
                })

    # ─────────────────────────────────────────────────────────────────────────
    # EXERCISE B — Cross-consistency vs fund_master (ALL ISINs)
    # ─────────────────────────────────────────────────────────────────────────
    b1_critical:  list[dict] = []
    b1_warn:      list[dict] = []
    b1_info:      list[dict] = []
    b2_geo:       list[dict] = []
    b2_geo_gap:   list[dict] = []  # benchmark has geo, fund_master has None
    b3_sector:    list[dict] = []
    b3_sector_gap:list[dict] = []
    b4_cap:       list[dict] = []
    b4_cap_gap:   list[dict] = []
    b5_hedging:   list[dict] = []
    b6_credit:    list[dict] = []
    b6_duration:  list[dict] = []
    b7_declared:  list[dict] = []

    for isin, rows in bmk_by_isin.items():
        master = fm.get(isin, {})
        if not master:
            continue
        nature     = master.get("Fund_Nature") or ""
        fm_geo     = master.get("Geography")
        fm_sec     = master.get("Sector_Focus")
        fm_theme   = master.get("Theme")
        fm_cap     = master.get("Market_Cap_Focus")
        fm_hedge   = master.get("Hedging_Policy")
        fm_fcur    = master.get("Fund_Currency")
        fm_acur    = master.get("Asset_Currency")
        fm_credit  = master.get("Credit_Quality")
        fm_dur     = master.get("Duration_Profile")
        fm_declared= master.get("Benchmark_Declared")
        fund_name  = master.get("Fund_Name","")
        mgmt       = master.get("Management_Company","")

        # Use the MORNINGSTAR source as primary if available (more structured),
        # fallback to KIID; for B1 check both.
        ms_row   = next((r for r in rows if r["source"] == "MORNINGSTAR"), None)
        kiid_row = next((r for r in rows if r["source"] == "KIID"), None)

        # ─ B1 asset_class ↔ Fund_Nature ─────────────────────────────────
        for row in rows:
            ac         = row.get("asset_class")
            confidence = row.get("confidence") or "HIGH"
            source     = row.get("source")
            bmk_name   = row.get("benchmark_name") or ""
            role       = row.get("benchmark_role") or "asset_proxy"

            if role == "hurdle_rate":
                continue  # hurdle benchmarks don't constrain asset class

            sev = _severity_b1(nature, ac, confidence)
            if sev in ("CRITICAL", "WARN"):
                record = {
                    "ISIN":       isin,
                    "Fund_Name":  fund_name,
                    "Mgmt_Co":    mgmt,
                    "Fund_Nature":nature,
                    "bmk_asset_class": ac,
                    "bmk_name":   bmk_name,
                    "source":     source,
                    "confidence": confidence,
                    "severity":   sev,
                    "hypothesis": _root_cause_b1(nature, ac or "?", source),
                }
                if sev == "CRITICAL":
                    b1_critical.append(record)
                else:
                    b1_warn.append(record)
            elif sev == "INFO":
                b1_info.append({
                    "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                    "bmk_asset_class": ac, "source": source, "bmk_name": bmk_name,
                })

        # Choose the best available benchmark for dimension checks
        primary = ms_row or kiid_row
        if not primary:
            continue
        p_name = primary.get("benchmark_name") or ""
        p_name_l = p_name.lower()

        # Also collect the KIID name for B7
        k_name = (kiid_row.get("benchmark_name") or "") if kiid_row else ""

        # ─ B2 Geography ──────────────────────────────────────────────────
        bmk_geo = _bmk_geography(p_name)
        if bmk_geo:
            if fm_geo and fm_geo != bmk_geo:
                pair = frozenset({fm_geo, bmk_geo})
                # Skip: Global fund_master or benign generalisation pairs
                if (fm_geo not in ("Global", "Mercados Emergentes", "Global EM")
                        and pair not in _GEO_BENIGN_PAIRS):
                    b2_geo.append({
                        "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                        "fm_geo": fm_geo, "bmk_geo": bmk_geo, "bmk_name": p_name,
                        "hypothesis": _root_cause_geo(fm_geo, bmk_geo, p_name),
                    })
            elif not fm_geo:
                b2_geo_gap.append({
                    "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                    "bmk_geo": bmk_geo, "bmk_name": p_name,
                })

        # ─ B3 Sector / Theme ─────────────────────────────────────────────
        bmk_sec = _bmk_sector(p_name)
        if bmk_sec:
            if fm_sec and fm_sec != bmk_sec:
                # Check if they are semantically equivalent (e.g. "Healthcare" / "Health Care")
                # or a known benign sub/super-set pair (e.g. "Inflation-Linked" / "Inflation").
                _sec_pair = frozenset({fm_sec, bmk_sec})
                if (fm_sec.lower().replace(" ","") != bmk_sec.lower().replace(" ","")
                        and _sec_pair not in _SECTOR_BENIGN_PAIRS):
                    b3_sector.append({
                        "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                        "fm_sector": fm_sec, "bmk_sector": bmk_sec, "bmk_name": p_name,
                        "hypothesis": (
                            f"fund_master.Sector_Focus='{fm_sec}' but benchmark signals '{bmk_sec}'. "
                            "Either the sector was inferred from the fund name wrongly, or the "
                            "Morningstar category assigns a different sector boundary."
                        ),
                    })
            elif not fm_sec:
                b3_sector_gap.append({
                    "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                    "bmk_sector": bmk_sec, "bmk_name": p_name,
                })

        # ─ B4 Market Cap ─────────────────────────────────────────────────
        # FIX-BMK-AUDIT-2 (2026-07-14): fm_cap="All Cap" is the FALLBACK sentinel
        # (no specific cap signal detected) — it is NOT a contradiction with a
        # specific benchmark cap. An "All Cap" fund can benchmark against a Mid Cap
        # or Small Cap index without being misclassified; the benchmark's cap just
        # describes the reference universe, not the fund's constraint. Treat All Cap
        # conflicts as gaps (informational) rather than as real conflicts.
        # Before this fix: 112 of 179 B4 conflicts were All Cap vs specific.
        bmk_cap = _tok_cap(p_name_l)
        if bmk_cap:
            if fm_cap and fm_cap != bmk_cap:
                if fm_cap == "All Cap":
                    # All Cap is a detection fallback → report as gap, not conflict
                    b4_cap_gap.append({
                        "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                        "bmk_cap": bmk_cap, "bmk_name": p_name,
                        "note": "fm=All Cap (fallback sentinel) — benchmark may reveal actual cap",
                    })
                else:
                    b4_cap.append({
                        "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                        "fm_cap": fm_cap, "bmk_cap": bmk_cap, "bmk_name": p_name,
                        "hypothesis": (
                            f"fund_master.Market_Cap_Focus='{fm_cap}' but benchmark signals '{bmk_cap}'. "
                            "Classifier may have inferred cap from name while benchmark reflects the "
                            "actual investable universe more accurately."
                        ),
                    })
            elif not fm_cap and nature == "Renta Variable":
                b4_cap_gap.append({
                    "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                    "bmk_cap": bmk_cap, "bmk_name": p_name,
                })

        # ─ B5 Currency / Hedging ─────────────────────────────────────────
        bmk_hedged = _tok_hedged(p_name_l)
        if bmk_hedged:
            if fm_hedge in (None, "Not Hedged", "Unhedged"):
                b5_hedging.append({
                    "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                    "fm_hedge": fm_hedge, "bmk_hedge_signal": bmk_hedged,
                    "bmk_name": p_name,
                    "hypothesis": (
                        "Benchmark name contains explicit hedging flag but "
                        f"fund_master.Hedging_Policy='{fm_hedge}'. "
                        "The benchmark here is the share-class hedge, not the mandate proxy — "
                        "verify whether the hedged benchmark matches the share class currency. "
                        "If Hedging_Policy should be 'Hedged', classify_utils needs to pick it up."
                    ),
                })

        # ─ B6 Credit / Duration (Fixed Income only) ──────────────────────
        is_fi = nature in ("Renta Fija Flexible", "Renta Fija Corto Plazo", "Monetario")
        if is_fi:
            bmk_credit   = _tok_credit(p_name_l)
            bmk_duration = _tok_duration(p_name_l)

            if bmk_credit and fm_credit:
                # Map fund_master Credit_Quality vocab to benchmark credit buckets
                IG_LABELS  = {"Investment Grade", "High Grade", "IG"}
                HY_LABELS  = {"High Yield", "Speculative", "HY"}
                bmk_is_hy  = bmk_credit == "High Yield"
                bmk_is_ig  = bmk_credit in ("Investment Grade", "Corporate", "Aggregate", "AAA",
                                             "Government", "Inflation-Linked", "Securitised")
                fm_is_hy   = fm_credit in HY_LABELS
                fm_is_ig   = fm_credit in IG_LABELS

                if bmk_is_hy and fm_is_ig:
                    b6_credit.append({
                        "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                        "fm_credit": fm_credit, "bmk_credit": bmk_credit, "bmk_name": p_name,
                        "hypothesis": _root_cause_credit(fm_credit, bmk_credit, p_name),
                    })
                elif bmk_is_ig and fm_is_hy:
                    # FIX-B6-AUDIT (2026-07-15): EM Sovereign bond funds are correctly
                    # classified as "High Yield" credit quality (many EM governments are
                    # sub-investment grade). The benchmark token "Government" does not
                    # imply Investment Grade for emerging-market sovereign debt.
                    # Suppress: benchmark contains "sovereign" + EM indicator.
                    _p_l = p_name.lower() if p_name else ""
                    _is_em_sov = (bmk_credit == "Government"
                                  and "sovereign" in _p_l
                                  and any(em in _p_l for em in ("em ", "emerg", "mercados em")))
                    if not _is_em_sov:
                        b6_credit.append({
                            "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                            "fm_credit": fm_credit, "bmk_credit": bmk_credit, "bmk_name": p_name,
                            "hypothesis": _root_cause_credit(fm_credit, bmk_credit, p_name),
                        })

            if bmk_duration and fm_dur:
                SHORT_DUR = {"Ultra-Short", "Short"}
                LONG_DUR  = {"Long", "Medium"}
                bmk_short = bmk_duration in SHORT_DUR
                bmk_long  = bmk_duration in LONG_DUR
                fm_short  = fm_dur in SHORT_DUR or "short" in (fm_dur or "").lower()
                fm_long   = fm_dur in LONG_DUR   or "long"  in (fm_dur or "").lower()
                if bmk_short and fm_long or bmk_long and fm_short:
                    b6_duration.append({
                        "ISIN": isin, "Fund_Name": fund_name, "Fund_Nature": nature,
                        "fm_duration": fm_dur, "bmk_duration": bmk_duration, "bmk_name": p_name,
                        "hypothesis": (
                            f"fund_master.Duration_Profile='{fm_dur}' but benchmark '{p_name}' "
                            f"signals '{bmk_duration}'. Possible block mis-assignment between "
                            "Renta Fija Corto Plazo and Renta Fija Flexible."
                        ),
                    })

        # ─ B7 Benchmark_Declared vs KIID source ──────────────────────────
        # FIX-BMK-AUDIT-3 (2026-07-14): improved token comparison.
        # The original split-and-len>3 approach had two failure modes:
        #   (a) Very short declared values (e.g. "€str", "€STR", "3m") produce
        #       zero tokens > 3 chars, so every extraction fires as drift even
        #       when both values refer to the same index (€STR ≡ Euro Short-Term Rate).
        #   (b) Composite declared benchmarks ("s&p 500 + 40% bloomberg…") produce
        #       tokens like "bloomberg","aggregat" that don't appear in the KIID
        #       extraction of just the primary component ("S&P 500 (Net Return)").
        # Fix (a): skip when dec_tokens is empty (declared too short/symbolic to compare).
        # Fix (b): after normalising punctuation, also check if either string is a
        #          prefix/suffix of the other at the token level (partial match).
        if fm_declared and k_name and kiid_row:
            import re as _re
            dec_l = fm_declared.lower()
            k_l   = k_name.lower()
            # Strip punctuation before tokenising so "s&p" and "1-3y" become comparable
            _punct = _re.compile(r'[&()\[\]+%]')
            dec_clean = _punct.sub(' ', dec_l)
            k_clean   = _punct.sub(' ', k_l)
            dec_tokens = set(t for t in dec_clean.split() if len(t) > 3)
            k_tokens   = set(t for t in k_clean.split()   if len(t) > 3)
            # Skip: declared is too short/symbolic to tokenise (e.g. "€str", "3m")
            if not dec_tokens:
                pass
            else:
                common = dec_tokens & k_tokens
                # Also accept as match when k_tokens is a non-empty subset of dec_tokens
                # (KIID extracted the primary component of a multi-part declared benchmark).
                k_subset_of_dec = bool(k_tokens) and k_tokens.issubset(dec_tokens)
                if not common and not k_subset_of_dec:
                    b7_declared.append({
                        "ISIN":           isin,
                        "Fund_Name":      fund_name,
                        "Fund_Nature":    nature,
                        "Declared":       fm_declared,
                        "KIID_extracted": k_name,
                        "hypothesis": (
                            f"fund_master.Benchmark_Declared='{fm_declared}' shares no tokens with "
                            f"KIID-extracted benchmark '{k_name}'. Possible extraction error in "
                            "benchmark_normalizer or the KIID references a different benchmark than "
                            "the one Morningstar uses as category proxy."
                        ),
                    })

    con.close()

    return {
        "meta": {
            "total_isins_in_fund_benchmarks": len(bmk_by_isin),
            "multi_benchmark_isins": len(multi_isins),
            "single_benchmark_isins": len(bmk_by_isin) - len(multi_isins),
            "total_fund_master_rows": len(fm),
        },
        "exercise_A": {
            "A1_source_disagree_material": a1_disagree_material,
            "A1_source_disagree_benign":   a1_disagree_benign,
            "A1_null_asset_class":         a1_null_ac,
            "A2_geo_diverge":              a2_geo_diverge,
            "A2_sector_diverge":           a2_sector_diverge,
            "A2_cap_diverge":              a2_cap_diverge,
            "A3_role_conflict":            a3_role_conflict,
        },
        "exercise_B": {
            "B1_critical":       b1_critical,
            "B1_warn":           b1_warn,
            "B1_info":           b1_info,
            "B2_geo_conflict":   b2_geo,
            "B2_geo_gap":        b2_geo_gap,
            "B3_sector_conflict":b3_sector,
            "B3_sector_gap":     b3_sector_gap,
            "B4_cap_conflict":   b4_cap,
            "B4_cap_gap":        b4_cap_gap,
            "B5_hedging":        b5_hedging,
            "B6_credit":         b6_credit,
            "B6_duration":       b6_duration,
            "B7_declared_drift": b7_declared,
        },
    }


# ── Console summary ───────────────────────────────────────────────────────────

def print_summary(findings: dict) -> None:
    meta = findings["meta"]
    A    = findings["exercise_A"]
    B    = findings["exercise_B"]

    print("\n" + "=" * 70)
    print("  BENCHMARK <-> CHARACTERIZATION CONSISTENCY AUDIT")
    print("=" * 70)
    print(f"  ISINs in fund_benchmarks  : {meta['total_isins_in_fund_benchmarks']:>5}")
    print(f"  Multi-benchmark ISINs     : {meta['multi_benchmark_isins']:>5}")
    print(f"  Single-benchmark ISINs    : {meta['single_benchmark_isins']:>5}")
    print(f"  fund_master rows          : {meta['total_fund_master_rows']:>5}")
    print()
    print("── EXERCISE A  (Multi-benchmark: KIID vs Morningstar) ──────────────")
    print(f"  A1  Source asset-class MATERIAL disagreement : {len(A['A1_source_disagree_material']):>5}")
    print(f"  A1  Source asset-class BENIGN disagreement   : {len(A['A1_source_disagree_benign']):>5}")
    print(f"  A1  NULL asset_class (either source)         : {len(A['A1_null_asset_class']):>5}")
    print(f"  A2  Geography diverge between sources        : {len(A['A2_geo_diverge']):>5}")
    print(f"  A2  Sector diverge between sources           : {len(A['A2_sector_diverge']):>5}")
    print(f"  A2  Market-cap diverge between sources       : {len(A['A2_cap_diverge']):>5}")
    print(f"  A3  Role conflict (hurdle+proxy non-alt)     : {len(A['A3_role_conflict']):>5}")
    print()
    print("── EXERCISE B  (All ISINs: benchmark vs fund_master) ───────────────")
    print(f"  B1  Asset-class vs Fund_Nature CRITICAL      : {len(B['B1_critical']):>5}")
    print(f"  B1  Asset-class vs Fund_Nature WARN          : {len(B['B1_warn']):>5}")
    print(f"  B1  Asset-class vs Fund_Nature INFO          : {len(B['B1_info']):>5}")
    print(f"  B2  Geography CONFLICT                       : {len(B['B2_geo_conflict']):>5}")
    print(f"  B2  Geography GAP (bmk has geo, master NULL) : {len(B['B2_geo_gap']):>5}")
    print(f"  B3  Sector CONFLICT                          : {len(B['B3_sector_conflict']):>5}")
    print(f"  B3  Sector GAP (bmk has sector, master NULL) : {len(B['B3_sector_gap']):>5}")
    print(f"  B4  Market-cap CONFLICT                      : {len(B['B4_cap_conflict']):>5}")
    print(f"  B4  Market-cap GAP (bmk has cap, master NULL): {len(B['B4_cap_gap']):>5}")
    print(f"  B5  Hedging flag conflict                    : {len(B['B5_hedging']):>5}")
    print(f"  B6  Credit Quality conflict (FI only)        : {len(B['B6_credit']):>5}")
    print(f"  B6  Duration conflict (FI only)              : {len(B['B6_duration']):>5}")
    print(f"  B7  Benchmark_Declared extraction drift      : {len(B['B7_declared_drift']):>5}")
    print("=" * 70)

    # Sample CRITICAL B1
    if B["B1_critical"]:
        print("\n  Sample CRITICAL B1 (first 5):")
        for r in B["B1_critical"][:5]:
            print(f"    [{r['ISIN']}] {r['Fund_Nature']} | bmk={r['bmk_asset_class']} "
                  f"({r['source']}, {r['confidence']}) | {r['Fund_Name'][:50]}")

    # Sample A1 material
    if A["A1_source_disagree_material"]:
        print("\n  Sample A1 MATERIAL disagreements (first 5):")
        for r in A["A1_source_disagree_material"][:5]:
            print(f"    [{r['ISIN']}] KIID={r['kiid_ac']} | MS={r['ms_ac']} "
                  f"| {r['Fund_Nature']} | agrees: {r['nature_agrees_with']}")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys as _sys
    # FIX-BMK-AUDIT-4 (2026-07-14): reconfigure stdout to UTF-8 so box-drawing
    # chars in print_summary don't crash under the default cp1252 console.
    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Benchmark consistency audit (read-only)")
    parser.add_argument("--db",  default=str(DB_PATH),
                        help="Path to fondos.sqlite (default: db/fondos.sqlite)")
    parser.add_argument("--out", default=None,
                        help="Write findings JSON to this path")
    args = parser.parse_args()

    db = Path(args.db)
    findings = run_audit(db)
    print_summary(findings)

    out_path = args.out
    if out_path is None:
        # Default: session scratchpad
        scratchpad = os.environ.get(
            "CLAUDE_SCRATCHPAD",
            r"C:\Users\ADMINI~1\AppData\Local\Temp\claude\c--desarrollo-fondos\1fbd0bfd-d2e7-442a-a862-61022382e27f\scratchpad"
        )
        out_path = os.path.join(scratchpad, "benchmark_audit_findings.json")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(findings, fh, indent=2, ensure_ascii=False)
    print(f"  Findings saved → {out_path}\n")
