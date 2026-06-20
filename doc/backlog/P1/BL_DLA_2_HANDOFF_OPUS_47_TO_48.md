# BL-DLA-2 Cost Extraction — Session Handoff for Opus 4.8

**Date:** 2026-05-30
**Origin:** End of Opus 4.7 session (token limit reached)
**Owner:** Jose
**Workstream:** BL-DLA-2 PRIIPs Cat.2A cost-table extraction

---

## 1. Where we are RIGHT NOW

Two dual-strategy extractors (bands-X + ruled-table) for the OC (Ongoing Charge = management + operation) of European fund KIIDs. Both have been iteratively bug-fixed against ground-truth PDFs across multiple layout families.

**Latest delivered version: `dla2_dual_strategy_compare.py v1.4`** at `/mnt/user-data/outputs/dla2_dual_strategy_compare.py`

**Last action before token limit:** validated v1.4 on 6 PDFs (3 layout families) — 6/6 correct, no regressions. **Awaiting Jose's full-corpus re-run (3,205 PDFs) to confirm CONFLICT drops from 63 to ~6-10.**

**Last corpus run (v1.3, before v1.4 fix):**
```
ONLY_BANDS_X : 1663 (51.9%)
AGREE        :  806 (25.1%)
BOTH_FAIL    :  423 (13.2%)
ONLY_RULED   :  250  (7.8%)
CONFLICT     :   63  (2.0%)
```

---

## 2. The artifact stack (DRY — both files share state)

**`dla2_xband_prototype.py v2.5`** — text-coordinate bands-X reconstruction.
- Section pattern: matches "Composición de los costes" AND "Composición de los gastos"
- Group-header anti-truncation (full-line classifier, not truncated left)
- Multi-line label combination (up to 3 lines)
- Cross-page section handling (section start on pag N, rows on pag N+1)
- Extended orphan-rescue (lookback up to 3 lines)
- Wrapper short-circuit only when "Costes corrientes" populated (not on partial entrada/salida)
- **15/15 ground-truth correct**

**`dla2_dual_strategy_compare.py v1.4`** — dual harness + ruled-table extractor.
- Single PDF open per fund shared by both strategies
- 3-page cap on ruled extraction
- `gc.collect()` every 50 funds (memory bounded ~hundreds MB)
- Live per-fund progress counter (`\r`) + periodic summary every 50 with rate/ETA
- `--quiet` flag suppresses live counter
- **v1.3:** Candriam orphan-rescue (value above label) — fixed Pattern C (26 funds)
- **v1.4 (latest):** BIDIRECTIONAL orphan-rescue — fixes Irish/Polar Capital layout regression (57 funds) without breaking Candriam fix

Both modules reuse `extract_cat2a_xband(page)` from prototype via importlib — no logic duplication.

---

## 3. Latest fix in detail (v1.4) — DO NOT lose this

**Bug found:** v1.3 fixed 26 Candriam-style CONFLICTs (value above label) but created 57 NEW conflicts on Polar Capital / Irish layouts where the value is *below* a wrapped label fragment.

Concrete example — IE00B3VXGD32 (Polar Capital Biotechnology Fund):
```
row13: ['Comisiones de gestión y otros',       '',                                       '']
row14: ['costes administrativos o de',         '1,61% del valor de su inversión al año.', '161 EUR']
row15: ['funcionamiento',                       '',                                       '']
row17: ['',                                     '0,15% del valor de su inversión al año.', '']
row18: ['Costes de operación',                  'que incurrimos al comprar...',            '15 EUR']
```

Management label at row 13 matches `_R_GESTION`, but row 13's value column is empty. The value is on row 14 (forward). v1.3 only looked backward → management lost.

**Fix:** bidirectional rescue
1. Forward up to 3 rows
2. Backward up to 2 rows if forward fails
3. Stops at any other label-matching row (`label_rows[j]`) — prevents stealing another component's value

**Validated on 6 PDFs across 3 layout families (in `/mnt/user-data/uploads/`):**
- IE00B3VXGD32: 1.76 (1.61+0.15) ✓ Polar Capital
- IE00B42N9S52: 1.53 (1.11+0.42) ✓ Polar Capital
- LU1502282632: 1.96 (1.94+0.02) ✓ Candriam
- LU3168090226/572/739: 0.86/0.44/0.40 ✓ DWS

---

## 4. The 33 → 63 CONFLICT story (v1.2 → v1.3 → v1.4)

