# P3-5A: the 11 preregistered trials, results and reading

Written 2026-11-21 after reading the runs, in the order the preregistration fixes (design window, full sample, confirmation episodes). Runs: `out/reports/p3_p35a_trials/20261121_163723/` (git-ignored artifacts), code `af63cc4`, preregistration `doc/decisiones/PREREG_P3_5A.md` with its Amendment 1. The BASELINE re-run in the same build reproduces the sealed run exactly (coverage 100 %, maximum score_base difference 0, eligibility agreement 100 %, daily short gates included), so it is a valid comparator. Everything below is in-sample on one design episode, with today's fees and attributes (a look-ahead), on a panel with no dead funds (FND-0436: it flatters L1 most); it is evidence for the forward shadow, never an adoption.

## Verdict by the signed rule
**No variant is a candidate.** The four conditions (all at the definitions of Amendment 1):
- **Condition 1 (drawdown <= 15 %, unstressed and under the primary tail stress, full sample and the three episodes):** the BASELINE itself fails it under the stress (worst stressed 12-month window -15.9 %). Passing variants: L2 (z0 2.0 and 1.5) and the combination C1. L1 makes the drawdown WORSE (-16.4 % at q 0.20), L3 is neutral, L4 improves it a little but stays 0.3-0.4 pp short under the stress (-15.3 %, -15.4 %).
- **Condition 2 (improvement):** only L4 passes the signed return test, clearly: +0.70 pp a year (H 36 months, lower bound +0.18) and +1.13 pp (24 months, lower bound +0.65), above the 0.5 pp materiality floor. L2 loses return (-0.62, -1.07 pp); L1 is +0.20 pp but its lower bound (-0.42) is below the -0.10 margin; L3 -0.08 / -0.21 pp. **For the risk track (L1-L3 and the combination) condition 2 is NOT EVALUABLE at the stated fees**: every sub-portfolio of the baseline earns a negative or near-zero net return there (Defensiva -1.39 %, Equilibrada +1.48 %, Dinamica -1.68 % a year, full-path drawdowns of 32 %, 38 %, 58 %), so no constant mix reaches the 15 % cap and the baseline's ceiling is undefined. The report prints that as False; it is not a verdict. See the decision below.
- **Condition 3 (no collateral damage):** L2 fails (non-crisis loss of 0.74 and 1.23 pp, limit 0.35); every other trial passes (the 36-month-window share is 0 for the baseline at the stated fees, so nothing can fall).
- **Condition 4 (beat the costed matched pool):** **no trial beats it.** At the stated fees the baseline nets 0.08 % a year against 1.54 % for the equal-weight eligible pool built from the same scores and costed the same way; L4 closes part of the gap (0.79 %, 1.23 %), the others do not.

## The reading, lever by lever
- **L1 (peer-relative filters), no case.** It readmits the funds the absolute filter held out, as intended, but in the 2008 window that is a deeper drawdown (-16.4 %, -14.8 % at q 0.35 against -14.6 %), the return gain is +0.20 pp and not distinguishable from zero. The survivor-only panel favours L1, so this is the best case for it.
- **L2 (overextension), a trade of return for drawdown.** It cuts the 2008 drawdown by about 2 pp and the full-sample one to -12.6 % / -12.8 %, at 0.6-1.1 pp of net return a year and more turnover (32 % to 36-38 % a month). Its geographic exposure moves as expected (Europe and North America up, Eastern Europe down at the 2008 top: 4.8 % to 0.9 %).
- **L3 (recovery modulation), inert.** The trigger is valid (3 transitions in the design window, 1.2 flips a year) but it sits in recovery 1.1 % of the dates, so it changes almost nothing.
- **L4 (one-off fees), the only lever with a clear effect, and it is about cost.** +0.7 / +1.1 pp at H = 36 / 24 months. Run at the variants' realised holding period (7.1 and 7.2 months, the informative runs, outside the 11 and the verdict) it gives **+2.4 pp, 2.5 % net against 1.6 % for the pool, a drawdown of -14.0 %**, and the champion beats the pool. That short amortisation is what the C4 rule excluded because at H this small the penalty acts as a near-veto on every fund that charges an entry fee; at the stated fees that veto is exactly what recovers the fee drag.
- **The headline is not a lever.** At the stated fees the baseline's net return is 0.08 % a year, below the costed matched pool by 1.5 pp: the selection's rotation (32 % a month) pays more in entry and exit fees than the ranking earns. That is the "retire ranking complexity" option of the plan (C2) arriving with evidence.

