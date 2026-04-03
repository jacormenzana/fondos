# -*- coding: utf-8 -*-
"""
shared/schema_checks.py  — v17

Definición canónica de columnas esperadas en fund_master,
fund_kiid_metadata e ingestion_log.

Usado por:
  - init_db.py          (assert_schema_alignment)
  - run_block.py        (assert_schema_alignment)
  - migrate_schema_vXX  (listas V*_FUND_MASTER_NEW)
  - pipeline validators

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
    "Ongoing_Charge",
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
    "Updated_At",
]

# ============================================================
# fund_kiid_metadata — columnas canónicas (v16, sin cambio en v17)
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
# Conjuntos para lookup O(1)
# ============================================================
FUND_MASTER_COLUMNS_SET        = frozenset(FUND_MASTER_COLUMNS)
FUND_KIID_METADATA_COLUMNS_SET = frozenset(FUND_KIID_METADATA_COLUMNS)
INGESTION_LOG_COLUMNS_SET      = frozenset(INGESTION_LOG_COLUMNS)

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


# ============================================================
# verify_db_schema
# ============================================================
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
        ("fund_master",        FUND_MASTER_COLUMNS_SET),
        ("fund_kiid_metadata", FUND_KIID_METADATA_COLUMNS_SET),
        ("ingestion_log",      INGESTION_LOG_COLUMNS_SET),
    ]

    for table, expected in checks:
        try:
            existing = {
                r[1]
                for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            absent = sorted(expected - existing)
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
    Comprueba que fund_master, fund_kiid_metadata e ingestion_log
    contienen todas las columnas definidas en este módulo (v17).

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
            "Ejecuta: python scripts/mig/migrate_schema_v17.py --db db/fondos.sqlite"
        )
        raise AssertionError("\n".join(lines))


def _schema_version() -> str:
    """Versión del schema definida en este módulo."""
    try:
        from shared.config import SCHEMA_VERSION
        return SCHEMA_VERSION
    except ImportError:
        return "17"
