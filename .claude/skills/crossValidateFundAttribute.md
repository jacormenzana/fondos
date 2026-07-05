---
name: crossValidateFundAttribute
description: Add or audit a dual-signal (fund name + KIID text) cross-validated fund_master attribute, following the discipline established for Asset_Currency/Fund_Currency. Use when asked to add cross-validation for a fund_master attribute, extend name+KIID-text checking to a new field (e.g. Geography, Hedging_Policy, Accumulation_Policy), or audit an attribute for the same bug class found in Asset_Currency (share-class-vs-asset confusion, hedge-target confusion, multi-value continuations, negation, permissive/secondary language).
---

# Cross-Validate a fund_master Attribute (name + KIID text)

## Core Principle

**Investigate real corpus text before writing any pattern.** The retired `Portfolio_Currency` attribute failed (98.7% NULL) because its extraction patterns were literal phrasings guessed in advance, never verified against real KIID text. Every pattern in this procedure must be built FROM observed corpus text, not designed first and tested second.

**Two independent signals, cross-checked, never one silently overriding the other.** Both the fund-name signal and the KIID-text signal can be wrong — proven both ways during the Fund_Currency work (118 real KIID-side bugs from a JPM-specific document-formatting quirk; 2 confirmed cases where the *name* was misleading and KIID text was correct). Disagreement is a signal to flag for review, not a tiebreak to resolve automatically, unless the specific failure mode is well understood and validated first (as with the JPM sub-fund-vs-share-class bug).

## When to use this skill

Invoke when asked to:
- Add cross-validation / a second independent signal for a fund_master attribute (e.g. "do the same thing for Geography that we did for Asset_Currency")
- Audit an existing attribute's KIID-text extractor for the same bug class as Asset_Currency/Fund_Currency
- Investigate why a fund_master attribute has an unexpectedly high NULL rate, or suspiciously uniform values across share classes of the same sub-fund

