# Technical Specification — DB Document Catalogue Harvest & KIID Sync

**Doc ID:** SPEC-KIID-001 · **Revision:** 2 (supersedes rev 1 entirely)
**Status:** Approved for Phase 0 · Phases 1–5 gated on §9
**Owner:** Jose
**Implementer:** ClaudeCode
**Date:** 2026-07-17

---

## 0. Read This First

This document is the **sole knowledge-transfer vehicle**. ClaudeCode has no access to the originating chat session. Everything required to implement is embedded here: URL anatomy (§4), empirical forensics (§5), the manual process being replaced (§3), design law (§6), and the reproducible analysis script (Appendix A).

**Revision 2 corrects two material errors in revision 1:**

| Rev 1 error | Correction |
|---|---|
| "Scraping is not on the critical path. Do not build the scraper first." | **Inverted.** The GUI harvest *is* the feature. Everything else is downstream of it. |
| Treated `codSus` as a known 7-value enumeration | **Wrong.** The `codSus` universe is unknown and must be **discovered empirically**, never assumed. |

Any prior `db_fondos_scraper.py` draft is void. Do not reference it.

---

## 1. Objective

Industrialize the manual process that produced `GestorDeFondosSerializado.xlsx`.

**Primary deliverable:** an automated harvest that reconstructs the complete Deutsche Bank España fund-document catalogue from the public GUI, discovering the full `codSus` universe rather than assuming it.

**Secondary deliverable:** using `codSus=KIID` rows from that catalogue, download net-new KIID PDFs into `C:\data\fondos\kiid`.

**Non-goals:** document versioning/lifecycle, re-download of changed documents, OCR/parsing of downloaded PDFs, downloading non-KIID classes (harvest their links; do not fetch the files).

---

## 2. Architecture Decision — `proyecto1`

| Criterion | Evidence |
|---|---|
| Discovery is already P1's mandate | `P1_discoverAllFundsPlusCost.bat` is the existing discovery entrypoint; this feature is discovery |
| Fund universe resident | `fund_master` (~3,200 rows) — the reconciliation counterpart |
| Metadata sink resident | `fund_kiid_metadata` |
| Downstream consumer resident | `srri_v5_final_corrected.py` consumes KIID PDFs |

The harvest closes the only external-ingestion gap in the P1 chain. Siting it in `proyecto0` would create a cross-project dependency on `fund_master` for zero benefit.

> **Confirm before build:** that `proyecto0` is not itself a discovery/ingestion project. If it is, revisit.

---

## 3. The Manual Process Being Replaced — FUNCTIONAL SPEC

Verbatim description of the original human workflow. **The automation must reproduce this exactly.**

1. Open the GUI: `https://www.deutsche-bank.es/es/particulares/ahorro-inversion/productos/documentacion-legal.html`
2. Open the combo box **"Consultar fondos de una gestora:"**
3. For **each** fund manager in the combo: select it.
4. The results table renders: `Código DB` · `Nombre de Fondo` · `Código ISIN` · `Enlaces`
5. For **each fund**, the `Enlaces` column lists **all documents that exist for that fund** — a variable set, one hyperlink per document.
6. Copy fund identity + every hyperlink into the workbook.

**The critical property:** step 5 is an *enumeration of what exists*, not a lookup against a fixed list. The document set varies per fund. This is why `codSus` cannot be hardcoded.

**Observed GUI labels (7, from the ALLIANCEBERNSTEIN screenshot) — reference only, not an allowlist:**
Informe Anual · Estatutos o Reglamentos · Folleto Completo · Informe Semestral · Fichas Gestora · KIID o DFI · Memorias De Comercialización

---

## 4. URL Anatomy — CONFIRMED

All document links resolve to a single endpoint:

```
https://www.servicios.deutsche-bank.es/documentacionfondos/getDocumento
    ?codDoc={identifier}
    &idioma={ES|NA}
    &codSus={document_class}
    &codCont={sub_type|NA}
```

**KIID observed form:**

