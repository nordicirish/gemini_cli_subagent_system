import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fetch_stocks

def test_daemon_scope():
    print(f"Initial ALL_TICKERS: {fetch_stocks.ALL_TICKERS}")
    assert len(fetch_stocks.ALL_TICKERS) > 0, "ALL_TICKERS must not be empty"
    print("ALL_TICKERS scope and initialization verified successfully!")

if __name__ == "__main__":
    test_daemon_scope()
