# HANDOFF_CONTEXT — Residual Cost Optimization Project

**Date:** 2026-06-14 · **Scope:** reliability of `fund_master` cost attributes (P1) · **Status:** diagnosis complete, corpus-sized, 3 deliverables shipped, residual backlog quantified.
**Autocontención:** this document is self-contained. A new developer can execute the backlog from it alone, without prior chat logs.

**Status legend (always apply):** **[VERIFIED]** measured against data on 2026-06-14 · **[CODED]** present in source, re-confirm by `grep` before editing · **[SPEC]** agreed target, not yet implemented — never report as done.

---

## 1. Project Executive Summary

`fund_master` cost attributes (`Management_Fee_Pct`, `Transaction_Cost_Pct`, `Performance_Fee_Pct`, `ACI_1Y`, `ACI_RHP`, `Entry_Fee_Pct`, `Exit_Fee_Pct`) are unreliable: values are **swapped, missing, EUR-derived instead of read as %, or defaulted to 0 when undetermined.**

**Root cause [VERIFIED].** Two decoupled cost paths exist:

| Path | Source | Output | State |
|---|---|---|---|
| **DLA2 arbitration** | reads PDF via column-preserving grid (`pdfplumber.extract_tables`) | `fund_kiid_metadata.Cost_*_BandsX/Ruled/Arbitration` | **Correct** (validated; used as ground truth) |
| **Legacy values** | parses flat `Raw_KIID_Text` (OCR, no `'|||'`) | `fund_master.*_Fee_Pct`, `ACI_*` | **Broken** (label/value misalignment) |

The legacy path is fed flat OCR text of **2-column PRIIPS KIDs**, where label-anchored windows bind a label to the wrong (or absent) value. The grid path already solves this — proven because arbitration values are correct on the same funds. The defect is **integration** (values path not fed the grid) plus **residual serializer/parser gaps**.

---

## 2. Current Status & Progress

### 2.1 Delivered (in `/mnt/user-data/outputs/`)

| Artifact | Purpose | State |
|---|---|---|
| `export_p1.py` | added `--block` CLI: relational filter `fund_master.heuristic_block` → ISIN subset → both sheets; dated filename `p1_export_<block>_YYYYMMDD.xlsx` | ✅ done, AST-clean |
| `diag_cost_extraction.py` | corpus harness: re-runs serializer-grid + parser per fund, compares vs arbitration truth + stored values; emits per-fund verdict CSV. **This is the validation GATE for all residual fixes.** | ✅ done, validated |
| `kiid_parser.py` | (a) false-ZERO fix `_ENTRY_FEE_ZERO_RE`; (b) `_fee_is_ceiling()` + Part-1 rule-A wiring (conditional fee → point NULL) | ✅ done, AST-clean, validated on 3 funds |

### 2.2 Key architectural decisions [VERIFIED]

- [x] **DLA2 arbitration `Cost_*_BandsX` (where `Arbitration='AGREE'`) = ground truth** for all cost validation.
- [x] **Percent is canonical** — confirmed by schema CHECKs (Mgmt ≤10, Transaction ≤5, Perf ≤30, ACI ≤50/25, `*_Max` ≤25, all percent magnitudes).
- [x] **Part 1 — rule A:** conditional/ceiling fee ("Hasta X%", "X% máximo", "up to X%") → `Entry_Fee_Pct`/`Exit_Fee_Pct` = **NULL**; the ceiling is kept in `*_Max` (priips extractor, unchanged). `0` reserved strictly for explicit zero.
- [x] **`Ongoing_Charge_Recurrent` excluded** from any ×100 migration — stale/known-bad on 600+ funds; never ground truth/tiebreaker. Resolve via re-run only.
- [x] **`Fee_Known_Flag` has no consumer and no CHECK** → new value `ENTRY_CONDITIONAL` is safe; no consumer adaptation required.
- [x] **The export was stale** (produced by a CACHED/pre-grid run): `DLA2_Table_Text` empty, values are plain-path. Current code wires the grid (`io.py` L557) — a re-run applies it.

### 2.3 Corpus measurement [VERIFIED — `cost_diag_20260614.csv`, 2,415 PRIIPS funds]

| Verdict | Count | % | Meaning |
|---|---|---|---|
| REGRID_FIXES | 1,867 | 77% | re-run alone fixes (grid → matches truth) |
| OK | 87 | 4% | already correct |
| RESIDUAL_OTHER | 405 | 17% | code work required |
| RESIDUAL_R3 | 5 | 0.2% | oper bleed |
| NO_TRUTH | 51 | 2% | arbitration ≠ AGREE |

