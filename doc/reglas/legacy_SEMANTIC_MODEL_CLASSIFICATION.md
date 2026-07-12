# Semantic Model — Classification Attribute Cluster

> **Purpose.** This document defines the functional domain of each classification attribute,
> the intended semantics of every allowed value, the applicability conditions (when an attribute
> is mandatory, conditional, or NULL), and the cross-attribute consistency rules that follow.
> It is the normative reference for classifiers and INTER rules. INTER rules must be derived from
> the rules here, not added ad-hoc when individual inconsistencies surface.
>
> **Scope.** The attributes covered form two interlocked clusters:
> - **Cluster A — Classification hierarchy**: `Fund_Nature` → `Family`
> - **Cluster B — Investment focus / specialization**: `Investment_Focus` → `Theme` + `Sector_Focus`
>
> Other attribute clusters (e.g. `Geography`/`Investment_Universe`, `Credit_Quality`/
> `Duration_Profile`, `Market_Cap_Focus`) are outside this document's scope but follow the
> same design principle: each cluster should have its own documented model.
>
> **Date**: 2026-07-11. **Source**: grounded in 3,200-fund DB distribution analysis.
>
> **Industry alignment.** The taxonomy in this document is intentionally aligned with two
> industry standards:
> - **GICS (Global Industry Classification Standard, MSCI/S&P)** — the `Sector_Focus` 8-bucket
>   taxonomy is a consolidation of GICS Level 1 sectors (11 → 8, combining Consumer
>   Staples+Discretionary, Information Technology+Communication Services, and grouping Real
>   Estate under Real Assets). The `Theme` sub-values follow GICS Level 2/3 sub-sectors where
>   applicable (e.g. `Biotechnology` = GICS sub-sector within Health Care; `Gold` / `Mining` =
>   GICS sub-sectors within Materials).
> - **Morningstar European Equity Categories** — the `Sector` vs `Thematic` distinction in
>   `Investment_Focus` mirrors Morningstar's split between "Sector Equity" (single-industry-sector
>   mandate, benchmarkable against a sector index) and "Thematic Equity" (cross-sector narrative).
>   The `Theme` values for cross-sector funds (`Megatrends`, `Inflation`, `Climate / Clean Energy`,
>   `Artificial Intelligence`, etc.) align with Morningstar's Thematic Equity sub-categories.
>
> **Known taxonomy gap.** Some `Theme` values (`Gold`, `Mining`, `Biotechnology`, `Insurance`,
> `Artificial Intelligence`, `Robotics`, `Digital`, `Cybersecurity`) represent GICS Level 2/3
> sub-sectors, one level below their parent `Sector_Focus`. This creates an implicit two-level
> hierarchy within the `Theme` + `Sector_Focus` pair (e.g. Theme=`Gold` → Sector_Focus=`Materials
> & Mining` means "Level 3 within Level 1"). This is intentional — `Theme` provides the
> fund-level specificity that a Level 1 `Sector_Focus` alone cannot convey. It becomes a
> semantic problem only when a sub-sector `Theme` is mistakenly assigned `Investment_Focus=
> 'Thematic'` (which would imply it is cross-sector).

---

## 1. Cluster A — Classification hierarchy

### 1.1 `Fund_Nature` (Level 1)

**Functional domain**: Primary regulatory/asset-class category. Determines which P1 block
classifies the fund and which attributes are applicable in the remaining clusters.

| Value | Meaning | Applicable blocks |
|-------|---------|------------------|
| `Renta Variable` | Equity-predominant (≥ ~60% equity) | renta_variable |
| `Mixtos` | Mixed (equity + bonds, no strong equity/FI dominance) | mixtos |
| `Renta Fija Flexible` | Unconstrained / flexible fixed income | rf_flexible |
| `Renta Fija Corto Plazo` | Short-term or investment-grade constrained FI | rf_corto |
| `Monetario` | UCITS money-market fund | monetarios |
| `Alternativo` | Alternative strategies (absolute return, real assets, long/short) | alternativos |
| `Estructurado` | Capital-protected structured products | restantes/alternativos |
| `Restantes` | Unclassified; residual | restantes |

**Applicability**: Mandatory for all funds. NULL only during in-flight processing before
the classifying block runs.

---

### 1.2 `Family` (Level 2)

**Functional domain**: Sub-classification within `Fund_Nature`. Encodes the investment style
or strategy type, providing finer granularity than `Fund_Nature` alone.

**Valid `Fund_Nature` → `Family` combinations** (DB-verified):

| Fund_Nature | Valid Family values |
|-------------|-------------------|
| `Renta Variable` | `Equity Core`, `Thematic Equity` |
| `Mixtos` | `Multi-Asset`, `Income Oriented` |
| `Renta Fija Flexible` | `Flexible Fixed Income`, `High Yield`, `Emerging Market Debt`, `Inflation-Linked`, `Income Oriented`, `Strategic Allocation` |
| `Renta Fija Corto Plazo` | `Short-Term Fixed Income` |
| `Monetario` | `Money Market` |
| `Alternativo` | `Absolute Return`, `Real Assets` |
| `Estructurado` | `Structured` |
| `Restantes` | NULL (residual category; no Family assigned) |

**Applicability**: Mandatory for all `Fund_Nature` values except `Restantes`.

**Consistency rule SC-A1** — `Family` must be within the valid set for its `Fund_Nature`.
A `Renta Variable` fund cannot carry `Family='Money Market'` or `Family='Short-Term Fixed
Income'`. Such combinations indicate either a wrong-KIID classification or a classifier bug.

**Known DB violations (2026-07-11)**:

