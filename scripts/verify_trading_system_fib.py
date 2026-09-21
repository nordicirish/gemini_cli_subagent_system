import sys
import os
import json

sys.path.insert(0, r"C:\github\gem_trading_agent_system\python")
from fetch_stocks import calculate_fib_forecast, cache

def verify():
    # Spot: 24.39, Open: 24.27, ATR: 1.59
    hist_df = cache.history.get("UMAC")
    res = calculate_fib_forecast(
        symbol="UMAC",
        current_price=24.39,
        hist_df=hist_df,
        open_price=24.27,
        atr=1.59,
        rsi=52.8,
        rvol=0.97,
        vwap=24.38
    )
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    verify()
