# -*- coding: utf-8 -*-
"""
shared/schema_checks.py  — v18

Definición canónica de columnas esperadas en fund_master,
fund_kiid_metadata e ingestion_log.

Usado por:
  - init_db.py          (assert_schema_alignment)
  - run_block.py        (assert_schema_alignment)
  - migrate_schema_vXX  (listas V*_FUND_MASTER_NEW)
  - pipeline validators

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

    # v20 (INTEGRATED_SPEC_v20_v2 §2B) — arbitración de coste DLA2 (6 nuevas)
    "Cost_Mgmt_BandsX",
    "Cost_Mgmt_Ruled",
    "Cost_Mgmt_Arbitration",
    "Cost_Oper_BandsX",
    "Cost_Oper_Ruled",
    "Cost_Oper_Arbitration",
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
# Job B (DESPLEGABLE YA): 6 columnas de arbitración de coste en
#   fund_kiid_metadata (16 → 22). Migración aditiva (sin rebuild).
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
assert len(FUND_KIID_METADATA_COLUMNS) == 22, (
    f"v20 metadata debe tener 22 columnas, tiene {len(FUND_KIID_METADATA_COLUMNS)}"
)


def check_schema_v20_job_b(conn) -> dict:
    """Valida la parte DESPLEGABLE de v20 (Job B): 6 columnas de coste en
    fund_kiid_metadata + la vista overall. No exige aún el rebuild de
    fund_master (Job A).

    Returns: {'ok': bool, 'issues': list[str]}
    """
    issues: list[str] = []
    cur = conn.execute("PRAGMA table_info(fund_kiid_metadata)")
    actual = {row[1] for row in cur.fetchall()}
    missing = [c for c in V20_KIID_META_NEW if c not in actual]
    if missing:
        issues.append(f"fund_kiid_metadata: faltan columnas v20 {missing}")

    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' "
        "AND name='v_cost_arbitration_overall'"
    )
    if not cur.fetchone():
        issues.append("Vista v_cost_arbitration_overall no existe (v20 Job B)")

    return {'ok': len(issues) == 0, 'issues': issues}


def check_schema_v20(conn) -> dict:
    """Valida v20 COMPLETO (Job A + Job B): fund_master 58 (4 drops ausentes,
    5 nuevas presentes) + metadata 22 + vista. Usar SOLO tras desplegar el
    rebuild de fund_master (Job A). Antes de eso usar check_schema_v20_job_b.
    """
    issues: list[str] = []

    cur = conn.execute("PRAGMA table_info(fund_master)")
    fm_actual = {row[1] for row in cur.fetchall()}
    drops_present = [c for c in V20_FUND_MASTER_DROP if c in fm_actual]
    new_missing   = [c for c in V20_FUND_MASTER_NEW if c not in fm_actual]
    if drops_present:
        issues.append(f"fund_master: columnas v20 que deberían estar borradas: {drops_present}")
    if new_missing:
        issues.append(f"fund_master: faltan columnas v20 nuevas: {new_missing}")
    # v3 §8-bis Q2: el rename Type→Vehicle_Structure debe haber aterrizado.
    for old, new in V20_FUND_MASTER_RENAME.items():
        if old in fm_actual:
            issues.append(f"fund_master: columna '{old}' debería renombrarse a '{new}'")
        if new not in fm_actual:
            issues.append(f"fund_master: falta columna renombrada '{new}'")
    if len(fm_actual) != 58:
        issues.append(f"fund_master: esperadas 58 columnas, hay {len(fm_actual)}")

    jb = check_schema_v20_job_b(conn)
    issues += jb['issues']

    return {'ok': len(issues) == 0, 'issues': issues}


def check_schema_v19(conn) -> dict:
    """
    Valida que la BD esté en schema v19.

    Returns:
        {'ok': bool, 'issues': list[str]}
    """
    issues = []
    cur = conn.execute("PRAGMA table_info(fund_master)")
    actual = {row[1] for row in cur.fetchall()}

    missing = EXPECTED_COLUMNS_V19 - actual
    extra   = actual - EXPECTED_COLUMNS_V19
    if missing:
        issues.append(f"Columnas faltantes en fund_master: {sorted(missing)}")
    if extra:
        issues.append(f"Columnas inesperadas en fund_master: {sorted(extra)}")
    if "Ongoing_Charge" in actual:
        issues.append(
            "Ongoing_Charge debería estar renombrada a "
            "Ongoing_Charge_Recurrent (v19)"
        )

    # fund_cost_schedule
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='fund_cost_schedule'"
    )
    if not cur.fetchone():
        issues.append("Tabla fund_cost_schedule no existe (v19)")

    # Índices
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name IN ('idx_cost_schedule_isin', 'idx_cost_schedule_rhp')"
    )
    n_idx = len(cur.fetchall())
    if n_idx != 2:
        issues.append(f"Faltan índices fund_cost_schedule (esperado 2, hay {n_idx})")

    return {'ok': len(issues) == 0, 'issues': issues}


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
        ("fund_master",        FUND_MASTER_COLUMNS_V20_SET),
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
    contienen todas las columnas definidas en este módulo (v20:
    fund_master = 58 cols con Vehicle_Structure; sin Type/Subtype/
    Currency_Hedged/Is_ESG/Portfolio_Currency; metadata = 22).

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
            "Ejecuta: python -m shared.init_db"
        )
        raise AssertionError("\n".join(lines))


def _schema_version() -> str:
    """Versión del schema definida en este módulo."""
    try:
        from shared.config import SCHEMA_VERSION
        return SCHEMA_VERSION
    except ImportError:
        return "17"
