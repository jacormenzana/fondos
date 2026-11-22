# PREREG P3-8: the CPI input of the regime classifier (average or maximum of Spain and the euro area; publication lag 2 or 1 month)

**Status: PREREGISTERED 2026-11-22, committed before any code or number of this study.** Ticket FND-0439. Owner approval of 2026-11-22 with three rules: (1) verify the INE and Eurostat release calendars before using a 1-month lag; (2) the `IPC + M3` target (FND-0303) stays decoupled: only the regime classifier's input is evaluated; (3) this brief preregistration first, judged on maximum drawdown and net return, and **stop for the owner's review before anything changes in production**. Nothing in production is touched by this study.

## 1. Question and design
Today the classifier reads `ipc_yoy_avg` = the mean of Spanish and euro-area year-on-year CPI, each lagged 2 months (`regime_classifier._load_macro_series`; thresholds 3 % and 4 % in `_classify_row`). The owner asks whether a more timely input would classify better. A 2 x 2 design on the classifier input ONLY:

| Arm | CPI input | Lag |
|---|---|---|
| R0 (reference, today) | mean of ES and EU | 2 months |
| R1 | maximum of ES and EU | 2 months |
| R2 | mean of ES and EU | 1 month |
| R3 | maximum of ES and EU | 1 month |

Everything else is identical (the other classifier inputs, thresholds, regime weights, the PIT scores, the construction, the stated-fee cost regime, the frozen P3-7 snapshot of IPC / fees). Trials = R1, R2, R3 (Holm family 3, level 0.05 / 3 = 0.0167); R0 is re-run in the same build.

**Decoupling (rule 2).** The benchmark target (`shared/objective.py`, IPC + M3) is NOT recomputed: the study evaluates against the sealed target and changes only the regime labels. If anything were ever adopted it would be a separate classifier input, never a change of `ipc_yoy_avg` (which feeds the target).

## 2. Release calendars (rule 1), checked 2026-11-22 on the official pages (read through search summaries; the calendar pages themselves were not opened)
- **INE (Spain).** The final CPI is published around the 12th-15th of the following month (2026: 15/01, 13/02, 13/03, 14/04, 14/05, 12/06, 15/07); the flash ("indicador adelantado") on the 27th-30th of the reference month ([INE press notes 2026](https://www.ine.es/dyngs/Prensa/es/notasPrensa.htm?cid=1254736176802); [flash October 2026](https://ine.es/dyngs/Prensa/adIPC1026.pdf)).
- **Eurostat (euro area).** The flash HICP comes on the last working day of the reference month or shortly after it (October 2026: 4 November; September: 2 October); the full HICP about 15-18 days after the end of the reference month (October 2026 final: 18 November) ([Eurostat release of 4 Nov 2026](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-04112026-ap); [HICP metadata](https://ec.europa.eu/eurostat/cache/metadata/en/prc_hicp_esms.htm)).
- **Conclusion.** At a month-end decision date *t* the FINAL CPI of month *t-1* has been public for about 2 weeks on both sources (latest case 18-19th of *t*): **a 1-month lag has no look-ahead.** Lag 0 would: the euro-area flash is published after the month-end, so lag 0 is NOT tested. The stored index values are the final series (CPI and HICP are essentially never revised), the same assumption as the 2-month baseline. The calendars of earlier years are assumed to follow the same pattern (not checked year by year).

## 3. Measures and rule
Full sample of the PIT run (261 monthly returns), stated fees fund by fund (the owner's regime) and the flat 25 bp as cross-check: **net annual return** and **maximum drawdown**, with the paired monthly difference to R0 and the stationary bootstrap of P3-5A (block 6, 4,000 resamples). A trial is classified:
- **ACCEPTABLE** iff (a) its maximum drawdown, at the stated fees AND at 25 bp, is no deeper than R0's by more than 0.5 pp, and (b) the Holm-adjusted lower bound of the paired annual net-return difference at the stated fees is above -0.10 pp (non-inferiority);
- otherwise **HARMFUL**.
An ACCEPTABLE trial is reported as **BENEFICIAL** if, in addition, its net return is at least 0.5 pp a year higher or its maximum drawdown at least 1.0 pp shallower than R0's (reported, not a gate); else **NEUTRAL**. Descriptive, never gating: the number of months whose regime label changes, the regime shares, the mean sub-portfolio weights, the turnover and the minimum detectable effect of the paired test (`pit_p35a_prep.mde_pp`). **The classification is information for the owner; the study never changes production and stops at the report.**

**Power, stated in advance.** Only a few months change label (15 of 321 for R1 and 18 for R3 on the classifier alone), so the paired difference is a sparse series: the study can show non-inferiority, it cannot be expected to show a material gain. NEUTRAL is the likeliest outcome; the argument for a more timely input would then rest on principle, not on this evidence.

## 4. What was already read before this preregistration (contamination log)
- On 2026-11-22: the classifier-only counterfactual (labels and weights, no returns): R1 changes 15 of 321 months, R3 18; Stagflation 8.4 % -> 12.1 % / 11.2 % of months; mean Defensive weight 53.2 % -> 53.0 % / 53.1 %; the last months (2026-08..11) stay in the energy-shock regime, which outranks inflation. No return or drawdown of any alternative has been computed.
- The baseline's performance (P3-7 report: 0.08 % net at the stated fees, drawdown -26.4 %) is known. No locked episode is involved (all spent by P3-5A).
- Provenance of the values: the aggregators and lags are the owner's; the margins (0.5 pp, 1.0 pp, -0.10 pp), the bootstrap and the Holm rule come from P3-5A / P3-7 / Amendment 5, chosen before this study.

## 5. Out of scope
Lag 0, a Spain-only input, recalibrating the 3 % / 4 % thresholds, the M2 / M3 inputs, the target, and any production change.

## 6. Log of what was read and what went wrong (2026-11-22, written after the runs; nothing here changes a value of sections 1-5)
- The regime histories of the four arms were frozen from one read of the classifier's inputs before the first run (snapshot `04aa54682f9b`); the reconstruction of the reference input equalled the classifier's own series and R0 equalled the sealed history exactly.
- Before this preregistration, a first counterfactual of the labels returned "no change" because `classify_historical()` caches its first result; it was caught by a sanity check (forcing the CPI to 10 % changed 182 months) and the study script clears the cache and refuses to continue if an override changes no label. The first tests of the CPI input were too weak (constant growth hid the lag and the aggregator) and were strengthened after a mutation check.
- The identity gate (R0 against the sealed run) passed. The trial table was read once, after all four arms were built. Result and reading: `doc/audit/P3_8_REGIME_CPI_REPORT.md`. R1 and R3 carry the label HARMFUL only because their non-inferiority bound cannot be shown (the minimum detectable effect, 0.25 and 0.19 pp, exceeds the 0.10 pp margin); R2 is NEUTRAL.
