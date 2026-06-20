# =============================================================
# PATCHES BL-49 / BL-50 / BL-53/56 / BL-57 — pipeline.py
# =============================================================
#
# Aplicar como str_replace en /core/pipeline.py.
# Cada bloque tiene OLD (texto exacto a buscar) y NEW (texto a sustituir).
# Validar con `python -c "import ast; ast.parse(open('pipeline.py').read())"`
# después de cada parche.
#
# Orden de aplicación (estricto): P1 → P2 → P3 → P4 → P5.
# =============================================================


# =============================================================
# PATCH P1 — Import de EffectiveReader y barrido global
# =============================================================
# Localización: bloque de imports (líneas ~73)
# Añadir DESPUÉS de `from core.sqlite_writer import publish_fund, log_ingestion`

OLD_P1 = """from core.sqlite_writer import publish_fund, log_ingestion

#print("[DEBUG] Cargo pipeline.py")        """

NEW_P1 = """from core.sqlite_writer import (
    publish_fund,
    log_ingestion,
    global_post_pipeline_normalize_db,   # BL-53/56/57: barrido global
)
from core._db_utils import EffectiveReader   # BL-49/50: lectura efectiva

#print("[DEBUG] Cargo pipeline.py")        """


# =============================================================
# PATCH P2 — Instanciar EffectiveReader al inicio del bucle por fondo
# =============================================================
# Localización: justo después de la línea 321
#   `kiid_text = kiid_meta = parsed = classification = pdf_bytes = None`
# Insertar la creación del reader, accesible en TODO el cuerpo del try.

OLD_P2 = """            kiid_text = kiid_meta = parsed = classification = pdf_bytes = None

        try:
            row = df_master[df_master[\"ISIN\"] == isin]"""

NEW_P2 = """            kiid_text = kiid_meta = parsed = classification = pdf_bytes = None

        # BL-49/50: lector de valor efectivo (dict ciclo > BD > None) con
        # caché por ISIN. Centraliza el patrón de fallback a BD para todas
        # las reglas INTER posteriores. Una sola SELECT por fondo (lazy).
        eff = EffectiveReader(conn, isin)

        try:
            row = df_master[df_master[\"ISIN\"] == isin]"""


# =============================================================
# PATCH P3 — BL-50: P04 con fallback a BD vía _eff()
# =============================================================
# Localización: bloque P04 que infiere Investment_Universe desde Geography
# (líneas ~686-704). Cambiar la lectura de Geography para usar eff.get().

OLD_P3 = """            # Investment_Universe: inferir desde Geography si NULL (P04)
            # BL-50: catálogos ampliados para cobertura bidireccional completa.
            if not fund_master_record.get(\"Investment_Universe\"):
                _geo = fund_master_record.get(\"Geography\")
                _nat = fund_master_record.get(\"Fund_Nature\")
                if _nat in (\"Monetario\", \"Renta Fija Corto Plazo\"):
                    fund_master_record[\"Investment_Universe\"] = \"Liquidity\"
                elif _geo in (\"EEUU\", \"China\", \"Japón\", \"India\", \"Brasil\",
                              \"Corea del Sur\", \"Australia\", \"Canadá\",
                              \"México\", \"Rusia\", \"Italia\", \"Alemania\",
                              \"Francia\", \"España\", \"Reino Unido\", \"Suiza\"):
                    fund_master_record[\"Investment_Universe\"] = \"Country\"
                elif _geo in (\"Europa\", \"Asia\", \"Emergentes\",
                              \"Latinoamérica\", \"Europa del Este\",
                              \"Asia Pacífico\", \"Oriente Medio\", \"África\",
                              \"Europa Central\", \"América del Norte\"):
                    fund_master_record[\"Investment_Universe\"] = \"Regional\"
                elif _geo == \"Global\":
                    fund_master_record[\"Investment_Universe\"] = \"Global\""""

NEW_P3 = """            # Investment_Universe: inferir desde Geography si NULL (P04)
            # BL-50: catálogos ampliados para cobertura bidireccional completa.
            # BL-50/2: fallback a BD vía eff.get() — antes leía solo el dict
            # del ciclo, perdiendo Geography preservada por COALESCE para
            # fondos CACHED. Resolvía 7 casos confirmados (5×EEUU + 2×Asia).
            if not fund_master_record.get(\"Investment_Universe\"):
                _geo = eff.get(\"Geography\", fund_master_record)
                _nat = fund_master_record.get(\"Fund_Nature\")
                if _nat in (\"Monetario\", \"Renta Fija Corto Plazo\"):
                    fund_master_record[\"Investment_Universe\"] = \"Liquidity\"
                elif _geo in (\"EEUU\", \"China\", \"Japón\", \"India\", \"Brasil\",
                              \"Corea del Sur\", \"Australia\", \"Canadá\",
                              \"México\", \"Rusia\", \"Italia\", \"Alemania\",
                              \"Francia\", \"España\", \"Reino Unido\", \"Suiza\"):
                    fund_master_record[\"Investment_Universe\"] = \"Country\"
                elif _geo in (\"Europa\", \"Asia\", \"Emergentes\",
                              \"Latinoamérica\", \"Europa del Este\",
                              \"Asia Pacífico\", \"Oriente Medio\", \"África\",
                              \"Europa Central\", \"América del Norte\"):
                    fund_master_record[\"Investment_Universe\"] = \"Regional\"
                elif _geo == \"Global\":
                    fund_master_record[\"Investment_Universe\"] = \"Global\""""


# =============================================================
# PATCH P4 — BL-49: propagación recíproca HP → CH
# =============================================================
# Localización: tras el bloque BL-45 (líneas ~981-991), añadir BL-49/3.

