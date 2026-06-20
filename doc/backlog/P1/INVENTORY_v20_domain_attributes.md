# v20 — Finalized Domain Inventory, Thresholds & Heuristic Refactor

**Scope:** delegated domain definitions for the categorical attributes, the
institutional thresholds behind them, and the heuristic-block refactor that
maps the existing block signals onto the v20 domains. Standards basis: UCITS
KIID / PRIIPs KID, Morningstar fixed-income category conventions, MMFR
2017/1131.

**Architecture (root-cause / DRY):** the 7 heuristic blocks were **not edited**.
All v20 derivation is centralized in `classify_utils.derive_v20_attributes()`,
invoked once at the top of `apply_semantic_validation()` — the single chokepoint
every block (and every `restantes` fallback path) already calls last. The blocks
keep emitting their legacy signals (`Type`, `Subtype`, ES-`Geography`, `Family`,
`Profile`); the engine transforms them into the v20 columns. Idempotent.

---

## 1. Geography (+ Development_Status split)

The old `Geography` mixed *space* and *development tier* (`Emergentes`). v20
splits them into two orthogonal axes.

**`Geography` (spatial, EN):** `Global · Europe · North America · Asia-Pacific ·
Japan · China · India · Latin America · Eastern Europe · Middle East & Africa`.

**`Development_Status`:** `Developed · Emerging · Frontier · Global/Mixed`.

**ES→EN mapping:** Europa→Europe, EEUU→North America, Asia→Asia-Pacific,
Japón→Japan, Latinoamérica→Latin America, Europa del Este→Eastern Europe,
China/India/Global unchanged. `Emergentes` is **not** a place → `Middle East &
Africa` if the name says MENA/Gulf/Africa, otherwise `Global`, and the tier
moves to `Development_Status='Emerging'`.

**Development tier rules:** frontier markers → Frontier; emerging markers or
es-geo `Emergentes` or spatial geography ∈ {China, India, Latin America, Eastern
Europe, Middle East & Africa} → Emerging; {Europe, North America, Japan} →
Developed; Global / Asia-Pacific (mixed DM+EM) / unknown → Global/Mixed.

---

## 2. Duration_Profile (fixed-income duration band)

**Allowed:** `Ultra-Short · Short · Intermediate · Long · Flexible · Not Applicable`.

**Thresholds (effective/modified duration, years — Morningstar/sector baseline):**

| Band | Duration | Typical |
|---|---|---|
| Ultra-Short | < 1 | enhanced cash, money-plus, MMF |
| Short | 1 – 3.5 | short-term bond/credit |
| Intermediate | 3.5 – 6 | core / aggregate |
| Long | > 6 | long govt / long credit |
| Flexible | unconstrained | dynamic/strategic/total-return mandate (duration actively varied) |

**Heuristic by nature:** Monetario → Ultra-Short (WAM < 1y); RF Corto → Ultra-Short
on explicit ultra/0-1/enhanced-cash signal else Short; RF Flexible → Flexible by
default (mandate roams), with Long / Short / Intermediate when the name pins a
band; **everything else → Not Applicable**. Coverage matches the FI universe
(RF Corto + RF Flexible + Monetario).

---

## 3. Credit_Quality

**Allowed:** `Investment Grade · High Yield · Mixed · Not Applicable`.

**Thresholds (portfolio average rating — S&P/Fitch/Moody’s):**

| Class | Average rating | Definition |
|---|---|---|
| Investment Grade | ≥ BBB- / Baa3 | predominantly IG (≈ ≥80% IG) |
| High Yield | ≤ BB+ / Ba1 | predominantly sub-IG |
| Mixed | crosses the IG/HY boundary | crossover / flexible-credit; no sleeve ≈ ≥80% |

Holdings aren’t available to the heuristic, so the average-rating test is
approximated by name/mandate signals. **By nature:** Monetario → IG (MMFR high-
quality requirement); RF Corto → IG unless HY signal; RF Flexible → HY on HY
signal, Mixed on crossover/flexible/strategic/unconstrained/total-return, IG on
govt/sovereign/aggregate/IG signal, else Mixed (flexible-credit default);
Equity/Mixtos/Alternativo/Estructurado → **Not Applicable**; Restantes → NULL.

---

## 4. Profile (risk profile = two-axis model)

**Allowed:** `Conservador · Moderado · Dinámico · Agresivo`.

SRRI alone mislabels asset classes (e.g. equity flagged SRRI 2). v20 uses
**`Profile = clamp(block_profile, nature_floor, nature_cap)`** — the block’s
SRRI-derived guess, bounded by an asset-class floor/cap (ordinal
Conservador 0 → Agresivo 3):

| Fund_Nature | floor – cap |
|---|---|
| Monetario | Conservador – Conservador |
| Renta Fija Corto Plazo | Conservador – Moderado |
| Renta Fija Flexible | Moderado – Dinámico |
| Mixtos | Conservador – Dinámico |
| Renta Variable | Moderado – Agresivo |
| Alternativo | Moderado – Dinámico |
| Estructurado | Moderado – Dinámico |
| Restantes | Conservador – Agresivo |

SRRI base buckets: 1-2→Conservador, 3-4→Moderado, 5-6→Dinámico, 7→Agresivo.
**Note:** `Agresivo` requires SRRI=7. The clamp bounds but does not *promote*; an
SRRI=7 promotion step belongs in the SRRI-aware pipeline stage (deferred). Today
the engine can reach Agresivo only if a block already produced it.

