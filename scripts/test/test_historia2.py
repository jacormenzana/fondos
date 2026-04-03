import mstarpy, json

fund = mstarpy.Funds(term='F0GBR04DPK', language='en-gb', pageSize=1)
print("Fondo:", fund.name, "| code:", fund.code)

# ---- performanceTable completo ----
print("\n=== performanceTable ===")
try:
    pt = fund.performanceTable()
    # Volcar tabla completa
    table = pt.get('table', {})
    print("startDate:", pt.get('startDate', 'N/A')[:10])
    print("table keys:", list(table.keys()) if isinstance(table, dict) else type(table))
    print(json.dumps(table, indent=2)[:3000])
except Exception as e:
    print("ERROR:", e)

# ---- trailingReturn completo ----
print("\n=== trailingReturn (monthly) ===")
try:
    tr = fund.trailingReturn(duration='monthly')
    # Buscar datos numericos con años
    total = tr.get('totalReturnNAV', {})
    print("totalReturnNAV keys:", list(total.keys()) if isinstance(total, dict) else type(total))
    print(json.dumps(total, indent=2)[:2000])
except Exception as e:
    print("ERROR:", e)