## What these numbers cannot say
One design episode and three confirmation episodes (two of them mild: pool drawdown 4-5 %); today's fees and attributes; a panel with no dead funds; minimum detectable effect 0.62 pp a year (so only L4's gains and L2's losses are visible at all); a tail stress with its own assumptions (Amendment 1, item 4); the risk-track KPI undefined at the stated fees.

## Integrity check: the database moved during the runs
The runs read the IPC series, the fees, the geography and the daily NAV from the live database at the start of each trial, and the chain took about three hours while another session's P1/P2 cycle was running. Two things changed in that window, and both were checked AFTER the runs, read-only, before the numbers were relied upon: (1) the IPC series gained one row (2026-10-31): the baseline, S1 and S2 ran on 321 rows, S3 to X8 on 322. The row is invisible to every evaluation date (the IPC is published with a 2-month lag and the last evaluation date is 2026-10-31); rescoring the last six dates and 9 earlier ones under the new series gives a `score_base` IDENTICAL to the sealed run's (maximum difference 0.000000 over 43,530 rows). (2) 2,947 `fund_master` rows were rewritten between 16:50 and 17:00 Madrid time, across S1 and S2: costing the sealed baseline's weights and the matched pool with today's fees reproduces the series saved by the baseline at the start of the chain to 1e-16 over 2,461 funds, so the fee VALUES did not change. The 14 runs are therefore comparable. For any rebuild (Amendment 2), freeze the DB-derived inputs (IPC, fees, geography) in a snapshot first, so the question cannot arise.

## Decisions for the owner
1. **Accept that nothing is a candidate**, and decide the direction: (a) close L1 and L3 (no evidence); (b) keep L2 only if the 1-2 pp of drawdown is worth 0.6-1.1 pp of return; (c) treat L4 with the realised holding period as the next preregistered study (its own preregistration, OFF, forward shadow), because it is the only change that beats the pool; (d) decide the plan's option "retire ranking complexity" (C2) on the matched-pool evidence. My recommendation: (c) and (d) together, nothing adopted now.
2. **The risk-track KPI.** Amendment 2 (dated, after reading, therefore disclosed as post hoc) would add the ceiling at the flat 25 bp per side as a reported cross-check, where it is defined. It needs the 14 runs rebuilt (the scoring is cached, about an hour). It cannot change the verdict of the signed rule; it would let L1-L3 be judged on their own KPI. Recommended; not started.
3. **FND-0435 stays open** until the one full receipt at package delivery.

## Full report of the run

### Adoption table (generated by `scripts/audit/p3/p3_p35a_trials.py --stage report`; `ceil_pass` False means NOT EVALUABLE where `ceil_ann` is NaN)

Baseline re-run in the same build: {'coverage': 1.0, 'max_abs_score_base_diff': 0.0, 'eligible_agreement': 1.0}. Holm family = 11 trials (level 0.0045); the historical test of condition 2 is non-inferiority (lower bound > -0.1 pp a year); adoption is left to the forward shadow. Condition 1 is evaluated at the PIT's flat 25 bp per side (Amendment 1: at the stated fees the baseline itself is far deeper, see `maxdd_fee`), both unstressed and under the primary tail stress (realised P10 path, a x k = 10 %).

## 1. DESIGN (2008, 2007-11..2009-07): drawdown of the master portfolio, unstressed and under the primary stress (worst 12-month window starting in the design window)

