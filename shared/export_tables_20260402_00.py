# shared/export_tables.py
# -*- coding: utf-8 -*-
"""
Motor generico de exportacion de tablas SQLite a Excel.

Cualquier proyecto puede reutilizar esta funcion para volcar sus tablas
a un Excel con formato consistente. Cada proyecto define su propia
configuracion (que tablas, que columnas excluir, nombres de hoja, etc.)
y llama a export_tables() con esa configuracion.

Uso tipico desde un modulo de proyecto:

    from shared.export_tables import export_tables, TableExportConfig

    TABLES = [
        TableExportConfig(
            table="fund_master",
            sheet_name="1_FundMaster",
            exclude_cols=["Inference_Trace"],
        ),
        TableExportConfig(
            table="fund_nav_monthly",
            sheet_name="2_NAVMensual",
            row_limit=200_000,
        ),
    ]

    out = export_tables(
        tables=TABLES,
        output_path=Path("out/mi_export_20260322.xlsx"),
        db_path=DB_PATH,
    )
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


# ============================================================
# Configuracion de tabla
# ============================================================

@dataclass
class TableExportConfig:
    """
    Configuracion de exportacion para una tabla.

    Atributos:
        table         Nombre de la tabla en SQLite.
        sheet_name    Nombre de la hoja en el Excel resultante.
                      Si es None se usa el nombre de la tabla.
        exclude_cols  Columnas a omitir (ej. columnas binarias o muy pesadas).
        row_limit     Limite de filas exportadas. None = sin limite.
        order_by      Clausula ORDER BY opcional (sin la palabra ORDER BY).
        where         Clausula WHERE opcional (sin la palabra WHERE).
    """
    table:        str
    sheet_name:   Optional[str]       = None
    exclude_cols: list[str]           = field(default_factory=list)
    row_limit:    Optional[int]       = None
    order_by:     Optional[str]       = None
    where:        Optional[str]       = None

    @property
    def effective_sheet(self) -> str:
        return self.sheet_name or self.table


# ============================================================
# Motor de exportacion
# ============================================================

def export_tables(
    tables:      list[TableExportConfig],
    output_path: Path,
    db_path:     Path,
    verbose:     bool = True,
) -> Path:
    """
    Exporta una lista de tablas SQLite a un fichero Excel multi-hoja.

    Parametros:
        tables:       lista de TableExportConfig con las tablas a exportar.
        output_path:  ruta completa del fichero .xlsx a generar.
        db_path:      ruta a fondos.sqlite.
        verbose:      si True, imprime progreso por consola.

    Devuelve la ruta del fichero generado.

    El fichero se sobreescribe si ya existe.
    El directorio de salida se crea automaticamente si no existe.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not Path(db_path).exists():
        raise FileNotFoundError(f"BD no encontrada: {db_path}")

    if verbose:
        print(f"\nExportacion -> {output_path}")
        print(f"BD:            {db_path}")
        print(f"Tablas:        {len(tables)}\n")

    conn = sqlite3.connect(str(db_path))

    try:
        with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
            for cfg in tables:
                # Construir query
                query = f"SELECT * FROM {cfg.table}"
                if cfg.where:
                    query += f" WHERE {cfg.where}"
                if cfg.order_by:
                    query += f" ORDER BY {cfg.order_by}"
                if cfg.row_limit:
                    query += f" LIMIT {cfg.row_limit}"

                try:
                    df = pd.read_sql_query(query, conn)

                    # Excluir columnas pesadas o irrelevantes
                    drop = [c for c in cfg.exclude_cols if c in df.columns]
                    if drop:
                        df = df.drop(columns=drop)

                    df.to_excel(writer, sheet_name=cfg.effective_sheet,
                                index=False)

                    if verbose:
                        note = f"  (limit {cfg.row_limit:,})" if cfg.row_limit else ""
                        print(f"  [{cfg.effective_sheet}]  "
                              f"{len(df):>8,} filas x {len(df.columns):>3} cols"
                              f"{note}")

                except Exception as e:
                    if verbose:
                        print(f"  ERROR en {cfg.table}: {e}")

    finally:
        conn.close()

    if verbose:
        size_mb = output_path.stat().st_size / 1_048_576
        print(f"\nFichero generado: {output_path}  ({size_mb:.1f} MB)")

    return output_path


# ============================================================
# Helpers de nomenclatura
# ============================================================

def dated_filename(prefix: str, ext: str = "xlsx") -> str:
    """
    Genera un nombre de fichero con fecha del dia: '<prefix>_YYYYMMDD.<ext>'

    Ejemplo: dated_filename("p1_export") -> "p1_export_20260322.xlsx"
    """
    return f"{prefix}_{date.today().strftime('%Y%m%d')}.{ext}"