Do NOT use for: numeric/continuous fields (cost figures, dates) — those need tolerance-based reconciliation (see `cost_arbitration.py`'s dual-path pattern), not categorical cross-validation. Do NOT use for attributes with no plausible independent second signal (e.g. SRRI has no name-side equivalent).

## Precondition check — is this attribute a good candidate?

Before starting, confirm ALL of:
1. **Small, closed value space** (an enum, an ISO code list, a handful of categories) — a mismatch between signals must be unambiguous, not a judgment call.
2. **At least one signal has genuine structural backing**, not just a heuristic — e.g. an industry-wide share-class naming convention, or a fixed section of a regulated document (KIID objective window, cost table). Two heuristics agreeing is weak evidence; a heuristic agreeing with a structural signal is strong evidence.
3. **A second, independent signal actually exists** in the corpus. If not, this pattern doesn't apply — don't force it.

If any of these fail, stop and say so rather than building a weaker version of this pattern.

## Procedure

### 1. Baseline
- Query current NULL rate / coverage for the target attribute.
- Read the existing extraction logic (if any) end to end before touching it.
- If the attribute was previously removed from schema (check `V20_DELETED_ATTRIBUTES` / tombstones in `schema_checks.py`, `SCHEMA_REFERENCE.md`), find out *why* — a prior near-100%-NULL failure is diagnostic (it means the old patterns were too literal, not that the concept is unextractable).

### 2. Investigate real corpus text (never guess)
- Pull a sample of KIID `Raw_KIID_Text` for funds where the name gives no signal.
- Search for candidate phrase families broadly first (e.g. all verb forms, not just one gender/number), then narrow.
- For every candidate match, read enough surrounding context (not just the matched phrase) to judge whether it's describing the ATTRIBUTE ITSELF or something adjacent — the recurring trap is a *structurally similar* sentence about a different subject:
  - the share class / clase de participación (not the fund's own attribute)
  - the objective/return measurement framing ("expresada en...", "moneda base del fondo") — proven unreliable via a genuine counter-example (a diversified fund using identical phrasing)
  - a narrow technical mechanism (a specific access quota, a benchmark description) too specific to represent the whole fund
  - a hedge target (share-class-level, not asset-level)
  - a multi-value enumeration ("... u otras divisas", "otras monedas del G7") — the phrase names the attribute but explicitly says it's NOT a single dominant value
  - a negation ("deuda **no** denominada en...")
  - a permissive/secondary mention ("podrán ser invertidos") — optional/secondary, not the primary declared value

### 3. Design the classifier
- Prefer a **closest-keyword classifier** over "does this keyword appear anywhere in the window": for each verb/anchor match, find whichever of two keyword vocabularies (include-subject vs. exclude-subject) is grammatically closest to the anchor, not just present somewhere in a wide lookback window. ("Anywhere in window" produces real false exclusions — e.g. "rentabilidad" appearing far before the true, closer subject "valores".)
- Add guards for each trap class found in step 2: negation, multi-value continuation, permissive/secondary language, hedge-target confusion.
- Prefer leftmost/closest-to-anchor match resolution over "first pattern in an arbitrary list wins" — list-order dependency is a silent source of wrong answers when multiple candidates exist in the same text.
- Build the name-side extractor too, if a naming convention exists — reuse existing suffix/pattern-mask logic already in `classify_utils.py` where applicable (e.g. `_TRAILING_SHARE_CLASS_SUFFIX`, `_HEDGE_TARGET_CURRENCY`) rather than duplicating it.

### 4. Corpus-wide regression check — precise, not approximate
- **Never count "phrase present somewhere in the corpus."** That measures the wrong thing and produces numbers 5-10x too large. Count precisely: only funds where the upstream step would actually *consult* this signal, and where the new logic's output *differs* from the old.
- Manually review every flagged difference, not a sample — this is how the closest-keyword bug, the multi-value-continuation bug, and the permissive-language bug were each found: by reading full context on funds the naive first pass got wrong.
- Explicitly re-check: does the fix introduce any NEW disagreement on cases that were previously correct? (Both directions of regression matter.)

### 5. Wire as cross-validation, not silent override

**Default backbone: `COALESCE(kiid_text, name)`.** KIID text is a regulated document and structurally authoritative; the fund-name convention is a secondary, market-side signal. Compute both signals as standalone variables *before* building the record dict — never chain them with Python's `or` directly inside the dict literal, or the second signal will never even be evaluated once the first answers, and disagreement can never be detected (this exact bug existed in the first Asset_Currency rollout: `name or kiid_text` short-circuited, so `detect_asset_currency_from_kiid_text` was silently dead code whenever the name matched). Correct shape:
```python
_x_kiid = detect_x_from_kiid_text(kiid_text)
_x_name = detect_x_from_name(fund_name)
fund_master_record["X"] = _x_kiid or _x_name
```
- If both signals exist and agree (or one is None), proceed normally.
- If both exist and disagree, this is a `Data_Quality_Flag` issue. **Do not write `log_ingestion(...)` and mutate `fund_master_record["Data_Quality_Flag"]` directly at the disagreement site.** As of `FIX-DQ-1` (2026-07-05), pipeline.py accumulates every quality issue for the current ISIN in a local `_dq_issues: list[tuple[str, str, str, str]]` — `(check_code, dq_level, log_status, message)` — and flushes it exactly once, unconditionally, via `_finalize_data_quality_issues()` right before `publish_fund`. Append to it instead:
  ```python
  if x_mismatch:
      _dq_issues.append((
          "X_NAME_KIID_MISMATCH", "WARN", "WARN",
          f"... revisar manualmente, ninguna señal es autoritativa por sí sola."
      ))
  ```
  This exists because the old pattern — mutate `Data_Quality_Flag` inline, guarded by `== "OK"` so a later check wouldn't clobber an earlier one — silently dropped information whenever two independent problems co-occurred on the same fund: whichever check ran first "won," and later checks' `log_ingestion` calls were sometimes gated behind the very same guard (found in `INTER_NTC_CONTRADICTION`), meaning the disagreement was never even written to `ingestion_log`. The accumulator removes the ordering dependency: every issue is always logged, and the final `Data_Quality_Flag` is the maximum severity across all of them (`shared.config.DATA_QUALITY_SEVERITY`), not whichever mutation happened to run last. Each issue is also persisted to `fund_data_quality_issues` (one row per `(ISIN, check_code)`, rebuilt every cycle) — the queryable "what's wrong with this fund right now" table, distinct from `ingestion_log`'s full historical event stream.
- Do not silently prefer one signal, unless the specific disagreement pattern has been individually investigated and is well understood (as with the JPM sub-fund-base-currency fallback) — and even then, prefer fixing the root cause over hard-coding a preference rule.

### 5b. Secondary pattern — validating one attribute against an *already-derived* cross-attribute signal

Not every attribute has its own independent name-side and KIID-text-side extractors. Some attributes are instead validated against a signal already computed for a *different* pair of attributes. `Hedging_Policy` is the worked example: it has no meaningful standalone name/KIID dual-signal of its own, but its declared value can be checked for **plausibility** against the Asset_Currency/Fund_Currency comparison already built in step 5 (`detect_fx_share_class_mismatch`).

The trap when reusing an existing comparison function this way: check what its "no conflict" return value actually means before trusting it. `detect_fx_share_class_mismatch()` returns `False` both when the two currencies are confirmed equal AND when either is `None` (unknown) — a conflation that is safe for its original caller (BL-44, where "unknown" should never grant an exemption) but dangerous here, where "unknown" must NOT be read as "confirmed no mismatch." Asset_Currency is `None` for ~70% of the corpus by conservative design, so treating its absence as "confirmed same currency" produced 628 false-positive flags in the first pass — corpus-tested and caught before being applied. The fix: still call the shared function (don't duplicate its comparison logic inline), but gate its result behind an explicit `bool(x) and bool(y)` "both known" guard, and only treat the function's `False` as meaningful when that guard passes:
```python
_both_known = bool(asset_ccy) and bool(fund_ccy)
_raw_mismatch = detect_fx_share_class_mismatch(asset_ccy, fund_ccy, hedging_policy=None, srri=None)
_confirmed_no_mismatch = _both_known and not _raw_mismatch
if hedging_claims_hedged and _confirmed_no_mismatch:
    _dq_issues.append((
        "HEDGCCY_NO_MISMATCH_INCONSISTENCY", "WARN", "WARN",
        f"Hedging_Policy={hedging_eff!r} pero Asset_Currency == Fund_Currency "
        f"(ambas confirmadas) -- no hay descalce de divisa que justifique la "
        f"cobertura declarada; revisar manualmente."
    ))
    # corpus count after the both-known guard: 36 (down from 628 with the naive version)
```
Only flag one direction: a fund claiming to be hedged with no currency mismatch to hedge is suspicious; a fund with a confirmed mismatch that does NOT claim to be hedged is a normal, legitimate structure (unhedged currency-exposed share class) and must not be flagged.

### 6. Tests
- Add a pytest file mirroring `test_asset_currency.py`/`test_fund_currency.py`'s structure: one test per guard class found in step 2, plus at least one end-to-end test reproducing the real bug that motivated the work.

### 7. Retroactive backfill
- Direct overwrite (not COALESCE) only when correcting a same-session-discovered bug in a brand-new or clearly-wrong value. Use COALESCE for anything that could represent a legitimate prior value.
- Log every corrected ISIN to `ingestion_log` with before/after values and the fix name.

### 8. Document
- Update the active session-summary doc (`doc/backlog/P1/SESSION_SUMMARY_*.md` or equivalent) with: root cause, corpus-wide numbers (before/after), guard classes added, test count, backfill count.
- If a schema attribute was added, bump `SCHEMA_VERSION` and update `schema_checks.py`/`SCHEMA_REFERENCE.md` per existing convention.

## Anti-patterns (do not do these)

- Writing a pattern before checking what the real KIID text actually says.
- Counting "phrase appears in corpus" instead of "signal actually changes the extracted value."
- Checking "keyword appears anywhere in a wide window" instead of "closest keyword to the anchor."
- Auto-resolving a name-vs-text disagreement in one fixed direction without investigating the specific failure mode first.
- Skipping the regression test file because "it's just a small fix."
- Stopping after finding the first bug in a pattern — the Fund_Currency work needed three separate rounds (colon-table format, then cross-validation exposing 2 false alarms, then a case with no cost table at all) before the picture was complete.

## Reference implementations

- `proyecto1/core/classify_utils.py`: `detect_asset_currency_from_name`, `detect_asset_currency_from_kiid_text`, `detect_fund_currency_from_name` — full worked examples of every guard class above.
- `proyecto1/core/kiid_parser.py`: `_detect_fund_currency` (`FIX-FUNDCCY-1`) — example of fixing a document-format-specific extraction failure.
- `proyecto1/core/pipeline.py`: `FUNDCCY_NAME_KIID_MISMATCH` / `ASSETCCY_NAME_KIID_MISMATCH` (`FIX-FUNDCCY-3`/`FIX-ASSETCCY-3`) — the COALESCE(kiid_text, name) backbone + Data_Quality_Flag wire-up for both currencies. `HEDGCCY_NO_MISMATCH_INCONSISTENCY` (`FIX-HEDGCCY-1`) — the secondary pattern (step 5b), reusing `detect_fx_share_class_mismatch` with an explicit both-known guard.
- `proyecto1/tests/test_asset_currency.py`, `test_fund_currency.py` — test structure to mirror.
- `proyecto1/core/classify_utils.py`: `detect_geography` / `detect_geography_from_kiid` (`FIX-GEO-NAME-1`/`FIX-GEO-KIID-1`, 2026-07-05) — the most severe case found to date, worth reading end-to-end before starting a new attribute. Both a name-side AND a KIID-side extractor had independent root-cause bugs, found by literally reading raw KIID text for every disagreement rather than guessing:
  - Name-side: 3 fallback rules read the share-class **currency denomination** (`" usd "`, `"usdh"`, `" eur "`) as if it were investment geography — same share-class-vs-asset confusion as Currency, just a different attribute pair. Also a plain substring check (`"us " in name_l`) with no left-word-boundary, silently matching inside unrelated words (`JAN`**`US `**` HENDERSON`, `AMUNDI REND `**`PLUS `**`RESP`) — fixed with `re.compile(r"\bus\b")`.
  - KIID-side: benchmark-name fragments (`"s&p 500"`, `"dow jones"`, `"russell 1000/2000"`, `"nasdaq"`) were kept in the signal list under a comment claiming they were "reliable indirect signals" — corpus-verified false (244 hits, always as ONE component of a blended multi-region composite index, never the fund's own objective). The two remaining bare, verb-less signals (`"estados unidos"`, `"norteamerica"`) needed a 3-way guard (`_geo_bare_match_guard`): negation ("empresas... **fuera de** los estados unidos"), issuer/instrument detail ("MBS... **emitidos por** organismos... de estados unidos" — describing a bond's issuer, not the fund's mandate), and multi-region enumeration ("empresas **norteamericanas y europeas**" → the correct answer is `Global`, not either single region).
  - Corpus-wide: 324 → 25 name/KIID disagreements (of 619-981 funds with both signals) after both fixes — read `SESSION_SUMMARY_20260704_classification_fixes.md` §FIX-GEO-1 for the full before/after numbers and the backfill impact analysis (this was the first attribute where the backfill itself needed a user checkpoint — ~1,375 funds/43% of the corpus were affected, since removing a bad signal can turn a wrong-but-present value into a correct `NULL` when no other signal exists).
  - Gotcha specific to Geography (no analogue in Currency): `Geography` is stored EN-translated (`classify_utils._derive_geography_en`, ES→EN) and `Development_Status` is *derived from* `Geography`, both normally computed inside the block via `apply_semantic_validation`. Recomputing the COALESCE value independently in `pipeline.py` (as this pattern requires, since `classification.get("Geography")` is already block-mixed and can't be un-mixed) means you must *also* re-run `_derive_geography_en`/`derive_development_status` on your recomputed value in `pipeline.py` before assigning — otherwise the untranslated Spanish value (or a `Development_Status` computed from the block's stale, pre-COALESCE `Geography`) leaks into `fund_master`. Caught via a 5-ISIN dry run through the real pipeline before committing to the full backfill — this is why step 4's corpus check should be followed by an actual `run_block.py --list-isin` dry run on a handful of ISINs, not just a standalone script re-implementing the extractors.
  - **A second, easy-to-miss variant of the same translation gotcha, found only AFTER the full 1,372-ISIN backfill ran, not the dry run**: `pipeline.py` has *other*, unrelated late fallback blocks that also assign `Geography` directly — BL-50's Investment_Universe→Geography inference and the Benchmark_Declared/v20 name-pattern inference (`_inferred_geo_b`, `_inferred_geo`) — each with their OWN raw Spanish literals (`"Europa"`, `"EEUU"`, `"Emergentes"`, `"Italia"`, `"Asia"`), never routed through `_derive_geography_en` either, and pre-dating this session entirely. They only became visible because this fix routes far more funds through those late fallbacks than before (removing a bad signal creates more `Geography=NULL` cases upstream for them to catch). **Lesson: when an attribute has more than one write site in `pipeline.py` (`grep` for every `fund_master_record["X"] = ` occurrence, not just the primary COALESCE one), a translation/normalization fix applied at the primary site does not cover the others — audit all of them, not just the one this pattern tells you to touch.** One of those literals (`"Italia"`) turned out to have no entry in `_GEO_ES_TO_EN` *or* `DOMAIN_VALUES['Geography']` at all — an invalid domain value being silently persisted, fixed by adding the missing map entry rather than papering over it in `pipeline.py`.
- `proyecto1/tests/test_geography.py` — test structure for a case with two independently-buggy extractors (vs. Currency's single-extractor-bug case).
