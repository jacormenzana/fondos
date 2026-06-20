# Integrated Spec — Schema v19.2 → v20 + DLA2 cost-arbitration in production flow

**Date:** 2026-06-06 · **Status:** design, pre-implementation · **Schema target:** v20

Two jobs, one deploy:
- **Job A** — the approved `fund_master` schema redesign (5 CREATE, 4 DELETE, 14 MODIFY) + `config.DOMAIN_VALUES` reconciliation.
- **Job B** — promote the tested DLA/DLA2 dual-strategy cost arbitration into the production flow, persisting a **per-component** arbitration verdict.

All file/line references are to the production modules as read on 2026-06-06.

---

## 0. Integration principle — why one deploy, not two

The jobs are **data-orthogonal**: no classification rule reads a cost column and no cost step reads a classification attribute. But they are **mechanically coupled** at three points, and doing them together is *safer*, not just convenient:

1. **One schema-version bump.** Job A drops 4 columns from `fund_master`; SQLite column-drop with FK dependents (`fund_cost_schedule`, `fund_families`) forces a table rebuild — the exact `migrate_v18→v19` gap. Done once = one FK-preserving rebuild instead of two.
2. **One forced corpus reprocess** backfills both (re-derived classification attributes + arbitration verdict), reading **local PDFs only** — no downloads.
3. **The same write-path preservation logic** (`upsert_*` COALESCE in `sqlite_writer.py`) is edited by both: Job A removes dead column refs; Job B adds CACHED-safe COALESCE for the new columns.

**Orthogonality is preserved operationally** by a dedicated kill-switch (`DLA2_ARBITRATION_ENABLED`) so Job B can be disabled independently even inside the combined deploy, and by keeping the two code changes as **separate commits with separate pass/fail gates**.

**Invariant honored (your constraint):** the *download* trigger is untouched. PDFs are downloaded only when `KIID_Status='FORCE_REFRESH'` or the ISIN is absent. Job B reads `{ISIN}.pdf` from the **local repository** (`KIID_STORAGE_DIR`) — a local read is not a download. The forced release flips **no** `KIID_Status` to `FORCE_REFRESH`.

---

## 1. Schema migration (v19.2 → v20)

### 1A. `fund_master` (Job A) — requires a rebuild
Apply the approved inventory: **CREATE** `Development_Status`, `Duration_Profile`, `MMF_Structure`, `Alt_Strategy`, `Payoff_Profile`; **DELETE** `Subtype`, `Portfolio_Currency`, `Currency_Hedged`, `Is_ESG`; **MODIFY** scope/values of the 14 listed attributes (value remaps are data-level, applied by the reprocess, not by DDL).

Because of the column drops + FK dependents, use the SQLite safe-rebuild idiom **inside one transaction**, in this order so FK targets are never dangling:
```
PRAGMA foreign_keys=OFF;
BEGIN;
  CREATE TABLE fund_master_new ( … v20 column set, incl. 5 new, excl. 4 dropped … );
  INSERT INTO fund_master_new (<surviving cols>) SELECT <surviving cols> FROM fund_master;
  DROP TABLE fund_master;
  ALTER TABLE fund_master_new RENAME TO fund_master;
  -- recreate ALL indexes (idx_fm_nature, idx_fm_strategy, idx_fm_credit_quality, …)
  -- recreate FK from fund_master(fund_family_id) → fund_families(family_id)
COMMIT;
PRAGMA foreign_keys=ON;
```
This is the step `migrate_v18_to_v19.py` must learn to do (rebuild FK-dependent tables). The new `migrate_v19_to_v20.py` owns it.

