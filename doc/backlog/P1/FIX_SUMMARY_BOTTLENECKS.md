# Fix Summary — Bottleneck Remediation (P1)

**Date:** 2026-06-28  
**File:** `proyecto1/core/cost_table_parser.py`

---

## Bottlenecks Analyzed

| # | Bottleneck | Affected Funds | Status |
|---|---|---|---|
| 1 | `ACI_RHP` missing after regrid R1/R2 | 27 | Partial (11/27 fixed, 16 pending diag) |
| 2 | `swap_mgmt_oper` stored wrong vs. truth | 15 | Partial (5/15 fixed, 10 pending diag) |
| 3 | `oper_bleed` regrid oper wrong (R3) | 5 | **Complete (5/5 fixed)** |

---

## Bottleneck 1 — `ACI_RHP` missing (27 funds)

### ACI Sub-group A — 4 funds `[FIXED 4/4]`

**ISINs:** FR001400RZ04, LU0433182689, LU0565136552, LU1095740749

**Root Cause (LU funds):** `"Incidencia anual de los costes**"` split across **4 DLA2 cells** (`|||`-separated); `_match_with_join` only tried k∈{2,3} — k=4 required to join all fragments.

**Root Cause (FR001400RZ04):** Continuation row had `'1'` and `'año'` in separate cells; word fragments (`'En'`, `'caso'`, `'de'`, `'salida'`) inflated `n_cols`, breaking compact-value pairing.

**Fixes applied:**

| Fix | Description |
|---|---|
| **FIX-P1-Z (a)** | Extend `_match_with_join` from k∈{2,3} → k∈{2,3,4} |
| **FIX-P1-Z (b)** | FIX-P1-C extension: join adjacent continuation cells when year keyword is split (e.g. `'1'`+`'año'`) |
| **FIX-P1-Z (c)** | FIX-P1-U extension: drop short fragments (len<7, no digit/year/RHP content) that inflate `n_cols` |

---

### ACI Sub-group B — 5 funds `[NON-ACTIONABLE]`

**ISINs:** LU0438092883, LU0446997610, LU0579408591, LU1232087814, LU1339879162

**Root Cause:** No OT cost table present in KID text — structurally absent from these KIDs (SSGA/Alger funds). Cannot extract what does not exist.

**Fixes applied:** None.

---

### ACI Sub-group C — 2 funds `[FIXED 2/2]`

**ISINs:** LU2066956926, LU2066957221

**Root Cause (a):** `"últimos 12 años"` boilerplate in scenarios section matched HORIZON_CONTEXT → produced spurious `hy=12.0` entry stored as ACI_RHP.

**Root Cause (b):** `"Incidencia anual de los costes"` table lies beyond the 1500-char OT window. A scenario-return percentage (-47.5%) was extracted as `aci_pct` for the `hy=1.0` entry, making `all(aci_pct is None)` False → FIX-P1-E global fallback did not fire. RHP entry (`hy=-1.0, is_rhp=True`) had `aci_pct=None`.

**Fixes applied:**

| Fix | Description |
|---|---|
| **FIX-P1-AA** | In HORIZON_CONTEXT loop: reject year matches preceded by `"últimos/last N"` — historical boilerplate, not holding-period labels |
| **FIX-P1-AB** | Trigger FIX-P1-E also when RHP entry exists with `aci_pct=None`; patch RHP entry in-place (do not replace all results); extend ACI anchor search to full document text when anchor is beyond OT window |

---

### ACI Remaining — 16 funds `[PENDING]`

Not yet analyzed. Require full corpus diagnostic run to assess root causes.

---

## Bottleneck 2 — `swap_mgmt_oper` wrong (15 funds)

### Group C — 5 funds `[FIXED 5/5]`

**ISINs:** FR0000989626, FR0013296332, LU0099730524, LU0787086031, LU0611475780

**Root Cause:** FIX-P2-SWAP (EUR-derived `mgmt%` override) firing as **false positive**. When `mgmt ≈ oper` by rounding noise, `abs_tol=0.001` was too permissive — incorrectly replaced correct `mgmt%` with EUR-derived value.

**Fix applied:**

| Fix | Description |
|---|---|
| **FIX-P1-X** | Tighten `abs_tol` 0.001 → 0.00005 in bleed-signature equality guard inside FIX-P2-SWAP |

### Remaining — 10 funds `[PENDING]`

Not yet analyzed. Require full corpus diagnostic run.

---

## Bottleneck 3 — `oper_bleed` wrong (5 funds, R3)

### Group B — 5 funds `[FIXED 5/5]`

**ISINs:** FR0010016477, FR0010760694, FR0012088771, LU0151324422, LU0151324935

**Root Cause:** Grid `oper%` contaminated by swap bleed (mgmt value copied into oper slot). EUR amount in KID was correct. `parse_investment_base` returned `None` for `"Ejemplo de inversión:  3 años 10 000 EUR"` because `"3 años"` appeared between the label and the base number, blocking the base-detection regex.

**Fix applied:**

| Fix | Description |
|---|---|
| **FIX-P1-Y** | EUR-derived `transaction_cost_pct` override: if EUR/base > grid% by >20% relative, replace grid value with EUR-implied % |
| **FIX-P1-Y (base)** | Extend `_BASE_PATTERNS[1]` with `(?:\d+\s*a[ñn]os?\s+)?` to skip intervening RHP duration before base amount |

---

## Fix Registry

| Fix ID | Location | Purpose |
|---|---|---|
| FIX-P1-X | `cost_table_parser.py` | `abs_tol` 0.001 → 0.00005 in FIX-P2-SWAP bleed guard |
| FIX-P1-Y | `cost_table_parser.py` | EUR-derived `transaction_cost_pct` override + `_BASE_PATTERNS[1]` extension |
| FIX-P1-Z | `cost_table_parser.py` | `_match_with_join` k→4; FIX-P1-C continuation cell joining; FIX-P1-U short-fragment filter |
| FIX-P1-AA | `cost_table_parser.py` | Reject `"últimos/last N años"` patterns as year labels in HORIZON_CONTEXT loop |
| FIX-P1-AB | `cost_table_parser.py` | Patch RHP-entry `aci_pct` from global anchor search when ACI table is beyond OT window |
| FIX-P1-E ext | `cost_table_parser.py` | Single-col `is_rhp=True`; backward 300-char pct search with ACI_ROW guard |

---

## Scorecard

| Bottleneck | Total | Fixed | Non-actionable | Pending |
|---|---|---|---|---|
| ACI_RHP missing | 27 | 6 | 5 | 16 |
| swap_mgmt_oper | 15 | 5 | 0 | 10 |
| oper_bleed (R3) | 5 | 5 | 0 | 0 |
| **Total** | **47** | **16** | **5** | **26** |

> **Next step:** Run full corpus diagnostic (`diag_cost_extraction.py`) to measure corpus-wide impact and identify root causes for the 26 remaining funds.
