# test_backtesting.py
import sys
sys.path.insert(0, '.')
from proyecto2.src.db import get_connection
from proyecto3.src.backtesting import Backtester

conn = get_connection()
bt   = Backtester(conn)

results = bt.run(start_date="2005-01-01")
print()
print(bt.summary(results))