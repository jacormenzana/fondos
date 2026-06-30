import requests, json

url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10dd_edpt1"
params = {
    "format":  "JSON",
    "lang":    "EN",
    "na_item": "B9",
    "sector":  "S13",
    "unit":    "PC_GDP",
    "geo":     ["ES", "EA"],
}

r = requests.get(url, params=params, timeout=30)
data = r.json()

# Ver estructura de dimensiones
dims = data.get("dimension", {})
print("=== Dimensiones disponibles ===")
for dim_name, dim_data in dims.items():
    idx = dim_data.get("category", {}).get("index", {})
    print(f"  {dim_name}: {idx}")

print()
print("=== Primeros 10 valores ===")
values = data.get("value", {})
for k, v in list(values.items())[:10]:
    print(f"  [{k}]: {v}")

print()
# Calcular numero de combinaciones
periods = list(dims.get("time", {}).get("category", {}).get("index", {}).keys())
geos    = list(dims.get("geo", {}).get("category", {}).get("index", {}).keys())
print(f"Periodos: {len(periods)} | Geografias: {geos}")
print(f"Total esperado: {len(periods) * len(geos)} | Total recibido: {len(values)}")
