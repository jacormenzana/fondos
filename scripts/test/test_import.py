# test_import.py — ejecutar desde c:\desarrollo\fondos
import sys
sys.path.insert(0, r'c:\desarrollo\fondos')

from proyecto1.core.srri_v5_geometric import SRRIV5Geometric
from proyecto1.core.srri_v4_geometric import SRRIV4Geometric
from proyecto1.core.srri_text import extract_srri
print("Imports OK")
print(f"SRRIExtractor: {SRRIV5Geometric}")