```
                  dd_design_25bp  dds_design_25bp  dd_design_fee  dds_design_fee
trial                                                                           
T0_baseline              -0.1459          -0.1586        -0.1720         -0.1818
S1_L1_q20                -0.1635          -0.1746        -0.1928         -0.2007
S2_L1_q35                -0.1482          -0.1597        -0.1834         -0.1883
S3_L2_z2.0               -0.1264          -0.1413        -0.1670         -0.1736
S4_L2_z1.5               -0.1280          -0.1427        -0.1712         -0.1760
S5_L3_k0.5               -0.1489          -0.1613        -0.1799         -0.1889
S6_L3_k0                 -0.1507          -0.1629        -0.1874         -0.1956
S7_L4_H36m               -0.1396          -0.1531        -0.1580         -0.1695
S8_L4_H24m               -0.1404          -0.1539        -0.1563         -0.1680
I1_L1q20_L3k0.5          -0.1659          -0.1767        -0.2021         -0.2090
I2_L1q35_L3k0            -0.1525          -0.1635        -0.1956         -0.1992
C1_all_first             -0.1349          -0.1490        -0.1697         -0.1800
X7_L4_H_realised         -0.1397          -0.1521        -0.1505         -0.1617
X8_L4_H_realised         -0.1397          -0.1521        -0.1505         -0.1617
```

## 2. FULL SAMPLE: returns, drawdowns, stress, turnover

```
                  ann_25bp  ann_fee  pool_ann_fee  maxdd_25bp  maxdd_repo_25bp  maxdd_fee  stressed_worst_25bp  stressed_worst_p05_25bp  stressed_worst_s20_25bp  turnover  holding_months
trial                                                                                                                                                                                     
T0_baseline         0.0369   0.0008        0.0154     -0.1459          -0.1459    -0.2638              -0.1586                  -0.1629                  -0.1712    0.3220          6.9093
S1_L1_q20           0.0385   0.0028        0.0163     -0.1635          -0.1635    -0.2320              -0.1746                  -0.1790                  -0.1856    0.3146          7.2376
S2_L1_q35           0.0381   0.0028        0.0157     -0.1482          -0.1482    -0.2024              -0.1597                  -0.1653                  -0.1710    0.3159          7.2442
S3_L2_z2.0          0.0352  -0.0053        0.0151     -0.1264          -0.1264    -0.3010              -0.1413                  -0.1457                  -0.1560    0.3583          6.3745
S4_L2_z1.5          0.0334  -0.0098        0.0148     -0.1280          -0.1280    -0.3353              -0.1427                  -0.1471                  -0.1572    0.3777          6.0084
S5_L3_k0.5          0.0366  -0.0000        0.0153     -0.1489          -0.1489    -0.2769              -0.1613                  -0.1656                  -0.1735    0.3278          6.8014
S6_L3_k0            0.0361  -0.0013        0.0153     -0.1507          -0.1507    -0.2972              -0.1629                  -0.1671                  -0.1749    0.3368          6.6177
S7_L4_H36m          0.0369   0.0079        0.0150     -0.1396          -0.1396    -0.1805              -0.1531                  -0.1575                  -0.1664    0.3079          7.0840
S8_L4_H24m          0.0376   0.0123        0.0151     -0.1404          -0.1404    -0.1563              -0.1539                  -0.1582                  -0.1671    0.2966          7.2276
I1_L1q20_L3k0.5     0.0382   0.0019        0.0162     -0.1659          -0.1659    -0.2458              -0.1767                  -0.1811                  -0.1874    0.3207          7.1067
I2_L1q35_L3k0       0.0372   0.0008        0.0156     -0.1525          -0.1525    -0.2360              -0.1635                  -0.1691                  -0.1744    0.3303          6.9435
C1_all_first        0.0368   0.0038        0.0157     -0.1349          -0.1349    -0.2011              -0.1490                  -0.1536                  -0.1630    0.3450          6.6106
X7_L4_H_realised    0.0358   0.0254        0.0156     -0.1397          -0.1397    -0.1505              -0.1521                  -0.1567                  -0.1643    0.2785          7.6742
X8_L4_H_realised    0.0356   0.0253        0.0157     -0.1397          -0.1397    -0.1505              -0.1521                  -0.1567                  -0.1643    0.2771          7.7577
```

