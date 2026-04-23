import time
import json
import os
import sys

def test_connection():
    print("STEP 1: Checking environment variables...", flush=True)
    print(f"GEMINI_API_KEY in env: {bool(os.environ.get('GEMINI_API_KEY'))}", flush=True)
    print(f"GOOGLE_API_KEY in env: {bool(os.environ.get('GOOGLE_API_KEY'))}", flush=True)

    print("\nSTEP 2: Loading config.json...", flush=True)
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        api_key = config.get("GEMINI_API_KEY")
        print(f"API Key from config: {api_key[:10]}...", flush=True)
    except Exception as e:
        print(f"Error loading config: {e}", flush=True)
        return

    print("\nSTEP 3: Importing google-genai...", flush=True)
    try:
        from google import genai
        print("Import successful.", flush=True)
    except Exception as e:
        print(f"Import failed: {e}", flush=True)
        return

    print("\nSTEP 4: Initializing genai.Client...", flush=True)
    try:
        client = genai.Client(api_key=api_key)
        print("Client initialized.", flush=True)
    except Exception as e:
        print(f"Client init failed: {e}", flush=True)
        return

    model = config.get("MODEL_PRO_1", "gemini-2.5-pro")
    print(f"\nSTEP 5: Testing model: {model} with 10s timeout...", flush=True)
    
    import threading
    
    def call_api():
        try:
            response = client.models.generate_content(
                model=model,
                contents="ping"
            )
            print(f"SUCCESS: {response.text[:50]}", flush=True)
        except Exception as e:
            print(f"FAILED: {e}", flush=True)

    thread = threading.Thread(target=call_api)
    thread.start()
    thread.join(timeout=10)
    
    if thread.is_alive():
        print("STALL DETECTED: API call took longer than 10 seconds.", flush=True)
        print("Check your internet connection or proxy settings.", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    test_connection()
