# P3 turning-point diagnostic (FND-0435): what the score did at the 2008 top and bottom

Written 2026-11-21. READ-ONLY analysis of the sealed PIT run `20261119_202509` (9 artifact hashes verified), `scripts/audit/p3/p3_crisis_diagnostic.py` with `--d1d2`, about 3 minutes, report folder `out/reports/p3_crisis_diagnostic/<stamp>/`. Design on 2008 (top: 2007-11..2008-07; bottom: 2008-09..2009-07, i.e. the design dates +-1 month), 2020 descriptive only; every other stress episode stayed LOCKED (`PREREG_P3_5A.md` is not in git HEAD). Definitions are frozen in `proyecto3/src/pit_crisis_decompose.py`. Forward returns are HINDSIGHT. One episode, in-sample: these are hypotheses for P3-5a, not findings of record (R7, R9).

## Method check (D0)
- The IPC series (the one input the seal lacks) was read read-only from the database and validated: the rescored `score_base` equals the sealed one on every row found again (max difference 0.000000, 96.9 % of the sealed rows found; the rest are family representatives that differ because the rescore has no daily NAV, hence no short gates). No D1 / D2 number is printed unless that check passes.
- Reproduced: static-mix ceiling under max drawdown 15 % = 4.14 % (7.75 % with 2007-11..2009-03 removed, 6.34 % with its losses halved).
- Corrected: an earlier D0 table (committed 9ec7db9) inferred eligibility from the reason text and counted `Credit_Quality=...` exclusions as eligible; now read from the `eligible` column and tested. Corrected values: Renta Fija Flexible eligibility 73 % (2007-06) -> 35 % (2008-10-31), Corto Plazo 64 % -> 46 %; the scratch claims 75 % -> 35 % / 47 % reproduce at the END values, not at the start values.
- NOT reproduced (stay scratch-derived): "trailing 3-year +107 % against +34 %" (the recomputed selected-vs-pool median nominal 36-month returns are below), and "Defensiva in Monetario 35 % against 11 %" (recomputed: Monetario is 33 % of the master weight at the top dates and 24 % at the bottom dates).

## D1, the top (stop rule met: metric and lookback named)
Selected funds against the pool median, mean over the 2007-11..2008-07 dates. Every metric looks back over the whole history since inception.
| Sub-portfolio | Carrying metrics (share of the score gap) | Trailing 36 m, selected vs pool | Next 12 m (hindsight), selected vs pool | Worst drawdown next 12 m |
|---|---|---|---|---|
| Defensiva | sharpe 32 %, alpha_persistence 27 %, return_ann_real 23 %; the nature bonus lifts the mean score 0.78 -> 0.90 | +9.4 % vs +4.4 % | +2.3 % vs +0.3 % | -0.8 % vs -3.6 % |
| Equilibrada | alpha_persistence 33 %, return_ann_real 23 %, capture 17 % | +39.3 % vs +9.9 % | -30.4 % vs -12.1 % | -36.5 % vs -16.3 % |
| Dinamica | alpha_persistence 48 %, capture 32 % | +47.1 % vs +30.0 % | -28.8 % vs -29.7 % | -35.7 % vs -36.3 % |
Reading: the loss of the top sits in Equilibrada, which bought the funds that had run hottest (alpha_persistence and since-inception real return) and fell 18 pp more than its pool; Dinamica chased winners too but fell like its pool; Defensiva was fine. Hindsight and in-sample.

## D2, the bottom, all three sub-portfolios
- Equilibrada and Dinamica: at 2008-10-31 only 3 of 486 Renta Variable funds were eligible (1 %): 95 % fail the since-inception max-drawdown filter independently and 80 % the real-return filter (limits -30 % / -2 % in Equilibrada, -40 % / 0 % in Dinamica); only 24 % of Mixtos pass the metric filters. Eligibility stayed at 1 % through 2009-03. Equilibrada was left with Mixtos 5 + Renta Fija Flexible 5, Dinamica with 5 Renta Variable of its 6 eligible.
- Defensiva: Monetario eligibility stayed at 84 % while Renta Fija Flexible fell to 35 % (55 % pass the metric filters, 15 % stop at a short gate, the rest at the since-inception return and drawdown filters).
- Did the since-inception drawdown filter hold back the 2009 recomposition? Yes for the master portfolio: with that one filter off, the high-beta share is 15.5 % instead of 6.6 % and the hindsight 12-month return 11.0 % instead of 6.7 % (+4.3 pp); Equilibrada's recomposition was held back too (its Renta Variable pass rate is 1 % at the bottom).