OLD_P4 = """            # BL-45 v24: Hedging_Policy inferida desde Currency_Hedged cuando HP=NULL
            # Si Currency_Hedged está poblado pero Hedging_Policy es NULL, son
            # semánticamente equivalentes → propagar el valor (199 fondos).
            # Se ejecuta tras BL-31 para usar los valores ya validados (_ch_p/_hp_p).
            # Solo actúa si Hedging_Policy sigue NULL tras BL-31.
            if not (fund_master_record.get(\"Hedging_Policy\") or _hp_bd):
                _ch_eff = fund_master_record.get(\"Currency_Hedged\") or _ch_bd
                if _ch_eff == \"Hedged\":
                    fund_master_record[\"Hedging_Policy\"] = \"HEDGED\"
                elif _ch_eff == \"Unhedged\":
                    fund_master_record[\"Hedging_Policy\"] = \"UNHEDGED\""""

NEW_P4 = """            # BL-45 v24: Hedging_Policy inferida desde Currency_Hedged cuando HP=NULL
            # Si Currency_Hedged está poblado pero Hedging_Policy es NULL, son
            # semánticamente equivalentes → propagar el valor (199 fondos).
            # Se ejecuta tras BL-31 para usar los valores ya validados (_ch_p/_hp_p).
            # Solo actúa si Hedging_Policy sigue NULL tras BL-31.
            if not (fund_master_record.get(\"Hedging_Policy\") or _hp_bd):
                _ch_eff = fund_master_record.get(\"Currency_Hedged\") or _ch_bd
                if _ch_eff == \"Hedged\":
                    fund_master_record[\"Hedging_Policy\"] = \"HEDGED\"
                elif _ch_eff == \"Unhedged\":
                    fund_master_record[\"Hedging_Policy\"] = \"UNHEDGED\"

            # BL-49/3: propagación inversa HP → CH cuando CH=NULL pero HP poblado.
            # Causa raíz previa: BL-31 solo dispara con AMBOS poblados; BL-45
            # solo cubre CH→HP. Faltaba la simetría HP→CH. Resuelve los 29
            # fondos del export con HP poblado y CH=NULL.
            if not (fund_master_record.get(\"Currency_Hedged\") or _ch_bd):
                _hp_eff_b49 = fund_master_record.get(\"Hedging_Policy\") or _hp_bd
                if _hp_eff_b49 == \"HEDGED\":
                    fund_master_record[\"Currency_Hedged\"] = \"Hedged\"
                elif _hp_eff_b49 == \"UNHEDGED\":
                    fund_master_record[\"Currency_Hedged\"] = \"Unhedged\""""


# =============================================================
# PATCH P5 — BL-53/56/57: Barrido global post-pipeline
# =============================================================
# Localización: función run_block() retorna `published`. Tras retornar de
# todos los bloques, ejecutar normalización global. Esta función se invoca
# desde el orquestador (ej: run_pipeline.py o similar). Aquí se añade un
# helper en pipeline.py que el orquestador invoca DESPUÉS de procesar todos
# los bloques.

OLD_P5 = """            kiid_text = kiid_meta = parsed = classification = pdf_bytes = None
            gc.collect()

    return published
"""

NEW_P5 = """            kiid_text = kiid_meta = parsed = classification = pdf_bytes = None
            gc.collect()

    return published


# =============================================================
# BL-53/56/57: Barrido global post-pipeline (Principio #1 + #2)
# =============================================================
#
# Causa raíz arquitectónica:
#   _post_upsert_normalize_db() en sqlite_writer.py opera sobre el ISIN
#   recién upserted (WHERE ISIN=?). Los fondos no procesados en el ciclo
#   (KIID_Status=WRONG_DOC, sin bloque que los recoja, errores) conservan
#   indefinidamente sus valores stale en inglés en BD.
#
# Solución: tras procesar TODOS los bloques del ciclo, ejecutar UNA query
# global sin filtro de ISIN que normaliza Sector_Focus/Type/Subtype y
# traduce Family. Idempotente; coste ~150ms sobre 3.204 filas.
#
# El orquestador (run_pipeline.py o cmd) debe invocar:
#   from core.pipeline import run_global_normalization
#   run_global_normalization(conn)
# tras el último run_block() y antes del cierre de la BD.
# =============================================================

def run_global_normalization(conn) -> dict:
    \"\"\"
    Aplica normalización lingüística global sobre fund_master.

    Cubre los fondos que no entraron en ningún bloque del ciclo y que por
    tanto no pasaron por _post_upsert_normalize_db (filtrado por ISIN).

    Returns:
        dict con métricas de filas afectadas para logging/auditoría.
    \"\"\"
    metrics = global_post_pipeline_normalize_db(conn)
    print(f\"[GLOBAL_NORM] {metrics}\")
    return metrics
"""


# =============================================================
# RESUMEN DE APLICACIÓN
# =============================================================
# Total parches: 5
# Líneas de código modificadas: ~80
# Imports nuevos: 2 (EffectiveReader, global_post_pipeline_normalize_db)
# Funciones nuevas en pipeline.py: 1 (run_global_normalization)
#
# El orquestador externo (run_pipeline.py / cmd) DEBE actualizarse para
# invocar run_global_normalization(conn) tras el último run_block().
# Sin esa invocación, el barrido global no se ejecuta y el preventivo
# arquitectónico de BL-53/56/57 queda inactivo.
#
# Validación post-aplicación:
#   python -c "import ast; ast.parse(open('proyecto1/core/pipeline.py').read()); print('AST OK')"
# =============================================================
