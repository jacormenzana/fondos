# -*- coding: utf-8 -*-
"""
shared/schema_checks.py  — v18

Definición canónica de columnas esperadas en fund_master,
fund_kiid_metadata e ingestion_log.

Usado por:
  - run_block.py / export_metrics.py / _db_utils.py  (assert_schema_alignment)
  - pipeline validators

SQLite retirado (2026-09-26, FND-0102): se eliminaron los check_schema_v19..v26 (PRAGMA table_info).

Cambios v19:
  - fund_master: Ongoing_Charge → Ongoing_Charge_Recurrent + 11 nuevas v19.
  - EXPECTED_COLUMNS_V19: set canónico de 57 columnas.
  - check_schema_v19(): función de validación completa.
  - V19_FUND_MASTER_NEW + V19_FUND_MASTER_RENAMED.

Cambios v18:
  - fund_kiid_metadata: añadida DLA2_Table_Text (BL-DLA-2 Sub-fase 2B)
  - V18_KIID_META_NEW: constante con la columna nueva
  - assert_schema_alignment: mensaje actualizado a v18

Cambios v17:
  - fund_master: añadidas Investment_Focus, Credit_Quality, Fee_Known_Flag
  - V17_FUND_MASTER_NEW: constante con las 3 columnas nuevas
  - assert_schema_alignment: mensaje actualizado a v17
  - verify_db_schema: ahora también comprueba ingestion_log
"""


# ============================================================
# fund_master — columnas canónicas (v17)
# ============================================================
FUND_MASTER_COLUMNS: list[str] = [
    # Identificación
    "ISIN",
    "Fund_Name",
    "Management_Company",

    # Clasificación estructural
    "Fund_Nature",
    "Profile",
    "Type",
    "Strategy",
    "Family",
    "Style_Profile",
    "Subtype",

    # Exposición
    "Geography",
    "Theme",
    "Investment_Universe",
    "Investment_Focus",       # v17 NUEVO
    "Market_Cap_Focus",       # v16
    "Sector_Focus",           # v16
    "Credit_Quality",         # v17 NUEVO

    # Atributos cualitativos
    "Is_ESG",
    "Exposure_Bias",
    "Benchmark_Type",

    # Bloques heurísticos
    "Heuristic_Block",
    "Heuristic_Core",

    # SRRI y calidad
    "SRRI",
    "SRRI_Quality_Flag",
    "Data_Quality_Flag",

    # Divisa y cobertura
    "Fund_Currency",
    "Portfolio_Currency",
    "Hedging_Policy",
    "Currency_Hedged",        # v16

    # Política de inversión
    "Replication_Method",
    "Derivatives_Usage",
    "Benchmark_Declared",
    "Leverage_Used",

    # Costes y condiciones
    "Ongoing_Charge_Recurrent",  # v19: renombrado desde Ongoing_Charge
    "Entry_Fee_Pct",
    "Exit_Fee_Pct",
    "Fee_Known_Flag",         # v17 NUEVO
    "Accumulation_Policy",    # v16
    "Sfdr_Article",
    "Recommended_Holding_Period",
    "Liquidity_Profile",
    "Distribution_Frequency",

    # Fund family
    "fund_family_id",

    # Trazabilidad
    "Inference_Trace",
    "Created_At",
    "Updated_At",
    # v19 BL-COST-2: bloque coste PRIIPs/KID-aware (11 nuevas)
    "KID_Format",
    "KID_Currency",
    "Cost_Extraction_Quality",
    "Cost_RHP_Years",
    "Entry_Fee_Pct_Max",
    "Exit_Fee_Pct_Max",
    "Management_Fee_Pct",
    "Transaction_Cost_Pct",
    "Performance_Fee_Pct",
    "ACI_1Y",
    "ACI_RHP",
]

