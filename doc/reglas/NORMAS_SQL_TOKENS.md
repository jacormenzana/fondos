# Database Query & Output Limits (Token Efficiency)

**CRITICAL RULE:** Database query outputs are the primary source of context bloat. You MUST minimize the textual output returned from any DB tool.

**SCOPE:** These rules apply EXCLUSIVELY to Agent diagnostic, analytical, and exploratory sessions. They do not supersede production code standards defined in NORMAS_IMPLEMENTACION.md.

## 1. Zero-Exploration by Default
- **OBLIGATORY:** Grep SCHEMA_REFERENCE.md for the target table BEFORE executing any exploratory SELECT. Never read the whole file.
- **PROHIBITED:** Never run `SELECT * FROM table` without a `LIMIT`.
- **PROHIBITED:** Never dump entire database records, full dataframes, or unpaginated SQL results into the context.

## 2. The 3-Stage Resolution Protocol
Execute DB tasks strictly in this order. Do not advance to the next stage unless the current one fails to answer the question:
1. **Aggregates First:** Use `COUNT()`, `MAX()`, `MIN()`, `SUM()`, `DISTINCT`, and `GROUP BY` to understand data shape.
2. **Targeted Samples:** If raw data is needed for pattern recognition, use `ORDER BY` on a deterministic key plus `LIMIT 15`, so the sample is reproducible.
3. **Exact Point Lookups:** Use explicit `WHERE` clauses on primary keys/indexed columns to fetch specific diagnosis rows.

## 3. Strict Output Constraints
- **Column Pruning:** `SELECT` only the exact columns required. Never retrieve unused payload columns.
- **Row Limits:** Hard cap for exploratory queries is `LIMIT 50`. For large datasets, use deterministic/keyset pagination.
- **Reporting:** When reporting validation results to the user or writing to memory, return ONLY the mathematical summary (e.g., "Checked 15,000 rows. 12 duplicates found. 0 missing values.") and a maximum of 3 representative exception examples. Never discard the remaining exceptions: write the full list (e.g. affected ISINs) to a file under out/ or the session scratchpad and cite its path. A skill's own report format takes precedence; the 3-example cap applies to ad-hoc output.
- **SQL in Loops:** PROHIBITED. Do not execute iterative single-row queries inside loops. Push joins and aggregations to the DB engine.

## 4. Governance & Precision Integrity
- **No Silent Truncation:** If a financial calculation, scoring metric, or reconciliation task requires an exhaustive population, you MUST NOT use `LIMIT` in the calculation itself. Execute the full calculation via SQL aggregates or a backend script, and return ONLY the final computed metric to the chat context.
- **Precision:** Preserve numeric precision, currency formats, and NULL semantics exactly as stored. Do not coerce types prematurely.
- **Immutability:** Assume read-only access for all analytical queries. Any destructive (`DROP`, `DELETE`) or mutating (`UPDATE`, `INSERT`) operation requires explicit human authorization unless executing an approved pipeline.

## 5. Definition of Done
A DB task is complete ONLY when:
1. The exact question is answered.
2. The output payload contains zero redundant tokens.
3. No join-multiplication (cartesian products) skewed the aggregates.