```
.../getDocumento?codDoc={ISIN}&idioma=ES&codSus=KIID&codCont=NA
```

> **Design law:** this anatomy is documentation, **not** a URL builder. See §6.1.

---

## 5. Validated Intelligence — `GestorDeFondosSerializado.xlsx`

Method: hyperlink targets read via `openpyxl` (`cell.hyperlink.target`). The visible cell text is a **label only**; the URL lives in the hyperlink object. Reproducible via Appendix A.

| Metric | Value |
|---|---|
| Unique funds | 3,227 |
| Fund managers | 43 |
| Total hyperlinks | 22,404 |
| Columns | `Gestor`, `Código DB`, `Nombre de Fondo`, `Código ISIN`, `Enlaces` |

### 5.1 `codSus` distribution in the manual snapshot

| codSus | Count | Label mapping | `codDoc` semantics |
|---|---|---|---|
| LIIC | 12,799 | 4 report types via `codCont` | Non-ISIN numeric code, shared many-to-one across funds. **Hypothesis: umbrella/sub-fund parent. Unverified — do not rely on it.** |
| KIID | 3,206 | KIID o DFI | ISIN — **3,206/3,206 (100%)** |
| MECO | 3,173 | Memorias De Comercialización | ISIN |
| FGES | 3,132 | Fichas Gestora | ISIN |
| LFII | 77 | **Unknown** | Unverified |
| CCA | 14 | **Unknown** | Unverified |
| ISSF | 3 | **Unknown** | Unverified |

`codCont` (LIIC only): `10`=Informe Anual · `11`=Estatutos o Reglamentos · `12`=Folleto Completo · `13`=Informe Semestral

### 5.2 Evidence that the document set is per-fund variable

This is the empirical core of the spec. **Read the anomalies, not the round numbers:**

- **21 funds have no KIID link** (3,227 − 3,206). A KIID does not exist for every fund.
- LIIC = 12,799, not 4 × 3,227 = 12,908 → **109 missing LIIC documents.** Coverage is ragged.
- LFII (77), CCA (14), ISSF (3) are **long-tail classes** appearing on a handful of funds only.
- MECO (3,173) and FGES (3,132) each fall short of the fund count by different amounts.

**Conclusion:** no fund is guaranteed any given document. Constructing URLs from an ISIN × `codSus` cross-product would fabricate thousands of links to documents that do not exist.

### 5.3 The snapshot is provably incomplete

Public web search surfaced live `getDocumento` URLs with **`codSus=FMDB`** and **`codSus=AIMA`** (fund fact sheets, `codDoc`=ISIN). Neither appears anywhere in the workbook.

**Consequence:** the workbook's 7 classes are a floor, not the universe. Whether the GUI exposes FMDB/AIMA is **unknown** — the harvest will answer this. This is the direct justification for §6.2.

### 5.4 Front-end behaviour — CONFIRMED

DevTools (Network, Fetch/XHR filter, Preserve log active): selecting a different gestora produces **0 network requests**.

**Conclusions:**
1. **No REST API exists.** Any API-client design is invalid.
2. The full catalogue ships in the initial page payload; the combo performs **client-side filtering only**.
3. **Therefore a single page load may yield all 43 gestoras × ~3,200 funds.** Confirm in Phase 0 — if true, iterating the combo is unnecessary and the harvest is one HTTP request.

---

## 6. Design Law — NON-NEGOTIABLE

### 6.1 Capture `href` verbatim. Never construct a URL.

The manual process **copied hyperlinks**. The automation **copies hyperlinks**. Read `a['href']` and store it as-is.

This single rule eliminates every open question in §5.1:
- LIIC umbrella semantics: irrelevant — the href already contains the correct `codDoc`.
- Missing documents: impossible to fabricate — a link absent from the DOM is absent from the catalogue.
- Unknown classes (LFII/CCA/ISSF): captured automatically, no handler required.

`codSus`, `codCont`, `codDoc`, `idioma` are **parsed out of** the captured href (`urllib.parse`) for indexing. They are never inputs to a builder.

