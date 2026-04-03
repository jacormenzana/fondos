# proyecto1/src/analysis/export_p1.py
# -*- coding: utf-8 -*-
"""
Exportacion de tablas del Proyecto 1 a Excel.

Genera un fichero Excel con las tablas principales gestionadas por P1:
  Hoja 1_FundMaster  — Universo completo de fondos clasificados
  Hoja 2_KIIDMetadata — Metadatos y estado de KIIDs procesados

Notas de diseño:
  - Raw_KIID_Text se excluye por defecto (texto completo OCR, muy pesado).
    Usar --include-kiid-text para incluirlo (util para analisis de parsing).
  - Inference_Trace se excluye por defecto (cadena de trazabilidad interna).
  - NAV mensual NO se exporta aqui — es dominio de P2.
  - Nomenclatura: p1_export_YYYYMMDD.xlsx

Uso:
    cd c:/desarrollo/fondos
    python -m proyecto1.src.analysis.export_p1
    python -m proyecto1.src.analysis.export_p1 --output c:/ruta/personalizada
    python -m proyecto1.src.analysis.export_p1 --include-kiid-text
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH, DATA_DIR

# Directorio de salida: c:/desarrollo/fondos/out/export/
_OUT_DIR: Path = DATA_DIR.parent / 'out'
from shared.export_tables import TableExportConfig, export_tables, dated_filename

# ============================================================
# Directorio de salida por defecto
# ============================================================
EXPORT_DIR: Path = _OUT_DIR / "export"

# ============================================================
# Configuracion de tablas P1
# ============================================================

def get_tables(include_kiid_text: bool = False) -> list[TableExportConfig]:
    """
    Devuelve la configuracion de exportacion de P1.

    include_kiid_text: si True, incluye Raw_KIID_Text en fund_kiid_metadata.
                       Util para analisis del parser OCR pero genera ficheros
                       mucho mas grandes (~500 MB vs ~10 MB).
    """
    kiid_exclude = [] if include_kiid_text else ["Raw_KIID_Text"]

    return [
        TableExportConfig(
            table="fund_master",
            sheet_name="1_FundMaster",
            exclude_cols=["Inference_Trace"],
            order_by="Fund_Nature, Management_Company, Fund_Name",
        ),
        TableExportConfig(
            table="fund_kiid_metadata",
            sheet_name="2_KIIDMetadata",
            exclude_cols=kiid_exclude,
            order_by="ISIN",
        ),

    ]


# ============================================================
# Funcion principal
# ============================================================

def export_p1(
    output_dir:       Path | None = None,
    db_path:          Path | None = None,
    include_kiid_text: bool = False,
) -> Path:
    """
    Exporta las tablas de P1 a un fichero Excel con fecha en el nombre.

    Parametros:
        output_dir:        directorio de salida (default: out/export/)
        db_path:           ruta a la BD (default: DB_PATH de shared/config)
        include_kiid_text: incluir columna Raw_KIID_Text (default: False)

    Devuelve la ruta del fichero generado.
    """
    
    print(f"DEBUG _ROOT     = {_ROOT}")
    print(f"DEBUG DB_PATH   = {DB_PATH}")
    print(f"DEBUG EXPORT_DIR= {EXPORT_DIR}")
    
    
    output_dir = Path(output_dir) if output_dir else EXPORT_DIR
    db_path    = Path(db_path)    if db_path    else DB_PATH

    out_path = output_dir / dated_filename("p1_export")
    tables   = get_tables(include_kiid_text=include_kiid_text)

    return export_tables(
        tables=tables,
        output_path=out_path,
        db_path=db_path,
        verbose=True,
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Exportacion de tablas P1 a Excel"
    )
    parser.add_argument(
        "--output", default=None,
        help="Directorio de salida (default: out/export/)"
    )
    parser.add_argument(
        "--db", default=None,
        help="Ruta alternativa a fondos.sqlite"
    )
    parser.add_argument(
        "--include-kiid-text", action="store_true",
        help="Incluir columna Raw_KIID_Text (fichero mas grande)"
    )
    args = parser.parse_args()

    export_p1(
        output_dir=args.output,
        db_path=args.db,
        include_kiid_text=args.include_kiid_text,
    )
