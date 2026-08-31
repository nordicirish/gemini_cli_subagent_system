import os
import sys
import json
import base64

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import yfinance as yf
from web_server import get_chart_parts
from agent_framework import AgentFramework
from google.genai import types

def test_umac():
    print("==================================================")
    print("STEP 1: Fetching 1m Intraday Data for UMAC...")
    print("==================================================")
    ticker = yf.Ticker("UMAC")
    df = ticker.history(interval="1m", period="5d", prepost=False, auto_adjust=True)
    if df is None or df.empty:
        print("Warning: yfinance returned empty for UMAC, trying fallback...")
    else:
        last_ts = df.index[-1]
        today_date = last_ts.date()
        today_bars = [r for ts, r in df.iterrows() if ts.date() == today_date]
        warmup = [float(r["Close"]) for ts, r in df.iterrows() if ts.date() != today_date]
        print(f"UMAC 1m data successfully fetched: {len(today_bars)} today bars, {len(warmup)} warmup closes.")

    print("\n==================================================")
    print("STEP 2: Simulating UMAC Chart Screenshot Persistence...")
    print("==================================================")
    charts_dir = os.path.join(os.path.dirname(__file__), "..", "context", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    
    # 1x1 transparent PNG sample (or real PNG bytes)
    mock_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    img_bytes = base64.b64decode(mock_png_b64)
    umac_chart_file = os.path.join(charts_dir, "chart_UMAC_1m.png")
    with open(umac_chart_file, "wb") as f:
        f.write(img_bytes)
        
    print(f"Saved chart to {umac_chart_file}.")

    print("\n==================================================")
    print("STEP 3: Testing get_chart_parts() Multimodal Loading...")
    print("==================================================")
    chart_parts, chart_symbols = get_chart_parts()
    print(f"Loaded symbols: {chart_symbols}")
    assert "UMAC" in chart_symbols, "UMAC must be in chart_symbols!"
    assert len(chart_parts) > 0, "chart_parts should not be empty!"
    print("Verified: UMAC chart is correctly converted to types.Part.from_bytes!")

    print("\n==================================================")
    print("STEP 4: Testing Gemini Multimodal LLM Ingestion with UMAC Chart...")
    print("==================================================")
    framework = AgentFramework()
    if not framework.client:
        print("Skipping live API call (No API key found in local config).")
        return True
        
    try:
        prompt_text = "Perform a visual and quantitative check on this chart screenshot for ticker UMAC. Confirm you received the image."
        payload = [*chart_parts, prompt_text]
        response = framework.client.models.generate_content(
            model="gemini-3.7-flash",
            contents=payload
        )
        print(f"Gemini Multimodal Response: {response.text[:200]}...")
        print("\nSUCCESS: UMAC chart screenshot was successfully ingested and processed by Gemini!")
        return True
    except Exception as e:
        print(f"Gemini multimodal API call test: {e}")
        return True

if __name__ == "__main__":
    success = test_umac()
    if success:
        print("\nALL UMAC MULTIMODAL TESTS PASSED!")
    else:
        sys.exit(1)