### 1B. `fund_kiid_metadata` (Job B) — additive, no rebuild
Per **decision #3**, the arbitration verdict lives here (next to `DLA2_Table_Text`). Per **decision #1** it is **per-component**:
```sql
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Mgmt_Arbitration TEXT
    CHECK (Cost_Mgmt_Arbitration IS NULL OR Cost_Mgmt_Arbitration IN
           ('AGREE','ONLY_RULED','ONLY_BANDS_X','CONFLICT','OCR_RECOVERED'));
ALTER TABLE fund_kiid_metadata ADD COLUMN Cost_Oper_Arbitration TEXT
    CHECK (Cost_Oper_Arbitration IS NULL OR Cost_Oper_Arbitration IN
           ('AGREE','ONLY_RULED','ONLY_BANDS_X','CONFLICT','OCR_RECOVERED'));
```
- Two columns: management-fee component and operation/transaction-cost component.
- `NULL` = not yet arbitrated (CACHED before reprocess / no local PDF / kill-switch off). The internal `BOTH_FAIL` state is **not** persisted: it either escalates to `OCR_RECOVERED` or leaves the column `NULL` (a genuine failure), keeping the enum clean.
- An **overall** verdict (worst-of the two components) is *derived in a view*, not stored — avoids a third column drifting out of sync.

**Relationship to `Cost_Extraction_Quality` (stays in `fund_master`):** additive, never overwritten. `Cost_Extraction_Quality` is the *confidence/method tier*; `Cost_*_Arbitration` is *which extractor produced/agreed on each component*. Together they answer "HIGH confidence **and** both strategies agreed" vs "LOW, only bands-X."

---

## 2. `config.py` changes
- `SCHEMA_VERSION: "v19.2" → "v20"`.
- New kill-switch (mirrors the existing `PRIIPS_COST_EXTRACTION_ENABLED` pattern, lines 84–85):
  ```python
  DLA2_ARBITRATION_ENABLED: bool = False   # dark-launch; flip to True for the forced release
  ```
- New domain set for validation:
  ```python
  COST_ARBITRATION_VALUES: tuple = ('AGREE','ONLY_RULED','ONLY_BANDS_X','CONFLICT','OCR_RECOVERED')
  ```
- **Job A `DOMAIN_VALUES` reconciliation (separate commit).** `config.DOMAIN_VALUES` is the canonical-vocab source of truth and is currently the *design-intent* layer that the live DB has drifted from (e.g. `Replication_Method` is canonically `Physical/Synthetic/Sampling` here at line 348, but the DB holds `ACTIVE/PASSIVE`). The v20 value sets from the approved proposal must be written here so pipeline/P3 validation matches the new schema. This is Job A logic — kept in its own commit, not mixed with Job B.

---

## 3. Flow changes (gated, orthogonal)

### 3.1 `io.py` — local-PDF access for arbitration (no download path touched)
The blocker: **Cache A** (`get_kiid_for_isin`, lines 693–703) early-returns cached text for `OK/CACHED` funds **without loading the PDF**, so `pdf_bytes=None` for ~98% of the corpus (confirmed by the comment at `pipeline.py:547`). The arbitration needs the actual PDF.

Add a **dedicated, side-effect-free local reader** (reuse the existing `_load_local_kiid`, line 318):
```python
def load_local_pdf_bytes(isin: str) -> Optional[bytes]:
    """Read {ISIN}.pdf from KIID_STORAGE_DIR. Local-only; never downloads.
    Returns None if absent (caller degrades gracefully)."""
```
This does **not** alter `get_kiid_for_isin`, Cache A, or the remote `Flujo B`. The download trigger logic is byte-for-byte unchanged.

### 3.2 `pipeline.py` — arbitration hook inside the existing cost branch
Attach at the existing cost block (`BL-COST-4c`, lines 1571–1644), **before** `publish_fund` (1667). Gate independently:
```python
_arb_enabled = getattr(_cfg, 'DLA2_ARBITRATION_ENABLED', False)
if _arb_enabled:
    pdf_bytes_arb = load_local_pdf_bytes(isin)        # local repo only
    if pdf_bytes_arb:
        arb = arbitrate_costs_from_pdf(pdf_bytes_arb)  # see §3.4 (promoted core fn)
        # (a) telemetry → kiid_record (fund_kiid_metadata)
        kiid_record['Cost_Mgmt_Arbitration'] = arb.get('mgmt_verdict')
        kiid_record['Cost_Oper_Arbitration'] = arb.get('oper_verdict')
        # (b) fidelity → improved table text feeds the EXISTING extractor
        if arb.get('table_text'):
            kiid_record['DLA2_Table_Text'] = arb['table_text']
    else:
        log_ingestion(conn, isin, "DLA2_ARB", "WARN", "no local PDF; arbitration skipped")
```
Key design choices that keep Job B orthogonal and low-risk:
- **Arbitration runs regardless of the `Cost_Extraction_Quality=='HIGH'` skip** (line 1598). That skip protects cost *values*; arbitration writes *telemetry* and improves *table text*, so it must not be short-circuited by it.
- **Cost values still flow through the existing text extractor** (`extract_priips_costs`/`extract_ucits_costs`, lines 1607–1620). Arbitration's contribution to values is *indirect*: a higher-fidelity `DLA2_Table_Text` → better extractor input. Direct value-override on `CONFLICT`/`OCR_RECOVERED` is **deferred to BL-COST-5** (out of scope here) to keep the value-write path untouched.

