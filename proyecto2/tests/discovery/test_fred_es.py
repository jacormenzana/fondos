import requests

# Probar varias series candidatas para IPC Espana en FRED
series_ids = [
    "ESPCPALTT01IXOBSAM",   # OCDE CPI Spain
    "ESPCORESTICKM159SFRBATL",  # Core CPI Spain
    "CP0000ESM086NEST",     # HICP Spain (Eurostat via FRED)
    "FPCPITOTLZGESP",       # World Bank CPI Spain
]

base = "https://fred.stlouisfed.org/graph/fredgraph.csv"

for sid in series_ids:
    try:
        r = requests.get(base, params={"id": sid}, timeout=15)
        if r.status_code == 200 and "DATE" in r.text:
            lines = r.text.strip().split("\n")
            print(f"OK  {sid}: {len(lines)-1} registros | primeras: {lines[1][:40]}")
        else:
            print(f"NO  {sid}: HTTP {r.status_code}")
    except Exception as e:
        print(f"ERR {sid}: {e}")
