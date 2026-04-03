import requests
import json

def get_ua():
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Obtener token
r = requests.get(
    "https://www.morningstar.com/funds/xnas/afozx/chart",
    headers={"user-agent": get_ua()},
    timeout=15
)
txt = r.text
idx   = txt.find("token")
token = txt[idx + 7 : txt.find("}", idx) - 1]
print(f"Token OK: {token[:40]}...")

# Descargar NAV desde enero 2025
ms_id = "F0GBR04EFH"
r2 = requests.get(
    "https://www.us-api.morningstar.com/QS-markets/chartservice/v2/timeseries",
    headers={
        "user-agent":    get_ua(),
        "authorization": f"Bearer {token}",
    },
    params={
        "query":           f"{ms_id}:nav,totalReturn",
        "frequency":       "m",
        "startDate":       "2025-01-01",
        "endDate":         "2026-03-22",
        "trackMarketData": "3.6.3",
        "instid":          "DOTCOM",
    },
    timeout=15
)

data = r2.json()
series = data[0]["series"]
print(f"Status: {r2.status_code}")
print(f"Registros: {len(series)}")
for row in series[-3:]:
    print(f"  {row}")
