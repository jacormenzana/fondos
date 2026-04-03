# test_report.py
import sys
sys.path.insert(0, '.')
from proyecto2.src.db import get_connection
from proyecto3.src.monthly_report import generate_report

conn = get_connection()
path = generate_report(conn)  # sin output_dir -- usa REPORTS_DIR de config
print(f"Informe guardado en: {path}")