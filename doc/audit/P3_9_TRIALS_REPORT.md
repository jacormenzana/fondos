# P3-9 trials: layer 3, nature-bonus scale (N) and Equilibrada chasing control (E) on top of AC, 2026-11-23

Preregistration `doc/decisiones/PREREG_P3_9_LAYER3.md` (331ced8). Run `out/reports/p3_p39_trials/20261123_p39` (git-ignored; full tables in its `REPORT.md` and `adoption_table.csv`), sealed PIT run 20261119_202509, P3-7 snapshot `42d92e50d483`. Built by `scripts/audit/p3/p3_p39_trials.py` (levers `5a39674`, runner `3153108`, report stage `06f633a`, committed and tested before any result was read).

## Decision
**No arm is a forward-shadow candidate. Nothing is adopted and no forward shadow of layer 3 is opened.** The levers stay in the code, OFF (`P3_L5_NATURE_SCALE_ENABLED`, `P3_L6_CHASE_CONTROL_ENABLED`). Layer 3 as preregistered is closed on this evidence; FND-0435 closes through its delivery commit.

## Integrity
Identity gate PASSED in both parts: T0 equals the sealed run (gross and turnover differences 0, score_base identical, eligibility identical, every switch OFF) and the re-run base AC equals the P3-7 AC series (difference 0). All arms: same rows and families as the sealed run, eligibility of every matched row unchanged, one snapshot. Cold builds took 488-508 s per arm.

## Results (base AC: net 3.12 % a year at the stated fees, 25 bp maximum drawdown -12.5 %, stressed worst window -13.9 %)
| Arm | Net a year, stated fees (pp vs AC: mean, lower bound at Holm 0.0083) | Max drawdown 25 bp (gain vs AC, pp) | Stressed worst window (gain vs AC, pp) | Rule 1 | Rule 2 | Rule 3 | Rule 4 |
|---|---|---|---|---|---|---|---|
| N5 (bonus x k 0.5) | +0.20, -0.14 | -12.95 % (-0.44) | -14.55 % (-0.61) | FAIL | FAIL | FAIL | pass |
| N0 (no bonus) | +0.09, -0.44 | -13.89 % (-1.38) | -15.77 % (-1.84) | FAIL | FAIL | FAIL | pass |
| E5 (chasing f 0.5) | -0.29, -0.72 | -12.15 % (+0.36) | -13.60 % (+0.33) | FAIL | FAIL | FAIL | fail |
| E0 (chasing f 0) | -0.55, -1.07 | -12.17 % (+0.34) | -13.64 % (+0.30) | pass | FAIL | FAIL | fail |
| NE5 | -0.08, -0.57 | -12.57 % (-0.06) | -14.23 % (-0.30) | FAIL | FAIL | FAIL | pass |
| NE0 | -0.67, -1.45 | -13.47 % (-0.96) | -15.44 % (-1.50) | FAIL | FAIL | FAIL | fail |
Rule 1 = the 15 % cap and the stressed worst window no more than 0.5 pp deeper than AC (and the same in each informative episode); rule 2 = non-inferiority, lower bound above -0.10 pp; rule 3 = a drawdown or stressed-window gain of at least 1.0 pp; rule 4 = non-crisis degradation at most 0.35 pp and the share of 36-month windows at or above 3.2 % not below AC's (0.323).

## Reading
- **The nature bonus protects; removing it hurts.** N0 deepens the drawdown by 1.4 pp and the stressed window by 1.8 pp for no return gain (+0.09 pp, standard error 0.22). N5 is milder in the same direction. This is the opposite of the hindsight hint of the diagnostic (bonus 1.0 helped at the 2008 bottom), which was one episode and a 12-month forward return; over the full history the bonus is a net risk control.
- **The chasing control buys 0.3 pp of drawdown for 0.3-0.5 pp of return a year.** E5 and E0 shallow the drawdown by about 0.35 pp, a third of the 1.0 pp preregistered threshold, and lose 0.29 and 0.55 pp of net return with the lower bound far below -0.10. D3 of the preregistration chose strict non-inferiority; under the stated-trade alternative (up to -0.50 pp of return for at least 2.0 pp of drawdown) neither arm would qualify either.
- **Non-inferiority is hard to show on this history:** even N5, with a mean return +0.20 pp better, has a lower bound of -0.14 pp (standard error 0.14 pp at the Holm level 0.0083). That is why the preregistration said the history can only screen.
- **The base arm is the finding of this study:** AC at the stated fees nets 3.12 %, within 0.1 pp of the lower edge of the restated band (3.2-4.0 %), with a 25 bp drawdown of -12.5 % and a stressed worst window of -13.9 %, inside the 15 % cap, against 0.08 % and -14.6 % / -15.9 % for the live arm (T0).
- Survivorship (FND-0436) flatters every row; the stress rule is the primary one of P3-5A. The informative episodes were read by P3-5A and are not independent confirmation.

## Consequence
The route to the restated mandate is the cost layer already in the forward shadow (AC, reviews at 6 / 12 / 24 / 36 months), not the ranking levers of layer 3. The two open items of Package L that remain with a mechanism are P3-6 (blocked on the Geography label defect FND-0440) and the mandate wording in the reports (the net band replaces 6.94 %).
