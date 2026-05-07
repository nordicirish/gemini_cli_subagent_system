import requests
import json
import os

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

FINNHUB_API_KEY = config.get("FINNHUB_API_KEY")

def test_finnhub_quote(symbol):
    print(f"Testing Quote for {symbol}...")
    url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_API_KEY}"
    r = requests.get(url, timeout=5)
    print(f"Status Code: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Response: {data}")
    else:
        print(f"Response: {r.text}")

def test_finnhub_candle(symbol):
    print(f"Testing Candle for {symbol}...")
    url = f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}&resolution=D&from=1710000000&to=1715000000&token={FINNHUB_API_KEY}"
    r = requests.get(url, timeout=5)
    print(f"Status Code: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        print(f"Status: {data.get('s')}")
    else:
        print(f"Response: {r.text}")

test_finnhub_quote("AAPL")
test_finnhub_candle("AAPL")
