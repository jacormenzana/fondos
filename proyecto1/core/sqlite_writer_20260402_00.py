# core/sqlite_writer.py
# -*- coding: utf-8 -*-
"""
Publicación idempotente del output estructural de Proyecto 1 en SQLite.
"""

import sqlite3
from pathlib import Path
from typing import Dict, Iterable, Optional, Any
import datetime

# Importación diferida para evitar dependencia circular en arranque
def _get_normalizer():
    try:
        from core.benchmark_normalizer import normalize_benchmark
        return normalize_benchmark
    except Exception:
        return None

# ============================================================
# ESQUEMA SQLITE
# Lee el schema canónico desde db/schema_fondos.sql.
# Un único punto de verdad para P1, P2 y P3.
# ============================================================

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema_fondos.sql"


def _load_schema_sql() -> str:
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema canónico no encontrado: {_SCHEMA_PATH}\n"
            "Asegúrate de que db/schema_fondos.sql existe en la raíz del proyecto."
        )
    return _SCHEMA_PATH.read_text(encoding="utf-8")



# ============================================================
# CONEXIÓN
# ============================================================

def get_connection(sqlite_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(sqlite_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Crea todas las tablas del sistema leyendo db/schema_fondos.sql."""
    conn.executescript(_load_schema_sql())
    conn.commit()


def close_connection(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.close()


# ============================================================
# HELPERS
# ============================================================

def _safe_int(value: Optional[Any]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


# ============================================================
# FUND MASTER UPSERT
# ============================================================

def upsert_fund_master(conn: sqlite3.Connection, record: Dict[str, Optional[Any]]) -> None:
    """
    Upsert canónico en fund_master.
    """

    sql = """
    INSERT INTO fund_master (
        ISIN,
        Fund_Name,
        Management_Company,
        Fund_Nature,
        Profile,
        Type,
        Strategy,
        Family,
        Style_Profile,
        Geography,
        Theme,
        Is_ESG,
        Exposure_Bias,
        Benchmark_Type,
        Subtype,
        Market_Cap_Focus,
        Sector_Focus,
        Currency_Hedged,
        Investment_Universe,
        Accumulation_Policy,
        Heuristic_Block,
        Heuristic_Core,
        SRRI,
        Fund_Currency,
        Portfolio_Currency,
        Hedging_Policy,
        Replication_Method,
        Derivatives_Usage,
        Benchmark_Declared,
        Ongoing_Charge,
        Entry_Fee_Pct,
        Exit_Fee_Pct,
        Sfdr_Article,
        Recommended_Holding_Period,
        Leverage_Used,
        Liquidity_Profile,
        Distribution_Frequency,
        fund_family_id,
        Inference_Trace,
        SRRI_Quality_Flag,
        Data_Quality_Flag,
        Updated_At
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ISIN) DO UPDATE SET
        Fund_Name = excluded.Fund_Name,
        Management_Company = excluded.Management_Company,
        Fund_Nature = excluded.Fund_Nature,
        Profile = excluded.Profile,
        Type = excluded.Type,
        Strategy = excluded.Strategy,
        Family = excluded.Family,
        Style_Profile = excluded.Style_Profile,
        Geography = excluded.Geography,
        Theme = excluded.Theme,
        Is_ESG = excluded.Is_ESG,
        Exposure_Bias = excluded.Exposure_Bias,
        Benchmark_Type = excluded.Benchmark_Type,
        Subtype = excluded.Subtype,
        Market_Cap_Focus    = COALESCE(excluded.Market_Cap_Focus,    fund_master.Market_Cap_Focus),
        Sector_Focus        = COALESCE(excluded.Sector_Focus,        fund_master.Sector_Focus),
        Currency_Hedged     = COALESCE(excluded.Currency_Hedged,     fund_master.Currency_Hedged),
        Investment_Universe = COALESCE(excluded.Investment_Universe, fund_master.Investment_Universe),
        Accumulation_Policy = COALESCE(excluded.Accumulation_Policy, fund_master.Accumulation_Policy),
        Heuristic_Block = excluded.Heuristic_Block,
        Heuristic_Core = excluded.Heuristic_Core,
        SRRI = CASE
                   WHEN excluded.SRRI_Quality_Flag IS NOT NULL
                    AND excluded.SRRI_Quality_Flag != 'NONE'
                   THEN excluded.SRRI          -- parser extrajo algo → nuevo valor
                   ELSE fund_master.SRRI       -- no extraído → preservar existente
               END,
        -- Atributos extraídos del texto: preservar valor existente si el nuevo es NULL
        -- Un ciclo CACHED puede no re-extraer todos los atributos
        Fund_Currency              = COALESCE(excluded.Fund_Currency, fund_master.Fund_Currency),
        Portfolio_Currency         = COALESCE(excluded.Portfolio_Currency, fund_master.Portfolio_Currency),
        Hedging_Policy             = COALESCE(excluded.Hedging_Policy, fund_master.Hedging_Policy),
        Replication_Method         = COALESCE(excluded.Replication_Method, fund_master.Replication_Method),
        Derivatives_Usage          = COALESCE(excluded.Derivatives_Usage, fund_master.Derivatives_Usage),
        Benchmark_Declared         = COALESCE(excluded.Benchmark_Declared, fund_master.Benchmark_Declared),
        Ongoing_Charge             = COALESCE(excluded.Ongoing_Charge, fund_master.Ongoing_Charge),
        Accumulation_Policy        = COALESCE(excluded.Accumulation_Policy, fund_master.Accumulation_Policy),
        Entry_Fee_Pct              = COALESCE(excluded.Entry_Fee_Pct, fund_master.Entry_Fee_Pct),
        Exit_Fee_Pct               = COALESCE(excluded.Exit_Fee_Pct, fund_master.Exit_Fee_Pct),
        Sfdr_Article               = COALESCE(excluded.Sfdr_Article, fund_master.Sfdr_Article),
        Recommended_Holding_Period = COALESCE(excluded.Recommended_Holding_Period, fund_master.Recommended_Holding_Period),
        Leverage_Used              = COALESCE(excluded.Leverage_Used, fund_master.Leverage_Used),
        Liquidity_Profile          = COALESCE(excluded.Liquidity_Profile, fund_master.Liquidity_Profile),
        Distribution_Frequency = excluded.Distribution_Frequency,
        Inference_Trace = excluded.Inference_Trace,
        SRRI_Quality_Flag = CASE
                   WHEN excluded.SRRI_Quality_Flag IS NOT NULL
                    AND excluded.SRRI_Quality_Flag != 'NONE'
                   THEN excluded.SRRI_Quality_Flag
                   ELSE fund_master.SRRI_Quality_Flag
               END,
        Data_Quality_Flag = excluded.Data_Quality_Flag,
        Updated_At = excluded.Updated_At
    ;
    """

    now = datetime.datetime.utcnow().isoformat(timespec="seconds")

    params = (
        record["ISIN"],
        record.get("Fund_Name"),
        record.get("Management_Company"),
        record.get("Fund_Nature"),
        record.get("Profile"),
        record.get("Type"),
        record.get("Strategy"),
        record.get("Family"),
        record.get("Style_Profile"),
        record.get("Geography"),
        record.get("Theme"),
        int(record.get("Is_ESG", 0)),
        record.get("Exposure_Bias"),
        record.get("Benchmark_Type"),
        record.get("Subtype"),
        record.get("Market_Cap_Focus"),
        record.get("Sector_Focus"),
        record.get("Currency_Hedged"),
        record.get("Investment_Universe"),
        record.get("Accumulation_Policy"),
        record.get("Heuristic_Block"),
        int(record.get("Heuristic_Core", 0)),
        _safe_int(record.get("SRRI")),
        record.get("Fund_Currency"),
        record.get("Portfolio_Currency"),
        record.get("Hedging_Policy"),
        record.get("Replication_Method"),
        record.get("Derivatives_Usage"),
        record.get("Benchmark_Declared"),
        record.get("Ongoing_Charge"),
        record.get("Entry_Fee_Pct"),
        record.get("Exit_Fee_Pct"),
        record.get("Sfdr_Article"),
        record.get("Recommended_Holding_Period"),
        record.get("Leverage_Used"),
        record.get("Liquidity_Profile"),
        record.get("Distribution_Frequency"),
        record.get("fund_family_id"),
        record.get("Inference_Trace"),
        record.get("SRRI_Quality_Flag"),
        record.get("Data_Quality_Flag"),
        now
    )

    conn.execute(sql, params)


# ============================================================
# KIID METADATA UPSERT
# ============================================================

def upsert_kiid_metadata(conn: sqlite3.Connection, kiid_record: Dict[str, Optional[Any]]) -> None:
    """
    UPSERT extendido con validación cruzada SRRI.

    Usa INSERT ... ON CONFLICT DO UPDATE en lugar de INSERT OR REPLACE para
    preservar Raw_KIID_Text existente cuando el nuevo valor es NULL.
    INSERT OR REPLACE hace DELETE+INSERT y borraría el texto si el campo
    viene vacío en algún ciclo de re-ingesta.
    """

    sql = """
    INSERT INTO fund_kiid_metadata (
        ISIN,
        KIID_Class,
        KIID_URL,
        KIID_PDF_Hash,
        KIID_Status,
        Language,
        Raw_KIID_Text,
        KIID_Published_Date,
        KIID_Downloaded_At,
        SRRI,
        SRRI_Visual,
        SRRI_Textual,
        SRRI_Validation_Status,
        Processing_Time_Ms,
        Processing_Breakdown
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(ISIN, KIID_Class) DO UPDATE SET
        KIID_Class            = excluded.KIID_Class,
        KIID_URL              = COALESCE(excluded.KIID_URL, fund_kiid_metadata.KIID_URL),
        KIID_PDF_Hash         = COALESCE(excluded.KIID_PDF_Hash, fund_kiid_metadata.KIID_PDF_Hash),
        -- Preservar OK si el nuevo estado es CACHED (caché no degrada estado real)
        KIID_Status           = CASE
                                    WHEN excluded.KIID_Status = 'CACHED'
                                    THEN COALESCE(fund_kiid_metadata.KIID_Status, 'CACHED')
                                    ELSE excluded.KIID_Status
                                END,
        Language              = excluded.Language,
        -- COALESCE: preservar texto existente si el nuevo es NULL
        Raw_KIID_Text         = COALESCE(excluded.Raw_KIID_Text, fund_kiid_metadata.Raw_KIID_Text),
        KIID_Published_Date   = excluded.KIID_Published_Date,
        -- Preservar fecha de descarga real: solo actualizar si el nuevo valor no es NULL
        -- (lecturas de caché deben mantener la fecha de la descarga física original)
        KIID_Downloaded_At    = COALESCE(excluded.KIID_Downloaded_At, fund_kiid_metadata.KIID_Downloaded_At),
        -- SRRI: preservar valor existente si el nuevo es NULL
        -- En modo CACHED el parser no extrae visual → no destruir el dato anterior
        SRRI                  = COALESCE(excluded.SRRI, fund_kiid_metadata.SRRI),
        SRRI_Visual           = COALESCE(excluded.SRRI_Visual, fund_kiid_metadata.SRRI_Visual),
        SRRI_Textual          = COALESCE(excluded.SRRI_Textual, fund_kiid_metadata.SRRI_Textual),
        SRRI_Validation_Status = COALESCE(excluded.SRRI_Validation_Status, fund_kiid_metadata.SRRI_Validation_Status),
        Processing_Time_Ms     = excluded.Processing_Time_Ms,
        Processing_Breakdown   = excluded.Processing_Breakdown
    ;
    """

    params = (
        kiid_record.get("ISIN"),
        kiid_record.get("KIID_Class"),
        kiid_record.get("KIID_URL"),
        kiid_record.get("KIID_PDF_Hash"),
        kiid_record.get("KIID_Status"),
        kiid_record.get("Language"),
        kiid_record.get("Raw_KIID_Text"),
        kiid_record.get("KIID_Published_Date"),
        kiid_record.get("KIID_Downloaded_At"),
        _safe_int(kiid_record.get("SRRI")),
        _safe_int(kiid_record.get("SRRI_Visual")),
        _safe_int(kiid_record.get("SRRI_Textual")),
        kiid_record.get("SRRI_Validation_Status"),
        kiid_record.get("Processing_Time_Ms"),
        kiid_record.get("Processing_Breakdown"),
    )

    conn.execute(sql, params)




def insert_nav_series(conn: sqlite3.Connection, isin: str, nav_series: Iterable[Dict[str, Any]]) -> None:
    sql = """
    INSERT OR IGNORE INTO fund_nav_monthly
        (ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source, Ingested_At)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    rows = []
    now = datetime.datetime.utcnow().isoformat(timespec='seconds')
    for r in nav_series:
        date_val = r.get("Date")
        rows.append((
            isin,
            date_val,
            float(r.get("NAV")),
            r.get("NAV_Currency"),
            r.get("NAV_Type", "NAV"),
            int(bool(r.get("Is_Estimated", False))),
            r.get("Data_Source"),
            now
        ))
    if rows:
        conn.executemany(sql, rows)



def log_ingestion(conn: sqlite3.Connection, isin: Optional[str], step: str, status: str, message: Optional[str] = None) -> None:
    sql = """
    INSERT INTO ingestion_log (ISIN, step, status, message, created_at)
    VALUES (?, ?, ?, ?, ?)
    """
    now = datetime.datetime.utcnow().isoformat(timespec='seconds')
    conn.execute(sql, (isin, step, status, message, now))
    conn.commit()


def _upsert_kiid_benchmark(
    conn: sqlite3.Connection,
    benchmark_declared: str,
    isin: str,
) -> None:
    """
    Normaliza Benchmark_Declared del KIID y lo persiste en fund_benchmarks
    con source='KIID'. Solo escribe si hay resultado normalizado o al menos
    texto limpio (confidence=LOW para no normalizados pero detectados).
    """
    normalize_benchmark = _get_normalizer()
    if normalize_benchmark is None:
        return

    try:
        norm = normalize_benchmark(benchmark_declared)
        now  = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

        conn.execute("""
            INSERT OR REPLACE INTO fund_benchmarks
                (ISIN, source, benchmark_raw, benchmark_id, benchmark_name,
                 provider, asset_class, confidence, extracted_at)
            VALUES (?, 'KIID', ?, ?, ?, ?, ?, ?, ?)
        """, (
            isin,
            benchmark_declared,
            norm.canonical_id   if norm else None,
            norm.canonical_name if norm else benchmark_declared,
            norm.provider       if norm else None,
            norm.asset_class    if norm else None,
            norm.confidence     if norm else 'LOW',
            now,
        ))
    except Exception:
        pass  # No interrumpir el pipeline por un fallo de benchmark


def publish_fund(conn: sqlite3.Connection, fund_master_record: Dict[str, Optional[Any]], nav_series: Optional[Iterable[Dict[str, Any]]] = None, kiid_record: Optional[Dict[str, Optional[Any]]] = None) -> None:
    try:
        with conn:
            upsert_fund_master(conn, fund_master_record)
            if kiid_record:
                upsert_kiid_metadata(conn, kiid_record)
            if nav_series:
                insert_nav_series(conn, fund_master_record["ISIN"], nav_series)
            # B2: normalizar Benchmark_Declared y escribir en fund_benchmarks (source=KIID)
            _bench = fund_master_record.get("Benchmark_Declared")
            if _bench and _bench != "NO_BENCHMARK":
                _upsert_kiid_benchmark(conn, _bench, fund_master_record["ISIN"])
            log_ingestion(conn, fund_master_record.get("ISIN"), "PUBLISH_FUND", "OK", None)
    except Exception as e:
        try:
            log_ingestion(conn, fund_master_record.get("ISIN"), "PUBLISH_FUND", "ERROR", str(e))
        finally:
            raise