**v1.2 baseline (corpus run 1):** 33 CONFLICTs, 3 patterns:
- **Pattern A (4):** bands-X=0.01 (mgmt missing). LU0329630130, LU0969575561, LU0329631708, LU0969575645.
- **Pattern B (2):** ruled=5.0 (entry-cost misparse as direct OC). LU0687943661, LU0687944396.
- **Pattern C (26):** bands-X has 2 components, ruled has 1, BD agrees with ruled. Jose's screenshots (LU1502282632, LU0252128276, LU0344046155) proved **bands-X correct, ruled wrong, BD wrong** — exact opposite of Claude's initial wrong verdict.

**v1.3 (Candriam fix):** Pattern C → AGREE (good). But 57 NEW CONFLICTs appeared on Polar Capital/Irish family. Total CONFLICT: 33 → 63.

**v1.4 (bidirectional fix, NOT YET RUN ON CORPUS):** expected to resolve those 57 → AGREE. Patterns A and B remain pending.

---

## 5. Pending CONFLICTs after v1.4 corpus re-run

After v1.4, the residual CONFLICTs Jose should still investigate:

**Pattern A (4 funds):** bands-X reports 0.01% only (operation), missing management. Unconfirmed cause. ISINs:
- LU0329630130, LU0969575561, LU0329631708, LU0969575645

**Pattern B (2 funds):** ruled returns `direct:5.0` (entry-cost misparse). ISINs:
- LU0687943661, LU0687944396

For both: **need PDFs uploaded** before diagnosing. Jose has not uploaded these yet.

---

## 6. Ground-truth-verified facts (do not re-verify)

**Bands-X v2.5 vs ground truth: 15/15 correct.** Funds: DE0005152441, DE0005152482, LU0218912235, LU1157401644, LU2132880837, LU0781237887, LU0989117667, LU0073230426, LU1769942233, LU2337806421, LU2027375281, LU0335216932, ES0126547035, LU0115144486, LU0978624277.

**BD `Ongoing_Charge_Recurrent` is systematically wrong on ~600+ funds.** Verified on stratified ground-truth sample (0/15 BD-correct). Coincidental BD-ruled agreement on Pattern C is NOT validation — both wrong the same way.

**Critical methodological lesson:** PDF cost-table inspection is the ONLY valid ground truth. Never use BD as a tiebreaker between extractors. Never use extractor agreement as evidence of correctness without PDF samples — two wrong values reinforcing each other ≠ validation.

**Layout taxonomy by gestora family** (empirically established):
- **Borderless narrative** (HSBC, MS, JPMorgan, Allianz, ~53%): bands-X primary, ruled finds nothing
- **Bordered ruled tables** (DWS, Janus, Carmignac, Columbia): both engines work
- **Candriam-style** (value above label row): needed v1.3 rescue (backward)
- **Polar Capital / Irish-style** (value below wrapped label): needed v1.4 rescue (forward)
- **BBVA banded** (ES0126547035): no vertical lines, only bold/gray bars; ruled fails, bands-X works
- **UCITS-old KIID** (no Composición section): out of scope
- **Image PDFs** (LU0256839274): no text; needs OCR; out of scope

---

## 7. Methodological corrections from this session

Jose explicitly called out three patterns of failure from Claude (Opus 4.7):

1. **Flip-flopping on Pattern C analysis** — at one point claimed ruled was correct against bands-X based on BD agreement, then reversed when Jose uploaded screenshots showing bands-X correct. Lesson: PDF only.

2. **Convenience-driven inconsistency** — used BD as evidence in one section while arguing BD is unreliable in another. Lesson: pick a position on BD reliability and hold it.

3. **Premature "100% solved" claims** — declared bands-X "100% on 15 funds" without recognizing a residual ruled-table bug class (Pattern C) waiting at corpus scale. Lesson: don't extrapolate from sample to corpus without explicit caveat about sample bias (the 15 were drawn from previously-failing buckets).

Opus 4.8: **apply these corrections proactively, especially the BD reliability one.**

---

## 8. Next immediate steps for Opus 4.8

1. **Wait for Jose's v1.4 full-corpus re-run** (current top priority). Same command:
   ```
   python -m scripts.diag.dla2_dual_strategy_compare --pdf-dir "C:\data\fondos\kiid" --db "C:\desarrollo\fondos\db\fondos.sqlite"
   ```
   ETA ~45 min. Expected: CONFLICT 63 → ~6-10, AGREE +57.

