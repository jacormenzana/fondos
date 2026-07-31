# Executive Summary — Pending Action Points · P1 Classification Pipeline
**As of:** 2026-07-16 (post audit sessions 1–15 + OPT-B implementation, 884 tests, corpus 0 errors / 13 warnings)
**Full status history:** [SESSION_SUMMARY_20260704_classification_fixes_v2.md](SESSION_SUMMARY_20260704_classification_fixes_v2.md)
**Option B implementation:** COMPLETE (this session) — pending first live run + corpus diff

---

## Importance rubric

| Level | Meaning |
|-------|---------|
| **P0** | Architectural — blocks or degrades other work; the headline refactor |
| **P1** | Confirmed bug affecting classification correctness; or a blocking precursor to P0 |
| **P2** | Correctness issue with limited blast radius, or requires a design/domain decision |
| **P3** | Cosmetic / single-fund edge case / data-quality artefact |

---

## Pending action points

| ID | Action point | Status | Importance | Domain input? | Reference |
|----|-------------|--------|------------|---------------|-----------|
| **OPT-B** | **Invert the classification pipeline: Nature-first, block-dispatch-second** — single-pass 2-of-3 vote (name/KIID/benchmark), retire INTER-DBLCLAIM + INTER-VOTE3 reclassification, ~3,200-fund FORCE_REFRESH migration. | ✅ IMPLEMENTED (2026-07-16) — pending first live run + corpus diff before closing | **P0** | No | [classify_utils.py:resolve_nature_vote](../../proyecto1/core/classify_utils.py), [pipeline.py:run_block](../../proyecto1/core/pipeline.py), [P1_discoverAllFunds.bat](../../scripts/launch/P1_discoverAllFunds.bat) |
| **RFC-RFF-POLICY** | **Resolve RFC-vs-RFF duration policy (≤3y rule)** — `resolve_rf_subtype()` updated; MODELO_SEMANTICO.md updated; 49 boundary tests; migrate ~55 affected RF funds. | ✅ COMPLETE (2026-07-31) — migration executed: net 35 RFC→RFF changes (19 HY guardrail, 15 no explicit ≤3y mandate, 1 BGF SRRI=4 borderline). FIX-P1-RFC-TRES-1: added "es" verb + un/dos/tres word-form numbers to `resolve_rf_subtype()` regex. FIX-P1-RFF-OVERRIDE-1: rf_flexible now calls `resolve_rf_subtype()` and overrides to RFC when KIID declares explicit ≤3y mandate — prevents block-ordering regression. 9 iShares GL 1-5 + BGF funds restored to RFC; 1104 tests pass. | **P1** | No | [classify_utils.py:2836](../../proyecto1/core/classify_utils.py), [rf_flexible.py:187](../../proyecto1/blocks/rf_flexible.py) |
| **BGF-CHINA-BOND** | BGF China Bond Family perpetually miscorrected by BL-44 + BL-62 + BL-64e ordering conflict (BL-44 re-routes to Restantes, BL-62 re-derives Family='Emerging Market Debt' lexically, overriding BL-64e's Family correction). All 3 BL44_NATURE_SRRI_R4 hits (15 funds) likely affected. Needs pipeline.py ordering investigation. | ✅ RESOLVED (2026-07-26) — all 8 BGF China Bond funds correctly `Renta Fija Flexible` / `Family=Flexible Fixed Income`; OPT-B NATURE_FIRST dispatch bypasses BL-44 ordering conflict entirely | **P1** | No | [pendingIssues.md](../../doc/operativos/pendingIssues.md) |
| **NORDEA-IU-LIQUIDITY** | NORDEA Investment_Universe='Liquidity' recurs at extraction level (not just inconsistency correction) each pipeline run, indicating a root-cause bug in the extractor rather than a persistence/INTER issue. | ✅ RESOLVED (2026-07-26) — 0 NORDEA funds with Investment_Universe='Liquidity' in live DB; INTER-13-LIQ + COALESCE persistence cleared all instances; 1 residual Liquidity elsewhere (BNP SMART FOOD, Renta Variable — different issue, non-blocking) | **P1** | No | [pendingIssues.md](../../doc/operativos/pendingIssues.md) |
| **MIXED-NATURE-FAMILIES** | 8 fund families contain share classes with different `Fund_Nature` values (e.g. some classes classified RFF, others Mixtos). Could be genuine multi-class structures or classification errors. Needs per-family audit to distinguish. | ✅ RESOLVED (2026-07-26) — live DB query confirms 0 mixed-nature families after OPT-B runs; one stale Robeco Financial Bd Ih row self-heals on next fund_family_builder run | **P1** | Possibly (for genuine multi-class structures) | [pendingIssues.md](../../doc/operativos/pendingIssues.md) |
| **BENCHMARK-B4-B7** | Residual benchmark-audit cases: B4 bucket = 67 funds (Benchmark_Type disagrees with Fund_Nature/Credit_Quality); B7 bucket = 64 funds (benchmark geography vs Geography attribute). Further triage required per fund to determine if genuine inconsistency or audit tool false positive. | ✅ RESOLVED (2026-07-26) — B4: 69→6 genuine (reordered `_BMK_CAP_TOKENS` compound-before-single + `BMK_CAP_BENIGN_PAIRS`); B7: 72→1 genuine (normalised S&P 500 variants, BofAML→BofA, €STR/ESTER→estr, ibex35; added `/` to punct-strip; NO_BENCHMARK sentinel skip). 6 remaining B4 are Mid Cap vs Small Cap Morningstar categories for Small-Mid Cap funds; 1 remaining B7 is WRONG_DOC self-heals. | **P2** | No | [audit_benchmark_consistency.py](../../proyecto1/tools/audit_benchmark_consistency.py) |
| **CARMIGNAC-FP** | Carmignac Patrimoine false positive in `eq_dominant` (incorrectly classifies as Renta Variable due to historical equity tilt; genuine Mixtos fund). Fix was reverted once to avoid regression; needs a bounded guard specific enough not to affect genuine equity-dominant funds. | ✅ RESOLVED (2026-07-26) — all 30 Patrimoine ISINs verified correct (→ Mixtos). Note: LU0099161993 (Carmignac Grande Eurp) had inverse symptom (→ Mixtos instead of RV); fixed separately by FIX-GRANDEURO-DERIV-1/2 + `_has_large_equity_mandate` guard | **P2** | No | [pendingIssues.md](../../doc/operativos/pendingIssues.md) |
| **SC-H2-ARCANO** | 3 ARCANO funds with SC-H2 violations (Credit_Quality↔benchmark credit inconsistency) appear to be caused by missing P2 benchmark data rather than a P1 classification error. Needs P2 pipeline run to confirm. | PENDING | **P2** | No | SC-H2 in classify_utils.py |
| **BL-B6-EM-GOVT** | Several BL-B6 (Investment Grade flag) fund cases involve EM government bond funds that are debatably IG; boundary between IG and Mixed for sovereign EM debt needs a policy. | PENDING | **P2** | Yes (IG threshold for EM sovereign) | [test_b6_ig_allowance_20260716.py](../../proyecto1/tests/test_b6_ig_allowance_20260716.py) |
| **WRONGDOC-STRAGGLERS** | Two WRONG_DOC-adjacent issues still unresolved: (a) JPM US ESG EQUITY EURHDG has a stale Geography='Asia-Pacific' from an old misclassification cycle (needs FORCE_REFRESH); (b) FAM_000947 DWS Multi Opp has a name collision in the master Excel between two different families. | ⚠ PARTIAL (2026-07-26) — (a) LU2363199204 FORCE_REFRESH set; Geography will self-correct on next P1 cycle. (b) DWS Multi Opp Excel name collision still requires manual master-file fix | **P3** | (b) needs manual Excel fix | — |
| **ACI-RHP-DIAG-MIRROR** | The ACI_RHP diagnostic metric does not mirror production guards (cosmetic inconsistency — diag shows different thresholds than production code). No classification impact. | PENDING | **P3** | No | — |
| **AXA-FLEX-PROPERTY** | AXA WF Flex Property is a real-estate fund holding both equity and real-estate debt; doesn't map cleanly to any current `Fund_Nature`. Needs a domain/policy decision on whether to add a `Real Assets` sub-nature or use `Alternativo` with `Alt_Strategy='Real Assets'`. | PENDING | **P3** | Yes (nature mapping) | [SESSION_SUMMARY v1 §🔴 New](SESSION_SUMMARY_20260704_classification_fixes.md) |
| **EFF-FIELDS-WHITELIST** | No automated guard exists to flag when `_EFF_FIELDS_WHITELIST` in `_db_utils.py` goes stale after a schema change. Previously caused silent null-out of all effective fields (fixed), but no prevention mechanism was added. | ✅ RESOLVED (2026-07-26) — `assert_eff_fields_alignment(conn)` added to `_db_utils.py`; called from `run_block.py` startup (after `assert_schema_alignment`); fail-fast with descriptive error if whitelist drifts | **P3** | No | `proyecto1/core/_db_utils.py` |

---

## Dependency graph

```
RFC-RFF-POLICY (P1, this session)
    └─→ OPT-B (P0, next approved session)

BGF-CHINA-BOND / NORDEA-IU / MIXED-NATURE (P1)
    └─→ Independent; can run in parallel with Option B prep

BENCHMARK-B4-B7 / CARMIGNAC / SC-H2-ARCANO / BL-B6-EM-GOVT (P2)
    └─→ Independent; lower priority

P3 items — cosmetic; tackle as bandwidth allows
```

---

*Generated 2026-07-16. Update this file when items are resolved — move resolved rows to the v2 session summary reconciliation table.*
