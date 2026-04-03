# test_scorer.py
import sys
sys.path.insert(0, '.')
from proyecto2.src.db import get_connection
from proyecto3.src.regime_classifier import RegimeClassifier
from proyecto3.src.fund_scorer import score_funds

conn = get_connection()
clf  = RegimeClassifier(conn)
reg  = clf.classify_current()
print(clf.current_regime_report())
print()

df = score_funds(conn, reg, dry_run=False)
print()
print("Top 10 Defensiva:")
print(df[df["subportfolio"]=="Defensiva"]
      .sort_values("score_final", ascending=False)
      [["isin","fund_nature","score_base","multiplier","score_final","eligible"]]
      .head(10).to_string())