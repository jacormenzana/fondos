# FIX — Cost_Extraction_Quality LOW  [END-TO-END VALIDATED through real _assess_quality on 9 PDFs]

## Correction of the record
An interim claim ("8/9 HIGH-eligible") was measured on the parser's %+EUR recovery, which is NOT the
quality tier. Running the REAL `priips_cost_extractor._assess_quality` first showed a **regression**
(MEDIUM_EUR→LOW). That exposed a second, latent defect. The complete fix is **TWO root-cause changes**.

## Root causes (both confirmed on real PDFs)
1. **Serializer (`dla_table_serializer` v1)** never fed the parser a usable table:
   - emitted plain `"Etiqueta: valor"` (parser strong path needs `|||`);
   - read its own no-OCR pdfplumber text → empty on scanned PDFs, and **column-woven** on 2-col layouts
     (DWS: `"Incidencia anual de los 4,3 % 2,3 % costes"`).
2. **Extractor (`priips_cost_extractor._assess_quality`)** anchored quality on `vr_rhp`, which
   cross-validates the **annual** ACI% against the **cumulative** total EUR — valid only at 1Y.
   At RHP>1Y (e.g. 3,7% vs 2840/10000=28,4%) → severe discrepancy → LOW. Masked until now because
   woven text never supplied ACI_rhp; the v2 serializer supplies it, triggering the latent bug.

## Fix (3 files)
- **`core/dla_table_serializer.py`** ← replace with `dla_table_serializer_v2.py`: grid-`|||` (pdfplumber
  `extract_tables` preserves columns) + text-fallback over OCR'd `Raw_KIID_Text` + best-source-per-section.
  New sig `serialize_tables(pdf_bytes, text="", debug=False)` (back-compatible; `emit_table_log` kept).
- **`core/priips_cost_extractor.py`** ← 1-line root-cause fix in `_assess_quality`:
  `anchor = vr_rhp if vr_rhp.status!='NONE' else vr_1y`  →  `anchor = vr_1y if vr_1y.status!='NONE' else vr_rhp`.
  (1Y is the only horizon where %↔EUR cross-validation is semantically valid; vr_rhp stays as fallback.)
- **`io.py`** ← 1-line: `serialize_tables(pdf_bytes, debug=False)` → `serialize_tables(pdf_bytes, text=kiid_text, debug=False)`.

## End-to-end validation (real _assess_quality, 9 LOW funds)
| Quality | BEFORE (raw text) | AFTER (v2 serializer + anchor fix) |
|---|---|---|
| HIGH | 0 | **6** |
| MEDIUM_CROSS | 0 | 2 |
| MEDIUM_EUR | 6 | 0 |
| LOW | 2 | 0 |
| NONE | 1 | 1 |

Per fund AFTER: IE00BZ6SDZ85 HIGH · LU0217138725 HIGH · LU0266118651 HIGH · LU0395796690 HIGH ·
LU2132882700 HIGH · LU2576232115 HIGH · LU1881477043 MEDIUM_CROSS · LU2155808491 MEDIUM_CROSS ·
LU1548496022 NONE (residual: no detectable table grid AND raw text weaves so badly even "Costes totales"
is unmatchable — its production OCR'd text may differ; otherwise needs extract_text(layout=True)/OCR-layout).
MEDIUM_CROSS = a real 5–6bp rounding gap at 1Y (correct, not a failure).

## Safety / non-regression of the anchor fix
The anchor change only affects funds whose 1Y row has BOTH %+EUR (→ HIGH/MEDIUM_CROSS). Genuine EUR-only
funds remain EUR_ONLY → MEDIUM_EUR (unchanged). vr_rhp is retained as fallback when no 1Y column exists.

## Deploy + verify
1. Replace `dla_table_serializer.py` (v2 content) and `priips_cost_extractor.py` (anchor 1-liner); apply io.py 1-liner.
2. AST-check the three. Corpus `FORCE_REFRESH` so `DLA2_Table_Text` repopulates and costs recompute.
3. Control SQL: `Cost_Extraction_Quality` distribution — expect large LOW/MEDIUM_EUR → HIGH/MEDIUM_CROSS shift
   (ceiling: ~82% of the 1,647 LOW carry %+EUR). Re-measure at corpus scale; treat this 9-fund result as directional.
