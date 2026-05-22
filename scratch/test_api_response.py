import requests
import json
import time
import sys

sys.stdout.reconfigure(encoding='utf-8')

print("Polling API /api/data until tickers are populated...")
for _ in range(60):
    try:
        r = requests.get("http://127.0.0.1:8000/api/data")
        if r.status_code == 200:
            data = r.json()
            tickers = [t["ticker"] for t in data.get("tickers", [])]
            if tickers:
                print("API tickers populated:", tickers)
                if "EURUSD=X" in tickers:
                    print("FAILURE: EURUSD=X is present in api/data tickers list!")
                else:
                    print("SUCCESS: EURUSD=X is NOT present in api/data tickers list!")
                    
                ssot_tickers = [t["ticker"] for t in data.get("SSoT_JSON", {}).get("tickers", [])]
                print("SSoT tickers:", ssot_tickers)
                if "EURUSD=X" in ssot_tickers:
                    print("FAILURE: EURUSD=X is present in SSoT tickers list!")
                else:
                    print("SUCCESS: EURUSD=X is NOT present in SSoT tickers list!")
                break
        time.sleep(1)
    except Exception as e:
        print("Polling error:", e)
        time.sleep(1)
else:
    print("Timed out waiting for tickers to populate.")
