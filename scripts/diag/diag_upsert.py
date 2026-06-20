import sys, traceback
sys.path.insert(0, '.')

from shared.db import get_connection
from core.sqlite_writer import upsert_fund_master, upsert_kiid_metadata

conn = get_connection()

# Reproducir exactamente lo que hace pipeline.py para IE00BJGT6Q17
fund_master_record = {
    "ISIN": "IE00BJGT6Q17",
    "Fund_Name": "TEST_DIAG",
    "Management_Company": "PIMCO",
    "Fund_Nature": "Mixtos",
    "Profile": "Moderado",
    "Type": None,
    "Strategy": None,
    "Family": None,
    "Style_Profile": None,
    "Geography": None,
    "Theme": None,
    "Is_ESG": 0,
    "Exposure_Bias": None,
    "Benchmark_Type": None,
    "Subtype": None,
    "Market_Cap_Focus": None,
    "Sector_Focus": None,
    "Currency_Hedged": None,
    "Investment_Universe": None,
    "Investment_Focus": None,
    "Credit_Quality": None,
    "Accumulation_Policy": None,
    "Heuristic_Block": "MIXTOS",
    "Heuristic_Core": 0,
    "SRRI": 3,
    "Fund_Currency": None,
    "Portfolio_Currency": None,
    "Hedging_Policy": None,
    "Replication_Method": None,
    "Derivatives_Usage": None,
    "Benchmark_Declared": None,
    "Ongoing_Charge": None,
    "Entry_Fee_Pct": None,
    "Exit_Fee_Pct": None,
    "Fee_Known_Flag": None,
    "Sfdr_Article": None,
    "Recommended_Holding_Period": None,
    "Leverage_Used": None,
    "Liquidity_Profile": None,
    "Distribution_Frequency": None,
    "fund_family_id": None,
    "Inference_Trace": None,
    "SRRI_Quality_Flag": "HIGH",
    "Data_Quality_Flag": "OK",
}

kiid_record = {
    "ISIN": "IE00BJGT6Q17",
    "KIID_Class": 1,
    "KIID_URL": None,
    "KIID_PDF_Hash": None,
    "KIID_Status": "OK",
    "Language": "en",
    "Raw_KIID_Text": "test",
    "KIID_Published_Date": None,
    "KIID_Downloaded_At": None,
    "SRRI": 3,
    "SRRI_Visual": 3,
    "SRRI_Textual": None,
    "SRRI_Validation_Status": "VISUAL_ONLY",
    "Processing_Time_Ms": 100,
    "Processing_Breakdown": "test:100ms",
    "DLA2_Table_Text": "Costes de entrada: 0 %",
}

print("Test 1: upsert_fund_master standalone...")
try:
    upsert_fund_master(conn, fund_master_record.copy())
    conn.commit()
    print("  OK")
except Exception as e:
    traceback.print_exc()
    print(f"  ERROR: {e}")

print("Test 2: upsert_kiid_metadata standalone...")
try:
    upsert_kiid_metadata(conn, kiid_record.copy())
    conn.commit()
    print("  OK")
except Exception as e:
    traceback.print_exc()
    print(f"  ERROR: {e}")

print("Test 3: ambos dentro de 'with conn:' (como publish_fund)...")
try:
    from core.sqlite_writer import publish_fund
    publish_fund(conn, fund_master_record.copy(), None, kiid_record.copy())
    print("  OK")
except Exception as e:
    traceback.print_exc()
    print(f"  ERROR: {e}")

# Verificar resultado
row = conn.execute(
    "SELECT DLA2_Table_Text FROM fund_kiid_metadata WHERE ISIN='IE00BJGT6Q17' AND KIID_Class=1"
).fetchone()
print(f"\nDLA2_Table_Text en BD: {row[0] if row else 'fila no encontrada'}")

conn.close()
