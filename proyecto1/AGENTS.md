# proyecto1 — P1 classification pipeline (area guide)

Loaded by Claude Code the first time a file under `proyecto1/` is read (`proyecto1/CLAUDE.md` imports this file); any other agent reads it before changing code here. Cross-project rules (design principles P#1-P#11, restrictions R-1..R-8, backlog, live-store safety) are in the root `AGENTS.md` and apply here.

## P1 — Classification Pipeline

### Execution order per fund

| Step | Module | What it does |
|------|--------|--------------|
| 1 | `proyecto1/core/io.py` | Fetch KIID (cache-first; HTTP only on FORCE_REFRESH) |
| 2 | `proyecto1/core/kiid_parser.py` | `parse_kiid_generic()` → SRRI, costs, dates, language |
| 3 | `proyecto1/blocks/<block>.py` | Specialized classifier → `classification` dict |
| 4 | `proyecto1/core/fund_characterizer.py` | Fill missing v3 attributes |
| 5 | `proyecto1/core/pipeline.py` | Orchestrate + INTER rules + defaults |
| 6 | `proyecto1/core/fund_writer.py` | Idempotent UPSERT with COALESCE |
| 7 | `proyecto1/core/fund_family_builder.py` | Run once after all blocks; group share classes into families |

### Classification blocks (sequential, mutually exclusive)

`monetarios → rf_corto → rf_flexible → renta_variable → mixtos → alternativos → restantes`

Each block exposes `classify_fund(name, kiid_text, ...)` and `get_universe_isins(df_master)`.  
`restantes` is residual: takes all unclassified ISINs; exposes `get_universe_isins(df_master, conn)`.

> **Paradigm note (current — `--nature-first --master-db`):** Block execution order is NOT the
> classification authority. The `--nature-first` dispatch routes each ISIN directly to its block
> based on Fund_Nature already persisted in the DB (via `resolve_nature_evidence()`). The
> sequential order above is the fallback dispatch chain for ISINs with no DB record — it does not
> determine nature for established funds. Single source of classification truth: `classify_utils.py`
> logic + the block's `classify_fund()` output. `resolve_nature_evidence` is KIID-primary (calibrated
> 2026-07-16; symmetric voting measured worse), with one bounded exception (ARB-1, FND-0288): a KIID
> vote that **no** other signal backs yields to two independent signals (name / KIID benchmark /
> Morningstar `asset_class`) that agree on another nature, unless the realized volatility fits that
> nature worse. Explicit-rule natures (Estructurado, Alternativo, Monetario) and the RF Corto/Flexible
> split are never arbitrated. Any change to a nature rule: measure old-vs-new over the whole universe first.

### KIID_Status state machine

| Status | Meaning |
|--------|---------|
| `CACHED` / `OK` | Text in DB → no HTTP, returned from DB in < 1s |
| `FORCE_REFRESH` | Re-download on next cycle |
| `WRONG_DOC` | PDF mismatch; fund excluded from all blocks |
| `NOT_FOUND` | URL unreachable |
| `RETIRED` | KIID row left the Deutsche Bank catalogue; set by `p1_kiid_sync --retire-orphans` together with `In_Current_Universe=0`; reversed by `--sync` (→ `FORCE_REFRESH`) if the row returns |

HTTP policy: 3 retries (1s/2s/4s backoff), timeout 15s. 429 does NOT retry.  
`scripts/launch/mark_stale.py` marks max 50 funds/cycle as FORCE_REFRESH (age > 180 days).

### Fund discovery & KIID lifecycle (`proyecto1/harvest/`) — new paradigm

> **Paradigm note:** ISIN discovery and lifecycle management is now driven by the **Deutsche Bank
> live XML catalogue** (`catalogo.xml`), not by `GestoresDeFondosv1.xlsx`. The Excel is still
> supported via `--master` mode but is no longer authoritative for which funds exist or are retired.
> `--master-db` loads the pipeline universe from `db_document_catalogue` (latest harvest snapshot).

