# Diagnóstico rápido — ejecutar en c:\desarrollo\fondos\
import sys
sys.path.insert(0, 'c:\\desarrollo\\fondos')
from proyecto1.core.srri_v4_geometric import SRRIV4Geometric
from proyecto1.core.srri_v5_geometric import SRRIV5Geometric
import sqlite3, pathlib

# Coger un fondo con KIID_Status=OK (procesado con PDF)
conn = sqlite3.connect('db/fondos.sqlite')
row = conn.execute("""
    SELECT km.ISIN, km.KIID_URL, km.SRRI_Textual
    FROM fund_kiid_metadata km
    WHERE km.KIID_Status = 'OK'
      AND km.SRRI_Textual IS NOT NULL
    LIMIT 1
""").fetchone()
print(f"ISIN: {row[0]}, URL: {row[1]}, SRRI_Textual: {row[2]}")

# Descargar el PDF
import requests
pdf = requests.get(row[1], timeout=30).content

# Probar extractor
engine = SRRIV5Geometric(isin=row[0])
result = engine.extract(pdf)
print(f"SRRI_Visual resultado: {result}")