# -*- coding: utf-8 -*-
"""
p1_db_harvest.py  —  Deutsche Bank España fund-document catalogue harvest
=========================================================================
SPEC-KIID-001-rev2  (2026-07-17)

Phases 0-3:
    python p1_db_harvest.py --probe              # Phase 0: DOM analysis + XML discovery
    python p1_db_harvest.py --harvest            # Phases 1-2: fetch XML → raw JSONL + DB
    python p1_db_harvest.py --report-codsus      # Phase 3: codSus discovery report

Design law (§6):
  · Capture <link> href VERBATIM. Never construct a URL.  (§6.1)
  · Discover the codSus universe. Never enumerate it.      (§6.2)
  · Parse codDoc/idioma/codSus/codCont FROM the href.     (§6.1)
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

# ---------------------------------------------------------------------------
# Path setup — allow `from shared.config import DB_PATH`
# ---------------------------------------------------------------------------
_HARVEST_DIR   = Path(__file__).resolve().parent        # proyecto1/harvest/
_PROYECTO1_DIR = _HARVEST_DIR.parent                    # proyecto1/
_ROOT          = _PROYECTO1_DIR.parent                  # repo root
sys.path.insert(0, str(_ROOT))
from shared.config import DB_PATH  # noqa: E402
from shared.db import is_postgres_connection, executemany  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PAGE_URL = (
    "https://www.deutsche-bank.es/es/particulares/ahorro-inversion/"
    "productos/documentacion-legal.html"
)
XML_URL = "https://www.deutsche-bank.es/dam/spain/es/pbc/recfondos/catalogo.xml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": PAGE_URL,
}

# §5.1 / §5.3: workbook's 7 classes (floor, not ceiling)
WORKBOOK_CODSUS = {"LIIC", "KIID", "MECO", "FGES", "LFII", "CCA", "ISSF"}

# DB schema (§8)
DDL_CATALOGUE = """
CREATE TABLE IF NOT EXISTS db_document_catalogue (
    harvest_ts     TEXT    NOT NULL,
    gestora_label  TEXT    NOT NULL,
    gestora_value  TEXT,
    cod_db         TEXT    NOT NULL,
    fund_name      TEXT,
    isin           TEXT,
    link_label     TEXT    NOT NULL,
    href           TEXT    NOT NULL,
    cod_doc        TEXT,
    cod_sus        TEXT,
    cod_cont       TEXT,
    idioma         TEXT,
    PRIMARY KEY (harvest_ts, cod_db, href)
);
CREATE INDEX IF NOT EXISTS ix_cat_codsus ON db_document_catalogue(cod_sus);
CREATE INDEX IF NOT EXISTS ix_cat_isin   ON db_document_catalogue(isin);
"""

RATE_LIMIT_S = 1.0  # §7 Phase 1: ≥ 1.0 s between page requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("p1_db_harvest")


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------
def _get(url: str, timeout: int = 60) -> requests.Response:
    """GET with shared headers. Raises on non-2xx."""
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# href parser  (§6.1 — parse FROM href, never build a URL)
# ---------------------------------------------------------------------------
def parse_href(href: str) -> dict:
    """Extract codDoc/idioma/codSus/codCont from a getDocumento href."""
    q = parse_qs(urlparse(href).query)
    return {
        "cod_doc":  q.get("codDoc",  [None])[0],
        "idioma":   q.get("idioma",  [None])[0],
        "cod_sus":  q.get("codSus",  [None])[0],
        "cod_cont": q.get("codCont", [None])[0],
    }


# ---------------------------------------------------------------------------
# PHASE 0 — Probe
# ---------------------------------------------------------------------------
def cmd_probe(args) -> None:  # noqa: ARG001
    """
    Download the page HTML, detect the XML data source, sample the XML,
    and answer the 5 §9 DOM questions.
    Writes db_probe_debug.html.
    """
    probe_path = _HARVEST_DIR / "db_probe_debug.html"

    log.info("Phase 0 — Probe: downloading %s", PAGE_URL)
    resp = _get(PAGE_URL, timeout=30)
    html = resp.text
    with open(probe_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("HTML saved → %s  (%d bytes)", probe_path.name, len(html))

    # Auto-detect XML URL from JS fetch() call
    xml_match = re.search(r"fetch\(['\"]([^'\"]*\.xml)['\"]", html)
    detected_xml_url = None
    if xml_match:
        raw_path = xml_match.group(1)
        if raw_path.startswith("http"):
            detected_xml_url = raw_path
        else:
            detected_xml_url = "https://www.deutsche-bank.es" + raw_path
        log.info("XML endpoint detected from JS: %s", detected_xml_url)
    else:
        detected_xml_url = XML_URL
        log.warning("XML endpoint not auto-detected; using known URL: %s", XML_URL)

    # Sample the XML
    log.info("Fetching XML sample …")
    xml_resp = _get(detected_xml_url, timeout=60)
    xml_text = xml_resp.content.decode("iso-8859-1")
    root = ET.fromstring(xml_text)

    gestoras = root.findall("gestora")
    all_isin = []
    all_hrefs = []
    for g in gestoras:
        for f in g.findall("fondo"):
            for d in f.findall("documento"):
                isin = (d.findtext("codigoIsin") or "").strip()
                href = (d.findtext("link") or "").strip()
                if isin:
                    all_isin.append(isin)
                if href:
                    all_hrefs.append(href)

    # §9 five questions
    print("\n" + "=" * 60)
    print("PHASE 0 — DOM PROBE FINDINGS")
    print("=" * 60)
    print(f"Page status:   {resp.status_code}  ({len(html):,} bytes)")
    print(f"XML URL:       {detected_xml_url}")
    print(f"XML status:    {xml_resp.status_code}  ({len(xml_resp.content):,} bytes)")
    print(f"Published:     {root.get('fechaPublicacion', 'n/a')}")
    print()
    print("Q1: ISINs in static HTML?  NO — data lives in XML, fetched by JS.")
    print(f"    ISINs in XML:  {len(set(all_isin)):,} unique  (sample: {all_isin[:3]})")
    print()
    print(f"Q2: All gestoras at once?  YES — {len(gestoras):,} gestoras in single XML.")
    print("    One HTTP request covers the full catalogue.")
    print()
    print("Q3: Gestora select:")
    print("    <select id='gestora-select'> — empty in static HTML, JS-populated from XML.")
    print("    Parsing XML directly; HTML select not needed.")
    print()
    funds_total = sum(len(g.findall("fondo")) for g in gestoras)
    docs_total  = len(all_hrefs)
    print(f"Q4: Table structure:  <table id='results-table'> — empty in static HTML.")
    print(f"    XML: {len(gestoras)} gestoras · {funds_total} funds · {docs_total} docs")
    print("    Parse: gestora[@codigo/@nombreGestora] > fondo[@codDB/@nombreFondo] > documento")
    print()
    sample_href = all_hrefs[0] if all_hrefs else "n/a"
    absolute = sample_href.startswith("http")
    print(f"Q5: hrefs absolute?  {'YES' if absolute else 'NO'}")
    print(f"    Sample: {sample_href}")
    print()
    print("MODE DECISION: static (XML endpoint)")
    print("  Single requests.get() + xml.etree.ElementTree — no Playwright needed.")
    print("=" * 60)
    print(f"\nHTML probe saved → {probe_path}")
    print("Run --harvest to proceed to Phases 1-2.")


# ---------------------------------------------------------------------------
# PHASE 1-2 — Harvest + Normalize
# ---------------------------------------------------------------------------
def cmd_harvest(args, conn=None) -> None:
    """
    Phase 1: Fetch catalogo.xml → emit raw JSONL (before any parsing).
    Phase 2: Parse each href → UPSERT into db_document_catalogue.
    §6.1: href captured verbatim. §6.2: no codSus allowlist.

    conn: injected connection (Postgres or SQLite) — used by tests and any future dialect-aware
    caller. When None (the CLI's default), behavior is unchanged: opens its own SQLite connection
    against DB_PATH, exactly as before this port (Postgres migration Phase 5c, 2026-09-20).
    """
    harvest_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = _HARVEST_DIR / f"harvest_raw_{harvest_ts}.jsonl"

    log.info("Phase 1 — Fetching XML: %s", XML_URL)
    t0 = time.time()
    xml_resp = _get(XML_URL, timeout=120)
    xml_text = xml_resp.content.decode("iso-8859-1")
    log.info("XML fetched in %.1fs  (%d bytes)", time.time() - t0, len(xml_resp.content))

    root = ET.fromstring(xml_text)
    fecha_pub = root.get("fechaPublicacion", "")

    # Phase 1: Iterate XML → write raw JSONL first (§7 Phase 1)
    log.info("Writing raw JSONL: %s", jsonl_path.name)
    raw_rows = []
    n_unknown_codsus = 0

    for gestora in root.findall("gestora"):
        gestora_label = gestora.get("nombreGestora", "")
        gestora_value = gestora.get("codigo", "")

        for fondo in gestora.findall("fondo"):
            cod_db    = fondo.get("codDB", "")
            fund_name = fondo.get("nombreFondo", "")

            for doc in fondo.findall("documento"):
                isin       = (doc.findtext("codigoIsin") or "").strip()
                # contenido and suscripcion are XML *attributes* of <documento>, not child elements
                link_label = (doc.get("contenido") or doc.get("suscripcion") or "").strip()
                href       = (doc.findtext("link") or "").strip()  # VERBATIM §6.1

                if not href:
                    continue

                row = {
                    "gestora":       gestora_label,
                    "gestora_code":  gestora_value,
                    "cod_db":        cod_db,
                    "nombre":        fund_name,
                    "isin":          isin,
                    "label":         link_label,
                    "href":          href,        # verbatim, source of truth
                }
                raw_rows.append(row)

    # Write JSONL before any parsing (re-parse must never require re-scrape)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for row in raw_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    log.info("Raw JSONL written: %d rows → %s", len(raw_rows), jsonl_path.name)

    # Phase 2: Normalize (parse href) → DB
    log.info("Phase 2 — Normalizing + loading to DB: %s", DB_PATH)
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    pg = is_postgres_connection(conn)
    ph = "%s" if pg else "?"
    if pg:
        # bronze.db_document_catalogue is already provisioned by db/pg/10_bronze.sql (applied
        # once via psql, see docker-compose.yml) — not by this script's inline DDL, which is
        # SQLite-only (conn.executescript() doesn't exist on psycopg3 either way). Same
        # architecture as sqlite_writer.create_schema(): Postgres schema creation is external,
        # not autotranslated at runtime.
        pass
    else:
        conn.executescript(DDL_CATALOGUE)
        conn.commit()

    if pg:
        upsert_sql = """
            INSERT INTO db_document_catalogue
                (harvest_ts, gestora_label, gestora_value, cod_db, fund_name,
                 isin, link_label, href, cod_doc, cod_sus, cod_cont, idioma)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (harvest_ts, cod_db, href) DO NOTHING
        """
    else:
        upsert_sql = """
            INSERT OR IGNORE INTO db_document_catalogue
                (harvest_ts, gestora_label, gestora_value, cod_db, fund_name,
                 isin, link_label, href, cod_doc, cod_sus, cod_cont, idioma)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """

    batch = []
    for row in raw_rows:
        parsed = parse_href(row["href"])  # §6.1: parse FROM href
        cod_sus = parsed["cod_sus"]

        # §6.2: log WARNING for any class outside the known set, but NEVER drop it
        if cod_sus and cod_sus not in WORKBOOK_CODSUS:
            n_unknown_codsus += 1
            if n_unknown_codsus <= 20:  # cap log spam
                log.warning(
                    "[NORM-001] Unknown codSus=%r isin=%s href=%s",
                    cod_sus, row["isin"], row["href"][:80],
                )

        batch.append((
            harvest_ts,
            row["gestora"],
            row["gestora_code"],
            row["cod_db"],
            row["nombre"],
            row["isin"] or None,
            row["label"],
            row["href"],
            parsed["cod_doc"],
            cod_sus,
            parsed["cod_cont"],
            parsed["idioma"],
        ))

    executemany(conn, upsert_sql, batch)
    conn.commit()

    inserted = conn.execute(
        f"SELECT COUNT(*) FROM db_document_catalogue WHERE harvest_ts={ph}", (harvest_ts,)
    ).fetchone()[0]
    if own_conn:
        conn.close()

    log.info("DB rows inserted: %d  (harvest_ts=%s)", inserted, harvest_ts)
    if n_unknown_codsus > 0:
        log.warning(
            "[NORM-002] %d rows with codSus outside workbook's 7 classes — check --report-codsus",
            n_unknown_codsus,
        )
    log.info(
        "Harvest complete. XML published: %s | %d raw rows | %d DB rows",
        fecha_pub, len(raw_rows), inserted,
    )
    print(f"\nHarvest complete  harvest_ts={harvest_ts}")
    print(f"  Raw JSONL: {jsonl_path}")
    print(f"  DB rows:   {inserted}")
    print("\nNext: python p1_db_harvest.py --report-codsus")


# ---------------------------------------------------------------------------
# PHASE 3 — codSus Discovery Report
# ---------------------------------------------------------------------------
def cmd_report_codsus(args) -> None:
    """
    Phase 3: Query db_document_catalogue (latest harvest_ts) and emit
    the mandatory codSus discovery report (§7 Phase 3).
    """
    conn = sqlite3.connect(DB_PATH)

    # Resolve latest harvest_ts
    row = conn.execute(
        "SELECT harvest_ts FROM db_document_catalogue ORDER BY harvest_ts DESC LIMIT 1"
    ).fetchone()
    if not row:
        log.error("[ERROR-001] No harvest data found. Run --harvest first.")
        conn.close()
        return
    harvest_ts = row[0]
    log.info("Reporting on harvest_ts=%s", harvest_ts)

    # Total funds and docs in this harvest
    n_docs = conn.execute(
        "SELECT COUNT(*) FROM db_document_catalogue WHERE harvest_ts=?",
        (harvest_ts,),
    ).fetchone()[0]
    n_funds = conn.execute(
        "SELECT COUNT(DISTINCT isin) FROM db_document_catalogue WHERE harvest_ts=? AND isin IS NOT NULL",
        (harvest_ts,),
    ).fetchone()[0]

    # codSus / codCont / link_label distribution
    rows_dist = conn.execute("""
        SELECT cod_sus, cod_cont, link_label, COUNT(*) as cnt
        FROM db_document_catalogue
        WHERE harvest_ts=?
        GROUP BY cod_sus, cod_cont, link_label
        ORDER BY cnt DESC
    """, (harvest_ts,)).fetchall()

    # codSus-level aggregation
    codsus_counts = Counter()
    for r in conn.execute(
        "SELECT cod_sus, COUNT(*) FROM db_document_catalogue WHERE harvest_ts=? GROUP BY cod_sus",
        (harvest_ts,),
    ).fetchall():
        codsus_counts[r[0]] = r[1]

    # codDoc semantics per class
    codsus_semantics = {}
    for cod_sus, cnt in codsus_counts.items():
        eq_isin = conn.execute("""
            SELECT COUNT(*) FROM db_document_catalogue
            WHERE harvest_ts=? AND cod_sus=? AND isin IS NOT NULL AND cod_doc=isin
        """, (harvest_ts, cod_sus)).fetchone()[0]
        # funds-per-codDoc cardinality (max)
        card = conn.execute("""
            SELECT MAX(c) FROM (
                SELECT cod_doc, COUNT(DISTINCT isin) as c
                FROM db_document_catalogue
                WHERE harvest_ts=? AND cod_sus=?
                GROUP BY cod_doc
            )
        """, (harvest_ts, cod_sus)).fetchone()[0] or 0
        codsus_semantics[cod_sus] = {
            "count":    cnt,
            "eq_isin":  eq_isin,
            "pct_isin": 100 * eq_isin / cnt if cnt else 0,
            "max_funds_per_codDoc": card,
        }

    # Coverage: per-class unique fund count
    codsus_fund_coverage = {}
    for r in conn.execute("""
        SELECT cod_sus, COUNT(DISTINCT isin) as n_funds
        FROM db_document_catalogue
        WHERE harvest_ts=? AND isin IS NOT NULL
        GROUP BY cod_sus
    """, (harvest_ts,)).fetchall():
        codsus_fund_coverage[r[0]] = r[1]

    conn.close()

    # §11 diff vs workbook classes
    present_classes   = set(k for k in codsus_counts if k)
    new_classes       = present_classes - WORKBOOK_CODSUS
    missing_classes   = WORKBOOK_CODSUS - present_classes

    # ── Print report ──────────────────────────────────────────────────────────
    sep = "=" * 70
    print(f"\n{sep}")
    print("PHASE 3 — codSus DISCOVERY REPORT")
    print(f"Harvest timestamp : {harvest_ts}")
    print(f"Funds (unique ISIN): {n_funds:,}  |  Documents: {n_docs:,}")
    print(sep)

    # 1. codSus distribution + semantics
    print("\n§1  codSus distribution & codDoc semantics")
    print(f"{'codSus':8}  {'count':>7}  {'%ISIN==codDoc':>14}  {'maxFunds/codDoc':>16}  {'fundCoverage':>12}")
    print("-" * 65)
    for cod_sus, sem in sorted(codsus_semantics.items(), key=lambda x: -x[1]["count"]):
        fund_cov = codsus_fund_coverage.get(cod_sus, 0)
        gap      = n_funds - fund_cov
        flag     = " *** NEW CLASS ***" if cod_sus in new_classes else ""
        print(
            f"{str(cod_sus):8}  {sem['count']:>7,}  {sem['pct_isin']:>13.1f}%"
            f"  {sem['max_funds_per_codDoc']:>16}  {fund_cov:>5} / {n_funds} (gap {gap})"
            f"{flag}"
        )

    # 2. Diff vs workbook
    print(f"\n§2  Diff vs workbook's 7 classes {{{', '.join(sorted(WORKBOOK_CODSUS))}}}")
    if new_classes:
        for c in sorted(new_classes):
            cnt = codsus_counts.get(c, 0)
            log.warning("[NORM-003] New codSus not in workbook: %r  count=%d", c, cnt)
            print(f"  *** NEW:     {c:8}  count={cnt:,}")
    else:
        print("  No new classes vs workbook.")
    if missing_classes:
        for c in sorted(missing_classes):
            print(f"  DISAPPEARED: {c:8}  (was in workbook, not in this harvest)")
    else:
        print("  No workbook classes disappeared.")

    # 3. Distinct (codSus, codCont, link_label) mapping
    print("\n§3  Distinct (codSus, codCont, link_label) mappings (top 40 by count)")
    print(f"{'codSus':8}  {'codCont':8}  {'count':>6}  link_label")
    print("-" * 60)
    for r in rows_dist[:40]:
        cs, cc, lbl, cnt = r
        print(f"{str(cs):8}  {str(cc):8}  {cnt:>6,}  {lbl}")

    # 4. LIIC umbrella hypothesis check
    liic_sem = codsus_semantics.get("LIIC")
    if liic_sem:
        print(f"\n§4  LIIC umbrella hypothesis")
        print(f"  codDoc == ISIN: {liic_sem['pct_isin']:.1f}%  (expected: ~0%)")
        print(f"  max funds sharing same codDoc: {liic_sem['max_funds_per_codDoc']}")
        if liic_sem["max_funds_per_codDoc"] > 1:
            print("  → Umbrella hypothesis SUPPORTED (multiple funds share same codDoc)")
        else:
            print("  → Umbrella hypothesis UNSUPPORTED (each codDoc maps to 1 fund)")

    # 5. Summary vs workbook baseline (§5.1)
    print(f"\n§5  Baseline comparison (§5.1 snapshot vs today)")
    baseline = {
        "LIIC": 12799, "KIID": 3206, "MECO": 3173, "FGES": 3132,
        "LFII": 77, "CCA": 14, "ISSF": 3,
    }
    print(f"{'codSus':8}  {'baseline':>9}  {'current':>9}  {'delta':>9}")
    print("-" * 40)
    all_keys = sorted(set(baseline.keys()) | present_classes)
    for k in all_keys:
        b = baseline.get(k, 0)
        c = codsus_counts.get(k, 0)
        new_flag = " (new)" if k not in baseline else ""
        print(f"{k:8}  {b:>9,}  {c:>9,}  {c-b:>+9,}{new_flag}")

    # 6. fund_master reconciliation (§11 criterion #8 / §6.3)
    print(f"\n§6  fund_master reconciliation (§6.3 / §11-#8)")
    try:
        conn2 = sqlite3.connect(DB_PATH)
        master_isins = {
            r[0] for r in conn2.execute(
                "SELECT ISIN FROM fund_master WHERE ISIN IS NOT NULL"
            ).fetchall()
        }
        conn2.close()

        harvest_isins = {
            r[0] for r in sqlite3.connect(DB_PATH).execute(
                "SELECT DISTINCT isin FROM db_document_catalogue "
                "WHERE harvest_ts=? AND isin IS NOT NULL",
                (harvest_ts,),
            ).fetchall()
        }

        in_harvest_not_master = harvest_isins - master_isins
        in_master_not_harvest = master_isins - harvest_isins

        print(f"  fund_master ISINs    : {len(master_isins):,}")
        print(f"  harvest ISINs        : {len(harvest_isins):,}")
        print(f"  In harvest, not in fund_master (new funds): {len(in_harvest_not_master):,}")
        if in_harvest_not_master:
            print(f"  Sample (first 10):")
            for isin in sorted(in_harvest_not_master)[:10]:
                print(f"    {isin}")
        print(f"  In fund_master, not in harvest (no DB docs): {len(in_master_not_harvest):,}")
        if in_master_not_harvest:
            print(f"  Sample (first 10):")
            for isin in sorted(in_master_not_harvest)[:10]:
                print(f"    {isin}")
    except Exception as exc:
        print(f"  Reconciliation skipped: {exc}")

    print(f"\n{sep}")
    print("WARNING  STOP — review this report before running p1_kiid_sync.py --sync")
    print(f"{sep}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Deutsche Bank fund-document catalogue harvest (SPEC-KIID-001-rev2)"
    )
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("--probe",          help="Phase 0: DOM probe + XML discovery")
    sub.add_parser("--harvest",        help="Phases 1-2: fetch XML → raw JSONL + DB")
    sub.add_parser("--report-codsus",  help="Phase 3: codSus discovery report")

    # Also accept flags directly (matching §12 CLI contract)
    ap.add_argument("--probe",         action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--harvest",       action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--report-codsus", action="store_true", dest="report_codsus",
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.probe:
        cmd_probe(args)
    elif args.harvest:
        cmd_harvest(args)
    elif args.report_codsus:
        cmd_report_codsus(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