# ============================================================
# fund_kiid_metadata — columnas canónicas (v18)
# ============================================================
FUND_KIID_METADATA_COLUMNS: list[str] = [
    # Clave
    "ISIN",
    "KIID_Class",

    # Localización
    "KIID_URL",
    "KIID_PDF_Hash",
    "KIID_Status",

    # Contenido extraído
    "Language",
    "Raw_KIID_Text",
    "KIID_Published_Date",
    "KIID_Downloaded_At",

    # SRRI
    "SRRI",
    "SRRI_Visual",
    "SRRI_Textual",
    "SRRI_Validation_Status",

    # Telemetría de proceso (v16)
    "Processing_Time_Ms",
    "Processing_Breakdown",

    # DLA Fase 2 — tablas Cat.1+2 cacheadas (v18)
    "DLA2_Table_Text",

    # v20 (INTEGRATED_SPEC_v20_v2 §2B) — arbitración de coste DLA2 (12 nuevas)
    "Cost_Mgmt_BandsX",
    "Cost_Mgmt_Ruled",
    "Cost_Mgmt_Arbitration",
    "Cost_Oper_BandsX",
    "Cost_Oper_Ruled",
    "Cost_Oper_Arbitration",
    "Cost_ACI_RHP_BandsX",
    "Cost_ACI_RHP_Ruled",
    "Cost_ACI_RHP_Arbitration",
    "Cost_ACI_1Y_BandsX",
    "Cost_ACI_1Y_Ruled",
    "Cost_ACI_1Y_Arbitration",
]

# ============================================================
# ingestion_log — columnas canónicas (nombres exactos de producción)
# ============================================================
INGESTION_LOG_COLUMNS: list[str] = [
    "id",
    "ISIN",
    "step",
    "status",
    "message",
    "created_at",
]

# ============================================================
# fund_benchmarks — columnas canónicas (P1 v2; Phase 2 +benchmark_role)
# ============================================================
# Tabla secundaria poblada por benchmark_normalizer.py post-ingesta
# (fund_writer._upsert_kiid_benchmark). Hasta v20 no estaba cubierta por
# verify_db_schema → la adición de benchmark_role era invisible (R2). Ahora
# se valida para detectar drift (p.ej. migración no aplicada).
FUND_BENCHMARKS_COLUMNS: list[str] = [
    "ISIN",
    "source",
    "benchmark_raw",
    "benchmark_id",
    "benchmark_name",
    "provider",
    "asset_class",
    "confidence",
    "benchmark_role",     # Phase 2 BL-BENCH-ROLE: hurdle_rate / asset_proxy
    "extracted_at",
]

# ============================================================
# fund_data_quality_issues — columnas canónicas (v22, FIX-DQ-1)
# ============================================================
# Estado ACTUAL de issues de calidad de datos por fondo (uno por ISIN +
# check_code), reconstruida en cada ciclo de pipeline.py. `level` usa el
# vocabulario de Data_Quality_Flag (OK/INFERRED/WARN/MISSING), no el de
# ingestion_log.status -- ver DATA_QUALITY_SEVERITY en shared/config.py.
FUND_DATA_QUALITY_ISSUES_COLUMNS: list[str] = [
    "id",
    "ISIN",
    "check_code",
    "level",
    "message",
    "detected_at",
]

# ============================================================
# Conjuntos para lookup O(1)
# ============================================================
FUND_MASTER_COLUMNS_SET        = frozenset(FUND_MASTER_COLUMNS)
FUND_KIID_METADATA_COLUMNS_SET = frozenset(FUND_KIID_METADATA_COLUMNS)
INGESTION_LOG_COLUMNS_SET      = frozenset(INGESTION_LOG_COLUMNS)
FUND_BENCHMARKS_COLUMNS_SET    = frozenset(FUND_BENCHMARKS_COLUMNS)
FUND_DATA_QUALITY_ISSUES_COLUMNS_SET = frozenset(FUND_DATA_QUALITY_ISSUES_COLUMNS)

# ============================================================
# Columnas nuevas por versión de schema
# (usadas por los scripts migrate_schema_vXX.py)
# ============================================================
V16_FUND_MASTER_NEW: list[str] = [
    "Market_Cap_Focus",
    "Sector_Focus",
    "Currency_Hedged",
    "Investment_Universe",
]

V16_KIID_META_NEW: list[str] = [
    "Processing_Time_Ms",
    "Processing_Breakdown",
]

V17_FUND_MASTER_NEW: list[str] = [
    "Investment_Focus",
    "Credit_Quality",
    "Fee_Known_Flag",
]

V18_KIID_META_NEW: list[str] = [
    "DLA2_Table_Text",   # BL-DLA-2 Sub-fase 2B: tablas Cat.1+2 cacheadas
]

V19_FUND_MASTER_RENAMED: dict = {
    "Ongoing_Charge": "Ongoing_Charge_Recurrent",  # BL-COST-2: semántica TER puro
}

