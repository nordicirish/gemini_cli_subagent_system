import urllib.request
import json

try:
    with urllib.request.urlopen("http://localhost:8000/api/data") as resp:
        data = json.loads(resp.read().decode('utf-8'))
        
    print("Keys in state:", list(data.keys()))
    print("Status in state:", data.get("status"))
    tickers = data.get("tickers", [])
    print(f"Total tickers: {len(tickers)}")
    for t in tickers:
        sym = t.get("ticker")
        fib = t.get("fib_forecast")
        print(f"Ticker: {sym}, Price: {t.get('price')}, Fib: {type(fib)}")
        if isinstance(fib, dict):
            print(f"  next_res: {fib.get('next_resistance')}, label: {fib.get('next_resistance_label')}, dist: {fib.get('distance_to_next_pct')}")
            print(f"  daily_peak: {fib.get('daily_peak_target')}, atr_conf: {fib.get('atr_confluence')}")
except Exception as e:
    print(f"Error fetching: {e}")