## Frozen counterfactuals (master portfolio, mean over the dates of each set; hindsight forward returns)
| Variant | Top: high-beta / Monetario / next-12m change | Bottom: high-beta / Monetario / credit eligibility / next-12m change |
|---|---|---|
| baseline | 17.1 % / 32.9 % / - | 6.6 % / 23.7 % / 47.8 % / - |
| nature bonus 1.0 | 16.8 % / 3.8 % / -0.8 pp | 4.8 % / 1.2 % / 47.8 % / +1.7 pp |
| max_dd weight 0 | 15.7 % / 28.9 % / -3.6 pp | 4.0 % / 27.5 % / 47.8 % / +5.4 pp |
| alpha_persistence weight 0 | 17.1 % / 32.9 % / -0.2 pp | 10.8 % / 32.0 % / 47.8 % / -1.5 pp |
| rolling 36 m metrics | 17.1 % / 32.9 % / -1.7 pp | 6.2 % / 27.7 % / 56.1 % / -1.0 pp |
| rolling 60 m metrics | 17.1 % / 32.9 % / -1.5 pp | 5.9 % / 34.5 % / 59.9 % / -0.7 pp |
| drawdown half-life 36 m | 17.1 % / 31.9 % / -1.2 pp | 8.0 % / 24.4 % / 49.1 % / -0.3 pp |
| short gates off | 17.1 % / 32.9 % / -1.1 pp | 9.4 % / 23.7 % / 59.3 % / +0.3 pp |
| since-inception drawdown filter off | 17.1 % / 32.9 % / 0.0 pp | 15.5 % / 23.7 % / 59.8 % / +4.3 pp |
What it says: (1) the Monetario weight is almost entirely the nature bonus (x1.25), not the filters, at both ends; (2) the max-drawdown WEIGHT protects at the top (-3.6 pp without it) and costs at the bottom (+5.4 pp without it): a symmetric trade-off, no free lever; (3) rolling windows and the half-life do not help at the top and barely move the bottom; (4) the one filter that clearly blocks the recomposition is the since-inception drawdown filter. Limits: the short gates are known only from the sealed first reason; a fund without a full rolling window has NaN metrics; one episode.

## D3 hindsight bound
The three ceilings of D0 (4.14 % / 7.75 % / 6.34 %) are the bound. Only perfect avoidance of 2007-11..2009-03 (7.75 %) clears the target's 6.94 % average; halving that episode's losses (6.34 %) does not. So curbing turning-point losses can raise the ceiling, but reaching the target needs near-perfect avoidance of one episode, in hindsight.

## D5 persistence anomaly (R1 persistent random against R2 redraw)
R1 earns 1.16 pp a year less gross than R2 (2.47 % against 3.63 %; the benchmark report says 1.25 pp). Not the cash treatment of missing returns: the covered weight is the same (99.6 %) and the ex-cash gap is identical. Not forced exits: R1 changes only 1.6 % of its positions a month. It is an AGE effect: 173 of R1's 261 weight-months sit in holdings older than 36 months, whose 1-month excess over the eligible pool is -0.24 %, and holdings aged 4-12 months -0.28 %, against about 0 for 1-3 and 13-36 months. Hypothesis (not proven): a fund that stays eligible for years has passed the since-inception drawdown and return filters for years, i.e. it is a past-performance survivor, and past performance does not persist; the same mechanism as the top. Consequence for P3-2': a hysteresis that keeps holdings longer inherits this drag, so band and cadence must be judged on net return, not only on turnover. Limit: excess is against the master-level eligible pool, not sub-portfolio specific.

## Integrity: the evidence has no deaths (new finding)
Among the 3,736 funds of the sealed NAV panel, none has its last NAV before 2022 (7 end in 2022, 3 in 2025, the other 3,726 run to 2026); the 811 funds flagged `In_Current_Universe = 0` almost all keep a live NAV into 2026 (801), with no flat tail. So the mortality and truncation rate at the 2008 dates is 0 and every selected fund has a complete 12-month forward path: the PIT universe holds only funds that are alive today. The "retired funds included" statement covers funds P1 stopped classifying, not funds that died. The survivorship adjustment of the FND-0429 plan (10 % attrition) was an assumption; this measures the premise it rests on. Every PIT number (champion, pool, R1, R2, ceilings) is conditional on survival. FND-0434 (mirrored NAV, LOW) is a different question (NAV copied between classes) and is not measured here.

## What the owner may want to decide
1. Whether the survivorship finding changes the priority of P3-5a (it raises the bar for every historical claim, 2008 most of all, because 2008 killed funds).
2. Row 12 (adoption objective) before `PREREG_P3_5A.md`; the levers this diagnostic supports by mechanism are listed in section 4 of `doc/backlog/PKG_L_p3_alignment.md`: the nature bonus (Monetario weight), the since-inception drawdown filter (rolling or decayed variant), and a performance-chasing control (alpha_persistence / since-inception return weight in Equilibrada).