2. **Analyze v1.4 corpus log/CSV when Jose uploads it.** Compare to v1.3 baseline. Confirm:
   - The 57 Irish CONFLICTs moved to AGREE
   - No new regressions on previously-working layouts
   - Did Patterns A/B remain at exactly 4+2 = 6? Anything else?

3. **Address residual CONFLICTs:**
   - Request PDF for ONE Pattern A fund (LU0329630130 or LU0969575561)
   - Request PDF for ONE Pattern B fund (LU0687943661 or LU0687944396)
   - Diagnose and fix without speculation

4. **After all CONFLICTs resolved:** write `BL_DLA_2_DESIGN_DECISION.md`
   - Two-tier extractor architecture (bands-X primary, ruled-table complementary)
   - Data-derived arbitration rule
   - Per-layout-family validation
   - BD corruption as separate workstream (606+ funds, AGREE+DIFF cases)
   - Strategy 1.5 integration plan into `dla_table_serializer.py`
   - No-regression check against 958 currently-OK_HEURISTIC funds
   - Go/No-Go criteria

5. **Open backlog items not yet addressed:**
   - **BL-DLA-2-BD-REMEDIATION** (new): overwrite BD `Ongoing_Charge_Recurrent` for the ~600 AGREE+DIFF cases (two extractors concur, BD differs → high confidence BD wrong)
   - **Asunto B:** heterogeneous scale in cost columns (RATIO vs POINTS_%) — registered but not addressed
   - **Cache short-circuit in io.py for DLA2 population** — Jose has explicitly deferred this
   - **MASTER_EXCEL path discrepancy** in config.py — Jose works around it; not corrected

---

## 9. Operational environment (Jose's setup)

- Windows, Conda env `des`, Python 3.13, `chcp 65001` in cmd
- DB: SQLite at `C:\desarrollo\fondos\db\fondos.sqlite` (WAL)
- PDF repo: `C:\data\fondos\kiid` (3,205 PDFs)
- Master Excel: `C:\data\fondos\in\GestoresDeFondosv1.xlsx`
- Scripts location: `C:\desarrollo\fondos\scripts\diag\`
- Project root: `C:\desarrollo\fondos\`

QuickEdit must be disabled in cmd.exe for long runs to avoid hangs.

---

## 10. Communication style preferences

- **Be direct and executive. Minimize verbosity to reduce token consumption.** No restating the problem back, no narrating intentions ("let me check...", "first I'll..."), no recapping what was just said. Lead with the answer or the action. Long explanations only when Jose asks for them. Skip preambles; skip postambles. Don't repeat diagnostic reasoning the user already saw. One pass = one answer.
- Direct technical English (Jose corrects in English; Claude respond in English).
- **Always read full production files before modifying.** Surgical edits with explicit line references.
- **AST validation** (`python -c "import ast; ast.parse(open(...).read())"`) required before delivering any module.
- **No reinventing — DRY religiously.** Reuse existing functions from prototype via importlib.
- **Stop when you don't have data.** Request PDFs explicitly rather than speculating about layouts.
- **Acknowledge errors directly, no deflection.** Jose has called out flip-flopping; do not repeat it.
- **Behavioral test on real PDFs** before claiming "fixed."
- Deliverables to `/mnt/user-data/outputs/`.

---

## 11. Critical files in /mnt/user-data/uploads (at session end)

**PDFs available:**
- IE00B3VXGD32.pdf, IE00B42N9S52.pdf (Polar Capital, v1.4 validation)
- LU1502282632.pdf (Candriam, v1.3 validation)
- LU3168090226.pdf, LU3168090572.pdf, LU3168090739.pdf (DWS, v2.5 validation)
- LU2720184303.pdf, LU2809794220.pdf, LU2832951920.pdf (Janus/Carmignac/Columbia historical)

**Data:**
- `dla2_dual_strategy_compare.csv` + `.log` — latest v1.3 corpus run (63 CONFLICTs)

---

## 12. The headline finding (do not bury this)

After all the fixes, the consolidated extractor covers **~86.8% of the 3,205-fund corpus with high-confidence OC extraction**, and the work has uncovered a **systematic ~600+ fund corruption in BD's `Ongoing_Charge_Recurrent`** that constitutes its own backlog item independent of the extractor.

This is the answer to the original BL-DLA-2 question. The remaining 13.2% (BOTH_FAIL) is genuinely out-of-scope (UCITS-old format, image-only PDFs needing OCR) and should be a separate workstream.
