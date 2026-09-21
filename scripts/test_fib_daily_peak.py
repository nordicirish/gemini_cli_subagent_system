import sys
import os
import pandas as pd
import numpy as np

# Add parent and python directories to path
sys.path.insert(0, os.path.abspath("python"))

from fetch_stocks import calculate_fib_forecast, cache

def test_fib_calculation():
    print("=== Testing calculate_fib_forecast ===")
    
    # Test Case 1: Active Breakout with ATR confluence
    # Open: 100.0, ATR: 8.0 -> daily_peak_atr = 100.0 + (1.25 * 8.0) = 110.0
    # Day Low (PA): 98.0, Day High (PB): 104.0 -> Impulse = 6.0
    # Current Price: 103.0 -> PC = 103.0
    # T1 (1.000): 103.0 + 6.0 = 109.0
    # Distance between T1 (109.0) and daily_peak_atr (110.0): |109 - 110| / 110 = 0.91% <= 1.8% -> ATR confluence = True!
    
    cache.day_high["TEST1"] = 104.0
    cache.day_low["TEST1"] = 98.0
    cache.day_open["TEST1"] = 100.0
    
    res = calculate_fib_forecast(
        symbol="TEST1",
        current_price=103.0,
        hist_df=None,
        open_price=100.0,
        atr=8.0,
        rsi=65.0,
        rvol=2.5,
        vwap=101.5
    )
    
    print(f"pA: {res['pA']}, pB: {res['pB']}, pC: {res['pC']}, impulse: {res['impulse']}")
    print(f"T1: {res['t1_100']} (limit: {res['t1_limit']})")
    print(f"T2: {res['t2_1618']} (limit: {res['t2_limit']})")
    print(f"T3: {res['t3_2618']} (limit: {res['t3_limit']})")
    print(f"Daily Peak Target: {res['daily_peak_target']}")
    print(f"Daily Peak ATR: {res['daily_peak_atr']}")
    print(f"ATR Confluence: {res['atr_confluence']}")
    print(f"Daily Peak Status: {res['daily_peak_status']}")
    print(f"Next Resistance: {res['next_resistance']} ({res['next_resistance_label']})")
    
    assert res["pA"] == 98.0
    assert res["pB"] == 104.0
    assert res["pC"] == 103.0
    assert res["impulse"] == 6.0
    assert res["t1_100"] == 109.0
    assert res["t1_limit"] == round(109.0 * 0.9975, 2)
    assert res["t2_1618"] == round(103.0 + (1.618 * 6.0), 2)
    assert res["daily_peak_atr"] == 110.0
    assert res["atr_confluence"] is True
    assert res["daily_peak_target"] == 109.0
    assert res["daily_peak_status"] == "EXPANDING"
    assert res["next_resistance"] == 104.0
    assert res["next_resistance_label"] == "Daily Peak"
    
    print("\nTest Case 1 Passed successfully!\n")
    
    # Test Case 2: Price >= T1 with volume exhaustion (rvol < 1.5, rsi > 70)
    res2 = calculate_fib_forecast(
        symbol="TEST1",
        current_price=109.5,
        hist_df=None,
        open_price=100.0,
        atr=8.0,
        rsi=75.0,
        rvol=1.1,
        vwap=101.5
    )
    print(f"Test Case 2 (Exhaustion) Status: {res2['daily_peak_status']}")
    assert res2["daily_peak_status"] == "PEAK_EXHAUSTED"
    print("Test Case 2 Passed successfully!\n")
    
    print("=== All calculate_fib_forecast tests passed! ===")

if __name__ == "__main__":
    test_fib_calculation()