`swap_mgmt_oper=Y`: **2,277 (94%)** → export broadly stale; re-run mandatory corpus-wide. ~81% become correct from the re-run with **zero code**.

---

## 3. Detected Discrepancies

### 3.1 Reference funds (deep-dive, PDF-verified)

| ISIN (provider) | Attribute | Stored (wrong) | Truth (PDF) | Failure |
|---|---|---|---|---|
| **FR0010664052** (Edmond) | Management_Fee_Pct | 0.41 | 2.49 | mgmt label/value detached in OCR; got oper value |
| | Performance_Fee_Pct | NULL | 0.37 | row beyond plain window |
| | ACI_RHP | NULL | 4.2 | OT 2nd column not parsed |
| | Entry_Fee_Pct | 0 (ZERO_CONFIRMED) | **NULL** (Max=3) | false-zero (exit "0 EUR" leaked) → **FIXED** + rule A |
| **LU0326422689** (BlackRock) | Management_Fee_Pct | NULL | 2.07 | mgmt mapped to Performance |
| | Performance_Fee_Pct | 2.07 | NULL | = mgmt value (swap) |
| | Transaction_Cost_Pct | NULL | 0.54 | oper % missed |
| | ACI_1Y / ACI_RHP | NULL / 7.6 | 7.6 / 3.7 | OT column swap |
| **FR0010760694** (Candriam) | Transaction_Cost_Pct | NULL | 0.74 | oper bleed → 0.10 (from perf range) |
| | Performance_Fee_Pct | NULL | 0.10–20% | variable range unparsed |
| | ACI_1Y | 2.38 | 2.4 | EUR-derived (238/100), `MEDIUM_EUR` |
| | ACI_RHP | NULL | 1.7 | OT 2nd column not parsed |
| | Entry_Fee_Pct | 0.01 | **NULL** (Max=1) | "1,00% máximo" conditional → rule A |

### 3.2 Global logic failures [VERIFIED]

| Failure | Evidence | Location |
|---|---|---|
| mgmt↔oper / mgmt↔perf swap | 2,277 funds (94%) | `cost_table_parser._parse_composition_plain` on flat OCR |
| ACI EUR-derivation | 450 funds `MEDIUM_EUR` | `priips_cost_extractor` ACI fallback when %-row label unmatched |
| 0-for-undetermined | Edmond entry | `_ENTRY_FEE_ZERO_RE` window crossed rows → matched exit "0 EUR" — **FIXED** |
| Unit split (ratio vs percent) | `Entry_Fee_Pct`=0.05 (ratio) vs `Entry_Fee_Pct_Max`=5 (percent) | two writers, two scales |
| `Ongoing_Charge_Recurrent` incoherent | 0.006 / 0.0108 / 0.042 (3 sources) | stale BD field, COALESCE-preserved |
| `Cost_Extraction_Quality=HIGH` false-positive | Edmond/BlackRock HIGH despite swaps | quality heuristic blind to misalignment |

---

## 4. Actionable Backlog (Next Steps)

> Order = leverage. Each item: action · verification. Re-run `diag_cost_extraction.py` after each code fix — verdict counts must move toward OK.

### P0 — Operational (no code; do first)
- [ ] **Corpus `FORCE_REFRESH` (local PDFs) under current code.** Resolves 77% swaps + most LOW/MEDIUM_EUR inflation.
  - *Verify:* re-run harness → `REGRID_FIXES` collapses toward `OK`; `SELECT ISIN,Management_Fee_Pct,Transaction_Cost_Pct,ACI_1Y,ACI_RHP FROM fund_master WHERE ISIN IN ('FR0010664052','LU0326422689','FR0010760694')` → Edmond 2.49/0.41/6.3/4.2; BlackRock 2.07/0.54; Candriam ACI 2.4/1.7.

### P1 — Largest residual: ACI_RHP (688 funds) [SPEC]
- [ ] Fix OT **second-column / header parsing** so the RHP column is captured even with grid. Root in `dla_table_serializer` OT row emission (`_grid_sections`/`_text_sections`) and/or `cost_table_parser._parse_costs_over_time_dla2` header tolerance.
  - *Verify:* harness `RHP_STILL_MISSING` count → ~0; `aci_recovered` shows `RHP`/`RHP+1Y` for the 688.

