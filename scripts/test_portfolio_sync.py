import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from fetch_stocks import _load_ssot_tickers

def test_portfolio_sync():
    print("Testing SSOT ticker extraction...")
    tickers = _load_ssot_tickers()
    print(f"Loaded tickers: {tickers}")
    assert isinstance(tickers, list), "Should return a list of tickers"
    assert len(tickers) > 0, "Should have loaded at least one ticker"
    print("SSOT ticker extraction verified successfully!")

if __name__ == "__main__":
    test_portfolio_sync()