### 6.2 Discover the `codSus` universe. Never enumerate it.

The set of `codSus` values is an **output** of the harvest, not a configuration constant.

- No `DOC_TIPOS` dict. No allowlist. No `if codSus == "KIID"` during capture.
- Capture every link on every fund row unconditionally.
- After the harvest, emit `SELECT codSus, codCont, COUNT(*)` as a discovery report.
- **Any `codSus` not in {LIIC, KIID, MECO, FGES, LFII, CCA, ISSF} is a finding.** Log it at WARNING and surface it in the report. Do not drop it. Do not fail on it.
- KIID filtering happens in Phase 4 — **after** capture, never during.

### 6.3 The GUI is authoritative for what exists; `fund_master` is not.

`fund_master` answers "which funds do we track". The GUI answers "which documents DB publishes". These are different questions. Reconcile them (§7.4); never substitute one for the other.

---

## 7. Implementation Phases

### Phase 0 — Probe (BLOCKING — see §9)

Capture the raw DOM. Determine extraction mode. **No parser may be written before this completes.**

### Phase 1 — Harvest

- Enumerate every `<option>` in the gestora combo → `(value, label)`.
- Obtain the fund rows for every gestora (mode per Phase 0; if §5.4 conclusion 3 holds, a single page load covers all).
- Per fund row, capture: `Código DB`, `Nombre de Fondo`, `Código ISIN`, gestora.
- Per `Enlaces` cell, capture **every** `<a>`: `(label_text, href)` — unconditionally, per §6.2.
- **Rate limit ≥ 1.0 s** between page requests. Sequential only.
- **Raw output first:** persist untouched `(gestora, cod_db, nombre, isin, label, href)` tuples to `harvest_raw_{YYYYMMDD_HHMMSS}.jsonl` **before any parsing.** Re-parsing must never require re-scraping.

### Phase 2 — Normalize

Parse each captured href → `codDoc`, `idioma`, `codSus`, `codCont`. Load to `db_document_catalogue` (§8).

### Phase 3 — `codSus` Discovery Report

Mandatory output, reviewed by Jose before Phase 5 runs.

- Distinct `(codSus, codCont, label)` observed, with counts.
- **Diff vs the 7 workbook classes** — new classes flagged, disappeared classes flagged.
- Assertion of `codDoc` semantics per class: `% == ISIN`, `% == Código DB`, `% other`.
- Funds-per-`codDoc` cardinality per class (this settles the LIIC umbrella hypothesis empirically).
- Coverage matrix: funds × class, absolute and % of fund count.

### Phase 4 — KIID Delta

- **Target:** `SELECT DISTINCT isin, href FROM db_document_catalogue WHERE codSus='KIID'` — from the harvest, not from `fund_master`.
- **Local:** `{p.stem for p in Path(r"C:\data\fondos\kiid").glob("*.pdf")}`
- **Delta:** `target − local`
- **Report:** missing · present · orphans (`local − target`). **Orphans are reported, never deleted.**

> **Gate:** confirm the existing repository uses `{ISIN}.pdf` before implementing. Do not infer the convention from an empty or unseen folder.

### Phase 5 — Download

| Requirement | Value |
|---|---|
| Method | `GET` via `requests.Session`, **using the captured href verbatim** |
| Concurrency | Sequential (max 2 workers) |
| Rate limit | ≥ 1.0 s |
| Timeout | 30 s |
| Retries | 3 · exponential backoff 2/4/8 s · **5xx and timeouts only** |
| `User-Agent` | Realistic desktop Chrome UA |
| `Referer` | The GUI page URL |

**Validation before persisting — the endpoint returns HTTP 200 with an HTML error body on failure. Status code is not a success signal:**

1. `Content-Type` contains `application/pdf`
2. First 4 bytes == `%PDF`
3. Size > 10 KB

Failure → do not write · record `(isin, reason)` · continue.

**Atomicity:** write `{ISIN}.pdf.tmp`, `os.replace()` to `{ISIN}.pdf` only after all three checks pass.