## 3. CONFIRMATION EPISODES (episode + 12 months; unstressed `dd_`, stressed `dds_`, flat 25 bp)

```
                  dd_2015-07/08_25bp  dds_2015-07/08_25bp  dd_2015-11..2016-01_25bp  dds_2015-11..2016-01_25bp  dd_2022-01..2023-01_25bp  dds_2022-01..2023-01_25bp
trial                                                                                                                                                              
T0_baseline                  -0.0374              -0.0393                   -0.0358                    -0.0379                   -0.1123                    -0.1176
S1_L1_q20                    -0.0374              -0.0416                   -0.0358                    -0.0360                   -0.1123                    -0.1183
S2_L1_q35                    -0.0364              -0.0381                   -0.0356                    -0.0359                   -0.1131                    -0.1197
S3_L2_z2.0                   -0.0416              -0.0430                   -0.0387                    -0.0405                   -0.1075                    -0.1134
S4_L2_z1.5                   -0.0430              -0.0444                   -0.0395                    -0.0412                   -0.1065                    -0.1130
S5_L3_k0.5                   -0.0404              -0.0420                   -0.0388                    -0.0406                   -0.1123                    -0.1176
S6_L3_k0                     -0.0425              -0.0439                   -0.0409                    -0.0425                   -0.1123                    -0.1176
S7_L4_H36m                   -0.0396              -0.0411                   -0.0343                    -0.0366                   -0.1141                    -0.1192
S8_L4_H24m                   -0.0389              -0.0419                   -0.0349                    -0.0368                   -0.1180                    -0.1227
I1_L1q20_L3k0.5              -0.0398              -0.0438                   -0.0382                    -0.0382                   -0.1123                    -0.1183
I2_L1q35_L3k0                -0.0425              -0.0436                   -0.0417                    -0.0414                   -0.1131                    -0.1197
C1_all_first                 -0.0431              -0.0470                   -0.0369                    -0.0371                   -0.1054                    -0.1122
X7_L4_H_realised             -0.0318              -0.0340                   -0.0289                    -0.0317                   -0.1138                    -0.1195
X8_L4_H_realised             -0.0318              -0.0340                   -0.0288                    -0.0317                   -0.1138                    -0.1191
```

## 4. CONDITION 2: non-inferiority at the stated fees (return KPI) and the ceiling KPI (risk track)

