# -*- coding: utf-8 -*-
"""
Created on Mon Jan 19 21:01:17 2026

@author: Administrador
"""

import sqlite3
import pandas as pd
from pathlib import Path


db_path = Path(r"C:\desarrollo\fondos\db\fondos.sqlite")
xls_path = Path(r"C:\data\fondos\out\p1_output.sqlite.xlsx")
tables = ["fund_master", "fund_kiid_metadata"]


#conn = sqlite3.connect(db_path)
#with pd.ExcelWriter("export.xlsx", engine="xlsxwriter") as writer:
#    for t in tables:
#        df = pd.read_sql_query(f"SELECT * FROM {t}", conn)
#        df.to_excel(writer, sheet_name=t, index=False)
#conn.close()


try:
    # 3. Conectar a la base de datos SQLite
    conn = sqlite3.connect(db_path)
    
    # 4. Crear el objeto ExcelWriter con el motor xlsxwriter
    with pd.ExcelWriter(xls_path, engine='xlsxwriter') as writer:
        for nombre_tabla in tables:
            # Leer cada tabla a un DataFrame
            query = f"SELECT * FROM {nombre_tabla}"
            df = pd.read_sql_query(query, conn)
            
            # Exportar el DataFrame a una hoja con el nombre de la tabla
            df.to_excel(writer, sheet_name=nombre_tabla, index=False)
            print(f"Hoja '{nombre_tabla}' añadida con éxito.")
            
    print(f"\nProceso finalizado. Archivo guardado en: {xls_path}")

except Exception as e:
    print(f"Error durante la exportación: {e}")

finally:
    # 5. Cerrar la conexión siempre
    if 'conn' in locals():
        conn.close()