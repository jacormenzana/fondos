# P3-6 (FND-0437): what hides behind the `Global` geography label, 2026-11-22

Read-only diagnostic ordered by the owner before any preregistration of a regional cap. Attributes and weights only: no return, no NAV, no metric was read; no production change. The three queries are in section 5.

## Decision needed
Treat the cap study as **unactionable for now** and move to layer 3 (regime / allocation, FND-0435), as the owner's rule says. Reasons, in order of weight:
1. **The label cannot carry a cap.** `Geography = Global` sits on funds whose declared benchmark or name is a single region (an S&P 500 index fund; a balanced fund against S&P 500 + 40 % US aggregate). A cap acting on the label would not see them.
2. **There are no holdings in the database,** only the label, the declared benchmark and the name. A cap on the true exposure of a `Global` fund cannot be measured, so it can neither be built on real exposure nor tested.
3. **No history to test it on.** `Geography` is today's attribute; the only past evidence is the 2008 Brinson split read with hindsight (P3-5A section 7).

What I would do instead: open the label defect as its own ticket (fix in the P1 classifier, root cause, not a value patch) and leave FND-0437 open with a review date after that fix.

## 1. The portfolio the label describes
PRODUCTION (30 funds, master weights): `Global` 87 %, Europe 7.5 %, North America 5.5 %. A cap on a named region would not bind.

## 2. What the same funds say when read by benchmark, then by name (indicative)
Implied region = the declared benchmark text if present, else the fund name; keyword rules (section 5), not the classifier. A text reading, not a measurement of holdings.

| Label | Implied by benchmark / name | Weight of the master | Funds |
|---|---|---|---|
| Global | none (no benchmark, no region in the name) | 0.355 | 8 |
| Global | world / ACWI | 0.225 | 8 |
| Global | **US** | **0.170** | **5** |
| Global | Europe (euro money-market and EUR-rate benchmarks) | 0.120 | 3 |
| Europe | none | 0.065 | 2 |
| North America | US | 0.055 | 3 |
| Europe | Europe | 0.010 | 1 |

- The 5 `Global` funds read as US are, by name: an S&P 500 index fund (0.035), a balanced fund against S&P 500 + 40 % US aggregate (0.035), a global-allocation fund with 36 % S&P 500 (0.035), a US-aggregate income fund in Defensiva (0.055) and a Nasdaq-biotech sector fund (0.010).
- **US explicit = 0.225 of the master** (0.17 + 0.055). Inside the two risk sleeves (Equilibrada + Dinamica, 0.45 of the master) the funds whose benchmark or name is explicitly US hold 0.17, that is **38 % of those sleeves**. The Europe line is money-market funds against euro rates, not an equity bet. No named emerging or Eastern-European weight is held today, so the regional mix that the 2008 Brinson split flagged is not what the portfolio shows now: the concentration today is a US tilt.
- 0.225 more is in world / ACWI funds. By index construction those are US-heavy (about two thirds of MSCI World, an outside figure, not measured here): that exposure is real and invisible in the data.

## 3. How general is the label problem (universe, 2026-11-22)
2,943 funds in the current universe, 2,941 with a `Geography`; `Global` 1,888. Among the 930 `Global` equity funds, 642 declare a benchmark: **501 read as world / ACWI (78 %), 78 as emerging / Asia, 15 as US, 12 as Europe, 7 as Japan, 29 other**, so about 17 % of the `Global` equity funds with a benchmark declare a regional one. Of the 459 `Global` equity and mixed funds with no benchmark, 7 carry a region in the name. Mixed funds: 81 world, 12 US, 9 Europe, 3 emerging, 44 other. Indicative: the keyword rules are crude (a "World ex-US" benchmark reads as world).

## 4. Reading against the owner's three tests
- Concentration material? Partly: a US tilt of about a fifth of the master (more than a third of the risk sleeves) behind labels that say 87 % `Global`.
- Actionable as a cap? No, until the label (or a holdings feed) is trustworthy: the cap would act on a column that misses the exposure it exists to limit.
- Testable on history? No (no point-in-time geography, locked episodes spent; the drawdown tail needs a measure of region exposure that does not exist).

## 5. Queries (read-only; run on the live store 2026-11-22)
```sql
-- 1. label by weight, PRODUCTION
SELECT fm.Geography, ROUND(SUM(w.weight)::numeric,4) FROM portfolio_weights w JOIN portfolio_scenarios s ON s.scenario_id=w.scenario_id
JOIN fund_master fm ON fm.ISIN=w.isin WHERE s.status='PRODUCTION' GROUP BY 1;
-- 2. implied region: b = lower(coalesce(Benchmark_Declared,'')), nm = lower(Fund_Name); benchmark rules first, name rules when no benchmark
-- world: (world|global|acwi|all country|ac world|all-country)  US: (s&p 500|s&p500|nasdaq|russell|msci usa|msci us |dow jones|us aggregate|us treasury|s&p us)
-- europe: (stoxx|msci europe|ftse developed europe|euro stoxx|msci emu|dax|cac|ftse 100|msci uk|bloomberg euro|euro aggregate|euro gov|eur1-month|german treasury|estr)
-- japan: (topix|nikkei|msci japan)  em/asia: (emerging|msci em|msci china|csi|hang seng|msci india|asia)
-- name: US (s&p 500|\musa\M|\mus\M|america), Europe (europe|euro ), EM/Asia (emerging|\mem\M|china|india|asia|pacific), Japan (japan)
-- 3. universe: In_Current_Universe=1 AND Geography='Global', grouped by Fund_Nature and the implied region of Benchmark_Declared
```