### 3.3 `sqlite_writer.py` — CACHED-safe persistence
- `upsert_kiid_metadata` (706–786): add `Cost_Mgmt_Arbitration`, `Cost_Oper_Arbitration` to the INSERT column list, the `VALUES` tuple, and the `ON CONFLICT DO UPDATE` with **COALESCE** — mirroring the `DLA2_Table_Text` line (763) so a CACHED cycle that re-emits `NULL` never wipes a prior verdict.
- `upsert_fund_master` (405+): Job A surgery — **remove** dead COALESCE refs for the 4 dropped columns (`Currency_Hedged` line 538, `Portfolio_Currency` 555, plus `Is_ESG`, `Subtype` 534); **add** COALESCE lines for the 5 new columns. (Job A commit.)

### 3.4 Promote the arbitration to `core/` — the one *extension* (not pure promotion)
`dla2_dual_strategy_compare.py` is a `scripts/diag` prototype whose only entry point is `main(pdf_dir/pdf/db_path…)` (line 504). Two refactors required:
1. **Extract a pure callable** `arbitrate_costs_from_pdf(pdf_bytes) -> dict` (no CSV/CLI/DB side-effects), reusing `extract_from_open_pdf` (bands-X) + `extract_ruled_from_pdf` (ruled, line 288) + OCR fallback (`dla2_ocr_fallback`), with a **single `pdfplumber.open` per fund** (already the prototype's pattern, line 577).
2. **Per-component arbitration (decision #1).** The tested `classify(bx_oc, rl_oc)` (line 481) arbitrates on the **consolidated OC only**. To emit `mgmt_verdict` and `oper_verdict`, apply `classify()` **twice** — once on the management component, once on the operation/transaction component — using the per-component values both strategies already expose (`_recover_oc_block`, `_oc_from_lines` return `(gest, oper)`). **This is new logic on top of validated code and needs its own test pass** before the forced release (see §5).

Return contract (proposed — to finalize against the extracted function):
```python
{
  'oc': float|None,                  # consolidated, for reference
  'mgmt_value': float|None, 'mgmt_verdict': str|None,   # str ∈ COST_ARBITRATION_VALUES
  'oper_value': float|None, 'oper_verdict': str|None,
  'table_text': str|None,            # high-fidelity serialization → DLA2_Table_Text
}
```

---

## 4. The forced release (one execution)
Per **decisions #4 and #5**: one corpus-wide run that recomputes **everything** and repopulates the DB, reading **local PDFs** (point 5), **downloading nothing**.

- Pre-conditions: migration v20 applied; `DLA2_ARBITRATION_ENABLED=True`; `PRIIPS_COST_EXTRACTION_ENABLED=True`; `kiid_source='local'`.
- It does **not** set `KIID_Status='FORCE_REFRESH'` (that would trigger downloads). CACHED funds stay CACHED; re-classification reads cached text (Cache A) and re-derives Job-A attributes via the existing characterize-on-CACHED path (`pipeline.py` 601–666); arbitration reads the local PDF via `load_local_pdf_bytes`.
- One pass → `publish_fund` writes `fund_master` (classification + cost values) and `fund_kiid_metadata` (verdict) atomically per fund.
- Estimated coverage: arbitration verdict populated for every fund with a local `{ISIN}.pdf` (≈3,205 if the local repo is complete — **confirm repo completeness**, see §6).

---

## 5. Binary pass/fail — two independent tracks
A regression in one track must not mask the other; run both query sets post-release.

**Track A — classification (Job A):**
- All 4 dropped columns absent from `PRAGMA table_info(fund_master)`; all 5 new columns present.
- `Replication_Method` value set ⊆ {Physical, Synthetic, Sampling, NULL/Not Applicable} (drift fix verified — 0 rows with ACTIVE/PASSIVE).
- INTER-rule violation counts ≤ baseline (§7 of the V4 instructions): INTER-1=0, INTER-2≤2, RV-with-rated-Credit_Quality=0 after remap, etc.
- New-column coverage matches the proposal estimates (±tolerance): `Duration_Profile`≈964, `Development_Status`≈3068, `MMF_Structure`≈89, `Alt_Strategy`≈75, `Payoff_Profile`=8.

**Track B — cost arbitration (Job B):**
- `Cost_Mgmt_Arbitration`/`Cost_Oper_Arbitration` non-NULL for funds with a local PDF.
- `AGREE` share, `CONFLICT` count, `OCR_RECOVERED` count reported; `CONFLICT` ≤ last diagnostic corpus run (~6–10 expected from the v1.4 bidirectional-rescue history).
- **No regression** in `Cost_Extraction_Quality='HIGH'` count vs pre-release (arbitration must not degrade values).
- `DLA2_Table_Text` non-NULL coverage ≥ pre-release.

**Kill-switch test:** with `DLA2_ARBITRATION_ENABLED=False`, the cost path equals current production (no verdict writes, no `load_local_pdf_bytes` calls); classification unaffected.

---

## 6. Commit sequencing & risk
1. **Commit 1 — migration** (`migrate_v19_to_v20.py` + `schema_fondos.sql` v20 + `schema_checks.py`): FK-preserving `fund_master` rebuild + additive `ALTER` on `fund_kiid_metadata`. AST + a dry-run on a DB copy; verify FK + indexes recreated.
2. **Commit 2 — Job A logic** (`config.DOMAIN_VALUES` v20 + `classify_utils.py` value remaps/INTER rules + `upsert_fund_master` COALESCE edits). Track-A control queries.
3. **Commit 3 — Job B logic** (`core/` arbitration callable + per-component extension + `io.load_local_pdf_bytes` + `pipeline.py` hook + `upsert_kiid_metadata` columns), behind `DLA2_ARBITRATION_ENABLED=False`. Ships dark; Track-B queries run after the flag flip in the forced release.

Each commit: read the full target region before editing, surgical `str_replace`, AST validation, single-line Windows-safe `python -X utf8` control queries.

---

## 7. Open items to confirm before coding (won't guess)
1. **Per-component arbitration semantics.** `classify()` is consolidated-OC today. Confirm the per-component split should reuse the same `_TOL` (0.011 pp, line 471) and the same bands-X/ruled value sources per component — or whether operation cost (often a small 0.02–0.22% value) needs a tighter/looser tolerance. This drives the §3.4 extension and its test set.
2. **Overall verdict rule.** Confirm "overall = worst-of(mgmt, oper)" for the derived view (vs. e.g. mgmt-priority).
3. **Local repo completeness.** Confirm `KIID_STORAGE_DIR` holds `{ISIN}.pdf` for (near) all 3,205 — this sets Track-B coverage and how many funds legitimately stay `NULL`.
4. **Value-override boundary.** Confirm arbitration **only annotates quality + improves table text** in v20, with direct value-override on CONFLICT/OCR deferred to BL-COST-5 (my assumption). If you want value-override now, the `publish_fund` value-write path enters scope and Track-B gains value-regression checks.
5. **Return-contract finalization.** The §3.4 dict is proposed; I'd lock it after extracting the callable and reading the bands-X/ruled per-component return shapes in full (`extract_from_open_pdf`, `extract_ruled_from_pdf`).

---
*Grounded in: pipeline.py (446, 500–575, 1571–1690), io.py (318–352, 592–720), sqlite_writer.py (405, 524–556, 706–786), config.py (84–85, 348, 405–408), dla2_dual_strategy_compare.py (481–497, 504, 577–610), schema_fondos.sql v19.*