| ISIN pattern | Fund_Nature | Family | Root cause |
|-------------|-------------|--------|------------|
| 1 fund | `Renta Variable` | `Short-Term Fixed Income` | Classifier error |
| 1 fund | `Renta Variable` | `Money Market` | Classifier error |
| 1 fund | `Restantes` | `Multi-Asset` | COALESCE artefact (old Family preserved after reclassification) |

---

## 2. Cluster B — Investment focus / specialization

This cluster uses three attributes together:

- **`Investment_Focus`** — *how* concentrated the investment mandate is
- **`Theme`** — *what topic/focus* the fund addresses (always a sub-descriptor)
- **`Sector_Focus`** — *which industry sector*, used only when the mandate is sector-concentrated

### 2.1 `Investment_Focus`

**Functional domain**: Degree and type of portfolio concentration.

| Value | Meaning | When to use |
|-------|---------|------------|
| `Broad` | Diversified portfolio without a single-sector or single-theme concentration; fund invests across multiple industries or themes | Core equity, balanced FI, global diversified |
| `Sector` | Portfolio concentrated in a **single industry sector** as classically defined (aligns with GICS/ICB sector boundaries); can be benchmarked against a sector index | Sector equity funds, sector bond funds |
| `Thematic` | Portfolio concentrated in a **cross-sector macro trend or narrative** that does not map to a single industry sector; the investment thesis spans multiple traditional sectors | Inflation protection, megatrends, multi-thematic |

**Critical semantic distinction — Sector vs Thematic**:

> A fund is `Sector` when its universe is **defined by an industry classification** (all companies
> in GICS Technology, all companies in GICS Healthcare, etc.), regardless of how many sub-themes
> exist within that sector. A fund is `Thematic` when its universe is **defined by a narrative**
> that cuts across sectors (e.g. "inflation beneficiaries" include commodities producers, RE, TIPS,
> and inflation-linked bonds — crossing Equity, Fixed Income, Real Assets).

**Applicability by `Fund_Nature`**:

| Fund_Nature | Investment_Focus applicability |
|-------------|-------------------------------|
| `Renta Variable` | Mandatory. Any of Broad / Sector / Thematic |
| `Mixtos` | Mandatory. Typically Broad; Sector/Thematic for specialized allocation funds |
| `Renta Fija Flexible` | Conditional. NULL for unconstrained strategies with no sector/thematic mandate; Broad / Sector / Thematic when explicitly specialized |
| `Renta Fija Corto Plazo` | Typically NULL or Broad. Sector only for single-sector short-term bond funds |
| `Monetario` | NULL or Broad only. No sector/thematic sub-classification |
| `Alternativo` | Typically NULL or Broad. Thematic for thematic alternative funds |
| `Estructurado` | NULL. Structured products do not sub-classify by investment focus |
| `Restantes` | NULL |

---

### 2.2 `Theme`

**Functional domain**: The specific topic, focus, or narrative that the fund addresses within
its `Investment_Focus` category. Operates at finer granularity than `Investment_Focus`.

**Relationship with `Investment_Focus`**:

| Investment_Focus | Theme | Role of Theme |
|-----------------|-------|--------------|
| `Broad` | Any; `Core/General` is the default | Descriptive tilt. A broad global equity fund may tilt toward Technology without being a pure sector fund. |
| `Sector` | Specific sub-theme within the sector; or `Core/General` for whole-of-sector mandates | Fine-grained sub-label. Adds specificity within the sector. |
| `Thematic` | The cross-sector narrative | **Required and defining**. Without Theme, a `Thematic` Investment_Focus is semantically empty. |

**Theme value semantics and valid `Investment_Focus` pairing**:

| Theme | Valid Investment_Focus | Notes |
|-------|----------------------|-------|
| `Core/General` | `Broad`, `Sector` | Default for broad mandates or sector funds without a specific sub-theme |
| `Technology` | `Sector` | Pure technology sector |
| `Artificial Intelligence` | `Sector`, `Thematic` | Sector: tech-sector AI pure-play. Thematic: AI as cross-industry transformation |
| `Robotics` | `Sector`, `Thematic` | Same boundary as AI |
| `Digital` | `Sector`, `Thematic` | Same boundary |
| `Cybersecurity` | `Sector`, `Thematic` | Same boundary |
| `Climate / Clean Energy` | `Sector`, `Thematic` | Sector: energy-transition pure-play. Thematic: multi-sector ESG narrative |
| `Energy` | `Sector` | Traditional energy sector. If cross-sector energy transition → use `Climate / Clean Energy` |
| `Healthcare` | `Sector`, `Thematic` | Sector: broad healthcare-sector mandate. Thematic: healthcare-innovation narrative spanning pharma + tech + devices |
| `Biotechnology` | `Sector`, `Thematic` | Sector: biotech as a GICS sub-sector. Thematic: genomics/biotech revolution narrative |
| `Water` | `Sector`, `Thematic` | Sector: utilities/infrastructure sector. Thematic: water-scarcity ESG narrative |
| `Gold` | `Sector` | Precious metals / commodities sector |
| `Mining` | `Sector` | Materials / mining sector |
| `Real Estate` | `Sector` | Real estate sector (REITs). If Real Assets includes infrastructure + RE → Family `Real Assets` / Alternativo |
| `Financials` | `Sector` | Financial services industry sector |
| `Insurance` | `Sector` | Financial services sub-sector |
| `Consumer Brands` | `Sector`, `Thematic` | Sector: consumer staples/discretionary. Thematic: brand-driven consumer narrative |
| `Silver Economy` | `Sector`, `Thematic` | Sector: ageing-related healthcare/consumer. Thematic: demographic macro-trend |
| `Megatrends` | **`Thematic` ONLY** | Explicitly multi-sector by definition. Cannot be a sector fund. |
| `Inflation` | **`Thematic` ONLY** | Macro monetary theme. Spans TIPS, commodities, RE, inflation-linked bonds — crosses asset classes and sectors. Cannot be a single industry sector. |

