# test_regime.py en raiz
import sys
sys.path.insert(0, '.')
from proyecto2.src.db import get_connection
from proyecto3.src.regime_classifier import RegimeClassifier
conn = get_connection()
clf = RegimeClassifier(conn)
print(clf.current_regime_report())
print()
print(clf.regime_summary())