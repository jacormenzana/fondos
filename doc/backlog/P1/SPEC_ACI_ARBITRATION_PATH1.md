# SPEC — Path 1: Extend Arbitration to Emit ACI (recover ~1,121 ACI_RHP)

**Date:** 2026-06-26 · **Status:** ready to implement · **Owner of execution:** Claude Code (has the real PDFs + pipeline loop)
**Project:** `fondos` · `C:\desarrollo\fondos` · SQLite `db\fondos.sqlite` (schema v20, WAL) · Conda env `des`, Windows.

---

## 0. ONE-PARAGRAPH CONTEXT

The cost **values** pipeline writes `fund_master.ACI_1Y` / `fund_master.ACI_RHP` (annual cost impact, 1-year and recommended-holding-period horizons) by parsing the KID "Costes a lo largo del tiempo" (costs-over-time, OT) table. On **~1,121 funds** this parse yields nothing usable because pdfplumber's table extraction collapses the OT table on certain issuer layouts (BNP/DWS/Amundi families) — the row data is mashed into run-on cells with no column structure. **Proven empirically (2026-06-26):** on 100% of these 1,121 funds, the **arbitration** extractor (`cost_arbitration.arbitrate_costs_from_pdf`, xBand + ruled strategies) already returns `AGREE` on BOTH mgmt and oper — i.e. the arbitration extractor *successfully reads the cost table* on every failing fund. The exact same situation on the **mgmt/oper** side was solved by `FIX-ARB-FALLBACK` (fill `fund_master` from the validated arbitration value when the values-path is NULL and arbitration verdict is `AGREE`). **This spec replicates that proven pattern for ACI.** The only genuinely new work is teaching the xBand primitive to extract the incidencia/ACI row (it currently extracts ONLY the OC composition: gestión + operación). The wiring, schema, and gating are a direct copy of FIX-ARB-FALLBACK.

**Scale of prize:** ~1,121 ACI_RHP recovered → genuine ACI_RHP residual goes to near-zero. ACI_1Y similarly recovered where missing.

---

## 1. WHAT EXISTS TODAY (verified by grep/read 2026-06-26 — re-confirm before editing)

### 1.1 Arbitration (already in production, `DLA2_ARBITRATION_ENABLED=True`)
- `core/cost_arbitration.py` :: `arbitrate_costs_from_pdf(pdf_bytes) -> dict`
  - returns `{'oc', 'mgmt':{bandsx,ruled,verdict}, 'oper':{bandsx,ruled,verdict}, 'table_text'}`
  - **NO ACI in the contract.** This must be extended.
  - It calls two primitives, both of which read the SAME open PDF (single `pdfplumber.open`):
    - **xBand:** `dla2_xband_prototype.extract_from_open_pdf(pdf_obj)` (defined L549; core extractor `extract_cat2a_xband` L284)
    - **ruled:** `dla2_dual_strategy_compare.extract_ruled_from_pdf(pdf_obj, max_pages=3)` + `_recover_oc_text`
- `pipeline.py` already has the arbitration hook + **FIX-ARB-FALLBACK** block (search marker `FIX-ARB-FALLBACK`). It computes `_arb_fields` (dict with `Cost_Mgmt_BandsX`, `Cost_Mgmt_Arbitration`, `Cost_Oper_BandsX`, `Cost_Oper_Arbitration`) and fills `fund_master_record['Management_Fee_Pct'|'Transaction_Cost_Pct']` when values-path is None AND verdict == 'AGREE'.

### 1.2 xBand primitive (`dla2_xband_prototype.py`) — what it extracts
- `extract_cat2a_xband(page, debug, xlabel_max=138.0)` (L284): operates on the **composition table** ("Composición de los costes" / "Costes corrientes"). It:
  1. builds `section_text` = {top_y → full line text} for the cost section,
  2. detects **component-label blocks** via `_COMPONENT_LABELS` regexes (L180) — canon tags `_OC_GESTION`, `_OC_OPERACION`, `Costes de entrada/salida`, group headers (canon=None),
  3. **band-assigns** each block its nearest `%`-bearing value-line by bounded proximity (`_prev_boundary`/`_next_boundary`, the loop at L~458-510), using `_extract_value_from_block` (L529) to pull the `%`,
  4. exposes per-component values: `result['_OC_GESTION_VAL']`, `result['_OC_OPERACION_VAL']` (L~505-508), and consolidated `result['Costes corrientes']`.
