import mstarpy

# Usar un fondo antiguo con mucha historia (F0GBR, deberia tener datos desde antes de 2008)
TEST_IDS = [
    ('F0GBR04DPK', 'LU0070214613'),  # fondo antiguo LU, ~316 NAV antes
    ('F000000C19', 'IE00B19Z6G02'),  # fondo F000
    ('0P0000OMTB', 'IE00B42NVC37'),  # fondo 0P
]

for ms_id, isin in TEST_IDS:
    print(f"\n{'='*60}")
    print(f"Fondo: {ms_id} | ISIN: {isin}")
    print('='*60)

    fund = mstarpy.Funds(term=ms_id, language='en-gb', pageSize=1)

    # Probar versiones 2, 3, 4
    for v in [2, 3, 4]:
        try:
            hd = fund.historicalData(version=v)
            datos = hd.get('graphData', {}).get('fund', [])
            start = hd.get('startDate', 'N/A')[:10]
            if datos:
                print(f"  version={v}: {len(datos)} registros | API startDate={start} | datos: {datos[0]['date']} -> {datos[-1]['date']}")
            else:
                print(f"  version={v}: sin datos (startDate={start})")
        except Exception as e:
            print(f"  version={v}: ERROR -> {e}")

    # Probar performanceTable (retornos anuales historicos)
    print()
    try:
        pt = fund.performanceTable()
        # Buscar retornos anuales
        if isinstance(pt, dict):
            print(f"  performanceTable keys: {list(pt.keys())[:8]}")
            # Buscar datos de años concretos
            for key in ['annualPerformance', 'annual', 'annualizedReturn', 'returns']:
                if key in pt:
                    print(f"    [{key}]: {str(pt[key])[:200]}")
        else:
            print(f"  performanceTable tipo: {type(pt)}")
    except Exception as e:
        print(f"  performanceTable: ERROR -> {e}")

    # Probar trailingReturn (puede incluir retornos a 10 años)
    try:
        tr = fund.trailingReturn(duration='monthly')
        if isinstance(tr, dict):
            keys = list(tr.keys())[:6]
            print(f"  trailingReturn keys: {keys}")
            # Buscar periodo 10Y
            for key in ['tenYear', 'return10Year', '10year', 'trailing']:
                if key in tr:
                    print(f"    [{key}]: {tr[key]}")
    except Exception as e:
        print(f"  trailingReturn: ERROR -> {e}")