---

## 5. Liquidity_Profile (dealing frequency)

**Allowed:** `Daily · Weekly · Bi-Weekly · Monthly · Not Applicable`.

This is **dealing/redemption frequency**, not the T+n settlement code from the
old data. UCITS retail funds must offer at least bi-monthly liquidity and in
practice >95% deal **daily**, so the industry-accurate default is `Daily`,
downgraded only on an explicit weekly/fortnightly/monthly dealing signal.

---

## 6. Structural attributes realigned in the same pass

- **Vehicle_Structure** `Open-End UCITS · ETF · Fund of Funds · Money Market Fund
  · Structured Product` — ETF signal→ETF; Monetario→Money Market Fund;
  Estructurado→Structured Product; fund-of-funds→Fund of Funds; else Open-End UCITS.
- **MMF_Structure** (MMFR) `CNAV · LVNAV · VNAV · Standard MMF · Not Applicable`
  — relocated from the dropped `Subtype` for Monetario; non-MMF → Not Applicable.
- **Alt_Strategy** `Long/Short · Market Neutral · Global Macro ·
  Relative Value/Arbitrage · Opportunistic · Volatility Target · Not Applicable`
  — relocated from `Subtype` for Alternativo; AR without a specific strategy →
  Opportunistic; Real Assets → Not Applicable; non-alt → Not Applicable.
- **Payoff_Profile** `Autocallable · Capital Protected · Fixed Coupon Band ·
  Not Applicable` — autocall / capital-protected / fixed-coupon signals; else N-A.
- **Exposure_Bias** repurposed to a **directional** axis `Long Only · Long/Short ·
  Market Neutral · Net Short · Not Applicable` (§2A.1 #11). Legacy FI/liquidity
  biases (Duration/Credit/Income/Liquidity Bias) carry no directional content →
  collapse to `Long Only`; their information now lives in Duration_Profile /
  Credit_Quality.
- **Investment_Universe** `Global · Regional · Country` — dropped non-geographic
  `Liquidity` (§2A.1 #5); Monetario/RF Corto fallback → `Global`.
- **Replication_Method** repurposed to replication *technique* `Physical ·
  Synthetic · Sampling · Not Applicable` (§2A.1 #9): passive Strategy →
  Physical (default technique); active Strategy → Not Applicable. The
  active/passive axis now lives exclusively in `Strategy`.
- **Style_Profile** legacy `Risk Control` / `Tactical` → `Strategic Allocation`.

---

## 7. Code modifications (all in `classify_utils.py`)

1. **New centralized engine** `derive_v20_attributes(record, fund_name)` + helpers
   (`_derive_geography_en`, `derive_development_status`, `derive_vehicle_structure`,
   `derive_mmf_structure`, `derive_alt_strategy`, `derive_payoff_profile`,
   `derive_duration_profile`, `derive_credit_quality`, `derive_liquidity_profile`,
   `derive_exposure_bias`, `_refine_profile`) + maps (`_GEO_ES_TO_EN`,
   `_PROFILE_BOUNDS_BY_NATURE`, `_ALT_STRATEGY_MAP`, `_STYLE_LEGACY_REMAP`).
   Idempotent; no taxonomy invented (industry-standard definitions only).
2. **One wiring line** at the top of `apply_semantic_validation()` — runs the
   engine before validation so the validator sees v20 values. Zero block edits.
3. **`_COUNTRY_GEOGRAPHIES` / `_REGION_GEOGRAPHIES`** realigned ES→EN so INTER-10
   (`validate_geography_universe`) keeps working on the EN vocabulary.
4. **INTER-1** (`validate_strategy_replication`) rewritten to the v20 technique
   vocabulary (passive→Physical, active→Not Applicable).
5. **BL-33 fallback** in `validate_all_semantic_consistency`: universe
   `Liquidity`→`Global`; emerging branch now sets EN `Geography='Global'` +
   `Development_Status='Emerging'` instead of ES `Geography='Emergentes'`.

**Validation:** AST clean; engine unit/integration tests pass (all natures +
idempotency incl. generic-EM and MENA edge cases); a 21-fund corpus across all 6
primary blocks in the real import topology yields **0 out-of-set categorical
values and 0 allowed-values warnings**.

---

## 8. Open flags (need your call / files I don’t have)

- **`fund_characterizer.py` / `pipeline.py`** run after block classification and
  very likely also emit `Replication_Method` ACTIVE/PASSIVE, `Investment_Universe`
  `Liquidity`, and set `Accumulation_Policy`. The block-time fixes guard the block
  path; these downstream emitters need the same v20 alignment. Upload them and I’ll
  apply the identical centralization.
- **`Accumulation_Policy` casing:** `validate_accumulation_distribution` tests
  `== 'ACCUMULATION'` (UPPER), but v20 casing writes `Accumulation` (Title). If the
  policy is set before that validator runs, INTER-2 could miss — needs pipeline
  ordering visibility to fix safely.
- **Mixtos `Credit_Quality`:** set to `Not Applicable` (multi-asset has no single
  credit profile). If you prefer `Mixed` for the bond sleeve, it’s a one-line flip.
- **`Agresivo` promotion** for SRRI=7 needs the SRRI-aware pipeline step (deferred).
