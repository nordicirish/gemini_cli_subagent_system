"""
Test decoupled tools: perform_web_forensic_search and fetch_dynamic_scout_tickers
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

def main():
    print("Testing decoupled tools (Zero Cloud API Calls)...")
    import tools
    import fetch_stocks

    print("[Test 1] Testing perform_web_forensic_search via Antigravity...")
    res = tools.perform_web_forensic_search("Verify market status and SPY price level")
    print("Forensic search result preview:", res[:200] if res else "None")
    assert res and not res.startswith("Error"), f"Search failed: {res}"
    print("✅ perform_web_forensic_search passed!")

    print("[Test 2] Testing _get_dynamic_scout_tickers via Antigravity...")
    tickers = fetch_stocks._get_dynamic_scout_tickers("Semiconductors")
    print("Scout tickers returned:", tickers)
    assert isinstance(tickers, list) and len(tickers) > 0, "No tickers returned!"
    print("✅ _get_dynamic_scout_tickers passed!")

    print("\nAll decoupled tools passed successfully!")
    sys.exit(0)

if __name__ == "__main__":
    main()