```
                        track  ret_mean_pp  ret_se_pp  ret_lb_pp ret_pass  ceil_ann  ceil_dd  ceil_dd_stressed  ceil_diff_pp  ceil_se_pp  ceil_lb_pp ceil_pass  ceil_at_base_mix_ann
trial                                                                                                                                                                               
T0_baseline          baseline          NaN        NaN        NaN      NaN       NaN      NaN               NaN           NaN         NaN         NaN       NaN                   NaN
S1_L1_q20                risk       0.2030     0.2391    -0.4206    False       NaN      NaN               NaN           NaN      0.8268         NaN     False                   NaN
S2_L1_q35                risk       0.1978     0.2481    -0.4493    False       NaN      NaN               NaN           NaN      0.8880         NaN     False                   NaN
S3_L2_z2.0               risk      -0.6200     0.2098    -1.1674    False       NaN      NaN               NaN           NaN      0.9503         NaN     False                   NaN
S4_L2_z1.5               risk      -1.0726     0.2708    -1.7790    False       NaN      NaN               NaN           NaN      0.9853         NaN     False                   NaN
S5_L3_k0.5               risk      -0.0816     0.0470    -0.2041    False       NaN      NaN               NaN           NaN      0.2702         NaN     False                   NaN
S6_L3_k0                 risk      -0.2100     0.1128    -0.5042    False       NaN      NaN               NaN           NaN      0.3527         NaN     False                   NaN
S7_L4_H36m             return       0.6984     0.2000     0.1767     True       NaN      NaN               NaN           NaN      0.7031         NaN     False                   NaN
S8_L4_H24m             return       1.1306     0.1858     0.6459     True       NaN      NaN               NaN           NaN      0.7409         NaN     False                   NaN
I1_L1q20_L3k0.5          risk       0.1203     0.2565    -0.5488    False       NaN      NaN               NaN           NaN      0.9338         NaN     False                   NaN
I2_L1q35_L3k0            risk       0.0034     0.2864    -0.7438    False       NaN      NaN               NaN           NaN      0.8220         NaN     False                   NaN
C1_all_first             risk       0.2937     0.3583    -0.6410    False       NaN      NaN               NaN           NaN      0.9671         NaN     False                   NaN
X7_L4_H_realised  informative       2.4193     0.3067     1.6193     True    0.0195  -0.1078            -0.117           NaN      1.1064         NaN     False                   NaN
X8_L4_H_realised  informative       2.4098     0.3011     1.6244     True    0.0193  -0.1078            -0.117           NaN      1.0708         NaN     False                   NaN
```

## 5. CONDITIONS 3 AND 4 AND THE VERDICT

```
                  noncrisis_degradation_pp  share36_variant  share36_baseline     c1     c2     c3     c4 candidate
trial                                                                                                              
T0_baseline                            NaN              NaN               NaN  False   None    NaN  False       NaN
S1_L1_q20                          -0.3387           0.0000               0.0  False  False   True  False     False
S2_L1_q35                          -0.4248           0.0044               0.0  False  False   True  False     False
S3_L2_z2.0                          0.7380           0.0000               0.0   True  False  False  False     False
S4_L2_z1.5                          1.2273           0.0000               0.0   True  False  False  False     False
S5_L3_k0.5                         -0.0000           0.0000               0.0  False  False   True  False     False
S6_L3_k0                           -0.0000           0.0000               0.0  False  False   True  False     False
S7_L4_H36m                         -0.5820           0.0000               0.0  False   True   True  False     False
S8_L4_H24m                         -1.0894           0.0221               0.0  False   True   True  False     False
I1_L1q20_L3k0.5                    -0.3387           0.0000               0.0  False  False   True  False     False
I2_L1q35_L3k0                      -0.4248           0.0044               0.0  False  False   True  False     False
C1_all_first                       -0.0462           0.0000               0.0   True  False   True  False     False
X7_L4_H_realised                   -1.9139           0.0708               0.0  False   True   True   True       NaN
X8_L4_H_realised                   -1.8989           0.0752               0.0  False   True   True   True       NaN
```

## 6. L3 stability (the frozen limit: at most 4 transitions in the 2008 design window and at most 2 flips a year)

```
                design_window_transitions design_ok flips_per_year flips_ok share_recovery share_stress
S5_L3_k0.5                              3      True       1.195581     True        0.01145     0.152672
S6_L3_k0                                3      True       1.195581     True        0.01145     0.152672
I1_L1q20_L3k0.5                         3      True       1.195581     True        0.01145     0.152672
I2_L1q35_L3k0                           3      True       1.195581     True        0.01145     0.152672
C1_all_first                            3      True       1.195581     True        0.01145     0.152672
```

## 7. Geographic exposure of the selected set (share of the invested weight; required for every L2 run), all dates and the 2008 top dates

