# test_portfolio.py
import sys
sys.path.insert(0, '.')
from proyecto2.src.db import get_connection
from proyecto3.src.regime_classifier import RegimeClassifier
from proyecto3.src.portfolio_builder import PortfolioBuilder
from proyecto3.src.portfolio_builder import rotation_plan

conn    = get_connection()
clf     = RegimeClassifier(conn)
reg     = clf.classify_current()
builder = PortfolioBuilder(conn)

portfolio = builder.build(
    reg,
    scenario_id="shock_energia_2026Q1",
    dry_run=False
)
print(portfolio.summary())


# Simular una segunda cartera (misma pero con dry_run para comparar)
portfolio2 = builder.build(reg, scenario_id="test_comparacion", dry_run=True)

ops = rotation_plan(conn, portfolio, portfolio2)
print("\nPlan de rotacion (comparando cartera consigo misma):")
for op in ops[:5]:
    print(f"  {op['subportfolio']:12s} | OUT:{op['isin_out']} -> IN:{op['isin_in']} | "
          f"{'ROTAR' if op['recomendar'] else 'MANTENER':8s} | {op['razon']}")