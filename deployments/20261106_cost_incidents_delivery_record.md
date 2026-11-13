# Delivery record: the cost-attribute audit incidents FND-0276, FND-0278, FND-0279, FND-0280, FND-0283, FND-0284, FND-0285

Written 2026-11-06 for the backlog record. The code was delivered in the commits listed below; this record is the delivery evidence that closes the seven tickets. Every incident is `CERTIFIED` in `control.audit_incident`, which is the closure gate of NORMAS_BACKLOG (FND-0264): `audit_incident.py --close-check` says CLOSABLE for all seven.

## What changed in the data (four unattended recomputes, `scripts/launch/p1_cost_recompute.py`)

| Run | Result |
|---|---|
| `post_20261105_183945_costs` | 2,947 funds, 0 errors; FND-0285 certified |
| `post_20261105_200845_costs` | violators of the five cost rules 37 -> 17 funds |
| `post_20261106_174913_costs` | FND-0278, FND-0283, FND-0284 certified |
| `post_20261106_191402_costs` | FND-0276 certified; 49 M&G orphans removed |
| `post_20261106_210000_costs` | the cost audit has 0 ALARMs; FND-0279 and FND-0280 certified |

Funds with a schedule row whose total does not fit its own annual impact: 50 at the start of the work, 6 at the end (all accepted, below).

## Code delivered (each with its full local test receipt)

`477e200` the coherence band (detect-only since `72d3897`); `72d3897` AMOUNT_PATTERN (1198 read as 119), "Gastos totales", the 1-year + RHP binder, impossible totals, base phrases; `4eedf12` a minimum subscription is not the example investment; `9f1e3df` the "n.a." layout; `5a752aa` page-break table, 2,500-character look-back, rising annual bound by coherence; `d5c48ab` label-less value lines validated by coherence; `daa03ec` DLA2 late table, annual wording, split label; `3e1b39d` a percentage after another cost row's name is not adjacent to the ACI label (DWS); `c0daaf8` the recommended period "es de N anos" (M&G); `f32c0b8` standing acceptances and the text-vouched tolerance of FND-0276.

## Owner decisions of 2026-11-06 and how they were executed

- **Publisher defects are accepted, not patched.** The M&G funds LU1665237613, LU1665237704, LU1665237886, LU1665237969 (the printed "Incidencia sobre el rendimiento" row contradicts the KID's own totals and footnote) and ES0143559005, ES0143559021 (management fee 3,27 % printed above a 3,1 % annual impact) are accepted as `PUBLISHER_DEFECT`. IE00BZ199J22 and LU2278574988 (each a layout that exists in one fund) are accepted as `EXTRACTION_LIMIT`; the extractor is deliberately not patched for them.
- **No 90-day clock.** These acceptances are *standing*: no calendar expiry, bound to the fingerprint of each fund's stored KID text and re-derived on every read (`accepted_residuals.load_accepted`). While the KID is unchanged they stand and are never renewed; a new KID from the publisher makes them lapse by themselves and the findings return as ALARMs. Events #9-#16 in `control.audit_exception_event`, recorded as "owner decision 2026-11-06, executed by agent:claude-sonnet-5-5".
- **FND-0276 is a tolerance, not a code fix.** `ACI_AMORTISATION_ORDER` applies only where the stored KID text does not print the stored pair; 73 of 73 funds it flagged print theirs (checked by `shared/statistical_audit/text_evidence.py`, independent of the extractor).

## Stated limits

- The schedule rows of the four M&G funds are not faithful to the document: their 1-year annual is an entry-fee fragment (1.25), not the printed 4,44 %; the fund-level `ACI_1Y` / `ACI_RHP` (4.44 / 8.91) are what the KID prints. The KID itself is inconsistent, which is why they are `PUBLISHER_DEFECT`.
- `ACI_AMORTISATION_ORDER` no longer reports rising pairs the document prints. That is real KID data (no entry fee to amortise), not an extraction defect.
- FND-0384 stays open: it is the record of the residual funds above.
- FND-0281 and FND-0282 are unchanged (ACCEPTED until 2027-01-15); their residual (25 and 40 funds) is a different set and a different decision.

## Addendum 2026-11-13 (package B, owner order): `ACI_MUST_AMORTISE` unit correction (FND-0276 follow-up)

- **Defect.** `ACI_MUST_AMORTISE` (FND-0276 / 0277, 2026-10-17) compared `Entry_Fee_Pct * (1 - 1 / Cost_RHP_Years)` with `ACI_AMORTISATION_VISIBLE_PP` (percentage points). `Entry_Fee_Pct` is a RATIO (0.05 = 5 %; `catalog_cost_columns`: kind "ratio"; 1,307 of 1,315 funds with both columns equal `Entry_Fee_Pct_Max` / 100). A 5 % charge therefore counted as 0.04 pp and the rule could never fire: 0 violators among the 575 funds with an equal ACI pair and a holding period over one year. The FND-0276 sweep that concluded "the 97 violators have an entry fee of 0.0525 % or less" read ratios as percent.
- **Fix.** The rule scales the ratio (`Entry_Fee_Pct * 100`). Corrected alone it flagged 3 funds (LU1139789298, LU1251922891, LU1770914999): their KIDs print "Annual cost impact (%) 1.7% 1.7%" at 1 and 5 years and state that the 5 % entry charge is paid to the distributor and is not part of the costs shown, so the equal pair is faithful. Rule and false positives are fixed together: an equal pair the KID prints is tolerated (`ACI_Equal_Pair_Printed`, `run_statistical_audit._mark_equal_pairs_printed`, `text_evidence.aci_pair_printed`, independent of the extractor), the same evidence as `ACI_AMORTISATION_ORDER`.
- **Result on the stored data after the 2026-11-13 recompute:** 0 violators. A copied pair with a stored charge and no printed pair is still an ALARM (unit test with real numbers).
- **Files.** `shared/statistical_audit/catalog_invariants.py`, `scripts/audit/run_statistical_audit.py`, `doc/reglas/AUDITORIA_ESTADISTICA.md`; tests `proyecto1/tests/test_statistical_audit_aci_grid.py` (cases converted to ratios), `proyecto1/tests/test_statistical_audit_criticality.py`, `tests/test_aci_components_rule_20261113.py`.