| Script | Purpose |
|--------|---------|
| `p1_db_harvest.py` | Phases 0–3: probe page, fetch `catalogo.xml` → raw JSONL + `db_document_catalogue`, codSus discovery report |
| `p1_kiid_sync.py` | Phases 4–5: KIID delta vs local PDFs, net-new downloads, retire-orphans, `kiid_lifecycle` |

**5-phase flow (run in order):**
1. `p1_db_harvest.py --harvest` — fetch XML → upsert into `db_document_catalogue` with `harvest_ts`
2. `p1_db_harvest.py --report-codsus` — **quality gate**: flags new funds (in harvest, not in `fund_master`) and retirement candidates (in `fund_master`, not in harvest); review before proceeding
3. `p1_kiid_sync.py` (dry-run) — delta: `missing` = to download; `orphans` = local PDFs not in latest harvest
4. `p1_kiid_sync.py --sync` — download net-new KIIDs (hrefs captured verbatim, §6.1 — never URL-built)
5. `p1_kiid_sync.py --retire-orphans` — archive orphan PDFs to `kiid_retired/YYYYMMDD/`; record in `kiid_lifecycle`

**Launcher:** `scripts\launch\P1_harvestFunds.bat` runs this flow (steps 1–3 + a shrink gate
`harvest_gate.py` between the report and any download/archive; `--sync`, `--retire-orphans`, `--full`
opt in to the mutating phases — default is read-only on the PDFs). Audits: `P1_audit.bat`, `P2_audit.bat`.

**Storage:**
Active: `C:\data\fondos\kiid\{ISIN}.pdf` · Retired: `C:\data\fondos\kiid_retired\YYYYMMDD\{ISIN}.pdf`
PDFs and `Raw_KIID_Text` (in `fund_kiid_metadata`) are **never deleted**.

`kiid_lifecycle` table — one row per lifecycle period per ISIN:
- `status = 'commercializing'` (active) or `'retired'`; `retire_dir` = YYYYMMDD archive subdirectory
- `retire_scope` (on retired periods): `FULL` = the ISIN has no document at all in the latest harvest;
  `PARTIAL` = other documents remain in the catalogue but the KIID row is gone. Both cases also get
  `In_Current_Universe=0` and `KIID_Status='RETIRED'`; `--master-db` loads only ISINs with a KIID row.

**"Orphan" (precise):** a local PDF in `kiid/` absent from the latest harvest's `db_document_catalogue` — fund was removed from the live XML catalogue. Not a fund that was "never registered".

**Retirement cause:** fund disappears from Deutsche Bank's `catalogo.xml` → absent from latest `harvest_ts` in `db_document_catalogue` → Phase 3 report flags it → `--retire-orphans` archives it.

