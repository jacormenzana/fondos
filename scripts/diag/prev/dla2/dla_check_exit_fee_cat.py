# scripts/diag/dla_check_exit_fee_cat.py
import pandas as pd
import sqlite3

CSV_PATH  = r"C:\desarrollo\fondos\proyecto1\db\dla_table_inventory.csv"
DB_PATH   = r"C:\desarrollo\fondos\db\fondos.sqlite"

# --- Cargar inventario de tablas
inv = pd.read_csv(CSV_PATH)
print(f"Inventario cargado: {len(inv)} filas")
print(f"Columnas: {list(inv.columns)}")  # imprimir para confirmar nombres exactos

# --- Cargar ISINs con Exit_Fee_Pct NULL desde SQLite
con = sqlite3.connect(DB_PATH)
null_exit = pd.read_sql(
    "SELECT ISIN FROM fund_master WHERE Exit_Fee_Pct IS NULL", con
)
con.close()
print(f"Fondos con Exit_Fee_Pct NULL: {len(null_exit)}")

# --- Cruce
merged = inv.merge(null_exit, on="ISIN", how="inner")
print(f"\nFondos con Exit_Fee_Pct NULL Y tabla detectada: {len(merged)}")

# Distribución por cat_max
dist = merged["cat_max"].value_counts().sort_index()
print("\nDistribución cat_max (fondos con Exit_Fee_Pct NULL):")
for cat, n in dist.items():
    pct = n / len(merged) * 100
    print(f"  Cat. {cat}: {n:4d}  ({pct:.1f}%)")

# Misma distribución para Entry_Fee y Ongoing_Charge
con = sqlite3.connect(DB_PATH)
null_entry = pd.read_sql(
    "SELECT ISIN FROM fund_master WHERE Entry_Fee_Pct IS NULL", con
)
null_oc = pd.read_sql(
    "SELECT ISIN FROM fund_master WHERE Ongoing_Charge IS NULL", con
)
con.close()

for label, df_null in [("Entry_Fee_Pct", null_entry), ("Ongoing_Charge", null_oc)]:
    m = inv.merge(df_null, on="ISIN", how="inner")
    dist2 = m["cat_max"].value_counts().sort_index()
    print(f"\nFondos con {label} NULL Y tabla detectada: {len(m)}")
    for cat, n in dist2.items():
        print(f"  Cat. {cat}: {n:4d}  ({n/len(m)*100:.1f}%)")