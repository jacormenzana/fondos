# HANDOFF — Execute INTEGRATED_SPEC_v20_v3 (fresh chat)

**Date:** 2026-06-07 · **Project:** fondos (P1) · **Lang:** Jose ↔ Claude in Spanish; docs/DB in English.
Open a **new chat in the same Project**, paste this, attach `INTEGRATED_SPEC_v20_v3.md`. Self-contained; no back-reference needed.

---

## 0. One-paragraph state
Schema v19.2 → v20, two jobs. **Job B (cost arbitration) is DONE** — implemented, AST-clean, integration-tested; ships behind `DLA2_ARBITRATION_ENABLED=False`. **Job A (fund_master redesign) is NOT started.** The authoritative inventory (5 CREATE / 4 DELETE / 14 MODIFY value-sets + casing) is now embedded in `INTEGRATED_SPEC_v20_v3.md §2A.1/§3-bis` — recovered from the originating chat where it had been decided but omitted. Root-cause framing: **`config.DOMAIN_VALUES` is canonical design-intent; the live DB is drift; fix direction is DB → design-intent.**

## 1. Authoritative source
`INTEGRATED_SPEC_v20_v3.md` is the single source of truth (replaces v2). Everything needed for Job A is in §2A.1 (inventory), §3-bis (casing), §6/§6-bis (plan + ripple), §8-bis (open decisions), §Y (optimizations). The schema/file mechanics are in §2–§4.

## 2. Job B deliverables already produced (in `/mnt/user-data/outputs/` of the prior chat; re-apply to the real tree)
`config.py`, `classify_utils.py` (`cost_values_agree`), `core/cost_arbitration.py` (new), `dla2_xband_prototype.py` (per-component accessor `_OC_GESTION_VAL`/`_OC_OPERACION_VAL`), `schema_fondos.sql`, `schema_checks.py` (metadata 22, V20 constants, `check_schema_v20*`), `sqlite_writer.py` (`upsert_kiid_metadata` +6 COALESCE), `pipeline.py` (arbitration hook + `pdf_bytes` lifetime + cost re-gate), `migrate_v19_to_v20.py` (Job B additive runs by default; fund_master rebuild guarded behind `--rebuild-fund-master`).
Job B was verified: DDL/view/enum-CHECK valid, migration idempotent (16→22), `BOTH_FAIL` survives CACHED (COALESCE), 58-col rebuild DDL = 58 + FK intact, callable contract OK. **If these files are not yet in the real tree, applying them is step 0.**

## 3. Execution order (what the new chat does)
**Commit 1 — Migration (no open decisions needed):**
- Apply `schema_fondos.sql` v20 + `schema_checks.py` (lists derived from config catalog; assert 58/22).
- Run `python -X utf8 -m scripts.mig.migrate_v19_to_v20` (Job B additive: +6 metadata cols + view). Confirm `check_schema_v20_job_b(conn)['ok']`.
- fund_master rebuild (`--rebuild-fund-master`) only AFTER §4.3 normalizer ripple is neutralized AND Recommended_Holding_Period TEXT→INTEGER is in the DDL.

**Commit 2 — Job A logic (partially gated):**
- `config.py` `DOMAIN_VALUES` v20 = §2A.1 sets; add `casing_rule` per attribute; consolidate to one catalog (§Y-1).
- `classify_utils`: casing-normalizer (one function, §Y-2); the value remaps that are unambiguous now (Tier-1/Tier-2 rows in §2A.1 that don't touch the 4 open decisions).
- Neutralize `Subtype`/`Currency_Hedged` refs in `sqlite_writer._post_upsert_normalize_db` + `global_post_pipeline_normalize_db` BEFORE any rebuild.
- `upsert_fund_master`: drop the 4 columns, add the 5 with COALESCE.
- **STOP at the 4 open decisions** (below) — do not fabricate.

**Commit 3 — Job B:** already done; flip `DLA2_ARBITRATION_ENABLED=True` only for the backfill.

**Backfill (one pass):** corpus FORCE_REFRESH + `kiid_source='local'`; Track-A + Track-B control SQL (§5).

## 4. BLOCKING open decisions (Jose must answer before commit-2 reprocess) — §8-bis
1. `Liquidity_Profile` — restore as dealing-frequency vs retire.
2. `Type` — keep column name with new value-set, OR rename to `Vehicle_Structure` (ripples to P2/P3).
3. `INTER-4/INTER-5` — redesign vs retire (Type→Vehicle_Structure makes Nature→Type meaningless; `ALLOWED_TYPE_BY_NATURE` affected).
4. Thresholds — `Profile=f(SRRI,Fund_Nature)` bands; `Credit_Quality` IG/HY→Mixed.

## 5. Hard constraints (project)
`python -X utf8` always; one-line `python -c`; Conda env `des`; Windows; DB `C:\desarrollo\fondos\db\fondos.sqlite`. Working agreement: read-before-edit, surgical `str_replace`, AST + grep after every write, control SQL vs V6 §7 baselines, explicit column-by-column DB writes, feature behind a `config.py` kill-switch (default OFF). Migration must run before `assert_schema_alignment` (metadata now 22).

## 6. First message to send in the new chat
> "Ejecuta el plan de INTEGRATED_SPEC_v20_v3. Empieza por Commit 1 (migración estructural + schema_checks) y la parte cerrada de Commit 2 (catálogo config + casing-normalizer + remaps sin ambigüedad + neutralizar normalizadores Subtype/Currency_Hedged + upsert_fund_master). Párate en las 4 decisiones de §8-bis."  
> Plus my answers to the 4 decisions (or "déjalas pendientes y haz solo lo cerrado").