- **It NEVER touches the OT table** ("Costes a lo largo del tiempo" / "Incidencia anual de los costes"). No incidencia label, no horizon logic, no RHP. Confirmed: zero `incidencia|aci|horizon|rhp|over_time` matches in the file.
- **KEY INSIGHT:** the x-band cell-detection (assign value to label by x-coordinate band + y-proximity) is exactly the technique that survives the collapsed layouts. The same machinery, pointed at the OT table's incidencia row, will recover ACI. The hard part (reading the collapsed PDF) is already solved; we are adding one more table + row to the same proven reader.

### 1.3 fund_master / fund_kiid_metadata
- `fund_master.ACI_1Y`, `fund_master.ACI_RHP` — **percent scale** (e.g. 3.0 = 3.0%), confirmed: arbitration `Cost_*_BandsX` == `fund_master.*_Pct` on funds where both succeed (no ×100 conversion needed when wiring).
- `fund_kiid_metadata` currently has arbitration columns: `Cost_Mgmt_BandsX/Ruled/Arbitration`, `Cost_Oper_BandsX/Ruled/Arbitration` (22 cols, schema v20 §2B).

---

## 2. DELIVERABLE — four coordinated changes (mirror FIX-ARB-FALLBACK exactly)

### CHANGE A — xBand: extract the incidencia/ACI row  ⚠️ THE ONLY NEW EXTRACTION LOGIC
**File:** `dla2_xband_prototype.py`
**Goal:** add a function that, from the same open page/section already processed, locates the OT table's incidencia row and returns per-horizon ACI plus the RHP pick.

Add `extract_aci_xband(page, debug=False)` (or extend `extract_cat2a_xband` to also process the OT section). Requirements:

1. **Locate the OT section.** Find the band whose text contains the OT header `Costes a lo largo del tiempo` / `Costs over time`. The incidencia row label matches:
   ```
   incidencia\s*(?:anual\s*)?de\s*los\s*costes? | annual\s*cost\s*impact
   ```
   (same pattern as `cost_table_parser.ACI_ROW` — reuse it; do NOT re-invent. Import or copy the exact regex, single source.)
2. **Identify horizon columns by x-band.** The OT table header has horizon columns ("En caso de salida después de 1 año", "... 5 años", "... período de mantenimiento recomendado"). Use the SAME x-coordinate banding the composition extractor uses: cluster the header words by x to get column x-ranges, then read the incidencia row's `%` cells at those same x-ranges. This is the technique that survives the collapse — apply it here.
3. **Per-horizon ACI.** Return `{'aci_1y': float|None, 'aci_rhp': float|None}` in **ratio→percent? NO**: return **percent scale** (e.g. 3.0 for 3.0%) to match `fund_master.ACI_*` and the existing `Cost_*_BandsX` convention. Parse `%` with the existing `_PCT`/`_extract_value_from_block` (percent-as-written).
4. **RHP-column identification.** Two signals, in priority:
   - (a) a horizon header containing `período de mantenimiento recomendado` / `recommended holding period` → that column is RHP;
   - (b) else extract `rhp_years` (reuse `priips_cost_extractor._extract_rhp_years` pattern — single source) and pick the horizon column whose year-value == rhp_years (±0.01). If only one horizon column exists, it IS the RHP (ultra-short MMF case).
5. **Defensive:** any failure → return `{'aci_1y':None,'aci_rhp':None}`. Never raise (the arbitration callable swallows exceptions, but keep the primitive clean too).

**VALIDATION (mandatory, corpus-scale — this is the step prior session-attempts skipped):**
- Build a standalone harness over the **1,121 genuine-missing ISINs** (list derivable: funds flagged `RHP_STILL_MISSING` in the latest `cost_diag` CSV where `parse_costs_over_time(fed)` returns no RHP+ACI — see §4 for the exact predicate).
- For each, run `extract_aci_xband` on the real PDF (`c:\data\fondos\kiid\{ISIN}.pdf`) and record `aci_1y`/`aci_rhp`.
- **Pass bar:** ≥90% of the 1,121 yield a non-None `aci_rhp`, AND on the subset that ALSO has a known ground-truth ACI_RHP (from arbitration AGREE-derived OT, or any fund where the values-path DID succeed historically) the xBand ACI matches truth within tolerance (abs 0.02). Report the distribution; do NOT declare success on a handful.
- **Anti-regression:** run `extract_aci_xband` on 200 funds where ACI_RHP is ALREADY correct in `fund_master` and confirm xBand agrees (no new wrong values). NULL is acceptable there (fill-only gating protects them), a WRONG value is not.