**Pipeline impact of retired ISINs:** `--master-db` / `--nature-first` load universe from `db_document_catalogue` MAX `harvest_ts` — retired ISINs have 0 rows there and **cannot be reached by the classifier**. Direct SQL is the correct mechanism for `fund_master` attribute updates on retired ISINs (NOT a P#7 violation — P#7 covers active, classifier-reachable funds only).

**Diagnosing a fund absent from the pipeline:** query `kiid_lifecycle WHERE isin=?` first.
- `status='retired'` → retired KIID; `In_Current_Universe=0`; update via SQL.
- No rows → check `db_document_catalogue`; may never have been in the XML catalogue.

### Key support modules

- Kill-switch registry (generated): `doc/reglas/NORMAS_IMPLEMENTACION.md` §8.
- `shared/schema_checks.py` — `assert_schema_alignment()` validates DB columns at startup
- `proyecto1/core/classify_utils.py` — **single source of truth** for all categorical normalization maps (EN→ES for Sector_Focus, Type, Family). Import from here; never duplicate elsewhere (P#11 / R-1).
- `proyecto1/core/cost_arbitration.py` — dual-path cost arbitration (PRIIPs vs UCITS)
- `proyecto1/core/priips_cost_extractor.py` + `ucits_cost_extractor.py` — cost extraction

### All P1 modules (machine-verified)

<!-- AUTO:BEGIN p1-module-map -->
| Module | Folder |
|--------|--------|
| `PATCHES_pipeline.py` | `proyecto1/core` |
| `benchmark_normalizer.py` | `proyecto1/core` |
| `classify_utils.py` | `proyecto1/core` |
| `cost_arbitration.py` | `proyecto1/core` |
| `cost_cross_validator.py` | `proyecto1/core` |
| `cost_currency.py` | `proyecto1/core` |
| `cost_format_router.py` | `proyecto1/core` |
| `cost_format_signals.py` | `proyecto1/core` |
| `cost_pct_anchored.py` | `proyecto1/core` |
| `cost_scale.py` | `proyecto1/core` |
| `cost_table_parser.py` | `proyecto1/core` |
| `cost_totals_arbitration.py` | `proyecto1/core` |
| `dla2_dual_strategy_compare.py` | `proyecto1/core` |
| `dla2_ocr_fallback.py` | `proyecto1/core` |
| `dla2_xband_prototype.py` | `proyecto1/core` |
| `dla_extractor.py` | `proyecto1/core` |
| `dla_table_serializer.py` | `proyecto1/core` |
| `fund_characterizer.py` | `proyecto1/core` |
| `fund_family_builder.py` | `proyecto1/core` |
| `fund_writer.py` | `proyecto1/core` |
| `io.py` | `proyecto1/core` |
| `kiid_official_name.py` | `proyecto1/core` |
| `kiid_parser.py` | `proyecto1/core` |
| `mark_stale.py` | `proyecto1/core` |
| `oc_resolution.py` | `proyecto1/core` |
| `pipeline.py` | `proyecto1/core` |
| `priips_cost_extractor.py` | `proyecto1/core` |
| `srri_text.py` | `proyecto1/core` |
| `srri_v4_geometric.py` | `proyecto1/core` |
| `srri_v5_geometric.py` | `proyecto1/core` |
| `ucits_cost_extractor.py` | `proyecto1/core` |
| `alternativos.py` | `proyecto1/blocks` |
| `mixtos.py` | `proyecto1/blocks` |
| `monetarios.py` | `proyecto1/blocks` |
| `renta_variable.py` | `proyecto1/blocks` |
| `restantes.py` | `proyecto1/blocks` |
| `rf_corto.py` | `proyecto1/blocks` |
| `rf_flexible.py` | `proyecto1/blocks` |
<!-- AUTO:END p1-module-map -->

### Commands

**P1 full pipeline:**
```batch
cd C:\desarrollo\fondos\scripts\launch
P1_discoverAllFunds.bat
```
Log: `proyecto1/log/log_pipeline_YYYYMMDD_HHMMSS.log`. Duration: ~8–12 min.

**P1 specific ISINs** (the only classification mode is nature-first over the harvest catalogue; `--block` and `--master <Excel>` are RETIRED, FND-0247:
refused before any connection is opened, and `pipeline.run_block(nature_first=False)` raises `NotImplementedError`):
```batch
cd C:\desarrollo\fondos\proyecto1
python run_block.py --nature-first --master-db --list-isin LU0232465467,LU1873127366
```

**P1 family-nature refresh** (recomputes the attributes derived from the nature of funds `fund_family_builder` corrected; the set is selected, never passed;
`P1_discoverAllFunds.bat` runs it after the builder; closure check: `scripts/audit/verify_family_refresh.py`):
```batch
cd C:\desarrollo\fondos\proyecto1
python run_block.py --family-nature-refresh --master-db
```

**Mark fund for re-download:**
```sql
UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH' WHERE ISIN='<isin>' AND KIID_Class=1;
```
