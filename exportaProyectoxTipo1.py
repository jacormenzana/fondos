# -*- coding: utf-8 -*-
"""
Created on Mon Jan 19 21:01:17 2026

@author: Administrador
"""

import sqlite3
import pandas as pd
from pathlib import Path

# Definición de rutas
db_path = Path(r"C:\desarrollo\fondos\proyecto1\p1_output.sqlite")
xls_path = Path(r"C:\data\fondos\out\p1_output.sqlite_tipo.xlsx")

heuristica = ["MONETARIOS", "RF_CORTO"]

try:
    xls_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        with pd.ExcelWriter(xls_path, engine="xlsxwriter") as writer:

            # ---------------------------------------------------------
            # 1. Procesar 'fund_master'
            # ---------------------------------------------------------
            placeholders = ",".join(["?"] * len(heuristica))
            query_master = f"""
                SELECT *
                FROM fund_master
                WHERE Heuristic_Block IN ({placeholders})
            """
            df_master = pd.read_sql_query(query_master, conn, params=heuristica)
            df_master.to_excel(writer, sheet_name="fund_master", index=False)

            # ---------------------------------------------------------
            # 2. Procesar 'fund_kiid_metadata'
            # ---------------------------------------------------------
            # Como ahora tienes un JOIN real por ISIN, pasamos a una sola 
            # consulta usando IN y eliminamos el bucle for y el CROSS JOIN.
            
            query_kiid = f"""
                SELECT md.*, fm.Heuristic_Block 
                FROM fund_kiid_metadata md
                INNER JOIN fund_master fm ON fm.ISIN = md.ISIN 
                WHERE fm.Heuristic_Block IN ({placeholders})
            """
            
            # Ejecutamos la única consulta pasando la lista de heurísticas
            df_kiid = pd.read_sql_query(query_kiid, conn, params=heuristica)
            df_kiid.to_excel(writer, sheet_name="fund_kiid_metadata", index=False)

    print(f"✅ Proceso completado con éxito. Archivo guardado en: {xls_path}")

except sqlite3.Error as e:
    print(f"❌ Error en la base de datos (SQLite): {e}")
    print("Verifica que la base de datos existe y que las tablas tienen el nombre correcto.")

except PermissionError:
    print("❌ Error de permisos al intentar guardar el archivo.")
    print("¿Es posible que tengas el archivo Excel abierto? Ciérralo e inténtalo de nuevo.")

except FileNotFoundError as e:
    print(f"❌ Archivo no encontrado: {e}")
    print("Verifica que la ruta de la base de datos sea correcta.")

except Exception as e:
    print(f"❌ Ocurrió un error inesperado: {e}")