**Outputs:** PDFs → `C:\data\fondos\kiid\` · `kiid_sync_report_{ts}.json` → `{downloaded[], failed[{isin,reason}], skipped_existing, orphans[]}`

---

## 8. Data Model

```sql
CREATE TABLE db_document_catalogue (
    harvest_ts     TEXT    NOT NULL,
    gestora_label  TEXT    NOT NULL,
    gestora_value  TEXT,              -- <option value>, DB-internal code
    cod_db         TEXT    NOT NULL,
    fund_name      TEXT,
    isin           TEXT,
    link_label     TEXT    NOT NULL,  -- e.g. "KIID o DFI"
    href           TEXT    NOT NULL,  -- VERBATIM. source of truth.
    cod_doc        TEXT,              -- parsed from href
    cod_sus        TEXT,              -- parsed from href — DISCOVERED, not assumed
    cod_cont       TEXT,              -- parsed from href
    idioma         TEXT,              -- parsed from href
    PRIMARY KEY (harvest_ts, cod_db, href)
);
CREATE INDEX ix_cat_codsus ON db_document_catalogue(cod_sus);
CREATE INDEX ix_cat_isin   ON db_document_catalogue(isin);
```

`harvest_ts` in the PK makes each run an immutable snapshot — catalogue drift becomes queryable over time, which is the long-term point of industrializing this.

---

## 9. HARD STOP — Phase 0 Gate

**The page DOM has never been inspected. No parser, selector, or gestora code list may be written until the artifact below exists.** Anything written now would be invented, not derived.

```bash
python -c "import requests;open('db_probe_debug.html','w',encoding='utf-8').write(requests.get('https://www.deutsche-bank.es/es/particulares/ahorro-inversion/productos/documentacion-legal.html',headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36'}).text)"
```

**Required from the artifact before Phase 1:**

1. Are ISINs present in the static markup? (`Ctrl+F` → `LU0`)
2. Are **all** gestoras' funds present at once, or only the default selection? — determines whether Phase 1 is one request or 43.
3. The gestora `<select>`: its selector, and the real `value` of each `<option>`.
4. The results-table container selector and row/cell structure.
5. Are hrefs absolute or relative?

**Mode decision rule:**

| Observation | Mode |
|---|---|
| ISINs in markup | `static` — one `requests.get` + BeautifulSoup. No browser. |
| ISINs only inside a `<script>` JSON blob | `static` — extract and parse the blob (preferred: cleanest source) |
| No ISINs anywhere | `playwright` — headless Chromium, iterate `<option>` values |

Given §5.4, **`static` is the likely outcome.** Do not install Playwright pre-emptively.

---

## 10. Session Progress Log

| # | Finding | Status |
|---|---|---|
| 1 | Workbook decoded — URLs live in `cell.hyperlink.target`, not cell text | **Confirmed** |
| 2 | 3,227 funds · 43 gestoras · 22,404 links | **Confirmed** |
| 3 | KIID `codDoc` ≡ ISIN, 3,206/3,206 | **Confirmed** |
| 4 | LIIC `codDoc` ≠ `Código DB`, 0/12,799 | **Confirmed** |
| 5 | LIIC `codCont` map 10/11/12/13 | **Confirmed** |
| 6 | LIIC `codDoc` = umbrella code | **Hypothesis — unverified.** Phase 3 settles it. Moot under §6.1. |
| 7 | REST API hypothesis | **Falsified** — DevTools: 0 XHR on gestora change |
| 8 | `codSus` universe ⊋ workbook's 7 (FMDB, AIMA found externally) | **Confirmed** — drives §6.2 |
| 9 | Page DOM structure | **Unknown** — Phase 0 pending |
| 10 | Prior `db_fondos_scraper.py` drafts (v1/v2) | **Void** — speculative selectors, built on the inverted rev-1 premise |

---

## 11. Acceptance Criteria

1. Phase 1 captures every `<a>` on every fund row **without reference to any `codSus` allowlist**. A grep for hardcoded `"KIID"`, `"LIIC"`, `"MECO"`, `"FGES"` in harvest/normalize code returns nothing.
2. Raw JSONL is persisted before parsing; re-parsing requires no re-scrape.
3. Phase 3 report reproduces the §5.1 distribution within tolerance, and **explicitly flags any class not in the workbook's 7**.
4. Every downloaded PDF used a **captured** href. Zero URLs are string-built.
5. `--dry-run` performs no writes and no downloads.
6. Zero non-PDF artifacts reach `C:\data\fondos\kiid` under any failure mode.
7. Re-running `--sync` immediately downloads zero files.
8. Funds absent from `fund_master` but present in the harvest are reported, not silently dropped.

---

## 12. CLI Contract

```
python p1_db_harvest.py --probe               # Phase 0. writes db_probe_debug.html
python p1_db_harvest.py --harvest             # Phases 1-2. writes raw JSONL + catalogue
python p1_db_harvest.py --report-codsus       # Phase 3. discovery report
python p1_kiid_sync.py  --dry-run             # Phase 4. delta only (DEFAULT)
python p1_kiid_sync.py  --sync                # Phase 5. download net-new
python p1_kiid_sync.py  --sync --limit 10     # smoke test
```

`--dry-run` is the default when no mode flag is supplied.

---

## Appendix A — Reproducible Workbook Forensics

Verifies every figure in §5 from source. Run before Phase 1 to confirm the baseline.

```python
import pandas as pd
from openpyxl import load_workbook
from urllib.parse import urlparse, parse_qs