### P2 — Composition value recovery (405 RESIDUAL_OTHER) [SPEC]
- [ ] **mgmt WRONG (53)** — *priority (wrong > missing).* Mis-extracted management value; trace per-fund via harness `mgmt_regrid` vs `mgmt_truth`.
- [ ] **mgmt MISSING (195)** — mgmt %/label detached even in grid; widen/repair grid cell extraction.
- [ ] **oper MISSING (136)** — operation % not captured; `cost_pct_anchored` anchor too narrow (fails on EdR "tendrán… compra y venta", Candriam "soportados cuando compramos y vendemos"; returns confident-wrong 1.0 on Candriam). Broaden anchor token set + add corpus regression check.
  - *Verify:* harness — these funds move to REGRID_FIXES/OK; no regression on the 10 issuers `cost_pct_anchored` already passes.

### P3 — Tails
- [ ] **oper bleed R3 (5)** — perf range "0,10% - 20%" bleeds into oper grid cell; serializer must prefer the anchored oper % over a bled cell.
- [ ] **`has_grid=False` (13)** — scanned PDFs / serializer no-grid; feed OCR'd text to serializer (per `cost_pct_anchored` docstring + REV-3).
- [ ] **Performance-fee range** ("X% - Y%") unparsed → `cost_table_parser` perf-range handling.

### P4 — Part 1 unit migration (independent; ready to build) [SPEC]
- [ ] **R-2 commit:** writer ratio→percent at assignment chokepoint for `Entry_Fee_Pct`/`Exit_Fee_Pct`; one-time migration ×100 of existing rows (`LIKE`/`TRIM`, not exact — R-9); **add `CHECK (… ≤25)`** mirroring `_Max`; COALESCE review in `publish_fund()`. Behind kill-switch, default OFF.
  - *Exclude* `Ongoing_Charge_Recurrent` (stale).
  - *Verify:* `SELECT COUNT(*) FROM fund_master WHERE Entry_Fee_Pct > 25` → 0 post-migration; conditional funds `Entry_Fee_Pct IS NULL AND Entry_Fee_Pct_Max IS NOT NULL`.

### P5 — Doc hygiene (no code risk) [SPEC]
- [ ] Update `Fee_Known_Flag` value set in `kiid_parser.py:~L1137` comment + V6 doc to: `EXTRACTED, ZERO_CONFIRMED, NOT_FOUND, EXIT_INFERRED_ZERO, ENTRY_CONDITIONAL, NULL`. Note `EXIT_EXPLICIT_ZERO`/`EXIT_EXTRACTED` are **trace-only**, never persisted.

---

## 5. Reference Assets

### 5.1 Source modules (uploads = production-as-read; outputs = patched deliverables)

| Module | Role | Key lines |
|---|---|---|
| `kiid_parser.py` ✅ outputs | entry/exit/ongoing point fees | `_ENTRY_FEE_ZERO_RE` L2454 (fixed); `_fee_is_ceiling` L2558 (new); PASO 10c L969 / 10d L996 (wired); `_empty_result` L1128–1130; stale comment L1137 |
| `cost_table_parser.py` | composition + over-time parser | `_parse_composition_plain` window L656; `MAX_FEE_PATTERN` L69–72 (number-first miss); `_parse_costs_over_time_plain` L514–520 (dup EUR) |
| `dla_table_serializer.py` | PDF → grid `'|||'` text | grid-first; OT header garble (R1); oper bleed (R3) |
| `cost_pct_anchored.py` | oper-% anchor | anchor token set too narrow (EdR/Candriam phrasings) |
| `priips_cost_extractor.py` | maps parser → fund_master; ×100 | `_ratio_to_pct` L97–105; fixed→Max L457–458; ACI assign L422–451 |
| `ucits_cost_extractor.py` | UCITS KIID path | OC pattern; reuses parser (DRY) |
| `pipeline.py` | per-fund flow | cost hook `extract_priips_costs(text=kiid_text)` L1674–1675; `Fee_Known_Flag` passthrough L840 |
| `io.py` | PDF fetch + serialize | serializer + kiid_text augmentation L554–557; CACHED path L666–686 (no re-serialize when `DLA2_Table_Text` NULL) |
| `sqlite_writer.py` | persistence | `Fee_Known_Flag` COALESCE write L478 (passthrough) |
| `fund_characterizer.py` | legacy v19 char | **no fee logic** (0 hits) |
| `schema_fondos.sql` | DDL v20 | `Fee_Known_Flag` L84 (no CHECK); `Ongoing_Charge_Recurrent` L64 / `Entry_Fee_Pct` L70 / `Exit_Fee_Pct` L71 (ratio, no CHECK); `_Max`/Mgmt/Transaction/Perf/ACI CHECKs L97–110 (percent); `rotation_costs` L544–551 (nature-keyed stopgap, **not** a consumer) |
| `export_p1.py` ✅ outputs / `export_tables.py` | export engine | `--block` filter via `where` |

