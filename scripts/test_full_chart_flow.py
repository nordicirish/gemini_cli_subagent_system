import os
import sys
import json
import base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from web_server import get_chart_parts
from fetch_stocks import CHART_SCREENSHOTS_BUFFER

def test_full_flow():
    print("Testing get_chart_parts helper with real saved images...")
    parts = get_chart_parts()
    print(f"Loaded {len(parts)} chart part(s).")
    assert len(parts) > 0, "Should have loaded at least one chart part from context/charts/"
    print("Multimodal parts loading verified successfully!")

if __name__ == "__main__":
    test_full_flow()
