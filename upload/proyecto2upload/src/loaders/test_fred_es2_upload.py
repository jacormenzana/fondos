import requests

series_ids = [
    "CP0000ESM086NEST",
    "FPCPITOTLZGESP",
]

base = "https://fred.stlouisfed.org/graph/fredgraph.csv"

for sid in series_ids:
    r = requests.get(base, params={"id": sid}, timeout=15)
    print(f"\n=== {sid} | HTTP {r.status_code} ===")
    print(r.text[:500])
