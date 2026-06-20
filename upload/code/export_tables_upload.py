# shared/export_tables.py
# -*- coding: utf-8 -*-
"""
Motor genérico de exportación de tablas SQLite a Excel.

Cualquier proyecto puede reutilizar esta función para volcar sus tablas
a un Excel con formato consistente. Cada proyecto define su propia
configuración (qué tablas, qué columnas excluir o incluir, nombres de
hoja, etc.) y llama a export_tables() con esa configuración.

Uso típico desde un módulo de proyecto:

    from shared.export_tables import export_tables, TableExportConfig

    TABLES = [
        TableExportConfig(
            table="fund_master",
            sheet_name="1_FundMaster",
            exclude_cols=["Inference_Trace", "Raw_KIID_Text"],
        ),
        TableExportConfig(
            table="fund_kiid_metadata",
            sheet_name="2_KIID",
            include_cols=["ISIN", "KIID_Status", "SRRI", "SRRI_Validation_Status"],
        ),
    ]

    out = export_tables(
        tables=TABLES,
        output_path=Path("out/mi_export_20260401.xlsx"),
        db_path=DB_PATH,
    )

Cambios v17:
  - include_cols: lista blanca de columnas a exportar (complemento a
    exclude_cols). Cuando se especifica, solo se exportan esas columnas
    en ese orden. Resuelve el bug de export_p1.py (~3.5MB vs ~50MB):
    Raw_KIID_Text estaba siendo excluida implícitamente por lógica
    en export_p1.py; ahora puede incluirse explícitamente con include_cols.
  - SELECT explícito por columna cuando include_cols está definido,
    en lugar de SELECT * (evita columnas binarias o pesadas no deseadas).
  - Errores por tabla se acumulan y se reportan al final, sin abortar
    el export completo por un fallo en una tabla.
  - chunk_size: parámetro opcional para tablas muy grandes (streaming
    por bloques con pd.read_sql_query + chunksize).
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


# ============================================================
# Configuración de tabla
# ============================================================

@dataclass
class TableExportConfig:
    """
    Configuración de exportación para una tabla.

    Atributos:
        table         Nombre de la tabla en SQLite.
        sheet_name    Nombre de la hoja en el Excel resultante.
                      Si es None se usa el nombre de la tabla.
        exclude_cols  Columnas a omitir del SELECT *.
                      Ignorado si include_cols está definido.
        include_cols  Lista blanca de columnas a exportar (en ese orden).
                      Si está definido, se hace SELECT explícito y se
                      ignora exclude_cols.
        row_limit     Límite de filas exportadas. None = sin límite.
        order_by      Cláusula ORDER BY opcional (sin la palabra ORDER BY).
        where         Cláusula WHERE opcional (sin la palabra WHERE).
        chunk_size    Si > 0, lee la tabla en bloques de ese tamaño
                      (útil para tablas con millones de filas).
    """
    table:        str
    sheet_name:   Optional[str]   = None
    exclude_cols: list[str]       = field(default_factory=list)
    include_cols: Optional[list[str]] = None
    row_limit:    Optional[int]   = None
    order_by:     Optional[str]   = None
    where:        Optional[str]   = None
    chunk_size:   int             = 0

    @property
    def effective_sheet(self) -> str:
        return self.sheet_name or self.table

    def build_query(self, conn: sqlite3.Connection) -> str:
        """
        Construye el SELECT SQL para esta configuración.

        Si include_cols está definido → SELECT explícito de esas columnas.
        Si solo exclude_cols → SELECT * y se filtra el DataFrame después.
        """
        if self.include_cols:
            # Verificar que las columnas existen en la tabla
            existing = {
                r[1]
                for r in conn.execute(
                    f"PRAGMA table_info({self.table})"
                ).fetchall()
            }
            valid_cols = [c for c in self.include_cols if c in existing]
            missing    = [c for c in self.include_cols if c not in existing]
            if missing:
                # Advertir pero no abortar — se exportan las que existen
                print(f"    AVISO [{self.table}]: columnas no encontradas "
                      f"en include_cols: {missing}")
            col_str = ", ".join(f'"{c}"' for c in valid_cols)
            query = f"SELECT {col_str} FROM {self.table}"
        else:
            query = f"SELECT * FROM {self.table}"

        if self.where:
            query += f" WHERE {self.where}"
        if self.order_by:
            query += f" ORDER BY {self.order_by}"
        if self.row_limit:
            query += f" LIMIT {self.row_limit}"

        return query


# ============================================================
# Motor de exportación
# ============================================================

def export_tables(
    tables:      list[TableExportConfig],
    output_path: Path,
    db_path:     Path,
    verbose:     bool = True,
) -> Path:
    """
    Exporta una lista de tablas SQLite a un fichero Excel multi-hoja.

    Parámetros:
        tables:       lista de TableExportConfig con las tablas a exportar.
        output_path:  ruta completa del fichero .xlsx a generar.
        db_path:      ruta a fondos.sqlite.
        verbose:      si True, imprime progreso por consola.

    Devuelve la ruta del fichero generado.

    El fichero se sobreescribe si ya existe.
    El directorio de salida se crea automáticamente si no existe.

    Los errores por tabla se acumulan y se reportan al final;
    un fallo en una tabla no aborta el export de las restantes.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not Path(db_path).exists():
        raise FileNotFoundError(f"BD no encontrada: {db_path}")

    if verbose:
        print(f"\nExportación -> {output_path}")
        print(f"BD:            {db_path}")
        print(f"Tablas:        {len(tables)}\n")

    conn = sqlite3.connect(str(db_path))
    errors: list[str] = []

    try:
        with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
            for cfg in tables:
                try:
                    query = cfg.build_query(conn)

                    if cfg.chunk_size > 0:
                        # Streaming por bloques para tablas grandes
                        chunks = pd.read_sql_query(
                            query, conn, chunksize=cfg.chunk_size
                        )
                        df = pd.concat(chunks, ignore_index=True)
                    else:
                        df = pd.read_sql_query(query, conn)

                    # Excluir columnas (solo cuando NO se usó include_cols)
                    if not cfg.include_cols and cfg.exclude_cols:
                        drop = [c for c in cfg.exclude_cols if c in df.columns]
                        if drop:
                            df = df.drop(columns=drop)

                    df.to_excel(
                        writer,
                        sheet_name=cfg.effective_sheet,
                        index=False,
                    )

                    if verbose:
                        mode = (
                            f"include={len(cfg.include_cols)}cols"
                            if cfg.include_cols
                            else f"exclude={len(cfg.exclude_cols)}cols"
                            if cfg.exclude_cols
                            else "all cols"
                        )
                        note = (
                            f"  (limit {cfg.row_limit:,})"
                            if cfg.row_limit else ""
                        )
                        print(
                            f"  [{cfg.effective_sheet}]  "
                            f"{len(df):>8,} filas x {len(df.columns):>3} cols"
                            f"  [{mode}]{note}"
                        )

                except Exception as exc:
                    msg = f"ERROR en {cfg.table}: {exc}"
                    errors.append(msg)
                    if verbose:
                        print(f"  {msg}")

    finally:
        conn.close()

    if verbose:
        size_mb = output_path.stat().st_size / 1_048_576
        print(f"\nFichero generado: {output_path}  ({size_mb:.1f} MB)")
        if errors:
            print(f"\nErrores ({len(errors)}):")
            for e in errors:
                print(f"  {e}")

    return output_path


# ============================================================
# Helpers de nomenclatura
# ============================================================

def dated_filename(prefix: str, ext: str = "xlsx") -> str:
    """
    Genera un nombre de fichero con fecha del día: '<prefix>_YYYYMMDD.<ext>'

    Ejemplo: dated_filename("p1_export") -> "p1_export_20260401.xlsx"
    """
    return f"{prefix}_{date.today().strftime('%Y%m%d')}.{ext}"
