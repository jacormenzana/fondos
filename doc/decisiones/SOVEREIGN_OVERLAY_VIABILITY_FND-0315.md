# SOVEREIGN_OVERLAY_VIABILITY_FND-0315 — the shadow overlay failed its pre-declared viability gate

**Status: FAILED HYPOTHESIS (2026-10-25).** The sovereign-stress *monitor* (FND-0294: rules R1–R5, `yield_10y_gov`, the cycle-report line) stays and is unaffected.
The *overlay* built on top of it (a confirmed trigger T2 that would tilt the portfolio defensively) is **not built**: no module, loader, ledger or tag exists.

## What was tested
A fail-fast prototype, run once, read-only, with every definition and criterion frozen in the backlog log of FND-0315 (log 1407) **before** the script existed.
A trigger T2 fired when a stress *event* (R2 periphery spread widening, or a 3-month 10y rise ≥ 0.75pp in DE/US/GB/JP) was *confirmed* in the same or the next
month by an OR of market legs per epicentre (EUR/CHF, GBP/USD, USD/JPY, broad-dollar realised-vol spikes above the own 5-year 90th percentile; CHF +2%; gold +5% in
the epicentre currency; Bund −0.25pp), held for 3 months. Sample: 2005-01..2026-09 (261 months). Gold: World Bank Pink Sheet (the IMF PGOLD series could not be
verified from this machine). Daily FX from `series_macro.fx_d_eur`; broad dollar `DTWEXBGS` daily from FRED.

## Pre-declared criteria
- (g) per epicentre, the confirmation fires in ≤ 15% of the months where it is defined, otherwise the epicentre cannot trigger T2;
- (a) T2 ON in ≥ 2 of the 6 labelled episodes; (e) T2 ON outside every episode window (±3 months) ≤ 5% of the 261 months.
Pass = at least one epicentre survives (g) **and** (a) **and** (e).

## Result
| Epicentre | Confirmation union (defined months) | Sum of leg rates | Outcome |
|---|---|---|---|
| PERIPHERY | 32.2% (84/261) | 49.4% | fails (g) |
| DE (EURO-CORE) | 31.4% (82/261) | 44.1% | fails (g) |
| US | 22.6% (59/261) | 26.1% | fails (g) |
| GB | 25.3% (66/261) | 28.0% | fails (g) |
| JP | 26.8% (70/261) | 28.7% | fails (g) |

No epicentre survives, so T2 does not exist. For information only (all epicentres, ignoring (g)):

| Variant | Episodes detected | False-ON months |
|---|---|---|
| T2 (event AND confirmation) | 5/6 | 20 (7.7%) |
| T1 (event only) | 6/6 | 27 (10.3%) |
| T2z (R5z in place of R5) | 5/6 | 36 (13.8%) |

Even without (g), T2 fails (e) (7.7% > 5%). Lift of the confirmation: PERIPHERY confirmed 16/24 event months inside episode windows and 4/5 outside; US 4/8 and 2/4;
GB 3/7 and 0/1; DE 1/3 and 2/2; JP no events with a defined confirmation. The legs fire on 5–17% of months each *by construction* (percentile and round-number
thresholds), so their OR is a sieve, not a filter; this is what the reviewers' arithmetic predicted and what the (g) criterion was added to catch.

## What this does and does not show
- It shows that **this** pre-declared confirmation design does not separate sovereign stress from ordinary months on the euro-era sample. Detection is not the problem
  (T1 sees 6/6 episodes); false alarms are.
- It does **not** show that no overlay can work, and it does not weaken the monitor (R1–R5 as a reported, never acting, warning).
- Any redesign (fewer or rarer legs, other shapes) would be a *new* hypothesis with a new pre-registration. Testing variants on these same 261 months is in-sample; the
  honest test is the forward window from 2026-11 (FND-0299 blind months). Candidates are parked in FND-0318.

## Consequences (backlog)
FND-0315 (Part 1), FND-0316 (Part 2), FND-0319 (government-fund attributes, needed only by Part 2) and FND-0321 (sunset: the inventory is empty) are closed as
REJECTED. FND-0317 (monitor v2), FND-0318 (research) and FND-0320 (`series_macro.gold` is a metals PPI) stay open: they do not depend on the overlay.

## Reproduce
Archived outside git in `out/audit/P2/sovereign_overlay_viability_20261025/` (git-ignored): `sovereign_overlay_viability.py`
(sha256 `733e372ff158959749637aa343865433e5017edd9030a1de4df20d7f4534b104`), `probe_sources.py` (`600258dbec1802c1464a721e053674dc08eb26150a06ae2b31fe7a334c8c07b0`),
`viability_result.json` (`d4e44f6132701668c57b714a61724632857a83c0cea5aefe18ec6aeed6888b4c`). Inputs: `series_macro` as of 2026-10-25, FRED `DTWEXBGS`, the World Bank
Pink Sheet monthly file. Frozen definitions: `gestion.backlog` log 1407 on FND-0315.