```
                      T0_baseline  S1_L1_q20  S2_L1_q35  S3_L2_z2.0  S4_L2_z1.5  S5_L3_k0.5  S6_L3_k0  S7_L4_H36m  S8_L4_H24m  I1_L1q20_L3k0.5  I2_L1q35_L3k0  C1_all_first  X7_L4_H_realised  X8_L4_H_realised
geography                                                                                                                                                                                                      
Asia-Pacific                0.016      0.022      0.021       0.018       0.019       0.017     0.017       0.016       0.015            0.022          0.021         0.019             0.015             0.015
China                       0.006      0.006      0.005       0.005       0.005       0.006     0.006       0.005       0.004            0.006          0.005         0.005             0.004             0.004
Eastern Europe              0.009      0.008      0.007       0.006       0.005       0.009     0.009       0.009       0.009            0.008          0.007         0.005             0.009             0.009
Europe                      0.289      0.283      0.286       0.288       0.290       0.289     0.288       0.289       0.289            0.282          0.286         0.285             0.300             0.300
Global                      0.606      0.609      0.609       0.603       0.594       0.606     0.606       0.610       0.614            0.609          0.609         0.606             0.611             0.611
India                       0.000      0.000      0.000       0.000       0.000       0.000     0.000       0.000       0.000            0.000          0.000         0.001             0.000             0.000
Japan                       0.002      0.001      0.001       0.001       0.001       0.002     0.002       0.001       0.001            0.001          0.001         0.000             0.001             0.001
Latin America               0.002      0.002      0.001       0.001       0.001       0.002     0.002       0.001       0.001            0.002          0.001         0.002             0.001             0.001
Middle East & Africa        0.000      0.000      0.000       0.000       0.000       0.000     0.000       0.000       0.000            0.000          0.000         0.000             0.000             0.000
North America               0.066      0.064      0.064       0.072       0.079       0.066     0.065       0.064       0.061            0.064          0.064         0.072             0.054             0.054
NaN                         0.006      0.006      0.006       0.006       0.006       0.006     0.006       0.006       0.006            0.006          0.006         0.006             0.005             0.005

                      T0_baseline  S1_L1_q20  S2_L1_q35  S3_L2_z2.0  S4_L2_z1.5  S5_L3_k0.5  S6_L3_k0  S7_L4_H36m  S8_L4_H24m  I1_L1q20_L3k0.5  I2_L1q35_L3k0  C1_all_first  X7_L4_H_realised  X8_L4_H_realised
geography                                                                                                                                                                                                      
Asia-Pacific                0.009      0.017      0.023       0.019       0.019       0.009     0.009       0.008       0.008            0.017          0.023         0.019             0.008             0.008
China                       0.000      0.000      0.000       0.000       0.000       0.000     0.001       0.000       0.000            0.000          0.000         0.000             0.000             0.000
Eastern Europe              0.048      0.032      0.025       0.009       0.009       0.053     0.053       0.049       0.049            0.032          0.025         0.006             0.049             0.049
Europe                      0.294      0.291      0.295       0.334       0.330       0.296     0.297       0.304       0.312            0.297          0.306         0.335             0.309             0.309
Global                      0.604      0.614      0.615       0.567       0.561       0.594     0.593       0.594       0.586            0.604          0.595         0.565             0.597             0.597
India                       0.000      0.000      0.000       0.000       0.000       0.000     0.000       0.000       0.000            0.000          0.000         0.000             0.000             0.000
Japan                       0.000      0.000      0.000       0.000       0.000       0.000     0.000       0.000       0.000            0.000          0.000         0.000             0.000             0.000
Latin America               0.003      0.001      0.000       0.006       0.006       0.003     0.001       0.003       0.003            0.001          0.000         0.006             0.003             0.003
Middle East & Africa        0.000      0.000      0.000       0.000       0.000       0.000     0.000       0.000       0.000            0.000          0.000         0.000             0.000             0.000
North America               0.011      0.014      0.012       0.036       0.046       0.015     0.015       0.011       0.011            0.018          0.021         0.039             0.004             0.004
NaN                         0.030      0.030      0.030       0.030       0.030       0.030     0.030       0.030       0.030            0.030          0.030         0.030             0.030             0.030
```