### CHANGE B — arbitration contract: add ACI verdict + values
**File:** `core/cost_arbitration.py`
- In `arbitrate_costs_from_pdf`, after the mgmt/oper block, call the new xBand ACI extractor (and, if you also add a ruled-ACI path, the ruled one) **on the same open `pdf_obj`** — do NOT reopen the PDF.
- Extend the return dict with:
  ```python
  'aci': {
      'rhp_bandsx': float|None, 'rhp_ruled': float|None, 'rhp_verdict': str|None,
      '1y_bandsx':  float|None, '1y_ruled':  float|None, '1y_verdict':  str|None,
  }
  ```
- Verdict via the SAME `_verdict(bx, rl, ocr_recovered, agree)` helper already in the file (reuse — DRY). If you implement only the xBand ACI path (no ruled-ACI), then `rl=None` always → verdict will be `ONLY_BANDS_X` when xBand succeeds. **That is acceptable** — but then the wiring gate in CHANGE D must accept `ONLY_BANDS_X` for ACI (since there is no second strategy to AGREE with). Document this explicitly. Preferred: implement ruled-ACI too so `AGREE` is meaningful and the gate stays identical to mgmt/oper. Decide based on CHANGE-A validation effort; xBand-only is the lower-risk MVP.
- Empty/early-return dict (`empty` at top of function) must include the `aci` sub-dict with all-None.

### CHANGE C — schema: new metadata columns + migration
**Files:** `migrate_v20_*.py` (new migration script), `schema_fondos.sql`, `schema_checks.py`
- Add to `fund_kiid_metadata` (additive ALTER, no rebuild — mirror §2B of INTEGRATED_SPEC_v20):
  ```sql
  ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_ACI_RHP_BandsX REAL;
  ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_ACI_RHP_Ruled  REAL;
  ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_ACI_RHP_Arbitration TEXT
     CHECK (Cost_ACI_RHP_Arbitration IS NULL OR Cost_ACI_RHP_Arbitration IN
       ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT'));
  ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_ACI_1Y_BandsX REAL;
  ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_ACI_1Y_Ruled  REAL;
  ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_ACI_1Y_Arbitration TEXT
     CHECK (Cost_ACI_1Y_Arbitration IS NULL OR Cost_ACI_1Y_Arbitration IN
       ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT'));
  ```
- Column count `fund_kiid_metadata` 22 → 28. Update `schema_checks.py` assert and `SCHEMA_VERSION` if you bump it (v20→v20.1 acceptable; coordinate with config).
- Migration script: idempotent (`PRAGMA table_info` guard before each ADD; `LIKE`/`TRIM` semantics where comparing). Dry-run on a DB copy first.

### CHANGE D — sqlite_writer + pipeline wiring (copy FIX-ARB-FALLBACK)
**Files:** `sqlite_writer.py` (`upsert_kiid_metadata`), `pipeline.py`
1. **sqlite_writer:** add the 6 new columns to `upsert_kiid_metadata` INSERT/VALUES/ON CONFLICT with **COALESCE** (CACHED-safe — preserve a real value, never overwrite with a CACHED NULL). Explicit column-by-column (no dict-dump).
2. **pipeline.py:** in the arbitration block, write the new `aci` verdict/value fields from `arb['aci']` into `kiid_record` (the 6 columns).
3. **pipeline.py FIX-ARB-FALLBACK extension:** add to the existing `_arb_map` loop (search marker `FIX-ARB-FALLBACK`):
   ```python
   ('ACI_RHP', 'Cost_ACI_RHP_Arbitration', 'Cost_ACI_RHP_BandsX'),
   ('ACI_1Y',  'Cost_ACI_1Y_Arbitration',  'Cost_ACI_1Y_BandsX'),
   ```
   Same gate: `fund_master_record.get(col) is None AND verdict == 'AGREE' AND bandsx is not None` → fill. **If CHANGE-B is xBand-only (verdict `ONLY_BANDS_X`),** change the gate for the ACI rows to accept `verdict in ('AGREE','ONLY_BANDS_X')`. Keep mgmt/oper rows `AGREE`-only. Scale: direct copy, NO conversion (percent==percent, confirmed).
   Log each fill: `FIX_ARB_FALLBACK INFO ACI_RHP={v} from Cost_ACI_RHP_BandsX`.

---

## 3. COMMIT SEQUENCE & GATES

