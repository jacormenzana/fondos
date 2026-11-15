# Package J - Champion-side data captures (selection quality, package 2)

Owner order and decisions 2026-11-15: freeze lifted for packages I and J; letters I / J / K. One page, rewritten in place. Plan: `doc/especificacion/PLAN_SELECCION_CALIDAD.md` (WP3). Exit metric: every capture has written its first snapshot and the roughly 10 funds with stale NAV are classified. Time box: 3 to 4 sessions (estimate). Writes only inside existing harvest and refresh paths; DDL only through `owner_deploy.py`; no P1 / P2 / P3 process without the owner's order.

## 1. Decision needed
None now. Package J starts after package I's first delivery unless the owner orders it in parallel.

## 2. State
Not started. Items: (1) append-only attribute history of `fund_master`; (2) KID version archive written by the existing harvest; (3) survivorship measurement (classify the ~10 stale-NAV funds; presence series from `db_document_catalogue`, 9 harvests since 2026-07-17); (4) macro vintages (classify revised vs unrevised indicators, capture-from-now, ALFRED probe); (5) free source survey (read probe only, no scraper, off-cycle, rate-limited, under the cycle lock). `nav_sources.first_nav_date` is renamed `nav_history_start` and never used as an inception date.

## 3. Decisions pending
None. The DDL of items 1, 2 and 4 is applied by the owner's watcher (`owner_deploy.py`).