V19_FUND_MASTER_NEW: list[str] = [
    "KID_Format",
    "KID_Currency",
    "Cost_Extraction_Quality",
    "Cost_RHP_Years",
    "Entry_Fee_Pct_Max",
    "Exit_Fee_Pct_Max",
    "Management_Fee_Pct",
    "Transaction_Cost_Pct",
    "Performance_Fee_Pct",
    "ACI_1Y",
    "ACI_RHP",
]


# ============================================================
# EXPECTED_COLUMNS_V19 (canónico v19 — 57 columnas en fund_master)
# ============================================================
EXPECTED_COLUMNS_V19: frozenset = frozenset(FUND_MASTER_COLUMNS)
assert len(EXPECTED_COLUMNS_V19) == 57, f"v19 debe tener 57 columnas en fund_master, tiene {len(EXPECTED_COLUMNS_V19)}"


# ============================================================
# v20 (INTEGRATED_SPEC_v20_v2) — deltas de schema
# ------------------------------------------------------------
# Job B (DESPLEGABLE YA): 12 columnas de arbitración de coste en
#   fund_kiid_metadata (16 → 28). Migración aditiva (sin rebuild).
# Job A (PENDIENTE de "approved inventory"): rebuild de fund_master
#   (57 → 58): −4 DELETE, +5 CREATE, 14 MODIFY (remaps de valor a nivel de
#   dato). Los NOMBRES de columna están determinados (abajo); los value-sets
#   y la lógica del clasificador que los puebla NO obran en el repo y no se
#   inventan (R-1 / §D). EXPECTED_COLUMNS_V20 documenta el objetivo de 58.
# ============================================================

V20_KIID_META_NEW: list[str] = [
    "Cost_Mgmt_BandsX",
    "Cost_Mgmt_Ruled",
    "Cost_Mgmt_Arbitration",
    "Cost_Oper_BandsX",
    "Cost_Oper_Ruled",
    "Cost_Oper_Arbitration",
    "Cost_ACI_RHP_BandsX",
    "Cost_ACI_RHP_Ruled",
    "Cost_ACI_RHP_Arbitration",
    "Cost_ACI_1Y_BandsX",
    "Cost_ACI_1Y_Ruled",
    "Cost_ACI_1Y_Arbitration",
]

# Job A — fund_master v20 (nombres determinados; población pendiente)
V20_FUND_MASTER_DROP: list[str] = [
    "Subtype",
    "Portfolio_Currency",
    "Currency_Hedged",
    "Is_ESG",
]
V20_FUND_MASTER_NEW: list[str] = [
    "Development_Status",
    "Duration_Profile",
    "MMF_Structure",
    "Alt_Strategy",
    "Payoff_Profile",
]
# v3 §8-bis Q2: rename de columna (no cambia el conteo). Type se repropone a
# forma jurídico-estructural del vehículo; los valores los puebla el reprocess.
V20_FUND_MASTER_RENAME: dict[str, str] = {
    "Type": "Vehicle_Structure",
}

# Lista canónica objetivo de fund_master v20 (58 columnas) — derivada de la v19.
FUND_MASTER_COLUMNS_V20: list[str] = (
    [V20_FUND_MASTER_RENAME.get(c, c)
     for c in FUND_MASTER_COLUMNS if c not in set(V20_FUND_MASTER_DROP)]
    + V20_FUND_MASTER_NEW
)
EXPECTED_COLUMNS_V20: frozenset = frozenset(FUND_MASTER_COLUMNS_V20)
# Set canónico v20 consumido por verify_db_schema/assert_schema_alignment
# (el check en vivo de run_block.py). Sustituye a FUND_MASTER_COLUMNS_SET (v19)
# que aún incluía Type/Subtype/Currency_Hedged/Is_ESG/Portfolio_Currency.
FUND_MASTER_COLUMNS_V20_SET = EXPECTED_COLUMNS_V20
assert len(EXPECTED_COLUMNS_V20) == 58, (
    f"v20 debe tener 58 columnas en fund_master, tiene {len(EXPECTED_COLUMNS_V20)}"
)
# Metadata v20 = 22 columnas.
assert len(FUND_KIID_METADATA_COLUMNS) == 28, (
    f"v20 metadata debe tener 28 columnas, tiene {len(FUND_KIID_METADATA_COLUMNS)}"
)

