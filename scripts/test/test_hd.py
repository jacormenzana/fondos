import mstarpy

print("=== F000000C19 ===")
fund = mstarpy.Funds(term='F000000C19', language='en-gb', pageSize=1)
print('code:', fund.code, '| isin:', fund.isin)
hd = fund.historicalData(version=4)
datos = hd.get('graphData', {}).get('fund', [])
print('registros:', len(datos))
if datos:
    print('rango:', datos[0]['date'], '->', datos[-1]['date'])
else:
    print('SIN DATOS')

print()
print("=== 0P0000OMTB ===")
fund2 = mstarpy.Funds(term='0P0000OMTB', language='en-gb', pageSize=1)
print('code:', fund2.code, '| isin:', fund2.isin)
hd2 = fund2.historicalData(version=4)
datos2 = hd2.get('graphData', {}).get('fund', [])
print('registros:', len(datos2))
if datos2:
    print('rango:', datos2[0]['date'], '->', datos2[-1]['date'])
else:
    print('SIN DATOS')

print()
print("=== startDate de la API (limite historico) ===")
print('F000 startDate:', hd.get('startDate', 'N/A'))
print('0P   startDate:', hd2.get('startDate', 'N/A'))
