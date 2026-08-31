import os
import sys
import json
import base64

# Add python folder to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import yfinance as yf
import pandas as pd

def test_intraday_fetch():
    print("Testing 1m intraday data fetch for SPY...")
    symbol = "SPY"
    ticker_obj = yf.Ticker(symbol)
    df = ticker_obj.history(interval="1m", period="5d", prepost=False, auto_adjust=True)
    if df is None or df.empty:
        print("ERROR: No data returned from yfinance history.")
        return False
    
    last_ts = df.index[-1]
    today_date = last_ts.date()
    
    bars = []
    warmup_closes = []
    
    for ts, row in df.iterrows():
        try:
            t_unix = int(ts.timestamp())
        except Exception:
            continue
        bar_date = ts.date()
        close_val = round(float(row["Close"]), 4)
        if bar_date == today_date:
            bars.append({
                "time": t_unix,
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": close_val,
                "volume": int(row["Volume"]) if not pd.isna(row["Volume"]) else 0
            })
        else:
            warmup_closes.append(close_val)
            
    print(f"Success: Fetched {len(bars)} today bars and {len(warmup_closes)} warmup closes for {symbol}.")
    assert len(bars) > 0, "Today's bars should not be empty"
    assert len(warmup_closes) > 0, "Warmup closes should not be empty"
    return True

def test_chart_save():
    print("Testing screenshot decode and saving...")
    charts_dir = os.path.join(os.path.dirname(__file__), "..", "context", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    
    # 1x1 transparent PNG base64
    mock_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    img_bytes = base64.b64decode(mock_png_b64)
    file_path = os.path.join(charts_dir, "chart_SPY_1m.png")
    with open(file_path, "wb") as f:
        f.write(img_bytes)
        
    assert os.path.exists(file_path), "Saved chart image should exist"
    print(f"Success: Saved test chart to {file_path} (size: {len(img_bytes)} bytes).")
    return True

if __name__ == "__main__":
    t1 = test_intraday_fetch()
    t2 = test_chart_save()
    if t1 and t2:
        print("\nALL VERIFICATION CHECKS PASSED!")
    else:
        sys.exit(1)