# v21 (2026-07-05): Asset_Currency añadida -- divisa de los activos/
# estrategia del fondo (distinta de Fund_Currency = divisa de la clase de
# participación), inferida del nombre vía classify_utils.
# detect_asset_currency_from_name(). Consumida por BL-44-FX
# (pipeline.py) y disponible para P2. NO es un revival de la
# Portfolio_Currency eliminada en v20 (nombre distinto, fuente distinta --
# nombre del fondo, no texto KIID -- y Portfolio_Currency permanece
# correctamente bloqueada en V20_DELETED_ATTRIBUTES).
V21_FUND_MASTER_NEW: list[str] = ["Asset_Currency"]
FUND_MASTER_COLUMNS_V21: list[str] = FUND_MASTER_COLUMNS_V20 + V21_FUND_MASTER_NEW
EXPECTED_COLUMNS_V21: frozenset = frozenset(FUND_MASTER_COLUMNS_V21)
FUND_MASTER_COLUMNS_V21_SET = EXPECTED_COLUMNS_V21
assert len(EXPECTED_COLUMNS_V21) == 59, (
    f"v21 debe tener 59 columnas en fund_master, tiene {len(EXPECTED_COLUMNS_V21)}"
)

# v23 (2026-07-18): In_Current_Universe — universe-membership flag.
# Not a classification attribute; not COALESCE-protected; regenerated
# each cycle by reconcile_universe_membership() in fund_writer.py.
# (v22 added fund_data_quality_issues only — no new fund_master column.)
V23_FUND_MASTER_NEW: list[str] = ["In_Current_Universe"]
FUND_MASTER_COLUMNS_V23: list[str] = FUND_MASTER_COLUMNS_V21 + V23_FUND_MASTER_NEW
EXPECTED_COLUMNS_V23: frozenset = frozenset(FUND_MASTER_COLUMNS_V23)
FUND_MASTER_COLUMNS_V23_SET = EXPECTED_COLUMNS_V23
assert len(EXPECTED_COLUMNS_V23) == 60, (
    f"v23 debe tener 60 columnas en fund_master, tiene {len(EXPECTED_COLUMNS_V23)}"
)

# v24 (2026-07-18): fund_nav_monthly + fund_nav_daily incorporadas al schema
# canónico. fund_nav_daily es nueva: serie diaria para métricas de horizonte
# corto (rolling_1m / rolling_3m / rolling_6m, metric_version='d1').
# No hay nuevas columnas en fund_master.
FUND_NAV_DAILY_COLUMNS: list[str] = [
    "ISIN",
    "Date",
    "NAV",
    "NAV_Currency",
    "NAV_Type",
    "Is_Estimated",
    "Data_Source",
    "Ingested_At",
]
FUND_NAV_DAILY_COLUMNS_SET: frozenset = frozenset(FUND_NAV_DAILY_COLUMNS)

FUND_NAV_MONTHLY_COLUMNS: list[str] = [
    "ISIN",
    "Date",
    "NAV",
    "NAV_Currency",
    "NAV_Type",
    "Is_Estimated",
    "Data_Source",
    "Ingested_At",
]
FUND_NAV_MONTHLY_COLUMNS_SET: frozenset = frozenset(FUND_NAV_MONTHLY_COLUMNS)


# ============================================================
# fund_metrics / fund_metric_timeseries / p2_pipeline_log
# Columnas canónicas de auditoría v26 (Pillar 3 — algorithm_version + batch_id)
# ============================================================
FUND_METRICS_AUDIT_COLUMNS: list[str] = [
    "algorithm_version",
    "batch_id",
]
FUND_METRIC_TIMESERIES_AUDIT_COLUMNS: list[str] = [
    "algorithm_version",
    "batch_id",
    # v27 (2026-09-27): real_flag pivoted out of the key into these three columns (see
    # db/pg/30_gold.sql and scripts/ops/apply_fmts_pivot.py) — asserted here so a database that
    # hasn't been through the pivot migration (or a rolled-back one) fails loudly at startup
    # instead of metrics_writer.py's INSERT failing deep inside the P2 per-fund loop.
    "value_nominal",
    "value_real",
    "has_real",
]
P2_PIPELINE_LOG_AUDIT_COLUMNS: list[str] = [
    "batch_id",
]


