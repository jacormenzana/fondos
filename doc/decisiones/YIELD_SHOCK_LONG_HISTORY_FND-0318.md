# YIELD_SHOCK_LONG_HISTORY_FND-0318 — the yield-shock rules on pre-1999 history, and what the source probes found

**Status: RESEARCH RECORD (2026-10-25). Nothing here changes a rule, a threshold, a feed or a weight.** The sovereign-stress monitor (FND-0294, monitor-only) keeps R4 = +1.0pp and
R5 = +0.75pp in 3 months exactly as pinned in `shared/sovereign_stress.RULE_PARAMS`.

## Item 1 — long-history run (pre-registered before any pre-1999 data was fetched; backlog log 1431 on FND-0318)
FRED/OECD `IRLTLT01{US,DE,GB,JP}M156N`, sample 1971-01..1998-12 (336 months with ≥ 2 countries; US from 1953, DE 1956, GB 1960, JP only from 1989). Five episodes labelled from
history **before** scoring (window ±3 months): 1979-09..1980-04, 1981-05..1981-09, 1987-03..1987-10, 1994-02..1994-12, 1996-02..1996-06.

| Rule | Episodes detected | False-ON months (of 336) | Euro era 2000-2026 for comparison |
|---|---|---|---|
| R4 (+1.0pp / 3m) | 4/5 | 39 (11.6%) | 1/6, 0 false |
| R5 (+0.75pp / 3m) | 5/5 | 65 (19.3%) | 4/6, 8 false (2.5%) |
| R5z (≥ 2.0 × max(sd, 0.15pp)), information only | 4/5 | 19 (5.7%) | 3/6, 22 false (6.9%) |

R5 false months by decade: 36 in the 1970s, 21 in the 1980s, 8 in the 1990s; by country (months where it fired): GB 46, US 17, DE 8, JP 6.

## How to read it (limits included)
- **The absolute threshold is regime-dependent.** In the high-inflation, high-volatility 1970s-80s a fixed +0.75pp over 3 months fires constantly; the volatility-normalised variant R5z
  is far quieter there (5.7% vs 19.3%) and the reverse holds in the euro era (6.9% vs 2.5%). Today's 60-month volatility of 3-month changes (DE 0.34, US 0.41, GB 0.43 pp) is
  closer to the euro era, which supports keeping R5 primary; it is not a reason to change anything.
- **The false rates are upper bounds.** The false months cluster in 1972-76 and 1990 (R5: 1973 7, 1974 7, 1976 6, 1990 8): the 1973-74 oil shock, the UK 1976 IMF crisis and the 1990 Gulf
  War are real stress periods that were **not** on the pre-declared episode list. The list was fixed before scoring and is not relabelled afterwards; five episodes is also a small sample.
- No threshold may be changed on this evidence: a change needs its own pre-declared, per-rule evidence run (and the forward window from 2026-11 is the honest test).

## Items 2-5 — source feasibility probes (read-only; adoption would be a separate pre-registered step)
| Item | Source | Finding (2026-10-25) |
|---|---|---|
| 2 US auctions | Treasury FiscalData `auctions_query`, field `bid_to_cover_ratio` | works; 10-year notes since 1979-10-31, newest listed 2026-08-12 (2.53) |
| 3 intra-month | FRED `DGS10` daily; ECB AAA euro curve 10y (`YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y`) | both work, 1-2 days behind (newest 2026-10-22) |
| 4 daily gold | Bundesbank SDMX API | reachable, but the guessed series keys return 404: inconclusive, not a negative; WGC needs registration; unofficial feeds not probed |
| 5 GB fallback | Bank of England `IUMALNPY` | NOT equivalent: mean 0.47pp (max 0.56) from the stored GB series |
| 5 JP fallback | Ministry of Finance JGB CSV (daily 10Y, since 1974) | monthly mean vs stored JP over 24 shared months: mean 0.038pp, max 0.102pp, i.e. 0.002pp outside the 0.10pp tolerance: a candidate only if a tolerance of about 0.15 is pre-declared |

**Operational note:** Python's TLS verification fails on this machine for the Bundesbank and MoF hosts while `curl` (Windows trust store) succeeds. Any future loader for those hosts must
fix the certificate path first; disabling verification is not an option.

## Reproduce
Archived outside git in `out/audit/P2/yield_shock_long_history_20261025/` (git-ignored): `long_history_shock_run.py` (sha256 `40bd7459fee3480652d8615bce21ed5382d113718b856a581920118ffe33eddd`),
`long_history_result.json` (`317d548c1b379f107bfa672b4f410980c9f206649e1b9c9544127ca5703441cf`), `probe_fnd0318_sources.py` (`39c48fffc6de4e52a573f0a28d66c158fd5816ae23a95a75a5011b35f1e9765e`),
`measure_jp_mof.py` (`b27fd5a26d0fb9fb7089fe1560e71c839850fdc1956a6d2240f941d80994d50c`), `measure_yield_splice.py` (`c88e75c4f8f83885145be8727266a4bc9b9e037a087bfbee99a44291e42582f2`).
