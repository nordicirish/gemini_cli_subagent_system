import requests
import json
import time

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

FINNHUB_API_KEY = config.get("FINNHUB_API_KEY")

def test_finnhub_candle_recent(symbol):
    to_ts = int(time.time())
    from_ts = to_ts - (30 * 24 * 3600) # 30 days
    print(f"Testing Recent Candle for {symbol}...")
    url = f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}&resolution=D&from={from_ts}&to={to_ts}&token={FINNHUB_API_KEY}"
    r = requests.get(url, timeout=5)
    print(f"Status Code: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Status: {data.get('s')}")
    else:
        print(f"Response: {r.text}")

test_finnhub_candle_recent("AAPL")
test_finnhub_candle_recent("NVDA")