# ============================================================
# verify_db_schema
# ============================================================
def _table_columns(conn, table: str) -> set[str]:
    """Column names for `table`, dialect-aware (Postgres migration Phase 5b).

    SQLite: PRAGMA table_info — unchanged, mixed-case names as declared.
    Postgres: information_schema.columns, unqualified by schema — table names
    are globally unique across bronze/silver/gold/control (migration plan
    §P2 "Namespaces"), so no schema lookup is needed. PG column names are
    lower_snake by the migration's rename_map, so callers must compare
    case-insensitively against this module's mixed-case canonical lists —
    see verify_db_schema().
    """
    # get_connection(backend="postgres") uses row_factory=dict_row (its own docstring:
    # "acceso por nombre de columna, NO por indice") — rows are dict-like, so this must
    # subscript by the SELECT alias, never positionally. Found live 2026-09-20: a first
    # version used r[0] here and raised KeyError(0) (str() == "0") on every table.
    cur = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table.lower(),),
    )
    return {r["column_name"] for r in cur.fetchall()}


def verify_db_schema(conn) -> dict[str, list[str]]:
    """
    Verifica que la BD tenga todas las columnas esperadas en las
    tres tablas canónicas.

    Devuelve dict {tabla: [columnas_faltantes]}.
    Dict vacío = schema alineado.

    Uso:
        missing = verify_db_schema(conn)
        assert not any(missing.values()), missing
    """
    missing: dict[str, list[str]] = {}

    checks = [
        ("fund_master",              FUND_MASTER_COLUMNS_V23_SET),
        ("fund_kiid_metadata",       FUND_KIID_METADATA_COLUMNS_SET),
        ("ingestion_log",            INGESTION_LOG_COLUMNS_SET),
        ("fund_benchmarks",          FUND_BENCHMARKS_COLUMNS_SET),
        ("fund_data_quality_issues", FUND_DATA_QUALITY_ISSUES_COLUMNS_SET),
        ("fund_nav_monthly",         FUND_NAV_MONTHLY_COLUMNS_SET),
        ("fund_nav_daily",           FUND_NAV_DAILY_COLUMNS_SET),
        # v26: audit columns on Gold tables (algorithm_version + batch_id)
        ("fund_metrics",             frozenset(FUND_METRICS_AUDIT_COLUMNS)),
        ("fund_metric_timeseries",   frozenset(FUND_METRIC_TIMESERIES_AUDIT_COLUMNS)),
        ("p2_pipeline_log",          frozenset(P2_PIPELINE_LOG_AUDIT_COLUMNS)),
    ]

    for table, expected in checks:
        try:
            existing = _table_columns(conn, table)
            # PG column names are lower_snake; compare case-insensitively there only —
            # SQLite path keeps its original exact-case comparison, unchanged.
            existing_cmp = {c.lower() for c in existing}
            expected_cmp = {c.lower() for c in expected}
            absent = sorted(expected_cmp - existing_cmp)
            if absent:
                missing[table] = absent
        except Exception as exc:
            missing[table] = [f"ERROR: {exc}"]

    return missing


# ============================================================
# assert_schema_alignment
# ============================================================
def assert_schema_alignment(conn) -> None:
    """
    Comprueba que fund_master, fund_kiid_metadata, ingestion_log,
    fund_benchmarks, fund_data_quality_issues, fund_nav_monthly y
    fund_nav_daily contienen todas las columnas definidas en este módulo
    (v25: fund_master = 60 cols; nav_sources.data_status añadida en v25
    para gestión del ciclo de vida de datos NAV; fund_nav_monthly +
    fund_nav_daily añadidas al schema canónico en v24).

    Lanza AssertionError con detalle si falta alguna columna.

    Uso típico en run_block.py:
        from shared.schema_checks import assert_schema_alignment
        assert_schema_alignment(conn)
    """
    missing = verify_db_schema(conn)
    if missing:
        lines = [
            f"Schema de BD desalineado con schema_checks.py (v{_schema_version()}):"
        ]
        for table, cols in missing.items():
            lines.append(f"  {table}: faltan {cols}")
        lines.append(
            "Aplica el DDL: db/pg/00_roles_schemas.sql .. 40_matviews.sql (con el rol propietario)"
        )
        raise AssertionError("\n".join(lines))


def _schema_version() -> str:
    """Versión del schema definida en este módulo."""
    try:
        from shared.config import SCHEMA_VERSION
        return SCHEMA_VERSION
    except ImportError:
        return "17"
