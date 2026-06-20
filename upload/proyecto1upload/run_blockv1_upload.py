# -*- coding: utf-8 -*-
"""
run_block.py

Ejecutor simple para lanzar procesamiento de bloques.
Uso:
    Ubicarse en directorio c:\\desarrollo\\fondos\\proyecto1 
    activar entorno des 
    lanzar run_block desde entono des 
    python run_block.py --block mixtos --db ../db/fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --sample 5 
    python run_block.py --block mixtos --db ../db/fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin LU0348784041,LU0232465467
    python run_block.py --block mixtos --db ../db/fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin LU0232465467,LU1873127366,FR0000989626,LU0135992385,LU1133289592,LU0210536867,LU0213962813,LU1502282632,IE0032875985,LU0073230426,LU0006277684,LU0236146428,LU0607519195,LU0726357873
    python run_block.py --block restantes --db ../db/fondos.sqlite --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin ES0145553014,ES0145553022,ES0155598008,ES0155598032,FR0000447823,FR0007008750,FR0007435920,FR0007457890,FR0010027623,FR0010135103    



"""

import argparse
import importlib
from pathlib import Path

#print("[DEBUG] Antes de cargar pipeline")        
from core.pipeline import run_block, load_master_excel
#print("[DEBUG] Antes de cargar sql_writer")
from core.sqlite_writer import get_connection, create_schema

BLOCKS_PACKAGE = "blocks"

def main():
    #print("[DEBUG] Entro en run_block")        
    p = argparse.ArgumentParser()
    p.add_argument("--block", required=True, help="Nombre del bloque (module name en blocks/)")
    p.add_argument("--db", required=True, help="Path a sqlite DB")
    p.add_argument("--master", required=True, help="Excel maestro (GestoresDeFondosv1.xlsx)")
    p.add_argument("--sample", type=int, default=None, help="sample size (opcional)")
    p.add_argument("--stop-on-error", action="store_true")

    # NUEVO
    p.add_argument(
        "--list-isin",
        default=None,
        help="Lista explícita de ISINs separada por comas (modo debug)"
    )
    
    #print("[DEBUG] Entro en parseo argumentos")    
    args = p.parse_args()

    list_isin = None
    if args.list_isin:
        list_isin = [x.strip() for x in args.list_isin.split(",") if x.strip()]

    db_path = Path(args.db)
    master_path = Path(args.master)

    # carga maestro
    #print("[DEBUG] Empiezo proceso de carga de maestro")        
    df_master = load_master_excel(master_path)
    print(f"[DEBUG] Maestro cargado: {df_master.shape}")

    # carga modulo de bloque
    module_name = f"{BLOCKS_PACKAGE}.{args.block}"
    print(f"[DEBUG] Carga modulo: {BLOCKS_PACKAGE}.{args.block}")
    block_mod = importlib.import_module(module_name)

    # conexion sqlite
    conn = get_connection(db_path)
    create_schema(conn)

    published = run_block(
        block_mod,
        df_master,
        conn,
        master_excel_path=master_path,
        sample_size=args.sample,
        stop_on_error=args.stop_on_error,
        list_isin=list_isin,   # ← aquí
    )

    print(f"Bloque {args.block} procesado. Registros publicados: {len(published)}")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()
