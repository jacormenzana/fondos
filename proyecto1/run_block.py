# -*- coding: utf-8 -*-
"""
run_block.py

Ejecutor simple para lanzar procesamiento de bloques.
Uso:
    Ubicarse en directorio c:\\desarrollo\\fondos\\proyecto1 
    activar entorno des 
    lanzar run_block desde entono des 
    
    
    python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --sample 5
    python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --sample 5 
    python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin LU0348784041,LU0232465467
    python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin LU0232465467,LU1873127366,FR0000989626,LU0135992385,LU1133289592,LU0210536867,LU0213962813,LU1502282632,IE0032875985,LU0073230426,
    python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin LU0232465467,LU1873127366,FR0000989626,LU0135992385,LU1133289592,LU0210536867,LU0213962813,LU1502282632,IE0032875985,LU0073230426,LU0006277684,LU0236146428,LU0607519195,LU1959429272,LU0070177588,IE00B45H7020,LU0726357873
    python run_block.py --block mixtos --db ..\db\fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin IE0031296019,LU0070212591,LU0171275786,LU0213962813,LU0348784041,LU1883314327   

"""

import argparse
import importlib
from pathlib import Path

from core.pipeline import run_block, load_master_excel
from core.sqlite_writer import get_connection, create_schema
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from shared.schema_checks import assert_schema_alignment
from shared.config import DB_PATH as _DEFAULT_DB_PATH

BLOCKS_PACKAGE = "blocks"


def main():

    p = argparse.ArgumentParser()
    p.add_argument("--block", required=True,
                   help="Nombre del bloque (module name en blocks/)")
    p.add_argument("--db", default=None,
                   help=(
                       f"Path a sqlite DB. "
                       f"Si se omite, usa la ruta canónica de shared.config: "
                       f"{_DEFAULT_DB_PATH}"
                   ))
    p.add_argument("--master", required=True,
                   help="Excel maestro (GestoresDeFondosv1.xlsx)")
    p.add_argument("--sample", type=int, default=None,
                   help="sample size (opcional)")
    p.add_argument("--stop-on-error", action="store_true")
    p.add_argument("--list-isin", default=None,
                   help="Lista explícita de ISINs separada por comas (modo debug)")
    p.add_argument("--kiid-source", default="auto",
                   choices=["auto", "local", "remote"],
                   help=(
                       "Modalidad de carga del PDF KIID cuando la caché de texto "
                       "en BD no acierta (BL-KIID-LOCAL-FIRST):\n"
                       "  auto   (def.): local-first si KIID_LOCAL_FIRST_ENABLED, "
                       "con fallback a descarga remota.\n"
                       "  local  : fuerza lectura del repositorio local "
                       "(C:\\data\\fondos\\kiid), con fallback a remoto si no existe.\n"
                       "  remote : fuerza descarga por URL del Excel maestro."
                   ))
    args = p.parse_args()

    list_isin = None
    if args.list_isin:
        list_isin = [x.strip() for x in args.list_isin.split(",") if x.strip()]

    # Ruta de BD: argumento explícito > DB_PATH canónica de shared.config
    db_path = Path(args.db) if args.db else _DEFAULT_DB_PATH
    master_path = Path(args.master)

    print(f"[DEBUG] BD: {db_path}")

    #Cargar maestro (memoria)
    df_master = load_master_excel(master_path)
    print(f"[DEBUG] Maestro cargado: {df_master.shape}")

    #Cargar bloque
    print(f"[DEBUG] Carga bloque: {BLOCKS_PACKAGE}.{args.block}")
    block_mod = importlib.import_module(f"{BLOCKS_PACKAGE}.{args.block}")
    print(f"[DEBUG] block_mod: {block_mod}")

    #Conexión y schema (idempotente)
    conn = get_connection(db_path)
    create_schema(conn)
    # create_schema usa executescript() que resetea isolation_level a ''.
    # isolation_level='' hace que Python gestione transacciones implícitas,
    # lo que impide ON CONFLICT DO UPDATE en SQLite 3.24+.
    # isolation_level=None delega el control de transacciones a SQLite/código
    # explícito (with conn:), que es el comportamiento correcto.
    conn.isolation_level = None
    assert_schema_alignment(conn)

    #Ejecutar bloque
    published = run_block(
        block_mod,
        df_master,
        conn,
        master_excel_path=master_path,
        sample_size=args.sample,
        stop_on_error=args.stop_on_error,
        list_isin=list_isin,
        kiid_source=args.kiid_source,
    )

    print(f"Bloque {args.block} procesado. Registros publicados: {len(published)}")


    # BL-53/56/57: Barrido global post-pipeline (Principio #1 + #2)
    # Cubre fondos no procesados en este ciclo (KIID_Status=WRONG_DOC,
    # excluidos del bloque, etc.) que conservan valores stale en BD.
    from core.pipeline import run_global_normalization
    run_global_normalization(conn)
    
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()

