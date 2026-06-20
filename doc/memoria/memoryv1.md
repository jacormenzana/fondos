Purpose & context
Jose is building a Spanish-language investment fund analysis platform (C:\desarrollo\fondos\) processing ~3,200 European UCITS/OICVM funds across three phases: P1 (fund discovery and classification), P2 (quantitative enrichment with macro factors), and P3 (regime-based portfolio selection and scoring). The platform targets capital preservation relative to IPC+M3 at ~6-7% nominal annual return with a maximum tolerable drawdown of 15% over a 3-5 year horizon.
The core database is SQLite at db/fondos.sqlite, running on Windows in a Conda environment named "des." The project uses a modular Python architecture with proyecto1/core/ modules, proyecto1/blocks/ classification blocks, and shared/ configuration. Key modules include pipeline.py, classify_utils.py, sqlite_writer.py, io.py, kiid_parser.py, dla_extractor.py, and specialized blocks (monetarios.py, rf_corto.py, rf_flexible.py, renta_variable.py, mixtos.py, alternativos.py, restantes.py).
Current state
The active workstream is BL-DLA-2 (also referred to as BL-COST), extracting PRIIPs Cat.2A cost table data (Ongoing Charge = management cost + operation cost) from ~3,205 fund KIIDs into the SQLite database. The current focus is on a dual-extractor architecture using pdfplumber:

bands-X strategy: for borderless/plain-text layouts
ruled-table strategy: for bordered/ruled table layouts (e.g., DWS family ~425 funds)
Arbitration by result quality (not fixed priority) — validated empirically as the correct architecture after Jose correctly predicted that a "ruled-first" approach would regress borderless funds

Recent corpus runs (3,205 PDFs each) have progressively reduced CONFLICT counts. A bidirectional rescue fix (v1.4) was pending a corpus run at the last session end, expected to yield ~6-10 conflicts.
A separate BD remediation workstream has been identified: BD.Ongoing_Charge_Recurrent column is systematically wrong on ~600+ funds (0/15 correct on ground-truth verification) and must never be used as ground truth.
On the horizon

Complete corpus validation of v1.4 bidirectional rescue fix
BD Ongoing_Charge_Recurrent remediation workstream
BL-COST-5: OC/ACI mismatch remediation for ~328 funds where Ongoing_Charge_Recurrent appears to be ACI@RHP mislabeled as TER — blocked pending dedicated Opus architectural review
BL-DLA-3: extraction of Cat.3 (PRIIPs performance scenarios) — blocked pending BL-DLA-2 closure and P2/P3 consumer documentation
P2 macro factor work: spread_hy (BAMLH0A0HYM2), vix (VIXCLS), term_spread (T10Y2YM) remaining for ingestion; OLS regression derivatives still pending
P3 (regime-dependent scoring) remains blocked pending P1 classification quality fixes
migrate_v18_to_v19.py needs correction to also rebuild FK-dependent tables during migration

Key learnings & principles
Architecture & design:

Normalization logic must live exclusively in classify_utils.py (R-1 — single source of truth)
Any modification to a persisted attribute requires pipeline fix + SQL migration + optional COALESCE change (R-2)
INTER rules must use effective values (_X_p or _X_bd), not just current in-memory record values (R-4) — this was the root cause of BL-44 persisting undetected
\b word boundaries fail on fused letter sequences in fund names (e.g., EURHDG) — use explicit character class boundaries (R-5)
Inference functions must use bounded windows, not full-text checks (R-6)
SQL migrations must use LIKE or TRIM-based comparisons, not exact matches, to handle padding in production databases (R-9)
COALESCE in sqlite_writer.publish_fund() preserves stale values for CACHED funds — architectural fix requires EffectiveReader pattern or explicit DB fallback reads

Cost extraction specific:

Small operation cost values (0.02%, 0.07%, 0.22%) are real industry-standard transaction cost ranges — do not dismiss as suspicious artifacts
BD is unreliable on 600+ funds; never use BD as a tiebreaker or ground truth for cost data
PDF inspection is the only valid evidence for cost extraction decisions
Never declare "100% solved" on small ground-truth samples without corpus-scale validation

Workflow:

Test before integrating architecture changes
Responses should be direct and executive with minimal verbosity to reduce token consumption
Always verify actual file content (e.g., via grep on specific changed lines) before declaring any delivery complete — never rely on version headers alone
Read files completely before touching them; apply surgical str_replace edits, not full rewrites
Run AST validation after every file write
Multi-line Python -c commands cannot be executed in Jose's terminal — must be provided as single lines
Python commands on Windows must be delivered as single lines for execution
sys.path for scripts in proyecto1\ must use parents[2] to reach the project root; proyecto1\core\ must be explicitly added when core modules import each other without the core. prefix

Session management:

Use Opus (Nivel-3) for architectural design and closing ambiguities; use Sonnet (Nivel-2) for implementation against closed specs
Generate handoff documents at session end for context continuity
Backlog documents must be fully self-contained (autocontención) — not incremental deltas referencing prior versions
Conversation titles prefixed with CERRADO_ indicate closed/completed sprint sessions

Approach & patterns

Empirical verification over explanation: Jose consistently pushes back on hypotheses until grounded in telemetry, PDF inspection, or SQL control queries — not speculation
Surgical edits: changes are scoped to the minimum necessary; full rewrites are avoided
Root cause before symptoms: architectural defects require architectural fixes, not SQL patches (with narrow exception of bypass-COALESCE UPDATEs when architecturally justified)
Binary-verifiable completion criteria: each sprint task has explicit, measurable pass/fail checks
Post-cycle SQL verification: standard procedure after every pipeline execution; control queries are provided with each implementation
Kill-switch feature flags: all new feature blocks use kill-switch constants in config.py (e.g., PRIIPS_COST_EXTRACTION_ENABLED)
DRY enforcement: no code duplication; violations are called out explicitly and corrected
Explicit column-by-column SQL writes: never dynamic dict dumps to the database
Defensive error handling: errors must never degrade pipeline output

Tools & resources

Languages/runtime: Python (Conda env des), Windows, python -X utf8 flag for all invocations
Database: SQLite at C:\desarrollo\fondos\db\fondos.sqlite (schema v19+); shared/db.py as single-source get_connection() with WAL mode
PDF extraction: pdfplumber (primary, with lines strategy for ruled tables), PyMuPDF/fitz (DLA extractor), Tesseract OCR (fallback)
Key data files: KIIDs stored at C:\data\fondos\kiid\; diagnostic scripts in scripts\diag\
External data: FRED API for macro series; Morningstar SAL service (api-global.morningstar.com/sal-service/v1/fund/performance/v4/) with SecuritySearch.ashx for ISIN→code resolution
Launch: pipeline run via C:\desarrollo\fondos\scripts\launch\ wrapper .bat (not run_block.py directly for ful