1. **CHANGE A alone**, behind nothing (it's a new function, called by nobody yet). Build + the §2A corpus validation harness. **GATE:** ≥90% RHP recovery on the 1,121, 0 wrong on the 200-control. Do not proceed until this passes — this is the load-bearing step.
2. **CHANGE C** (migration) — dry-run on DB copy, verify 28 cols + CHECK constraints + idempotency.
3. **CHANGE B + D** — wire it. Behind the existing `DLA2_ARBITRATION_ENABLED` flag (already True).
4. **Corpus reprocess:** `UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH', KIID_PDF_Hash=NULL WHERE KIID_Class=1;` → `.bat` launcher → `diag_cost_extraction.py`.
5. **GATE:** ACI_RHP missing drops from 1,122 toward ~0 (residual = funds where xBand ACI genuinely fails + the ~118 real-0.0 + true no-data). swap_mgmt_oper unchanged at 16. No regression in OK count (3,058).

Each commit: read region → surgical `str_replace` → `python -X utf8 -c "import ast; ast.parse(...)"` → grep changed lines → control SQL. Deploy-verify: `'MARKER' in inspect.getsource(deployed_module)` on the `core\` file BEFORE measuring.

---

## 4. EXACT PREDICATES & HELPERS (so the harness matches production)

**The 1,121 "genuine missing" predicate** (per-fund, on fed text = `Raw_KIID_Text + "\n" + serialize_tables(pdf)`):
```python
# rows = parse_costs_over_time(fed)   (core.cost_table_parser)
# rhp_years via priips_cost_extractor._extract_rhp_years(fed)
def picks_rhp(rows, rhp_years):
    for r in rows:                                   # is_rhp path
        if r.get('is_rhp'): return r.get('aci_pct')
    if rhp_years is not None:                         # target_match path
        for r in rows:
            hy = r.get('horizon_years')
            if hy is not None and hy >= 0 and abs(hy - rhp_years) <= 0.01:
                return r.get('aci_pct')
    return None
# genuine-missing  ==  flagged RHP_STILL_MISSING in diag  AND  picks_rhp(...) is None
```
Empirically (2026-06-26): of 1,122 flagged, **~1,121 are genuine** (parser truly None) and **100% have arbitration mgmt+oper AGREE** → xBand reads their table.

**ACI_ROW regex (single source — reuse, don't fork):** `cost_table_parser.ACI_ROW`
**RHP label regex:** `cost_table_parser.RHP_PATTERN`  ·  **RHP years:** `priips_cost_extractor._extract_rhp_years`
**% parse / value-from-band:** `dla2_xband_prototype._extract_value_from_block` (L529), `_PCT`.

---

## 5. HARD CONSTRAINTS (environment + method)

- Windows, conda `des`, **`python -X utf8`** always. Single-line `python -c` only. `|||` in cmd → `chr(124)*3`. PYTHONPATH resets per shell: `set PYTHONPATH=C:\desarrollo\fondos\proyecto1;C:\desarrollo\fondos\proyecto1\core;C:\desarrollo\fondos\shared`.
- Full runs via `.bat` launchers in `scripts\launch\`, not `run_block.py`.
- **Reprocess requires BOTH** `KIID_Status='FORCE_REFRESH'` AND `KIID_PDF_Hash=NULL` (hash-reuse shortcut in `io.py` L~748 otherwise skips `serialize_tables`). Funds flip back to OK+hash after the run.
- **NULL > wrong value.** Fill-only gating: never overwrite a successful values-path extraction; only fill NULLs.
- **Validate at corpus scale (3,205), never on a handful.** This is the explicit lesson from 3 prior ACI fix-attempts (P1-J/K/L) that passed on samples and failed/regressed at scale. CHANGE A's gate (§3.1) is non-negotiable.
- `BD.Ongoing_Charge_Recurrent` is systematically wrong — never ground-truth.
- `fund_master.ACI_*` and `Cost_*_BandsX` are PERCENT scale — no ×100 anywhere in the wiring.
- Every new behavior behind the existing `DLA2_ARBITRATION_ENABLED` kill-switch.

---

## 6. WHY THIS IS LOW-RISK EXCEPT CHANGE A

- B, C, D are mechanical copies of the shipped, validated FIX-ARB-FALLBACK (mgmt/oper) — same gate, same scale, same write-path, same COALESCE discipline.
- The only genuine engineering is CHANGE A (ACI extraction in xBand), and the band-detection machinery it needs already exists and is proven on these exact collapsed layouts (100% mgmt/oper AGREE on the target population). CHANGE A points that machinery at one additional table + row.
- The corpus-scale validation gate (§3.1) is what was missing in prior attempts; with the real PDFs in hand, Claude Code can run it properly.

**Expected outcome:** ACI_RHP genuine-missing 1,121 → near-zero; ACI_1Y similarly improved; swap_mgmt_oper stays 16; OK stays ≥3,058.
