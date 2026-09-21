import sys
import os
import json

sys.path.insert(0, r"C:\github\gem_trading_agent_system\python")
from fetch_stocks import cache

print("UMAC in cache.history:", "UMAC" in cache.history)
if "UMAC" in cache.history:
    df = cache.history["UMAC"]
    print("History length:", len(df))
    print("Latest rows:\n", df.tail(2))