**Consistency rule SC-B1** — `Theme='Megatrends'` → `Investment_Focus` MUST be `'Thematic'`.

**Consistency rule SC-B2** — `Theme='Inflation'` → `Investment_Focus` MUST be `'Thematic'`.

**Consistency rule SC-B3** — `Investment_Focus='Thematic'` → `Theme` MUST NOT be
`'Core/General'` (a thematic fund must have a named theme; Core/General implies Broad).

---

### 2.3 `Sector_Focus`

**Functional domain**: The **broad industry sector bucket** that the fund concentrates on.
Acts as the coarse-grained sector label (Level 1) when `Theme` (above) gives the fine-grained
sub-theme (Level 2) for sector funds.

**Critical applicability rule**:

> `Sector_Focus` is populated **if and only if** `Investment_Focus='Sector'`.
> It is NULL for `Investment_Focus='Broad'` and `Investment_Focus='Thematic'`.

This means:
- Thematic funds do NOT have a `Sector_Focus`. Their focus is defined entirely by `Theme`.
- Broad funds do NOT have a `Sector_Focus`.
- Only sector funds have both `Theme` (sub-theme) and `Sector_Focus` (sector bucket).

**Consistency rule SC-B4** — `Investment_Focus='Sector'` → `Sector_Focus` MUST NOT be NULL.

**Consistency rule SC-B5** — `Investment_Focus` ∈ `{'Broad', 'Thematic'}` → `Sector_Focus`
MUST be NULL.

**Canonical `Theme` → `Sector_Focus` mapping** (for `Investment_Focus='Sector'` funds):