WB = "GestorDeFondosSerializado.xlsx"
ws = load_workbook(WB).active

rows = []
for r in ws.iter_rows(min_row=2):
    if not r[4].hyperlink:
        continue
    q = parse_qs(urlparse(r[4].hyperlink.target).query)
    rows.append({
        "gestor":   r[0].value,
        "cod_db":   r[1].value,
        "nombre":   r[2].value,
        "isin":     r[3].value,
        "label":    r[4].value,
        "href":     r[4].hyperlink.target,
        "cod_doc":  q.get("codDoc",  [None])[0],
        "cod_sus":  q.get("codSus",  [None])[0],
        "cod_cont": q.get("codCont", [None])[0],
        "idioma":   q.get("idioma",  [None])[0],
    })

df = pd.DataFrame(rows)
print("links:", len(df), "| funds:", df.isin.nunique(), "| gestoras:", df.gestor.nunique())
print(df.cod_sus.value_counts())

# codDoc semantics per class
for cs, g in df.groupby("cod_sus"):
    eq_isin = (g.cod_doc == g.isin).mean()
    eq_db   = (g.cod_doc.astype(str) == g.cod_db.astype(str)).mean()
    card    = g.groupby("cod_doc").isin.nunique().max()
    print(f"{cs:6} n={len(g):6} isin={eq_isin:6.1%} coddb={eq_db:6.1%} max_funds_per_codDoc={card}")

# label -> (codSus, codCont)
print(df[["label","cod_sus","cod_cont"]].drop_duplicates().sort_values(["cod_sus","cod_cont"]).to_string())

# coverage gaps
tot = df.isin.nunique()
for cs, g in df.groupby("cod_sus"):
    print(f"{cs:6} covers {g.isin.nunique():5} / {tot} funds  (gap {tot - g.isin.nunique()})")
```

**Expected:** 22,404 links · 3,227 funds · 43 gestoras · KIID `isin=100.0%` · LIIC `coddb=0.0%` · KIID gap = 21.

---

## Appendix B — Artifacts to Commit Alongside This Spec

| Artifact | Purpose |
|---|---|
| `GestorDeFondosSerializado.xlsx` | Ground-truth baseline for the Phase 3 diff. Read-only. Never the harvest target. |
| `db_probe_debug.html` | Phase 0 output. Unblocks §9. |
| This document | Sole knowledge transfer. No chat access required. |