### 5.2 Test datasets (`/mnt/user-data/uploads/`)

| ISIN | Provider | PDF + screenshot | Characteristics |
|---|---|---|---|
| FR0010664052 | Edmond | `.pdf` + `.PNG` | DDF/PRIIPS; mgmt detach; entry "Hasta 3%" |
| LU0326422689 | BlackRock | `.pdf` + `.PNG` | mgmt↔perf swap; ACI column swap; entry 5% fixed |
| FR0010760694 | Candriam | `.pdf` + `.PNG` | oper bleed; EUR-derived ACI; entry "1,00% máximo"; perf range |

### 5.3 Documentation / data

| Asset | Location |
|---|---|
| Corpus diag output (validation baseline) | `cost_diag_20260614.csv` (2,415 PRIIPS) |
| Integrated schema/migration spec | `/mnt/project/INTEGRATED_SPEC_v20_v3.md` |
| Prior pipeline handoff | `/mnt/project/HANDOFF_v20_pipeline_run.md` |
| Pipeline launcher | `/mnt/project/P1_discoverAllFunds.bat` |

---

## 6. Constraints & Risks

### 6.1 Hard stops (process)
- [ ] **Never guess parser/extractor logic** — request the module first (satisfied for current scope; re-apply for new modules e.g. `cost_format_router.py`, `cost_cross_validator.py`, `srri_text.py` if touched).
- [ ] **No "100% solved" on small samples** — validate every fix against the **full corpus** via the harness, never 3 funds. PDF inspection is the only valid evidence for a cost decision.
- [ ] **Root cause > symptom patch (Principle #1)** — fix centrally; never an `UPDATE … WHERE` the next run undoes.

### 6.2 Technical debt / hazards
| Risk | Mitigation |
|---|---|
| **CACHED/COALESCE** preserves stale values; export was stale this way | INTER/cost reads must use effective values; verify CACHED path overwrites; re-run `FORCE_REFRESH` |
| **Unit split** (ratio vs percent point columns) | P4 migration; until then never compare point vs `_Max` without scaling |
| **`Ongoing_Charge_Recurrent` known-bad** (600+) | never ground truth / tiebreaker / ×100 |
| **`cost_pct_anchored` confident-wrong** (Candriam→1.0) | broaden anchor + corpus regression before relying on it |
| **Two writers per fee** (kiid_parser point + priips `_Max`) | keep `_Max` in priips; point in kiid_parser; don't duplicate |
| **P3 future consumer** of `Entry_Fee_Pct` (now NULL for conditional) | P3 must treat NULL as undetermined; `rotation_costs` 0.0-default is a separate nature path |
| **Sandbox/file reverts** (prior incident) | treat `/mnt/user-data/outputs/` as canonical; `grep` fix-markers before delivery |

### 6.3 Execution constraints (hard)
- [ ] Windows, conda env `des`; **`python -X utf8` always**; **single-line** `python -c` only.
- [ ] DB `C:\desarrollo\fondos\db\fondos.sqlite`; KIIDs `C:\data\fondos\kiid\`; diag scripts `scripts\diag\`.
- [ ] Full runs via `.bat` in `scripts\launch\`, not `run_block.py`.
- [ ] Every new feature behind a **kill-switch in `config.py`**, default OFF.
- [ ] Per change: read-before-edit · surgical `str_replace` · AST parse + `grep` changed lines · control SQL vs baseline.

---

## 7. First prompt for the new chat

> "Retomo Residual Cost Optimization. Lee HANDOFF_CONTEXT.md (adjunto). Estado: re-run FORCE_REFRESH [hecho/pendiente]; quiero atacar [P1 ACI_RHP 688 / P4 unit migration]. Adjunto el cost_diag más reciente y los módulos que pidas (dla_table_serializer.py, cost_table_parser.py, priips_cost_extractor.py según el item)."

Attach: latest `cost_diag_*.csv`, and the modules for the chosen backlog item.
