# -*- coding: utf-8 -*-
"""
shared/schema_checks.py  — v16
Definición canónica de columnas esperadas en fund_master y fund_kiid_metadata.
Usado por validators.py y pipeline para verificar consistencia antes del commit.
"""

# ============================================================
# fund_master — columnas canónicas (v16)
# ============================================================
FUND_MASTER_COLUMNS: list[str] = [
    # Identificación
    "ISIN",
    "Fund_Name",
    "Management_Company",

    # Clasificación P1
    "Fund_Nature",
    "Profile",
    "Type",
    "Strategy",
    "Family",
    "Style_Profile",
    "Geography",
    "Theme",
    "Is_ESG",
    "Exposure_Bias",
    "Benchmark_Type",
    "Subtype",

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

    # Política de inversión
    "Replication_Method",
    "Derivatives_Usage",
    "Benchmark_Declared",

    # Costes y condiciones
    "Ongoing_Charge",
    "Accumulation_Policy",
    "Entry_Fee_Pct",
    "Exit_Fee_Pct",
    "Sfdr_Article",
    "Recommended_Holding_Period",
    "Leverage_Used",
    "Liquidity_Profile",
    "Distribution_Frequency",

    # Fund family
    "fund_family_id",

    # Trazabilidad
    "Inference_Trace",
    "Updated_At",

    # Atributos v3 — fund_characterizer (v16)
    "Market_Cap_Focus",
    "Sector_Focus",
    "Currency_Hedged",
    "Investment_Universe",
]

# ============================================================
# fund_kiid_metadata — columnas canónicas (v16)
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

    # SRRI (v12)
    "SRRI",
    "SRRI_Visual",
    "SRRI_Textual",
    "SRRI_Validation_Status",

    # Telemetría de proceso (v16)
    "Processing_Time_Ms",
    "Processing_Breakdown",
]

# ============================================================
# ingestion_log — columnas canónicas (producción)
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
FUND_MASTER_COLUMNS_SET         = frozenset(FUND_MASTER_COLUMNS)
FUND_KIID_METADATA_COLUMNS_SET  = frozenset(FUND_KIID_METADATA_COLUMNS)
INGESTION_LOG_COLUMNS_SET       = frozenset(INGESTION_LOG_COLUMNS)

# ============================================================
# Columnas nuevas en v16 (para migrate_schema_v16.py)
# ============================================================
V16_FUND_MASTER_NEW = [
    "Market_Cap_Focus",
    "Sector_Focus",
    "Currency_Hedged",
    "Investment_Universe",
]

V16_KIID_META_NEW = [
    "Processing_Time_Ms",
    "Processing_Breakdown",
]


def verify_db_schema(conn) -> dict:
    """
    Verifica que la BD tenga todas las columnas esperadas.
    Devuelve dict con listas de columnas faltantes por tabla.
    Uso: result = verify_db_schema(conn); assert not any(result.values())
    """
    missing = {}

    for table, expected in [
        ("fund_master",        FUND_MASTER_COLUMNS_SET),
        ("fund_kiid_metadata", FUND_KIID_METADATA_COLUMNS_SET),
    ]:
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            absent   = sorted(expected - existing)
            if absent:
                missing[table] = absent
        except Exception as e:
            missing[table] = [f"ERROR: {e}"]

    return missing


# ============================================================
# assert_schema_alignment  (importada por run_block.py)
# Verifica que la BD tenga todas las columnas esperadas.
# Lanza AssertionError con detalle si falta alguna columna.
# ============================================================

def assert_schema_alignment(conn) -> None:
    """
    Comprueba que fund_master y fund_kiid_metadata en la BD
    contienen todas las columnas definidas en este módulo.

    Lanza AssertionError si falta alguna columna, con el
    detalle de qué tabla y qué columnas están ausentes.

    Uso típico en run_block.py:
        from shared.schema_checks import assert_schema_alignment
        assert_schema_alignment(conn)
    """
    missing = verify_db_schema(conn)
    if missing:
        lines = ["Schema de BD desalineado con schema_checks.py:"]
        for table, cols in missing.items():
            lines.append(f"  {table}: faltan {cols}")
        lines.append(
            "Ejecuta: python scripts/migrate_schema_v16.py"
        )
        raise AssertionError("\n".join(lines))
