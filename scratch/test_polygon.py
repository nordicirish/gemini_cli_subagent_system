import requests
import json
from datetime import datetime, timedelta

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

POLYGON_API_KEY = config.get("POLYGON_API_KEY")

def test_polygon_candle(symbol):
    print(f"Testing Polygon Candle for {symbol}...")
    end_dt = datetime.now().strftime('%Y-%m-%d')
    start_dt = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start_dt}/{end_dt}?adjusted=true&sort=asc&apiKey={POLYGON_API_KEY}"
    r = requests.get(url, timeout=5)
    print(f"Status Code: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Results Count: {data.get('resultsCount', 0)}")
    else:
        print(f"Response: {r.text}")

test_polygon_candle("AAPL")