| Theme | Sector_Focus |
|-------|-------------|
| `Technology` | `Technology & Innovation` |
| `Artificial Intelligence` | `Technology & Innovation` |
| `Robotics` | `Technology & Innovation` |
| `Digital` | `Technology & Innovation` |
| `Cybersecurity` | `Technology & Innovation` |
| `Climate / Clean Energy` | `Energy & Resources` |
| `Energy` | `Energy & Resources` |
| `Healthcare` | `Healthcare & Life Sciences` |
| `Biotechnology` | `Healthcare & Life Sciences` |
| `Silver Economy` | `Healthcare & Life Sciences` |
| `Water` | `Utilities & Environment` |
| `Gold` | `Materials & Mining` |
| `Mining` | `Materials & Mining` |
| `Real Estate` | `Real Assets` |
| `Financials` | `Financial Services` |
| `Insurance` | `Financial Services` |
| `Consumer Brands` | `Consumer` |
| `Core/General` | *sector-specific* (any; use the fund's explicit sector description) |

Themes `Megatrends` and `Inflation` have **no valid `Sector_Focus` mapping** — they must
always use `Investment_Focus='Thematic'` with `Sector_Focus=NULL`.

**Consistency rule SC-B6** — IF `Investment_Focus='Sector'` AND `Theme` IS NOT NULL THEN
`Sector_Focus` MUST equal the canonical mapping for that `Theme`. A fund with `Theme='Technology'`
and `Sector_Focus='Healthcare & Life Sciences'` is inconsistent.

---

## 3. Cross-cluster interactions

### 3.1 `Family='Thematic Equity'` vs `Investment_Focus`

`Family='Thematic Equity'` means the fund is an **equity fund with a specialized focus**.
It does NOT by itself determine whether the focus is a sector or a cross-sector theme.

| Family | Investment_Focus | When valid |
|--------|-----------------|-----------|
| `Thematic Equity` | `Sector` | Fund concentrates in a single industry sector (most common: 252 funds) |
| `Thematic Equity` | `Thematic` | Fund concentrates in a cross-sector theme (Megatrends, Inflation if equity-expressed) |
| `Thematic Equity` | `Broad` | **Not valid** — by definition a thematic equity fund has a focus |

**Consistency rule SC-C1** — `Family='Thematic Equity'` → `Investment_Focus` MUST be
`'Sector'` or `'Thematic'`. Never `'Broad'`.

### 3.2 `Family='Equity Core'` vs `Investment_Focus`

`Equity Core` = diversified equity fund. By definition, a core equity fund does not
concentrate on a single sector. However, sector-specific core funds exist (e.g.
"European Technology Core" — broad-within-technology, no sub-theme).

| Family | Investment_Focus | When valid |
|--------|-----------------|-----------|
| `Equity Core` | `Broad` | Standard broad-market equity fund (1,549 funds) |
| `Equity Core` | `Sector` | Core mandate within a sector (16 funds — e.g. energy core) |
| `Equity Core` | `Thematic` | **Not valid** — a core fund with a thematic mandate should be `Thematic Equity` |

**Consistency rule SC-C2** — `Family='Equity Core'` → `Investment_Focus` MUST NOT be
`'Thematic'`.

### 3.3 `Family='Inflation-Linked'` vs `Investment_Focus`

`Inflation-Linked` as a `Family` value applies to **fixed-income funds** whose portfolio
consists of inflation-linked bonds (OATs-i, TIPS, Bunds-i, etc.). It encodes a bond strategy,
not a thematic portfolio construction.

| Family | Investment_Focus | Sector_Focus | Theme | Valid? |
|--------|-----------------|-------------|-------|--------|
| `Inflation-Linked` | `Thematic` | NULL | `Inflation` | **Valid** — correct representation |
| `Inflation-Linked` | `Broad` | NULL | `Inflation` | **Valid** — also acceptable |
| `Inflation-Linked` | `Sector` | `Inflation-Linked` | `Inflation` | **Invalid** — `Inflation` is not a sector; `Inflation-Linked` is not in `Sector_Focus` DOMAIN_VALUES |

Rule SC-B1 (`Theme='Inflation'` → `Investment_Focus='Thematic'`) is sufficient to prevent the
third case.

---

## 3b. Cluster C — Bond-specific attributes

`Credit_Quality`, `Duration_Profile`, and `Market_Cap_Focus` are **asset-class-specific**
attributes. Their applicability depends on `Fund_Nature`.

| Attribute | Applicable natures | Non-applicable (→ `Not Applicable` or NULL) |
|-----------|-------------------|---------------------------------------------|
| `Credit_Quality` | `Renta Fija Flexible`, `Renta Fija Corto Plazo`, `Monetario` | `Renta Variable`, `Alternativo`, `Estructurado` |
| `Duration_Profile` | `Renta Fija Flexible`, `Renta Fija Corto Plazo`, `Monetario` | `Renta Variable`, `Alternativo`, `Estructurado` |
| `Market_Cap_Focus` | `Renta Variable`, `Mixtos` (equity component) | All fixed-income natures, `Monetario`, `Alternativo`, `Estructurado`, `Restantes` |

`Mixtos` is excluded from Credit_Quality/Duration_Profile auto-correction because mixed funds
legitimately carry a fixed-income component.

**Industry alignment:**
- `Credit_Quality` values (`Investment Grade`, `High Yield`, `Mixed`, `Not Applicable`) map
  directly to standard bond credit-rating categories defined by S&P/Moody's/Fitch tier levels
  and codified in UCITS KIID disclosure requirements.
- `Duration_Profile` values (`Ultra-Short` < 1y, `Short` 1-3y, `Intermediate` 3-7y,
  `Long` > 7y, `Flexible`, `Not Applicable`) align with the ICE BofA fixed-income index
  duration brackets and Morningstar fixed-income category classification.

**Consistency rules:** SC-D1 (Credit_Quality on equity → 'Not Applicable'), SC-D2
(Duration_Profile on equity → 'Not Applicable'). Both implemented in INTER-17.

---

## 3c. Cluster D — Fund structure attributes

### `MMF_Structure`

**Functional domain**: Legal/regulatory structure of a money-market fund under EU Regulation
2017/1131 (MMFR). Applies **only** to `Fund_Nature='Monetario'`.

| Value | Regulatory definition | MMFR reference |
|-------|-----------------------|---------------|
| `CNAV` | Constant Net Asset Value. NAV per unit fixed at €1.00 (or par). Only government/public-debt MMF. | Art. 29 MMFR |
| `LVNAV` | Low Volatility NAV. NAV per unit may deviate ≤0.2% from €1.00. Short-term MMF. | Art. 30 MMFR |
| `VNAV` | Variable NAV. NAV calculated at market value each dealing day. Standard or short-term MMF. | Art. 31 MMFR |
| `Standard MMF` | Standard non-short-term VNAV MMF (3-6 month weighted average maturity). | Art. 2(14) MMFR |
| `Not Applicable` | Not a UCITS MMF (applies to all non-Monetario funds). | — |

**Consistency rules:**
- **SC-E1**: `Fund_Nature ≠ 'Monetario'` AND `MMF_Structure ≠ 'Not Applicable'` → auto-correct to `'Not Applicable'`. Implemented in INTER-18.
- **SC-E2**: `Fund_Nature='Monetario'` AND `MMF_Structure='Not Applicable'` → WARN (all regulated MMF must have an explicit MMFR structure). Implemented in INTER-18.

### `Alt_Strategy`

**Functional domain**: Alternative investment strategy type. Applies only to
`Fund_Nature='Alternativo'`. Aligns with AIMA/HFR hedge fund strategy classification.

| Value | Meaning |
|-------|-------------------------------------------------|
| `Opportunistic` | No single strategy; opportunistic across opportunities |
| `Global Macro` | Top-down macro driven (currencies, rates, commodities) |
| `Long/Short` | Long equities, short equities via derivatives |
| `Market Neutral` | Zero net equity exposure (pairs trading, stat arb) |
| `Relative Value/Arbitrage` | Exploits spread differentials |
| `Volatility Target` | Volatility-managed portfolio |
| `Not Applicable` | Non-alternative funds |

`Alt_Strategy='Not Applicable'` on an Alternativo fund is permitted when the fund's strategy
does not map to a standard AIMA category. No auto-correction rule.

### `Payoff_Profile`

**Functional domain**: Payoff structure of structured/capital-protected products. Applies only
to `Fund_Nature='Estructurado'`. Currently only `Autocallable` is observed in the DB.

---

## 3d. Cluster E — Style and exposure attributes

### `Style_Profile`

**Functional domain**: Investment style / factor tilt of the fund's equity portfolio.

**Industry alignment — Morningstar Style Box + MSCI Factor Indices:**

| Value | Industry standard | Source |
|-------|------------------|--------|
| `Growth` | Growth style (high P/E, high earnings growth) | Morningstar Style Box |
| `Value` | Value style (low P/E, high dividend yield) | Morningstar Style Box |
| `Blend` | Blend of growth and value | Morningstar Style Box |
| `Income` | Dividend/income-focused equity | Morningstar Income category |
| `Low Volatility` | Minimum volatility / low beta factor | MSCI Min Vol factor |
| `Quality` | Quality factor (high ROE, low leverage, stable earnings) | MSCI Quality factor |
| `Momentum` | Momentum factor (recent price outperformance) | MSCI Momentum factor |
| `Strategic Allocation` | Multi-asset allocation style (for Mixtos/balanced funds) | Morningstar Allocation category |
| `Not Applicable` | Not an equity fund or no defined style | — |

**Applicability by `Fund_Nature`:**
- `Renta Variable` → any equity style value or `Not Applicable`
- `Mixtos` → `Strategic Allocation` (represents the balanced allocation style) or `Not Applicable`
- `Alternativo` → may carry an equity style if the fund is equity-oriented alternative (e.g., equity long/short with a blend style); `Not Applicable` preferred if no clear style
- `Restantes` → `Not Applicable` or NULL (residual; style from prior classification is stale) — WARN via SC-E3 (INTER-19)

**Consistency rule SC-E3**: `Fund_Nature='Restantes'` + `Style_Profile` ≠ `'Not Applicable'` and ≠ NULL → WARN. Implemented in INTER-19.

### `Exposure_Bias`

**Functional domain**: Net directional exposure of the fund.

| Value | Meaning | Typical Fund_Nature |
|-------|---------|-------------------|
| `Long Only` | Holds only long positions (no shorting) | Renta Variable, Mixtos, RF Flexible, RF Corto, Monetario |
| `Long/Short` | Combines long and short positions | Alternativo (Long/Short strategy) |
| `Market Neutral` | Zero net exposure (delta-neutral) | Alternativo |
| `Net Short` | Predominantly short positions | Alternativo (rare) |
| `Not Applicable` | Structured products | Estructurado |

All 3,213 standard (non-alternative, non-structured) funds carry `Long Only` — correct by
definition. No semantic rules needed beyond classification alignment.

### `Development_Status`

**Functional domain**: Market development classification of the fund's investment universe.
Aligns with **MSCI Market Classification** framework.

| Value | MSCI equivalent | Geography examples |
|-------|----------------|-------------------|
| `Developed` | MSCI Developed Markets | US, EU, Japan, UK, Canada, Australia |
| `Emerging` | MSCI Emerging Markets | China, India, Brazil, Korea, Mexico |
| `Frontier` | MSCI Frontier Markets | Vietnam, Nigeria, Kuwait |
| `Global/Mixed` | Blend (no single tier) | Global, multi-region, blended DM+EM |

**Valid `Geography` ↔ `Development_Status` combinations** (from DB analysis):
- `Europe` + `Developed`: 560 funds ✓ (Western Europe)
- `Europe` + `Emerging`: 2 funds ✓ (Eastern European EM funds — valid)
- `Global` + `Global/Mixed`: 1,539 funds ✓
- `Global` + `Emerging`: 164 funds ✓ (global EM equity)
- `China/India/Latin America/Eastern Europe` + `Emerging`: ✓
- `North America/Japan` + `Developed`: ✓

No systematic inconsistencies found. No additional rules needed.

---

## 4. Complete consistency rule summary

### Cluster A — Nature/Family alignment

| ID | Rule | Severity | Auto-correctable? | INTER rule |
|----|------|----------|-------------------|-----------|
| SC-A1 | `Family` must be in the valid set for `Fund_Nature` | ERROR | No — requires classifier fix | INTER-1 |

### Cluster B — Investment Focus / Theme / Sector

| ID | Rule | Severity | Auto-correctable? | INTER rule |
|----|------|----------|-------------------|-----------|
| SC-B1 | `Theme='Megatrends'` → `Investment_Focus='Thematic'`, `Sector_Focus=NULL` | WARN→auto | Yes | INTER-15 |
| SC-B2 | `Theme='Inflation'` → `Investment_Focus='Thematic'`, `Sector_Focus=NULL` | WARN→auto | Yes | INTER-15 |
| SC-B3 | `Investment_Focus='Thematic'` + `Theme='Core/General'` → `Investment_Focus='Broad'` | WARN | Yes | INTER-9 |
| SC-B4 | `Investment_Focus='Sector'` → `Sector_Focus` IS NOT NULL | WARN | No — requires knowing which sector | — |
| SC-B5 | `Theme` ∈ `_THEMATIC_ONLY_THEMES` → `Sector_Focus=NULL` | WARN→auto | Yes | INTER-15 |
| SC-B6 | `Theme` → `Sector_Focus` must match `THEME_SECTOR_MAPPING` | WARN→auto | Yes — apply mapping table | INTER-9 (upgraded) |
| SC-C1 | `Family='Thematic Equity'` + `Investment_Focus='Broad'` → WARN | WARN | No (classifier fix needed) | — |
| SC-C2 | `Family='Equity Core'` + `Investment_Focus='Thematic'` → WARN | WARN | No (classifier fix needed) | — |

### Cluster C — Bond-specific attributes (asset-class applicability)

| ID | Rule | Severity | Auto-correctable? | INTER rule |
|----|------|----------|-------------------|-----------|
| SC-D1 | `Fund_Nature` ∈ `{RV, Alternativo, Estructurado}` + `Credit_Quality` ≠ `'Not Applicable'` → correct to `'Not Applicable'` | WARN→auto | Yes | INTER-17 |
| SC-D2 | Same natures + `Duration_Profile` ≠ `'Not Applicable'` → correct to `'Not Applicable'` | WARN→auto | Yes | INTER-17 |

### Cluster D — MMF/alternative structure attributes

| ID | Rule | Severity | Auto-correctable? | INTER rule |
|----|------|----------|-------------------|-----------|
| SC-E1 | `Fund_Nature ≠ 'Monetario'` + `MMF_Structure ≠ 'Not Applicable'` → correct to `'Not Applicable'` | WARN→auto | Yes | INTER-18 |
| SC-E2 | `Fund_Nature='Monetario'` + `MMF_Structure='Not Applicable'` → WARN (should have explicit MMFR structure) | WARN | No — needs prospectus | INTER-18 |

### Cluster E — Style / exposure attributes

| ID | Rule | Severity | Auto-correctable? | INTER rule |
|----|------|----------|-------------------|-----------|
| SC-E3 | `Fund_Nature='Restantes'` + `Style_Profile` populated → WARN (stale attribute from prior classification) | WARN | No — may be FORCE_REFRESH candidate | INTER-19 |

### Cluster F — Operational consistency

| ID | Rule | Severity | Auto-correctable? | INTER rule |
|----|------|----------|-------------------|-----------|
| SC-F1 | `Strategy` ∈ `{'Indexado','Pasivo'}` + `Replication_Method='Active'` → correct to `Physical` | WARN→auto | Yes (default to `Physical`) | INTER-1 |
| SC-F2 | `Accumulation_Policy='Accumulation'` + `Distribution_Frequency` IS NOT NULL → null it | WARN→auto | Yes | INTER-2 |
| SC-F3 | `Is_ESG=1` + `SFDR_Article=6` → WARN (missing Art. 8/9 disclosure) | WARN | No — manual disclosure update needed | INTER-8 |
| SC-F4 | `Leverage_Used='Yes'` + `Profile='Conservative'` → WARN (leverage on conservative mandate) | WARN | No — requires prospectus review | INTER-7 |

---

## 5. Current known violations (2026-07-11)

All violations below are the result of legacy classifier behaviour before MODIFY #6 narrowed
`Sector_Focus` to sector-only funds, and before the semantic model was formally defined.

| ISIN count | Fund_Nature | Family | Investment_Focus | Theme | Sector_Focus | Rules violated | Correct state |
|-----------|-------------|--------|-----------------|-------|-------------|---------------|---------------|
| 3 (AXA inflation bonds) | RF Flexible | Inflation-Linked | Thematic | Inflation | Inflation-Linked | SC-B5, SC-B6 | Sector_Focus → NULL |
| 1 (SISF inflation bond) | RF Flexible | Inflation-Linked | Sector | Inflation | Inflation-Linked | SC-B2, SC-B5, SC-B6 | Investment_Focus → Thematic, Sector_Focus → NULL |
| 2 (PIMCO inflation equity) | Renta Variable | Thematic Equity | Sector | Inflation | Inflation-Linked | SC-B2, SC-B6 | Investment_Focus → Thematic, Sector_Focus → NULL |
| 2 (Pictet Megatrend) | Renta Variable | Thematic Equity | Thematic | Megatrends | Multi-Theme | SC-B5 | Sector_Focus → NULL |
| 1 (Pictet Megatrend) | Renta Variable | Thematic Equity | Sector | Megatrends | Multi-Theme | SC-B1, SC-B6 | Investment_Focus → Thematic, Sector_Focus → NULL |
| 3 (Healthcare+Thematic) | Renta Variable | Thematic Equity | Thematic | Healthcare | Healthcare & Life Sciences | SC-B5 | Investigate: may be correct as Sector (see note) |
| 1 (Real Estate+Thematic) | Alternativo | Real Assets | Thematic | Real Estate | Real Assets | SC-B5 | Investigate: may be correct as Sector |

**Note on the Healthcare+Thematic and Real Estate+Thematic cases**: The `Sector_Focus` value
is valid (it IS in DOMAIN_VALUES), so rule SC-B6 is not violated. The question is whether
`Investment_Focus='Thematic'` is correct or whether these should be `'Sector'`. This depends
on the fund's actual mandate. If the fund defines its universe as "all GICS Healthcare companies"
it is Sector. If it defines it as "innovation in healthcare services" (which might include tech
companies) it is Thematic. **This requires classifier-level decision, not an auto-correction.**

---

## 6. Implementation guidance

### For classifiers (`classify_fund` functions in each block)

1. When assigning `Investment_Focus='Sector'`, always simultaneously set `Sector_Focus` using
   the canonical mapping table in §2.3.
2. When assigning `Investment_Focus='Thematic'`, never set `Sector_Focus`.
3. When `Theme` is `'Inflation'` or `'Megatrends'`, always use `Investment_Focus='Thematic'`.
4. When `Family='Thematic Equity'`, never use `Investment_Focus='Broad'`.

### For INTER rules in `validate_all_semantic_consistency` (classify_utils.py)

Auto-correctable rules (SC-B1, SC-B2, SC-B5, SC-B6, SC-C1, SC-C2) should be implemented
as INTER rules that populate `corrected_record`. Non-auto-correctable rules (SC-A1, SC-B3,
SC-B4) should emit a WARN DQ entry without modifying the record.

Priority of implementation: SC-B1, SC-B2 (fix 6 Inflation/Megatrends cases) → SC-B5
(null stale Sector_Focus for Thematic) → SC-B6 (Theme↔Sector_Focus alignment) → SC-C1/C2.

### What NOT to do

Do not add ad-hoc INTER rules for individual fund cases. Every rule must be derived from this
semantic model and apply generically to all funds. A rule that only fires for 1 fund is either
a classifier bug (fix in the classifier) or a wrong-KIID case (fix via FORCE_REFRESH).

---

## 3e. Cluster F — Operational consistency attributes

These attributes govern fund operational characteristics and have pairwise consistency
requirements regardless of `Fund_Nature`. They are validated by INTER-1 through INTER-8 in
`validate_all_semantic_consistency`.

### `Strategy` ↔ `Replication_Method` (SC-F1)

Industry standard (ESMA ETF Guidelines ESMA/2012/832): index-tracking and passive strategies
**must** use a non-Active replication method.

| Strategy | Required Replication_Method |
|----------|-----------------------------|
| `Activo` | `Active` (or NULL) |
| `Indexado` | `Physical`, `Sampling`, or `Synthetic` |
| `Pasivo` | `Physical`, `Sampling`, or `Synthetic` |

If `Strategy='Indexado'` or `'Pasivo'` and `Replication_Method='Active'` → **auto-correct to
`Physical`** (most common) and WARN. Implemented in INTER-1.

**Industry note**: Morningstar distinguishes `Index` vs `Active` by replication, not by name.
An "Indexado" fund claiming `Active` replication is a misclassification.

### `Accumulation_Policy` ↔ `Distribution_Frequency` (SC-F2)

| Accumulation_Policy | Distribution_Frequency |
|---------------------|------------------------|
| `Accumulation` | Must be `NULL` (no distributions) |
| `Distribution` | Must be populated (how often) |
| `Mixed` | May be populated |

If `Accumulation_Policy='Accumulation'` AND `Distribution_Frequency` is NOT NULL
→ auto-correct `Distribution_Frequency` to `NULL`. Implemented in INTER-2.

**Industry note**: UCITS prospectus disclosures distinguish share classes by accumulation
(no dividend) vs distributing (periodic payments). The two are mutually exclusive.

### `Is_ESG` ↔ `SFDR_Article` (SC-F3)

| Is_ESG | SFDR_Article semantics | Expected |
|--------|------------------------|----------|
| `1` (True) | Fund claims sustainability characteristics | Art. 8 or Art. 9 |
| `0` (False) | No sustainability claim | Art. 6 or NULL |

If `Is_ESG=1` AND `SFDR_Article=6` → WARN only (fund classifies itself as ESG but has no SFDR
Art. 8/9 declaration; may be a disclosure gap, not necessarily a classification error).

**Industry note**: Under EU SFDR (2019/2088), Art. 6 is the default "no claim" category;
Art. 8 funds "promote" ESG characteristics; Art. 9 funds have a "sustainable investment
objective." `Is_ESG=1` on an Art. 6 fund indicates missing regulatory disclosure. Implemented
as WARN in INTER-8 (no auto-correction — the issue may be on either side).

### `Leverage_Used` ↔ `Profile` (SC-F4)

| Leverage_Used | Profile | Assessment |
|---------------|---------|------------|
| `Yes` | `Conservative` | WARN — unusual (leverage increases risk; contradicts conservative mandate) |
| `Yes` | `Moderate`, `Aggressive` | OK — expected for these profiles |
| `No` / `Limited` | `Conservative` | OK — expected |

**Industry note**: ESMA risk management guidelines (CESR/10-788) specifically identify
leverage as a source of incremental risk. A Conservative fund (SRRI 1-4) using leverage
should be flagged for manual review; the fund may be misclassified or may use leverage
exclusively for hedging (which doesn't necessarily increase net risk). WARN-only in INTER-7.

---

## 3f. Cross-cutting: Linguistic homogeneity (Principio #8)

Each attribute column uses a single language for all its categorical values. Mixing languages
in a column breaks GROUP BY aggregations and WHERE filters, fragmenting populations.

| Language | Columns |
|----------|---------|
| **Spanish** | `Fund_Nature`, `Profile`, `Geography` |
| **English** | `Family`, `Investment_Focus`, `Theme`, `Sector_Focus`, `Market_Cap_Focus`, `Style_Profile`, `Exposure_Bias`, `Development_Status`, `Credit_Quality`, `Duration_Profile`, `MMF_Structure`, `Alt_Strategy`, `Hedging_Policy`, `Replication_Method`, `Derivatives_Usage`, `Liquidity_Profile`, `Distribution_Frequency`, `SFDR_Article` |
| **Neutral / code** | `ISIN`, `Fund_Currency`, `Asset_Currency`, `SRRI`, numeric costs |

**Note on evolution**: Before v20 (2026), `Family` values were in Spanish ('RV Núcleo',
'Renta Fija Flexible', 'Retorno Absoluto', etc.). The migration to English ('Equity Core',
'Fixed Income Flexible', 'Absolute Return') happened with schema v20 to align with
Morningstar Category naming. Any Spanish family values in the DB are legacy and should be
migrated by `sqlite_writer._normalize_record` (defense-in-depth, per R-1).

Normalization maps for legacy Spanish→English translations live exclusively in
`classify_utils.py` (R-1). The defense-in-depth copy in `sqlite_writer._normalize_record`
is the only authorized duplicate.

---

## 3g. Cross-cutting: Sentinel vs NULL (Principio #10)

For attributes where *"intentionally diverse/multi-valued"* is semantically distinct from
*"not yet determined"*, a categorical **sentinel value** is required instead of `NULL`.

Current sentinels:

| Attribute | Sentinel | Meaning |
|-----------|---------|---------|
| `Asset_Currency` | `MCY` | Fund explicitly mandates multi-currency assets |
| `Geography` | `Global` | Fund explicitly covers multiple geographies |
| `Investment_Focus` | `Broad` | Fund covers broad universe (not sector or thematic) |

Collapsing diverse-by-design and unknown into the same `NULL` destroys information and breaks
COALESCE (Principio #1): a correct sentinel is non-NULL and will correctly overwrite a stale
single-value from a prior cycle, while `None` would be preserved by COALESCE.

Full rule: **Principio #10** (`doc/reglas/PRINCIPIO_10_INDETERMINADO_VS_NULL.md`).

---

## Appendix — Industry alignment

This appendix maps each attribute cluster to the external regulatory or industry framework it
is derived from. Future attribute additions should be grounded in one of these frameworks.

### A. Risk/profile attributes

| Attribute | Framework | Reference |
|-----------|-----------|-----------|
| `SRRI` | UCITS KIID Regulation (EU 583/2010), Annex I, Part 1 | 7-bucket standard deviation scale; mandatory disclosure |
| `Profile` | CNMV / MiFID II suitability profiling | Maps `Conservative` → SRRI 1-2, `Moderate` → 3-4, `Growth` → 5-6, `Aggressive` → 7 |
| `SFDR_Article` | EU SFDR Regulation (EU 2019/2088) | Art. 6 = no sustainability claim, Art. 8 = ESG characteristics, Art. 9 = sustainable investment objective |

**SRRI ↔ Profile mapping** (implemented in INTER-3):

| SRRI | Mapped Profile |
|------|---------------|
| 1–2 | Conservative |
| 3–4 | Moderate |
| 5–6 | Growth |
| 7 | Aggressive |

### B. Classification attributes

| Attribute | Framework | Reference |
|-----------|-----------|-----------|
| `Fund_Nature` | Morningstar Category + CNMV fund type taxonomy | Top-level asset class bucket; maps directly to CNMV `Tipo de Fondo` |
| `Family` | Morningstar Category (Level 2) | ~30 sub-categories within each nature |
| `Investment_Focus` | Morningstar Equity Category split: "Sector Equity" vs "Thematic Equity" | `Sector` = GICS industry sector; `Thematic` = cross-sector macro theme |
| `Sector_Focus` | GICS (Global Industry Classification Standard), 11 sectors consolidated to 8 | Defined as GICS Level-1 consolidation (see §2.3) |
| `Theme` | MSCI Thematic Indexes + Morningstar Thematic taxonomy | Maps to cross-sector macro trends (AI, Climate, Ageing, etc.) |
| `Development_Status` | MSCI Market Classification framework (Annual Market Classification Review) | Developed / Emerging / Frontier; annually reviewed |
| `Style_Profile` | Morningstar Style Box (3×3: Value/Blend/Growth × Small/Mid/Large) + MSCI Factor Indexes | Applied at cap level here; cap-neutral |
| `Market_Cap_Focus` | Morningstar Style Box size axis: Large > $10B, Mid $2-10B, Small < $2B | Standard institutional segmentation |

### C. Fixed-income attributes

| Attribute | Framework | Reference |
|-----------|-----------|-----------|
| `Credit_Quality` | S&P/Moody's/Fitch rating tiers; UCITS eligible assets directive | `Investment Grade` ≥ BBB−/Baa3; `High Yield` < BBB−; `Mixed` = blend |
| `Duration_Profile` | ICE BofA / Bloomberg bond index duration brackets | `Ultra-Short` < 1y; `Short` 1-3y; `Intermediate` 3-7y; `Long` > 7y; `Flexible` = unconstrained |

### D. MMF regulatory structure

| Value | Regulation | Legal constraint |
|-------|-----------|-----------------|
| `CNAV` | MMFR Art. 29 | Permitted only for public-debt/government CNAV MMF; redemptions at par |
| `LVNAV` | MMFR Art. 30 | Short-term MMF only; NAV deviation ≤ 0.20% (collar) |
| `VNAV` | MMFR Art. 31 | All short-term MMF not qualifying as CNAV/LVNAV; also Standard MMF |
| `Standard MMF` | MMFR Art. 2(14) | WAM 6 months, WAL 12 months; no CNAV/LVNAV permitted |

### E. ESG/Sustainability attributes

| Attribute | Framework | Reference |
|-----------|-----------|-----------|
| `SFDR_Article` | EU SFDR (Regulation 2019/2088), amended by RTS March 2023 | Art. 6/8/9 fund-level disclosure |
| `Is_ESG` | Derived from SFDR_Article (Art. 8 or Art. 9 → True) | Not a regulatory term; convenience flag |

### F. Derivatives / leverage

| Attribute | Framework | Reference |
|-----------|-----------|-----------|
| `Derivatives_Usage` | UCITS Directive (2009/65/EC) Art. 50; ESMA Guidelines on ETFs / risk measurement | `None` = no derivatives; `Hedging Only` = FX/rate hedge only; `Investment` = alpha-generating; `Both` = both uses |
| `Leverage_Used` | UCITS KIID leverage note; AIFMD (for AIFs) Art. 25 | Binary: `Yes` / `No` |

### G. Operational attributes

| Attribute | Framework | Reference |
|-----------|-----------|-----------|
| `Liquidity_Profile` | UCITS Directive + ESMA Liquidity Guidelines (ESMA/2020/1444) | `Daily` = standard UCITS redemption; `Weekly`/`Bi-Weekly` = less-liquid UCITS; `Monthly` = closed-end/illiquid |
| `Distribution_Frequency` | Fund prospectus + ESMA disclosure rules | Accumulating (retained) vs distributing (paid out) |
| `Replication_Method` | ESMA ETF Guidelines (ESMA/2012/832) | `Physical` = buys all index constituents; `Sampling` = holds subset; `Synthetic` = uses swaps; `Not Applicable` = non-index |
